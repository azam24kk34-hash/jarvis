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
    "gemini": {"name": "Google Gemini", "base": "https://generativelanguage.googleapis.com/v1beta/openai/",
               "model": "gemini-3.8-flash", "key_url": "https://aistudio.google.com/apikey", "vision": True},
    "groq": {"name": "Groq", "base": "https://api.groq.com/openai/v1",
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


class OpenAIBrain:
    def __init__(self, config, toolbox, on_status=None, usage=None):
        self.config = config
        self.toolbox = toolbox
        self.on_status = on_status or (lambda *a: None)
        self.usage = usage
        self.history = []
        self._lock = threading.Lock()

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

    def _client(self):
        import openai
        key = self._key()
        if not key:
            raise RuntimeError("NO_KEY")
        return openai.OpenAI(api_key=key, base_url=self.info["base"], max_retries=2, timeout=90)

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
        with self._lock:
            self.history.append({"role": "user", "content": f"{time_context()}\n{text}"})
            self._trim()
            start = len(self.history) - 1
            client = self._client()
            final = []
            try:
                for _ in range(8):
                    self.on_status("thinking", None)
                    content, calls = self._request(client, on_text)
                    msg = {"role": "assistant", "content": content or None}
                    if calls:
                        msg["tool_calls"] = [{"id": c["id"], "type": "function",
                                              "function": {"name": c["name"], "arguments": c["args"] or "{}"}} for c in calls]
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

    def _request(self, client, on_text):
        system = {"role": "system", "content": system_prompt(self.config, getattr(self.toolbox, "memory", None))}
        stream = client.chat.completions.create(model=self.model, messages=[system] + self.history,
                                                tools=self._tools(), stream=True, temperature=0.6)
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
                c = calls.setdefault(i, {"id": "", "name": "", "args": ""})
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
