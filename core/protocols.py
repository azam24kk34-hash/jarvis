"""Протоколи — іменовані сценарії з кількох дій, як «House Party Protocol» у фільмі.
Зберігаються в %APPDATA%\\Jarvis\\protocols.json, користувач створює нові голосом."""
import difflib
import json
import threading

from .config import app_dir

DEFAULT_PROTOCOLS = {
    "вечірка": {
        "description": "Гучність на максимум і музика для вечірки",
        "steps": [
            {"tool": "volume_control", "args": {"action": "set", "level": 80}},
            {"tool": "play_music", "args": {"query": "AC/DC Back in Black"}},
        ],
    },
    "моя музика": {
        "description": "Вподобані пісні з YouTube Music, перемішано",
        "steps": [
            {"tool": "volume_control", "args": {"action": "set", "level": 50}},
            {"tool": "youtube_music", "args": {"action": "liked", "shuffle": True}},
        ],
    },
    "тиша": {
        "description": "Пауза музики та вимкнення звуку",
        "steps": [
            {"tool": "media_control", "args": {"action": "play_pause"}},
            {"tool": "volume_control", "args": {"action": "mute_toggle"}},
        ],
    },
    "робота": {
        "description": "Робочий режим: браузер, пошта та тиха музика для концентрації",
        "steps": [
            {"tool": "open_website", "args": {"url": "https://mail.google.com"}},
            {"tool": "volume_control", "args": {"action": "set", "level": 25}},
            {"tool": "play_music", "args": {"query": "lofi focus music"}},
        ],
    },
    "діагностика": {
        "description": "Повна діагностика системи",
        "steps": [{"tool": "system_status", "args": {}}],
    },
}

# інструменти, які можна використовувати в протоколах (без небезпечних)
ALLOWED = {"open_application", "close_application", "open_website", "play_music", "volume_control",
           "media_control", "system_status", "get_weather", "open_path", "set_reminder", "power_action",
           "window_control", "press_keys", "get_calendar", "youtube_music", "browser_open", "restore_session"}


class Protocols:
    def __init__(self):
        self._file = app_dir() / "protocols.json"
        self._lock = threading.Lock()
        try:
            self.items = json.loads(self._file.read_text(encoding="utf-8"))
            flag = app_dir() / "protocols_v6.flag"
            if not flag.exists():  # оновлення: додаємо новий протокол «моя музика», нічого не видаляючи
                self.items.setdefault("моя музика", DEFAULT_PROTOCOLS["моя музика"])
                self._save()
                flag.write_text("1")
        except Exception:
            self.items = dict(DEFAULT_PROTOCOLS)
            self._save()

    def _save(self):
        self._file.write_text(json.dumps(self.items, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _key(name):
        n = name.lower().strip().strip("«»\"'")
        for w in ("протокол ", "протоколу ", "protocol "):
            n = n.replace(w, "")
        return n.strip()

    def find(self, name):
        k = self._key(name)
        if k in self.items:
            return k, self.items[k]
        for key, p in self.items.items():  # відмінки: «вечірку» → «вечірка», «ранку» → «ранок»
            n = max(3, min(len(key), len(k)) - 2)
            if key[:n] == k[:n] or difflib.SequenceMatcher(None, key, k).ratio() >= 0.75:
                return key, p
        return None, None

    def create(self, name, description, steps):
        bad = [s.get("tool") for s in steps if s.get("tool") not in ALLOWED]
        if bad:
            return f"Ці дії не можна додавати в протокол: {', '.join(map(str, bad))}"
        with self._lock:
            self.items[self._key(name)] = {"description": description or "", "steps": steps}
            self._save()
        return f"Протокол «{self._key(name)}» збережено ({len(steps)} кроків)"

    def delete(self, name):
        k, p = self.find(name)
        if not p:
            return "Такого протоколу немає"
        with self._lock:
            del self.items[k]
            self._save()
        return f"Протокол «{k}» видалено"
