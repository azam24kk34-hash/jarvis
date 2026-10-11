"""Власні фразочки: «коли я кажу X — відповідай Y». Спрацьовують миттєво, без ШІ й без витрат.
Навчити голосом: «Джарвіс, запам'ятай фразу: коли я кажу „хто у нас дракон“, відповідай „…“»,
або в налаштуваннях → «Мої фразочки». Зберігаються в %APPDATA%\\Jarvis\\phrases.json."""
import difflib
import json
import random
import re
import threading

from .config import app_dir


def norm(t):
    t = (t or "").lower().replace("’", "'").replace("ʼ", "'")
    t = re.sub(r"\b(джарвіс|джарвис|jarvis|будь ласка|скажи|скажи мені|а)\b", " ", t)
    t = re.sub(r"[^\w' ]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


class Phrases:
    def __init__(self):
        self._file = app_dir() / "phrases.json"
        self._lock = threading.Lock()
        try:
            self.items = json.loads(self._file.read_text(encoding="utf-8"))
        except Exception:
            self.items = []

    def _save(self):
        self._file.write_text(json.dumps(self.items, ensure_ascii=False, indent=1), encoding="utf-8")

    def add(self, trigger, response):
        trigger, response = trigger.strip(" «»\"'.,"), response.strip(" «»\"'")
        if len(norm(trigger)) < 2 or not response:
            return "Потрібні і фраза, і відповідь"
        with self._lock:
            k = norm(trigger)
            for it in self.items:
                if norm(it["trigger"]) == k:
                    # та сама фраза — додаємо ще один варіант відповіді (Джарвіс чергуватиме)
                    if response not in it["responses"]:
                        it["responses"].append(response)
                    self._save()
                    return f"Додав ще один варіант відповіді на «{trigger}»"
            self.items.append({"trigger": trigger, "responses": [response]})
            self._save()
        return f"Запам'ятав: на «{trigger}» відповідатиму «{response}»"

    def delete(self, trigger):
        k = norm(trigger)
        with self._lock:
            before = len(self.items)
            self.items = [it for it in self.items if norm(it["trigger"]) != k and k not in norm(it["trigger"])]
            self._save()
        return "Забув цю фразу" if len(self.items) < before else "Такої фрази не було"

    def match(self, text):
        """Відповідь на сказане, якщо воно схоже на одну з фраз (точно, входженням або «на слух»)."""
        t = norm(text)
        if not t or not self.items:
            return None
        best, score = None, 0.0
        for it in self.items:
            k = norm(it["trigger"])
            if not k:
                continue
            if t == k:
                s = 1.0
            elif len(k) >= 6 and (k in t or (len(t) >= 6 and t in k)):
                s = 0.9
            else:
                s = difflib.SequenceMatcher(None, t, k).ratio()
            if s > score:
                best, score = it, s
        return random.choice(best["responses"]) if best and score >= 0.82 else None

    def listing(self):
        return [{"trigger": it["trigger"], "responses": it["responses"]} for it in self.items]
