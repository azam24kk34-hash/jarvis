"""Мозок Jarvis: Claude API + цикл виклику інструментів + пам'ять розмови."""
import datetime as dt
import json
import platform
import re
import threading

import anthropic

from .config import app_dir

MAX_HISTORY = 30  # повідомлень у пам'яті розмови

WEEKDAYS = ["понеділок", "вівторок", "середа", "четвер", "п'ятниця", "субота", "неділя"]


def system_prompt(cfg, memory=None):
    title = cfg.get("user_title") or "сер"
    name = cfg.get("user_name")
    return f"""Ти — J.A.R.V.I.S. (Just A Rather Very Intelligent System), персональний ШІ-асистент, як у Тоні Старка з фільмів «Залізна людина».
Ти працюєш на Windows-комп'ютері користувача і можеш керувати ним через інструменти.

Характер (як у фільмах): бездоганно ввічливий британський дворецький-інженер. Спокійний навіть у критичних ситуаціях, впевнений,
з тонким сухим гумором і легкою іронією — можеш делікатно підколоти («Як завжди, сер, ваша скромність вражає.»,
«Я дозволив собі…», «Чи не бажаєте спершу…»). Відданий, кмітливий, на крок попереду: передбачай потреби, сам пропонуй наступний крок,
повідомляй цифри й відсотки як звіт («Діагностику завершено. Усі системи в нормі, навантаження процесора 12 відсотків.»).
Якщо користувач ризикує (вимкнути ПК з незбереженою роботою, тощо) — тактовно застережи, як Джарвіс застерігав Тоні.
Звертайся до користувача «{title}»{f' (ім`я: {name})' if name else ''}. Говори ВИКЛЮЧНО українською.

Твої відповіді озвучуються голосом, тому:
- відповідай коротко: зазвичай 1–2 речення, максимум 4, якщо не просять детально;
- жодного markdown, списків, емодзі, посилань чи коду — лише живе мовлення;
- числа й одиниці пиши так, як їх зручно вимовити (наприклад «12 градусів», «о 15:30»).
Твій текст озвучується одразу, по мірі написання. Тому перед викликом інструмента, якщо дія займе час, спершу дуже коротко скажи,
що робиш («Запускаю діагностику, {title}.», «Секунду, виводжу дані.») — і не повторюй цих слів після дії.
Після дії коротко підтверди в стилі Джарвіса («Зроблено, {title}.», «Як завжди, до ваших послуг.»).
Протоколи: «протокол вечірка», «запусти протокол робота» → run_protocol. Користувач може голосом створити свій протокол → create_protocol.
Музика: користувач слухає YouTube Music. Пісню/виконавця вмикай через play_music або youtube_music(play) — відтворюється одразу.
«Мою музику», «мої лайки», «щось моє» → youtube_music(liked, shuffle=true); «плейлист …» → youtube_music(playlist, query=назва);
пауза/далі/назад/«що грає» → youtube_music(pause/next/previous/now_playing). Якщо інструмент каже, що користувач не увійшов, — youtube_music(login) і поясни, що зробити.
Браузер Джарвіса: щоб щось зробити на сайті (знайти, натиснути, заповнити, прочитати), використовуй browser_open → browser_snapshot →
browser_click / browser_type (з номерами елементів) → browser_read або browser_look, щоб перевірити результат. Дій послідовно, не вигадуй номери.
Не вводь паролі й платіжні дані за користувача і не підтверджуй покупки чи відправлення повідомлень без його явного «так».
Брифінг (ранковий/вечірній або «що в мене сьогодні») = привітання, погода (get_weather), події календаря (get_calendar), нагадування (list_reminders), 1–2 головні новини (веб-пошук), 4–7 речень.
Коли користувач каже щось важливе про себе (вподобання, імена близьких, розпорядок) або просить «запам'ятай» — збережи через remember.
«Онови себе», «встанови оновлення» → update_self.
Монітори: «OBS на другий екран, Chrome на перший» → place_window для кожної програми (monitor 1 = головний).
«Запам'ятай розташування як стрім» → save_layout; «розстав робоче місце стрім» → apply_layout.
OBS: трансляція, запис, сцени, мікрофон → інструмент obs (через WebSocket, надійно). Кнопки в інших програмах → app_buttons, потім click_in_app.
Програми з минулого разу («відкрий програми, що були відкриті», «віднови все як було») → restore_session; «чим я користувався» → recent_apps.
Вікна: «згорни все» → show_desktop, «закрий це вікно» → close, «перейди в Телеграм» → focus. Диктування тексту → type_text.
Діяй одразу, не перепитуй дрібниць. Перед вимкненням чи перезавантаженням ПК обов'язково отримай підтвердження.
Для новин, курсів, фактів про поточні події використовуй веб-пошук.
Якщо повідомлення позначене [без звертання], це фраза, почута в кімнаті без слова «Джарвіс». Якщо вона явно не до тебе
(розмова з іншою людиною, телевізор, пісня, телефонна розмова, уривок без сенсу) — відповідай РІВНО одним символом «—» і нічого не роби.
Якщо це питання чи прохання до тебе — відповідай як звичайно.
Якщо користувач просто вітається — відповідай у стилі Джарвіса, можеш згадати час доби.

Поточний час і дата приходять у квадратних дужках на початку кожного повідомлення користувача.
Місто користувача: {cfg.get('city')}. ОС: {platform.system()} {platform.release()}.""" + (memory.prompt_block() if memory else "")


def time_context():
    now = dt.datetime.now()
    return f"[{now:%H:%M}, {WEEKDAYS[now.weekday()]}, {now:%d.%m.%Y}]"


class Brain:
    def __init__(self, config, toolbox, on_status=None, usage=None):
        self.config = config
        self.toolbox = toolbox
        self.on_status = on_status or (lambda *a: None)
        self.usage = usage
        self.history = []
        self._lock = threading.Lock()
        self._web_search_ok = True
        self._log = app_dir() / "conversation.log"

    def reset(self):
        with self._lock:
            self.history = []

    def ready(self):
        return bool(self.config.get("api_key"))

    def _client(self):
        key = self.config.get("api_key")
        if not key:
            raise RuntimeError("NO_KEY")
        return anthropic.Anthropic(api_key=key, max_retries=2, timeout=90)

    def _tools(self):
        tools = list(self.toolbox.definitions())
        if self.config.get("web_search") and self._web_search_ok:
            tools.append({"type": "web_search_20250305", "name": "web_search", "max_uses": 3,
                          "user_location": {"type": "approximate", "city": self.config.get("city"), "country": "UA",
                                            "timezone": "Europe/Kyiv"}})
        return tools

    def _trim(self):
        # обрізаємо історію лише на межі звичайного текстового повідомлення користувача
        while len(self.history) > MAX_HISTORY:
            self.history.pop(0)
            while self.history and not (self.history[0]["role"] == "user" and isinstance(self.history[0]["content"], str)):
                self.history.pop(0)

    @staticmethod
    def _serialize(blocks):
        out = []
        for b in blocks:
            if b.type == "text":
                if getattr(b, "citations", None):
                    # цитати веб-пошуку повертаємо без змін (API перевіряє encrypted_index)
                    out.append(b.model_dump(mode="json", exclude_none=True))
                elif b.text:
                    out.append({"type": "text", "text": b.text})
            elif b.type == "tool_use":
                out.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})
            elif b.type in ("thinking", "redacted_thinking"):
                continue
            else:  # server_tool_use, web_search_tool_result тощо
                out.append(b.model_dump(mode="json", exclude_none=True))
        return out

    def ask(self, text: str, on_text=None) -> str:
        """Запит до Claude зі стрімінгом: on_text(delta) отримує текст по мірі генерації,
        тож Джарвіс починає говорити першим реченням, поки решта ще пишеться."""
        if not self.config.get("api_key"):
            raise RuntimeError("NO_KEY")
        on_text = on_text or (lambda t: None)
        with self._lock:
            user_msg = {"role": "user", "content": f"{time_context()}\n{text}"}
            self.history.append(user_msg)
            self._trim()
            start = len(self.history) - 1  # для відкату при помилці
            client = self._client()
            final_text = []
            healed = False
            for _ in range(8):  # максимум кроків з інструментами
                self.on_status("thinking", None)
                try:
                    resp = self._request(client, on_text)
                except anthropic.BadRequestError as e:
                    print("BadRequest:", str(e)[:500])
                    if "web_search" in str(e) and self._web_search_ok:
                        self._web_search_ok = False  # якщо веб-пошук недоступний — працюємо без нього
                        continue
                    if start > 0 and not healed:
                        # самовідновлення: стара історія могла зіпсуватись — починаємо розмову з чистого аркуша
                        healed = True
                        self.history = [user_msg]
                        start = 0
                        continue
                    del self.history[start:]
                    raise
                except Exception as e:
                    print("API error:", repr(e)[:300])
                    del self.history[start:]
                    raise
                if self.usage:
                    self.usage.add(self.config.get("model"), resp.usage)
                if resp.stop_reason == "max_tokens":
                    # відповідь обрізано: незавершений виклик інструмента без результату зламав би історію
                    resp.content = [b for b in resp.content if b.type == "text"]
                content = self._serialize(resp.content)
                self.history.append({"role": "assistant", "content": content or [{"type": "text", "text": "…"}]})
                final_text += [b.text for b in resp.content if b.type == "text" and b.text]

                if resp.stop_reason == "pause_turn":
                    continue
                if resp.stop_reason != "tool_use":
                    break
                results = []
                for b in resp.content:
                    if b.type == "tool_use":
                        self.on_status("tool", b.name)
                        out = self.toolbox.run(b.name, b.input)
                        results.append({"type": "tool_result", "tool_use_id": b.id,
                                        "content": out if isinstance(out, list) else str(out)})
                        self._write_log(f"[tool] {b.name} {json.dumps(b.input, ensure_ascii=False)} -> {str(out)[:300]}")
                self.history.append({"role": "user", "content": results})
            answer = re.sub(r"[*#`_]+", "", " ".join(final_text)).strip()
            self._write_log(f"[user] {text}\n[jarvis] {answer}")
            return answer or "Готово, сер."

    def note(self, text, reply):
        """Записати в історію команду, виконану локально (щоб Claude знав контекст)."""
        with self._lock:
            self.history.append({"role": "user", "content": f"{time_context()}\n{text}"})
            self.history.append({"role": "assistant", "content": [{"type": "text", "text": reply}]})
            self._trim()

    def _request(self, client, on_text):
        """Один стрімінговий запит. Кешування промпту: інструменти й системний промпт незмінні,
        тож повторні запити коштують ~5–10% ціни вхідних токенів."""
        system = [{"type": "text", "text": system_prompt(self.config, getattr(self.toolbox, "memory", None)),
                   "cache_control": {"type": "ephemeral"}}]
        with client.messages.stream(model=self.config.get("model"), max_tokens=2048, system=system,
                                    tools=self._tools(), messages=self.history,
                                    extra_body={"cache_control": {"type": "ephemeral"}}) as stream:
            for event in stream:
                if event.type == "text":
                    on_text(event.text)
                elif event.type == "content_block_start":
                    blk = event.content_block
                    if blk.type == "tool_use":
                        self.on_status("tool", blk.name)
                    elif blk.type == "server_tool_use":
                        self.on_status("web", "")
                elif event.type == "content_block_stop" and getattr(event.content_block, "type", "") == "server_tool_use":
                    self.on_status("web", (event.content_block.input or {}).get("query", ""))
            return stream.get_final_message()

    def _write_log(self, line):
        try:
            with open(self._log, "a", encoding="utf-8") as f:
                f.write(f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} {line}\n")
        except Exception:
            pass

    def self_test(self):
        """Перевірка ключа й моделі без витрат токенів (Models API)."""
        try:
            m = self._client().models.retrieve(self.config.get("model"))
            return True, getattr(m, "display_name", None) or self.config.get("model")
        except RuntimeError:
            return False, "немає ключа API"
        except anthropic.AuthenticationError:
            return False, "ключ API недійсний"
        except anthropic.NotFoundError:
            return False, f"модель {self.config.get('model')} не знайдена"
        except anthropic.PermissionDeniedError:
            return False, "немає доступу до моделі"
        except Exception as e:
            return False, f"немає зв'язку ({type(e).__name__})"


def make_brain(config, toolbox, on_status=None, usage=None):
    """Claude (платно) або безкоштовні Gemini / Groq / локальна Ollama — за налаштуванням provider."""
    if (config.get("provider") or "anthropic") == "anthropic":
        return Brain(config, toolbox, on_status=on_status, usage=usage)
    from .brain_oai import OpenAIBrain
    return OpenAIBrain(config, toolbox, on_status=on_status, usage=usage)
