"""Довготривала пам'ять Джарвіса: факти про користувача, які він сам просить запам'ятати
(«Джарвіс, запам'ятай, що я п'ю каву без цукру»). Зберігається в %APPDATA%\\Jarvis\\memory.json."""
import datetime as dt
import json
import threading

from .config import app_dir

MAX_FACTS = 200


class Memory:
    def __init__(self):
        self._file = app_dir() / "memory.json"
        self._lock = threading.Lock()
        try:
            self.facts = json.loads(self._file.read_text(encoding="utf-8"))
        except Exception:
            self.facts = []

    def _save(self):
        self._file.write_text(json.dumps(self.facts, ensure_ascii=False, indent=1), encoding="utf-8")

    def add(self, fact: str):
        fact = fact.strip()
        if not fact:
            return "Порожній факт"
        with self._lock:
            if any(f["text"].lower() == fact.lower() for f in self.facts):
                return "Я вже це пам'ятаю"
            self.facts.append({"text": fact, "date": dt.date.today().isoformat()})
            self.facts = self.facts[-MAX_FACTS:]
            self._save()
        return f"Запам'ятав: {fact}"

    def forget(self, query: str):
        q = query.lower().strip()
        with self._lock:
            if q in ("all", "все", "усе"):
                n = len(self.facts)
                self.facts = []
                self._save()
                return f"Забув усе ({n})"
            keep = [f for f in self.facts if q not in f["text"].lower()]
            removed = len(self.facts) - len(keep)
            self.facts = keep
            self._save()
        return f"Забув {removed} запис(ів)" if removed else "Нічого схожого в пам'яті немає"

    def prompt_block(self):
        if not self.facts:
            return ""
        lines = "\n".join(f"- {f['text']}" for f in self.facts[-80:])
        return f"\n\nЩо ти знаєш про користувача (довготривала пам'ять, враховуй це природно, не перелічуй без потреби):\n{lines}"
