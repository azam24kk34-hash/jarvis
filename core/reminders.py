"""Нагадування й таймери, зберігаються між перезапусками."""
import datetime as dt
import json
import threading
import time

from .config import app_dir


class Reminders:
    def __init__(self, on_fire):
        self._file = app_dir() / "reminders.json"
        self._on_fire = on_fire
        self._lock = threading.Lock()
        self._items = []
        self._next_id = 1
        try:
            self._items = json.loads(self._file.read_text(encoding="utf-8"))
            self._next_id = max([int(r["id"]) for r in self._items] + [0]) + 1
        except Exception:
            pass
        self._started = False

    def start(self):
        """Запускати, коли HUD і голос готові — інакше прострочені нагадування «згорять» беззвучно."""
        if not self._started:
            self._started = True
            threading.Thread(target=self._loop, daemon=True, name="reminders").start()

    def _save(self):
        self._file.write_text(json.dumps(self._items, ensure_ascii=False, indent=1), encoding="utf-8")

    def add(self, text, when: dt.datetime):
        with self._lock:
            r = {"id": str(self._next_id), "text": text, "when": when.isoformat(timespec="seconds")}
            self._next_id += 1
            self._items.append(r)
            self._save()
        return r

    def list(self):
        with self._lock:
            return sorted(self._items, key=lambda r: r["when"])

    def cancel(self, rid):
        with self._lock:
            if rid == "all":
                n = len(self._items)
                self._items = []
                self._save()
                return f"Скасовано всі ({n})"
            before = len(self._items)
            self._items = [r for r in self._items if r["id"] != str(rid).lstrip("#")]
            self._save()
            return "Скасовано" if len(self._items) < before else "Не знайдено такого нагадування"

    def _loop(self):
        while True:
            now = dt.datetime.now().isoformat(timespec="seconds")
            due = []
            with self._lock:
                for r in list(self._items):
                    if r["when"] <= now:
                        due.append(r)
                        self._items.remove(r)
                if due:
                    self._save()
            for r in due:
                try:
                    self._on_fire(r)
                except Exception:
                    pass
            time.sleep(1)
