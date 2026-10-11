"""Облік витрат на Claude API: токени й приблизна вартість за день (%APPDATA%\\Jarvis\\usage.json)."""
import datetime as dt
import json
import threading

from .config import app_dir

# $ за мільйон токенів: (вхід, вихід, запис у кеш 5 хв, читання з кешу)
PRICES = {
    "claude-sonnet-5-5": (2.0, 10.0, 2.5, 0.10),
    "claude-opus-5-5": (4.0, 20.0, 5.0, 0.20),
    "claude-haiku-5-5": (0.10, 0.50, 0.125, 0.01),
    "claude-fable-5-1": (10.0, 50.0, 12.5, 0.25),
}
WEB_SEARCH_PRICE = 10.0 / 1000  # $ за один пошук


def _g(obj, name):
    v = getattr(obj, name, None) if obj is not None else None
    return v or 0


class Usage:
    def __init__(self, on_budget=None):
        self._file = app_dir() / "usage.json"
        self._lock = threading.Lock()
        self.on_budget = on_budget or (lambda pct, cost: None)
        self._warned = {}
        try:
            self.days = json.loads(self._file.read_text(encoding="utf-8"))
        except Exception:
            self.days = {}

    def add(self, model, u, budget=0.0):
        if u is None:
            return
        p = PRICES.get(model, PRICES["claude-sonnet-5-5"])
        inp, out = _g(u, "input_tokens"), _g(u, "output_tokens")
        cw, cr = _g(u, "cache_creation_input_tokens"), _g(u, "cache_read_input_tokens")
        searches = _g(getattr(u, "server_tool_use", None), "web_search_requests")
        cost = (inp * p[0] + out * p[1] + cw * p[2] + cr * p[3]) / 1e6 + searches * WEB_SEARCH_PRICE
        today = dt.date.today().isoformat()
        with self._lock:
            d = self.days.setdefault(today, {"cost": 0.0, "in": 0, "out": 0, "cached": 0, "requests": 0, "searches": 0})
            d["cost"] += cost
            d["in"] += inp + cw + cr
            d["out"] += out
            d["cached"] += cr
            d["requests"] += 1
            d["searches"] += searches
            # зберігаємо лише останні 60 днів
            self.days = dict(sorted(self.days.items())[-60:])
            try:
                self._file.write_text(json.dumps(self.days, indent=1), encoding="utf-8")
            except Exception:
                pass
            total = d["cost"]
        self._check_budget(today, total)
        return cost

    def add_free(self, u=None):
        """Безкоштовні провайдери (Gemini free, Groq, Ollama): рахуємо запити, вартість 0."""
        today = dt.date.today().isoformat()
        with self._lock:
            d = self.days.setdefault(today, {"cost": 0.0, "in": 0, "out": 0, "cached": 0, "requests": 0, "searches": 0})
            d["requests"] += 1
            d["in"] += _g(u, "prompt_tokens")
            d["out"] += _g(u, "completion_tokens")
            try:
                self._file.write_text(json.dumps(self.days, indent=1), encoding="utf-8")
            except Exception:
                pass

    def set_budget_getter(self, fn):
        self._budget = fn

    def _check_budget(self, today, total):
        budget = float(getattr(self, "_budget", lambda: 0)() or 0)
        if budget <= 0:
            return
        for pct in (100, 80):
            if total >= budget * pct / 100 and self._warned.get(today, 0) < pct:
                self._warned[today] = pct
                self.on_budget(pct, total)
                break

    def today(self):
        d = self.days.get(dt.date.today().isoformat(), {})
        month = dt.date.today().strftime("%Y-%m")
        m = sum(v["cost"] for k, v in self.days.items() if k.startswith(month))
        return {"cost": round(d.get("cost", 0.0), 4), "requests": d.get("requests", 0),
                "cached_pct": round(100 * d.get("cached", 0) / d["in"]) if d.get("in") else 0,
                "month": round(m, 3)}
