"""J.A.R.V.I.S. — точка входу: HUD-вікно (pywebview) + голос + мозок."""
import base64
import datetime as dt
import json
import os
import queue
import random
import re
import threading
import time

import psutil
import webview

from core.brain import make_brain
import sys

from core import autostart, log
from core.config import Config, resource_path
from core.apps import AppTracker, pretty
from core.browser import Browser
from core.memory import Memory
from core.monitor import Monitor
from core.protocols import Protocols
from core.quick import QuickCommands
from core.reminders import Reminders
from core.tools import Toolbox
from core.usage import Usage
from core.voice import WAKE_REPLIES, Voice

from core.version import VERSION  # noqa: E402
OFFLINE_LINE = "Сер, втрачено з'єднання з інтернетом. Частина моїх систем недоступна."

BRIEFING_PROMPT = ("[СИСТЕМА] Користувач щойно увімкнув комп'ютер — це перший запуск сьогодні. "
                   "Зроби ранковий брифінг як Джарвіс у фільмі: привітайся відповідно до часу доби, назви час, "
                   "погоду (get_weather на сьогодні), події календаря на сьогодні (get_calendar; якщо не підключено — пропусти мовчки), "
                   "нагадування на сьогодні (list_reminders), одну-дві головні новини України "
                   "(веб-пошук) і побажай продуктивного дня. 4–6 речень, без вступу «ось брифінг».")


class Jarvis:
    def __init__(self):
        self.config = Config()
        if not self.config.get("listen_v74"):  # оновлення 7.4: за замовчуванням — лише після «Джарвіс»
            self.config.update({"listen_mode": "wake", "listen_v74": True})
        self.window = None
        self._audio_done = threading.Event()
        self._commands = queue.Queue()
        self.reminders = Reminders(self._reminder_fired)
        self.protocols = Protocols()
        self.memory = Memory()
        self.browser = Browser(self.config)
        self.apps = AppTracker()
        self.toolbox = Toolbox(self.config, self.reminders, self.protocols,
                               ui_notify=lambda kind, data: self.js("HUD.holo", kind, data), memory=self.memory,
                               browser=self.browser, apps=self.apps)
        self.usage = Usage(on_budget=self._budget_alert)
        self.usage.set_budget_getter(lambda: self.config.get("daily_budget"))
        self.toolbox.on_quit = self._quit_for_update
        self.voice.music_playing = lambda: self.browser.music_playing
        self.quick = QuickCommands(self.config, self.toolbox, self.protocols)
        self._ducked = False
        self._pending_restore = 0.0
        self.brain = make_brain(self.config, self.toolbox, on_status=self._brain_status, usage=self.usage)
        self.voice = Voice(self.config, on_command=self.submit, on_state=self._voice_state,
                           play_audio=self._play_audio, on_heard=lambda t: self.js("HUD.heard", t),
                           on_stop=lambda: self.js("HUD.stopAudio"),
                           on_notice=lambda t: self._alert(t, "info"))
        self.monitor = Monitor(self.config, on_alert=self._alert, on_welcome=self._welcome)
        self._started = False
        threading.Thread(target=self._worker, daemon=True, name="brain").start()
        threading.Thread(target=self._mic_meter, daemon=True, name="mic-meter").start()

    def _mic_meter(self):
        """Живий рівень мікрофона для реактора (≈10 разів на секунду, лише коли щось змінюється)."""
        last = -1.0
        while True:
            time.sleep(0.1)
            if not self._started:
                continue
            lvl = round(self.voice.level, 2)
            if abs(lvl - last) >= 0.03:
                last = lvl
                self.js("HUD.mic", lvl)

    def self_test(self):
        """Перевірка систем при старті — як у фільмі, з доповіддю в HUD."""
        res = {"mic": self.voice.mic_ok}
        ok, info = self.brain.self_test()
        res["ai"], res["ai_info"] = ok, info
        try:
            self.voice.tts("Тест.", cache=True)
            res["voice"] = True
        except Exception as e:
            print("Voice self-test:", repr(e))
            res["voice"] = False
        from core.monitor import online
        res["net"] = online()
        print("Self-test:", res)
        self.js("HUD.systems", res)
        if self.brain.ready() and not ok:
            self._alert(f"Сер, мозок недоступний: {info}. Перевірте налаштування.", "warn")
        return res

    # --------------------------------------------------------- зв'язок з HUD
    def js(self, fn, *args):
        if not self.window:
            return
        try:
            self.window.evaluate_js(f"{fn}({', '.join(json.dumps(a, ensure_ascii=False) for a in args)})")
        except Exception as e:
            print("JS error:", e)

    def _voice_state(self, state):
        self.js("HUD.setState", state)
        # музика в YouTube Music тихішає, поки Джарвіс слухає команду або говорить
        if self.config.get("duck_music"):
            self.browser.duck(state in ("awake", "recognizing", "thinking", "speaking"))

    def _brain_status(self, kind, detail):
        self.js("HUD.setState", "thinking")
        if kind == "tool":
            self.js("HUD.toolUsed", detail)
        elif kind == "web":
            self.js("HUD.toolUsed", "web_search")
            self.js("HUD.holo", "intel", {"query": detail})

    def update_loop(self):
        """Автооновлення: через 1,5 хв після старту, далі кожні 3 години перевіряємо релізи на GitHub.
        Нову версію ставимо самі, коли Джарвіс нічим не зайнятий, а ви не працюєте за ПК
        (або одразу після запуску) — інсталятор тихо замінить програму й запустить її знову."""
        time.sleep(90)
        first = True
        while True:
            try:
                self.check_update(at_startup=first)
            except Exception as e:
                print("Update check error:", repr(e))
            first = False
            time.sleep(3 * 3600)

    def check_update(self, at_startup=False):
        from core import updater
        from core.monitor import idle_seconds
        new, url = updater.check(VERSION)
        if not (new and url):
            return
        title = self.config.get("user_title")
        if not self.config.get("auto_update"):
            self._alert(f"{title.capitalize()}, доступна нова версія {new}. Скажіть «онови себе», і я встановлю її.", "info")
            return
        # чекаємо слушного моменту: не перебиваємо розмову, музику-команди чи вашу роботу
        for _ in range(240):  # до 2 годин
            quiet = not self.voice.busy and not self.voice.speaking and self._commands.empty()
            if quiet and (at_startup or idle_seconds() > 120):
                break
            time.sleep(30)
        msg = f"{title.capitalize()}, встановлюю оновлення до версії {new}. Повернуся за хвилину."
        self.js("HUD.alert", msg, "info")
        self.js("HUD.addMessage", "jarvis", msg)
        self.voice.speak(msg, follow_up=False)
        print("Auto-update to", new)
        updater.install(url)
        self._quit_for_update()

    def _quit_for_update(self):
        """Звільняємо файли для інсталятора: закриваємо браузер і саме вікно (інсталятор потім запустить нову версію)."""
        time.sleep(3)
        try:
            self.browser.close()
        except Exception:
            pass
        try:
            self.window.destroy()
        except Exception:
            os._exit(0)

    def ensure_autostart(self):
        """Jarvis вмикається разом з комп'ютером. Щоразу оновлюємо шлях у реєстрі —
        якщо exe перенесли в іншу папку, автозапуск не зламається."""
        try:
            if not self.config.get("autostart_v6"):
                self.config.update({"autostart": True, "autostart_v6": True})
            if self.config.get("autostart"):
                autostart.set_enabled(True)
                print("Autostart:", autostart.command())
        except Exception as e:
            print("Autostart error:", repr(e))

    def toggle_visible(self):
        w = self.window
        try:
            if getattr(self, "_hidden", False):
                w.show()
                w.restore()
                self._hidden = False
            else:
                w.hide()
                self._hidden = True
        except Exception as e:
            print("Toggle window error:", repr(e))

    def _play_audio(self, mp3: bytes):
        self._audio_done.clear()
        self.js("HUD.playAudio", base64.b64encode(mp3).decode())
        self._audio_done.wait(timeout=len(mp3) / 6000 + 6)

    def submit(self, text, hidden=False, ambient=False):
        """ambient=True — фраза почута без «Джарвіс» (режим «завжди слухаю»)."""
        text = (text or "").strip()
        if text:
            self._commands.put((text, hidden, ambient))

    def _worker(self):
        while True:
            text, hidden, ambient = self._commands.get()
            if not hidden:
                if not ambient:
                    self.js("HUD.addMessage", "user", text)
                self.js("HUD.setState", "thinking")
                if time.time() < self._pending_restore and re.fullmatch(
                        r"\W*(так|давай|відкривай|відкрий|звісно|ага|yes|угу|так,? (відкрий|давай)|відкрий (усі|все|їх))\W*", text.lower()):
                    self._pending_restore = 0
                    self.js("HUD.toolUsed", "restore_session")
                    reply = self.toolbox.run("restore_session", {}).split(" (сесія")[0]
                    reply = f"Відновлюю робоче місце, {self.config.get('user_title')}. " + reply
                    self.js("HUD.addMessage", "jarvis", reply)
                    self.brain.note(text, reply)
                    self.voice.speak(reply)
                    self.js("HUD.setState", "listening" if self.voice.mic_ok else "idle")
                    continue
                try:
                    quick = self.quick.handle(text)
                except Exception as e:
                    print("Quick command error:", repr(e))
                    quick = None
                if quick:  # миттєва локальна команда — без Claude, без затримки й витрат
                    reply, tool = quick
                    if ambient:
                        self.js("HUD.addMessage", "user", text)
                    if tool:
                        self.js("HUD.toolUsed", tool)
                    self.js("HUD.addMessage", "jarvis", reply)
                    self.brain.note(text, reply)
                    self.voice.speak(reply)
                    self.js("HUD.setState", "listening" if self.voice.mic_ok else "idle")
                    continue
            if ambient and not self.brain.ready():
                self.js("HUD.setState", "listening" if self.voice.mic_ok else "idle")
                continue  # без мозку фонові фрази ігноруємо (прості команди вже перевірено вище)
            self.js("HUD.setState", "thinking")
            shown = {"v": not ambient}
            if not ambient:
                self.js("HUD.beginReply")

            def on_sentence(t, _text=text):
                if not shown["v"]:  # фонова фраза виявилась зверненням — тепер показуємо її в журналі
                    shown["v"] = True
                    self.js("HUD.addMessage", "user", _text)
                    self.js("HUD.beginReply")
                self.js("HUD.replyText", t)
            self.voice.busy = True
            # відповідь озвучується й з'являється в журналі реченнями, поки мозок її ще пише
            speech = self.voice.stream(follow_up=True, on_sentence=on_sentence)
            t0 = time.time()
            first = {"t": None}

            def feed(delta):
                if first["t"] is None:
                    first["t"] = time.time() - t0
                speech.feed(delta)
            try:
                self.brain.ask(f"[без звертання] {text}" if ambient else text, on_text=feed)
            except RuntimeError as e:
                if str(e) == "NO_KEY":
                    speech.feed(" Сер, мені потрібен ключ — безкоштовний ключ Gemini підійде. Відкриваю налаштування.")
                    self.js("HUD.openSettings")
                else:
                    speech.feed(f" Виникла помилка: {e}.")
            except Exception as e:
                print("Brain error:", repr(e))
                speech.feed(" " + self._error_text(e))
            self.voice.busy = False
            print(f"Timing: перший текст {first['t'] or 0:.1f} с, уся відповідь {time.time() - t0:.1f} с "
                  f"({self.config.get('provider')}/{getattr(self.brain, 'model', self.config.get('model'))})")
            speech.end()
            self.js("HUD.endReply")
            speech.wait()
            self.js("HUD.setState", "listening" if self.voice.mic_ok else "idle")

    def _error_text(self, e):
        name = type(e).__name__
        if name == "AuthenticationError":
            self.js("HUD.openSettings")
            return "Ключ API не приймається, сер. Перевірте його в налаштуваннях."
        if name in ("APIConnectionError", "APITimeoutError"):
            return "Немає з'єднання з сервером, сер. Перевірте інтернет."
        if name == "RateLimitError":
            if self.config.get("provider") != "anthropic":
                return "Безкоштовний ліміт запитів поки вичерпано, сер. Спробуймо за хвилину, а якщо не допоможе — завтра."
            return "Перевищено ліміт запитів, сер. Спробуймо за хвилину."
        if name in ("InternalServerError", "OverloadedError", "ServiceUnavailableError"):
            return "Сервери Anthropic зараз перевантажені, сер. Повторіть, будь ласка, за мить."
        if name == "NotFoundError":
            return "Модель не знайдено. Перевірте назву моделі в налаштуваннях."
        if name == "PermissionDeniedError":
            return "Немає доступу до цієї моделі, сер. Оберіть іншу в налаштуваннях."
        if name == "BadRequestError" and "credit" in str(e).lower():
            return "Сер, на рахунку Anthropic закінчились кошти. Поповніть баланс у консолі."
        return f"Перепрошую, сер, сталася помилка: {name}."

    def _budget_alert(self, pct, cost):
        title = self.config.get("user_title")
        if pct >= 100:
            self._alert(f"{title.capitalize()}, денний бюджет на API вичерпано: витрачено {cost:.2f} долара.", "warn")
        else:
            self._alert(f"{title.capitalize()}, використано {pct}% денного бюджету на API.", "info")

    def _say(self, text, follow_up=False, cache=False):
        threading.Thread(target=self.voice.speak, args=(text,), kwargs={"follow_up": follow_up, "cache": cache},
                         daemon=True).start()

    def _reminder_fired(self, r):
        title = self.config.get("user_title")
        self.js("HUD.reminder", r["text"])
        msg = f"{title.capitalize()}, нагадую: {r['text']}"
        self.js("HUD.addMessage", "jarvis", msg)
        self._say(msg, follow_up=True)

    def _alert(self, text, level):
        self.js("HUD.alert", text, level)
        self.js("HUD.addMessage", "jarvis", text)
        self._say(text, cache=(text == OFFLINE_LINE))

    def _welcome(self, minutes):
        title = self.config.get("user_title")
        parts = [f"З поверненням, {title}."]
        if minutes >= 60:
            parts.append(f"Вас не було {minutes // 60} год {minutes % 60} хв.")
        soon = [r for r in self.reminders.list()
                if r["when"] <= (dt.datetime.now() + dt.timedelta(hours=2)).isoformat(timespec="seconds")]
        if soon:
            parts.append(f"Нагадую: о {soon[0]['when'][11:16]} — {soon[0]['text']}.")
        else:
            parts.append("Поки вас не було, усе було спокійно.")
        text = " ".join(parts)
        self.js("HUD.holo", "welcome", {"title": title, "away": minutes})
        self.js("HUD.addMessage", "jarvis", text)
        self._say(text, follow_up=True)

    def run_protocol_direct(self, name):
        """Запуск протоколу кліком у HUD — миттєво, без звернення до Claude."""
        key, p = self.protocols.find(name)
        if not p:
            return
        title = self.config.get("user_title")
        msg = f"Протокол «{key}» активовано, {title}."
        self.js("HUD.addMessage", "jarvis", msg)
        self._say(msg)
        self.toolbox.run("run_protocol", {"name": key})

    def greet(self):
        h = dt.datetime.now().hour
        part = "Доброго ранку" if 5 <= h < 12 else "Доброго дня" if h < 18 else "Доброго вечора" if h < 23 else "Доброї ночі"
        title = self.config.get("user_title")
        today = dt.date.today().isoformat()
        if not self.brain.ready():
            text = (f"{part}, {title}. Я Джарвіс — ваш персональний асистент. Прості команди вже працюють. "
                    f"Щоб я міг розмовляти, введіть безкоштовний ключ Gemini в налаштуваннях.")
            self.js("HUD.openSettings")
        elif self.config.get("morning_briefing") and 5 <= h < 12 and self.config.get("last_briefing") != today:
            self.config.update({"last_briefing": today})
            self.js("HUD.holo", "briefing", {"date": dt.datetime.now().strftime("%d.%m.%Y")})
            names = self._restore_candidates()
            extra = (f" Наприкінці коротко запитай, чи відкрити програми з минулого разу: {', '.join(names)} "
                     f"(якщо користувач погодиться — restore_session)." if names else "")
            if names:
                self._pending_restore = time.time() + 180
            self.submit(BRIEFING_PROMPT + extra, hidden=True)
            return
        else:
            text = random.choice([
                f"{part}, {title}. Усі системи працюють у штатному режимі. Чим можу допомогти?",
                f"{part}, {title}. Системи онлайн, я до ваших послуг.",
                f"{part}, {title}. Радий бачити вас знову. Над чим працюємо сьогодні?",
            ])
        names = self._restore_candidates() if self.brain.ready() else []
        if names:
            text += f" Минулого разу були відкриті {self._join(names)}. Відкрити їх знову?"
            self._pending_restore = time.time() + 120
            self.brain.note("[система: Джарвіс щойно запустився]", text)
        self.js("HUD.addMessage", "jarvis", text)
        self.voice.speak(text, follow_up=bool(names))

    def _restore_candidates(self):
        """Програми з минулої сесії, які зараз не запущені."""
        try:
            from core.apps import running_exes
            exes, _ = self.apps.last_session()
            run = running_exes()
            return [pretty(e) for e in exes if e.lower() not in run][:6]
        except Exception as e:
            print("Restore candidates error:", repr(e))
            return []

    @staticmethod
    def _join(names):
        return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " і " + names[-1]


class Api:
    """Методи, доступні з JavaScript як window.pywebview.api.*"""

    def __init__(self, jarvis: Jarvis):
        self._j = jarvis
        self._net = psutil.net_io_counters()
        self._net_t = time.time()
        psutil.cpu_percent(None)

    _ready_lock = threading.Lock()

    def ready(self):
        j = self._j
        with self._ready_lock:
            first = not j._started
            j._started = True
        if first:
            j.reminders.start()
            j.apps.start()
            j.ensure_autostart()
            j.voice.start()
            j.monitor.start()
            j.voice.prewarm(WAKE_REPLIES + [OFFLINE_LINE])
            threading.Timer(2.0, j.self_test).start()
            threading.Thread(target=j.update_loop, daemon=True, name="updater").start()
            threading.Timer(3.4, j.greet).start()
        mini = "--autostart" in sys.argv and bool(j.config.get("start_mini"))
        return {"version": VERSION, "settings": j.config.public(), "protocols": self.protocols(), "mini": mini}

    def protocols(self):
        return [{"name": k, "description": v.get("description", "")} for k, v in self._j.protocols.items.items()]

    def run_protocol(self, name):
        threading.Thread(target=self._j.run_protocol_direct, args=(name,), daemon=True).start()

    def stop_speaking(self):
        self._j.voice.stop()

    def set_mini(self, on):
        """Міні-режим: маленький реактор поверх усіх вікон."""
        w = self._j.window
        try:
            scr = webview.screens[0]
            sw, sh = scr.width, scr.height
        except Exception:
            sw, sh = 1920, 1080
        if on:
            self._prev = (w.width, w.height)
            w.resize(300, 360)
            w.move(sw - 320, sh - 420)
        else:
            pw, ph = getattr(self, "_prev", (1400, 860))
            w.resize(pw, ph)
            w.move(max(0, (sw - pw) // 2), max(0, (sh - ph) // 2))
        try:
            w.on_top = bool(on)
        except Exception:
            pass
        return on

    def send_text(self, text):
        self._j.submit(text)

    def mic(self):
        self._j.voice.push_to_talk()

    def toggle_mute(self):
        v = self._j.voice
        v.muted = not v.muted
        return v.muted

    def audio_done(self):
        self._j._audio_done.set()

    def stats(self):
        now = time.time()
        net = psutil.net_io_counters()
        dtt = max(0.1, now - self._net_t)
        down = (net.bytes_recv - self._net.bytes_recv) / dtt / 1024
        up = (net.bytes_sent - self._net.bytes_sent) / dtt / 1024
        self._net, self._net_t = net, now
        vm = psutil.virtual_memory()
        try:
            disk = psutil.disk_usage("C:\\").percent
        except Exception:
            disk = psutil.disk_usage("/").percent
        b = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
        return {
            "cpu": psutil.cpu_percent(None), "cores": psutil.cpu_percent(None, percpu=True),
            "ram": vm.percent, "ram_used": round(vm.used / 2**30, 1), "ram_total": round(vm.total / 2**30, 1),
            "disk": disk, "battery": round(b.percent) if b else None, "plugged": bool(b.power_plugged) if b else None,
            "down": round(down, 1), "up": round(up, 1), "procs": len(psutil.pids()),
            "uptime": int(now - psutil.boot_time()),
            "reminders": self._j.reminders.list()[:4],
            "speaking": self._j.voice.speaking,
            "usage": self._j.usage.today(),
        }

    def get_settings(self):
        s = self._j.config.public()
        s["autostart"] = autostart.is_enabled() or False
        s["memory"] = self._j.memory.facts[-50:]
        return s

    def ytm_login(self):
        threading.Thread(target=self._j.toolbox.run, args=("youtube_music", {"action": "login"}), daemon=True).start()

    def open_log(self):
        try:
            os.startfile(str(log.LOG_PATH))  # noqa
        except Exception as e:
            return str(e)

    def forget_fact(self, text):
        return self._j.memory.forget(text)

    def save_settings(self, values):
        values = dict(values or {})
        for k in ("api_key", "openai_key", "gemini_key", "groq_key", "obs_password"):
            if k in values and (not values[k] or "…" in values[k]):
                values.pop(k)  # не перезаписуємо збережений ключ порожнім/прихованим
        old = (self._j.config.get("provider"), self._j.config.get("model"))
        self._j.config.update(values)
        j = self._j
        if (j.config.get("provider"), j.config.get("model")) != old or any(k.endswith(("_key", "_model")) for k in values):
            j.brain = make_brain(j.config, j.toolbox, on_status=j._brain_status, usage=j.usage)
            threading.Thread(target=j.self_test, daemon=True).start()
        if "autostart" in values:
            try:
                autostart.set_enabled(bool(values["autostart"]))
            except Exception as e:
                print("Autostart error:", e)
        return self._j.config.public()

    def reset_memory(self):
        self._j.brain.reset()

    def win_minimize(self):
        self._j.window.minimize()

    def win_toggle_fullscreen(self):
        self._j.window.toggle_fullscreen()

    def win_close(self):
        self._j.window.destroy()


def setup_hotkey(jarvis):
    """Глобальна гаряча клавіша Ctrl+Alt+J — говорити без wake word."""
    try:
        import keyboard
        keyboard.add_hotkey("ctrl+alt+j", jarvis.voice.push_to_talk)
        keyboard.add_hotkey("ctrl+alt+space", jarvis.toggle_visible)  # показати/сховати HUD
    except Exception as e:
        print("Hotkey unavailable:", e)


def selftest():
    """Jarvis.exe --selftest: перевіряє, що в зібраному exe є всі модулі й файли (запускається в CI)."""
    import importlib
    import tempfile
    lines, ok = [], True
    mods = ["anthropic", "edge_tts", "speech_recognition", "pyaudio", "keyboard", "psutil", "PIL.ImageGrab", "requests",
            "icalendar", "recurring_ical_events", "numpy", "onnxruntime", "webview", "playwright.sync_api",
            "obsws_python", "openai"]
    if sys.platform == "win32":
        mods += ["clr", "webview.platforms.edgechromium", "winreg", "uiautomation"]
    for m in mods:
        try:
            importlib.import_module(m)
            lines.append(f"OK   import {m}")
        except Exception as e:
            ok = False
            lines.append(f"FAIL import {m}: {e!r}")
    checks = {
        "ui/index.html": lambda: os.path.exists(resource_path("ui/index.html")),
        "wakeword model": lambda: __import__("core.wakeword", fromlist=["x"]).WakeWordDetector(
            resource_path("assets/wakeword")).process(bytes(2560 * 4)) >= 0,
        "flac converter": lambda: os.path.exists(__import__("speech_recognition").audio.get_flac_converter()),
        "tz Europe/Kyiv": lambda: bool(__import__("zoneinfo").ZoneInfo("Europe/Kyiv")),
        "pyaudio init": lambda: __import__("pyaudio").PyAudio().terminate() is None,
        "ui automation": lambda: sys.platform != "win32" or __import__("uiautomation").GetRootControl() is not None,
        "monitors": lambda: len(__import__("core.winlayout", fromlist=["x"]).monitors()) >= 1,
        "playwright driver": lambda: all(os.path.exists(p) for p in
                                         __import__("playwright._impl._driver", fromlist=["x"]).compute_driver_executable()),
    }
    for name, fn in checks.items():
        try:
            good = fn()
            ok &= bool(good)
            lines.append(f"{'OK  ' if good else 'FAIL'} {name}")
        except Exception as e:
            ok = False
            lines.append(f"FAIL {name}: {e!r}")
    out = os.path.join(tempfile.gettempdir(), "jarvis_selftest.txt")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + f"\nRESULT: {'PASS' if ok else 'FAIL'}\n")
    sys.exit(0 if ok else 1)


def main():
    if "--selftest" in sys.argv:
        selftest()
    from core.winlayout import enable_dpi_awareness
    enable_dpi_awareness()  # точні координати вікон на моніторах з масштабом
    log.setup(VERSION)
    # дозволяємо WebView2 відтворювати голос без кліку користувача
    os.environ.setdefault("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", "--autoplay-policy=no-user-gesture-required")
    jarvis = Jarvis()
    api = Api(jarvis)
    jarvis.window = webview.create_window(
        "J.A.R.V.I.S.", url=resource_path("ui/index.html"), js_api=api,
        width=1400, height=860, min_size=(300, 340), frameless=True, easy_drag=False,
        background_color="#02060c", text_select=False)
    setup_hotkey(jarvis)
    webview.start(http_server=True, debug=False)


if __name__ == "__main__":
    main()
