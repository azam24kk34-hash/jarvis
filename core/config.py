"""Налаштування Jarvis: зберігаються в %APPDATA%\\Jarvis\\config.json"""
import json
import os
import sys
import threading
from pathlib import Path

APP_NAME = "Jarvis"

DEFAULTS = {
    "api_key": "",
    "provider": "gemini",            # "gemini" / "groq" / "ollama" — безкоштовно; "anthropic" — Claude (платно)
    "gemini_key": "",
    "groq_key": "",
    "gemini_model": "",              # порожньо = модель за замовчуванням
    "groq_model": "",
    "ollama_model": "",
    "model": "claude-sonnet-5-5",
    "user_title": "сер",            # як Jarvis звертається до вас
    "user_name": "",                 # ваше ім'я (необов'язково)
    "city": "Київ",
    "voice": "uk-UA-OstapNeural",    # чоловічий голос; жіночий: uk-UA-PolinaNeural
    "voice_rate": "+8%",
    "voice_pitch": "-6Hz",
    "wake_word_enabled": True,
    "wake_engine": "google",         # "google" — слово «Джарвіс»; "offline" — фраза «Hey Jarvis» без інтернету
    "wake_threshold": 0.5,           # чутливість офлайн-детектора (менше = чутливіше)
    "wake_words": ["джарвіс", "джарвис", "jarvis", "джервіс", "жарвіс", "джарві", "чарвіс"],
    "speak_responses": True,
    "web_search": True,
    "mic_energy_threshold": 0,       # 0 = автокалібрування
    # --- поведінка як у фільмі
    "morning_briefing": True,        # ранковий брифінг при першому запуску за день
    "last_briefing": "",
    "proactive_alerts": True,        # попередження про батарею, пам'ять, мережу
    "welcome_back": True,            # «З поверненням, сер» після відсутності
    "welcome_after_min": 15,
    "interim_speech": True,          # «Вже виконую, сер» перед довгими діями
    "sfx": True,                     # звукові ефекти інтерфейсу
    "voice_fx": True,                # кінематографічна обробка голосу
    "calendar_ics": "",              # секретні iCal-адреси календарів (через пробіл/новий рядок)
    "autostart": True,               # запускати разом з Windows (одразу в міні-режимі)
    "autostart_v6": False,           # службове: одноразово ввімкнути автозапуск після оновлення
    "start_mini": True,              # при автозапуску — одразу міні-режим
    "music_service": "ytmusic",      # "ytmusic" — YouTube Music у браузері Джарвіса; "youtube" — звичайний YouTube
    "browser_channel": "msedge",     # браузер для Джарвіса: "msedge" або "chrome"
    "browser_minimize_music": True,  # після запуску музики згортати вікно браузера
    "duck_music": True,              # приглушувати музику, поки Джарвіс слухає/говорить
    "daily_budget": 1.0,             # $ на день; попередження на 80% і 100% (0 = без ліміту)
}


def app_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    p = Path(base) / APP_NAME
    p.mkdir(parents=True, exist_ok=True)
    return p


def resource_path(rel: str) -> str:
    """Шлях до ресурсів і в режимі розробки, і всередині PyInstaller exe."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, rel)


class Config:
    def __init__(self):
        self._path = app_dir() / "config.json"
        self._lock = threading.Lock()
        self.data = dict(DEFAULTS)
        if self._path.exists():
            try:
                self.data.update(json.loads(self._path.read_text(encoding="utf-8")))
            except Exception:
                pass
        # ключ можна задати і через змінну середовища
        if not self.data.get("api_key") and os.environ.get("ANTHROPIC_API_KEY"):
            self.data["api_key"] = os.environ["ANTHROPIC_API_KEY"]

    def get(self, key, default=None):
        return self.data.get(key, DEFAULTS.get(key, default))

    def update(self, values: dict):
        with self._lock:
            for k, v in values.items():
                if k in DEFAULTS:
                    self.data[k] = v
            self._path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")

    def public(self) -> dict:
        d = dict(self.data)
        for k in ("api_key", "gemini_key", "groq_key"):
            key = d.get(k, "") or ""
            d[k + "_set"] = bool(key)
            d[k] = (key[:6] + "…" + key[-4:]) if len(key) > 12 else ""
        return d
