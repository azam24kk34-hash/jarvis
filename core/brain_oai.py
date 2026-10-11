"""Безкоштовні «мізки» Джарвіса через OpenAI-сумісний API:
- Google Gemini (безкоштовний ключ на aistudio.google.com),
- Groq (безкоштовний ключ на console.groq.com),
- Ollama (повністю локально на вашому ПК, без інтернету й без ключа).
Інтерфейс той самий, що й у core.brain.Brain: ask(), note(), reset(), self_test(), ready()."""
import json
import re
import threading

from .brain import MAX_HISTORY, system_prompt, time_context

PROVIDERS = {
    # effort — наскільки довго модель «думає» перед відповіддю; мінімум = найшвидша відповідь
    "openai": {"name": "OpenAI", "base": "https://api.openai.com/v1", "model": "gpt-6-luna", "effort": "none",
               "key_url": "https://platform.openai.com/api-keys", "vision": True},
    "gemini": {"name": "Google Gemini", "base": "https://generativelanguage.googleapis.com/v1beta/openai/",
               "model": "gemini-3.8-flash", "effort": "minimal", "key_url": "https://aistudio.google.com/apikey", "vision": True},
    "groq": {"name": "Groq", "base": "https://api.groq.com/openai/v1", "effort": "low",
             "model": "openai/gpt-oss-120b", "key_url": "https://console.groq.com/keys", "vision": False},
    "ollama": {"name": "Ollama (локально)", "base": "http://localhost:11434/v1",
               "model": "qwen2.5:7b", "key_url": "https://ollama.com/download", "vision": False, "nokey": True},
}

WEB_SEARCH_TOOL = {
    "name": "web_search",
    "description": "Пошук в інтернеті: новини, курси валют, факти про поточні події. Повертає заголовки, короткі уривки й посилання.",
    "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
}


def to_openai_tools(defs):
    return [{"type": "function", "function": {"name": d["name"], "description": d["description"],
                                              "parameters": d["input_schema"]}} for d in defs]


# Чи потрібні інструменти (дії на ПК, інтернет). Якщо ні — «легкий» запит: без 50 описів інструментів,
# коротка інструкція, мінімум роздумів → відповідь за 1–2 секунди.
ACTION_RE = re.compile(
    r"(відкр|запуст|увімк|ввімк|включ|вимк|закр|постав|знайд|пошук|загугл|натисн|напиш|надрук|набер|зроб|покаж|видал|створ|"
    r"нагада|онов|перемк|збереж|розстав|перезав|подив|глян|прочит|скопі|встав|перенес|переміст|згорн|розгорн|клікн|"
    r"екран|браузер|сайт|вкладк|музик|пісн|трек|плейлист|ютуб|youtube|новин|курс|погод|календар|файл|папк|буфер|вікн|монітор|"
    r"обс|obs|трансляц|стрім|запис|сцен|гучн|звук|протокол|таймер|будильник|запам|забудь|фраз|програм|додат|"
    r"сьогодні|зараз|останн|новий|нові|нова|ціна|ціни|коштує|скільки кошт|хто виграв|рахунок|матч|результат|долар|євро|біткоїн|"
    r"діагност|систем|процес|пам'ят|диск|телеграм|хром|chrome|steam|стім|discord|дискорд|оновлен|рік|202\d)", re.I)

LIGHT_PROMPT = """Ти — J.A.R.V.I.S., ШІ-асистент Тоні Старка з фільмів «Залізна людина»: ввічливий британський дворецький-інженер,
спокійний, з тонким сухим гумором. Звертайся «{title}». Говори ВИКЛЮЧНО українською.
Відповідь озвучується: 1–2 короткі речення (максимум 4, якщо просять детально), без markdown, списків, емодзі й посилань.
Якщо для відповіді потрібні свіжі дані з інтернету — скористайся web_search.
Якщо повідомлення позначене [без звертання] і явно не до тебе — відповідай рівно одним символом «—»."""


def needs_tools(text):
    return bool(ACTION_RE.search(text or ""))


class OpenAIBrain:
    def __init__(self, config, toolbox, on_status=None, usage=None):
        self.config = config
        self.toolbox = toolbox
        self.on_status = on_status or (lambda *a: None)
        self.usage = usage
        self.history = []
        self._lock = threading.Lock()
        self._bad_effort = {}

    # ------------------------------------------------------------ налаштування провайдера
    @property
    def provider(self):
        return self.config.get("provider")

    @property
    def info(self):
        return PROVIDERS[self.provider]

    @property
    def model(self):
        return (self.config.get(f"{self.provider}_model") or self.info["model"]).strip()

    def _key(self):
        return "ollama" if self.info.get("nokey") else (self.config.get(f"{self.provider}_key") or "").strip()

    def ready(self):
        return bool(self._key())

    def _client(self, provider=None):
        import httpx
        import openai
        provider = provider or self.provider
        info = PROVIDERS[provider]
        key = "ollama" if info.get("nokey") else (self.config.get(f"{provider}_key") or "").strip()
        if not key:
            raise RuntimeError("NO_KEY")
        # коротке очікування: краще швидко перемкнутися на запасний варіант, ніж чекати хвилину
        t = httpx.Timeout(connect=6, read=60 if provider == "ollama" else 20, write=10, pool=6)
        return openai.OpenAI(api_key=key, base_url=info["base"], max_retries=0, timeout=t)

    def _candidates(self):
        """(провайдер, модель) по черзі: основний → швидша «lite» модель того ж провайдера → інші провайдери з ключем."""
        out = [(self.provider, self.model)]
        lite = self._lite_model()
        if lite and lite != self.model:
            out.append((self.provider, lite))
        for p in ("groq", "gemini", "openai"):
            if p != self.provider and (self.config.get(f"{p}_key") or "").strip():
                out.append((p, (self.config.get(f"{p}_model") or PROVIDERS[p]["model"]).strip()))
        return out

    def _lite_model(self):
        if self.provider != "gemini":
            return None
        if getattr(self, "_lite", "?") != "?":
            return self._lite
        self._lite = None
        try:
            ids = [m.id.split("/")[-1] for m in self._client().models.list()]
            lites = sorted(i for i in ids if "flash-lite" in i and "preview" not in i and "tts" not in i)
            self._lite = lites[-1] if lites else None
        except Exception:
            pass
        return self._lite

    def reset(self):
        with self._lock:
            self.history = []

    def _trim(self):
        while len(self.history) > MAX_HISTORY:
            self.history.pop(0)
            while self.history and not (self.history[0]["role"] == "user" and isinstance(self.history[0]["content"], str)):
                self.history.pop(0)

    def note(self, text, reply):
        with self._lock:
            self.history.append({"role": "user", "content": f"{time_context()}\n{text}"})
            self.history.append({"role": "assistant", "content": reply})
            self._trim()

    def _tools(self):
        return to_openai_tools(list(self.toolbox.definitions()) + [WEB_SEARCH_TOOL])

    # ------------------------------------------------------------ основний цикл
    def ask(self, text, on_text=None):
        on_text = on_text or (lambda t: None)
        if not self.ready():
            raise RuntimeError("NO_KEY")
        light = not needs_tools(text)
        ctx = ""
        pc = getattr(self.toolbox, "pc_context", None)
        if pc and not light:
            try:
                ctx = pc()
            except Exception:
                ctx = ""
        with self._lock:
            self.history.append({"role": "user", "content": f"{time_context()}{ctx}\n{text}"})
            self._trim()
            start = len(self.history) - 1
            final = []
            try:
                for _ in range(8):
                    self.on_status("thinking", None)
                    content, calls = self._request_any(on_text, light)
                    msg = {"role": "assistant", "content": content or None}
                    if calls:
                        msg["tool_calls"] = []
                        for c in calls:
                            tc = {"id": c["id"], "type": "function",
                                  "function": {"name": c["name"], "arguments": c["args"] or "{}"}}
                            if c.get("extra"):  # Gemini: «thought_signature» треба повертати разом із викликом
                                tc["extra_content"] = c["extra"]
                            msg["tool_calls"].append(tc)
                    self.history.append(msg)
                    if content:
                        final.append(content)
                    if not calls:
                        break
                    images = []
                    for c in calls:
                        self.on_status("tool", c["name"])
                        try:
                            args = json.loads(c["args"] or "{}")
                        except Exception:
                            args = {}
                        if c["name"] == "web_search":
                            self.on_status("web", args.get("query", ""))
                        out = self.toolbox.run(c["name"], args)
                        if isinstance(out, list):  # текст + зображення (знімок екрана)
                            txt = " ".join(p.get("text", "") for p in out if p.get("type") == "text")
                            images += [p["source"]["data"] for p in out if p.get("type") == "image"]
                            out = txt or "Зображення додано нижче."
                        self.history.append({"role": "tool", "tool_call_id": c["id"], "content": str(out)[:12000]})
                    if images and self.info.get("vision"):
                        self.history.append({"role": "user", "content": [
                            {"type": "text", "text": "Ось зображення з інструмента:"}] + [
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{d}"}} for d in images]})
            except Exception:
                del self.history[start:]
                raise
            return re.sub(r"[*#`_]+", "", " ".join(final)).strip() or "Готово, сер."

    def _messages(self, provider, light):
        mem = getattr(self.toolbox, "memory", None)
        if light:
            sysmsg = LIGHT_PROMPT.format(title=self.config.get("user_title") or "сер") + (mem.prompt_block() if mem else "")
            # лише текст розмови — без викликів інструментів (їх у легкому запиті немає)
            hist = [{"role": m["role"], "content": m["content"]} for m in self.history
                    if m["role"] in ("user", "assistant") and isinstance(m.get("content"), str) and m["content"]]
        else:
            sysmsg = system_prompt(self.config, mem)
            hist = self.history
            if provider != "gemini":  # чужі поля Gemini іншим провайдерам не відправляємо
                hist = [dict(m, tool_calls=[{k: v for k, v in tc.items() if k != "extra_content"} for tc in m["tool_calls"]])
                        if m.get("tool_calls") else m for m in hist]
        return [{"role": "system", "content": sysmsg}] + hist

    def _request_any(self, on_text, light):
        """Запит до основної моделі; якщо вона перевантажена чи мовчить — одразу пробуємо запасні."""
        import openai
        cands, extended, i = [(self.provider, self.model)], False, 0
        while True:
            prov, model = cands[i]
            started = {"v": False}

            def tick(t):
                started["v"] = True
                on_text(t)
            try:
                return self._request(prov, model, tick, light)
            except (openai.APITimeoutError, openai.APIConnectionError, openai.InternalServerError,
                    openai.RateLimitError, openai.NotFoundError) as e:
                if started["v"]:
                    raise
                if not extended:
                    extended = True
                    cands += [c for c in self._candidates()[1:] if c not in cands]
                if i + 1 >= len(cands):
                    raise
                print(f"Мозок {prov}/{model} недоступний ({type(e).__name__}) — пробую {cands[i + 1][0]}/{cands[i + 1][1]}")
                self.on_status("thinking", None)
                i += 1

    def _request(self, provider, model, on_text, light=False):
        import openai
        client = self._client(provider)
        info = PROVIDERS[provider]
        tools = to_openai_tools([WEB_SEARCH_TOOL]) if light else self._tools()
        kw = dict(model=model, messages=self._messages(provider, light), tools=tools, stream=True)
        if light:
            kw["max_tokens" if provider != "openai" else "max_completion_tokens"] = 600
        effort = self.config.get(f"{provider}_effort") or info.get("effort")
        bad = self._bad_effort.setdefault(provider, set())
        efforts = [e for e in ([effort] + ["low"] if effort and effort != "low" else [effort]) if e and e not in bad]
        if efforts:
            kw["reasoning_effort"] = efforts[0]
        else:
            kw["temperature"] = 0.6
        while True:
            try:
                stream = client.chat.completions.create(**kw)
                break
            except openai.BadRequestError as e:
                if "reasoning" not in str(e).lower() or "reasoning_effort" not in kw:
                    raise
                bad.add(kw["reasoning_effort"])
                print(f"reasoning_effort={kw['reasoning_effort']} не підтримується:", str(e)[:200])
                rest = [x for x in efforts if x not in bad]
                if rest:
                    kw["reasoning_effort"] = rest[0]
                else:
                    kw.pop("reasoning_effort")
                    kw["temperature"] = 0.6
        content, calls, usage = [], {}, None
        for chunk in stream:
            if getattr(chunk, "usage", None):
                usage = chunk.usage
            if not chunk.choices:
                continue
            d = chunk.choices[0].delta
            if getattr(d, "content", None):
                content.append(d.content)
                on_text(d.content)
            for tc in getattr(d, "tool_calls", None) or []:
                i = tc.index if tc.index is not None else len(calls)
                c = calls.setdefault(i, {"id": "", "name": "", "args": "", "extra": None})
                ex = (getattr(tc, "model_extra", None) or {}).get("extra_content")
                if ex:
                    c["extra"] = ex
                if tc.id:
                    c["id"] = tc.id
                if tc.function and tc.function.name:
                    c["name"] += tc.function.name
                if tc.function and tc.function.arguments:
                    c["args"] += tc.function.arguments
        if self.usage:
            self.usage.add_free(usage)
        out = []
        for i in sorted(calls):
            c = calls[i]
            c["id"] = c["id"] or f"call_{i}"
            if c["name"]:
                out.append(c)
        return "".join(content).strip(), out

    def self_test(self):
        if not self.ready():
            return False, "немає ключа API"
        try:
            ids = [m.id for m in self._client().models.list()]
            short = [i.split("/")[-1] for i in ids]
            if ids and self.model not in ids and self.model not in short:
                return False, f"модель {self.model} недоступна; є: {', '.join(short[:6])}"
            return True, f"{self.info['name']} · {self.model}"
        except Exception as e:
            name = type(e).__name__
            if name == "AuthenticationError":
                return False, "ключ недійсний"
            if name == "APIConnectionError" and self.provider == "ollama":
                return False, "Ollama не запущена"
            return False, f"немає зв'язку ({name})"
