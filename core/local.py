"""Локальна «голова» Джарвіса: прості розмови й усе, що він бачить на вашому ПК, — миттєво, без інтернету й ШІ.
Привітання, «як справи», «дякую», стан комп'ютера (процесор, пам'ять, диски, батарея, час роботи),
що відкрито, що грає, нагадування, що він про вас пам'ятає, жарти. Усе складніше — у мозок (Gemini/OpenAI/Claude)."""
import datetime as dt
import random
import re
import time

import psutil

JOKES = [
    "Я провів аналіз вашого розкладу, {t}. Висновок: кава — це не група продуктів, але я вже змирився.",
    "Мої алгоритми кажуть, що ідеальний план — той, який ви не стали б виконувати, {t}. Тому я склав два.",
    "Я б розповів жарт про UDP, {t}, але не впевнений, що він до вас дійде.",
    "Програміст ставить на тумбочку дві склянки: повну — якщо захоче пити, і порожню — якщо не захоче. Я зробив би так само, {t}.",
    "Я не кажу, що ваш пароль простий, {t}. Але я вгадав його раніше, ніж ви дописали.",
    "Скільки програмістів треба, щоб замінити лампочку? Жодного, {t}, це апаратна проблема.",
]

GREET = ["До ваших послуг, {t}.", "Слухаю, {t}.", "Я тут, {t}.", "Завжди на зв'язку, {t}."]
THANKS = ["Завжди до ваших послуг, {t}.", "Звертайтесь, {t}.", "Радий служити, {t}.", "Для того я й існую, {t}."]


def _n(text):
    t = (text or "").lower().replace("’", "'").replace("ʼ", "'")
    t = re.sub(r"\b(джарвіс|джарвис|jarvis|будь ласка|ну|а|слухай|скажи|скажи мені|підкажи)\b", " ", t)
    t = re.sub(r"[^\w' ]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _gb(b):
    return f"{b / 2**30:.0f}"


def _dur(sec):
    h, m = int(sec // 3600), int(sec % 3600 // 60)
    d, h = divmod(h, 24)
    parts = []
    if d:
        parts.append(f"{d} {_pl(d, 'день', 'дні', 'днів')}")
    if h:
        parts.append(f"{h} {_pl(h, 'годину', 'години', 'годин')}")
    if m or not parts:
        parts.append(f"{m} {_pl(m, 'хвилину', 'хвилини', 'хвилин')}")
    return " ".join(parts)


def _pl(n, one, few, many):
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def cpu_now():
    # перший виклик без інтервалу повертає 0 — тож міряємо коротко
    return psutil.cpu_percent(interval=0.25)


class LocalTalk:
    def __init__(self, config, toolbox):
        self.config = config
        self.tb = toolbox
        psutil.cpu_percent(interval=None)  # «прогріваємо» лічильник

    @property
    def title(self):
        return self.config.get("user_title") or "сер"

    def _f(self, pool):
        return random.choice(pool).format(t=self.title)

    # ------------------------------------------------------------ дані про ПК
    def status_line(self, full=False):
        vm = psutil.virtual_memory()
        cpu = cpu_now()
        s = f"Процесор завантажено на {cpu:.0f} відсотків, пам'ять — на {vm.percent:.0f} відсотків"
        b = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
        if b:
            s += f", батарея {b.percent:.0f} відсотків{' і заряджається' if b.power_plugged else ''}"
        s += "."
        if full:
            try:
                d = psutil.disk_usage("C:\\" if psutil.WINDOWS else "/")
                s += f" На диску C вільно {_gb(d.free)} гігабайт."
            except Exception:
                pass
            s += f" Комп'ютер працює {_dur(time.time() - psutil.boot_time())}."
        return s

    def open_apps(self):
        try:
            from .apps import pretty, snapshot
            names = []
            for exe, _ in snapshot():
                p = pretty(exe)
                if p not in names:
                    names.append(p)
            return names
        except Exception:
            return []

    def now_playing(self, only_if_playing=False):
        b = getattr(self.tb, "browser", None)
        if b is None or not getattr(b, "running", False):
            return None
        if only_if_playing and not getattr(b, "music_playing", False):
            return None
        try:
            info = b.ytm_control("now_playing")
            if info.get("title"):
                return f"{info.get('artist') or ''}, «{info['title']}»".strip(", ")
        except Exception:
            pass
        return None

    def context(self):
        """Короткий знімок ПК для мозку — щоб відповідав про ваш комп'ютер без зайвих інструментів."""
        bits = []
        try:
            vm = psutil.virtual_memory()
            bits.append(f"CPU {psutil.cpu_percent(interval=None):.0f}%, RAM {vm.percent:.0f}%")
        except Exception:
            pass
        apps = self.open_apps()
        if apps:
            bits.append("відкрито: " + ", ".join(apps[:12]))
        np = self.now_playing(only_if_playing=True)
        if np:
            bits.append("грає: " + np)
        return ("[ПК: " + "; ".join(bits) + "]") if bits else ""

    # ------------------------------------------------------------ розмова
    def handle(self, text):
        t = _n(text)
        if not t or len(t) > 80:
            return None
        T = self.title

        # привітання й «ти тут?»
        if re.fullmatch(r"(привіт|здоров|здрастуй\w*|вітаю|хай|hello|hi|добр\w+ (ранку|день|вечір|ночі)|доброго (ранку|дня|вечора)|"
                        r"ти тут|ти мене чуєш|ти на зв'язку|чуєш мене|ти де|прокинься|ти живий|алло|агов)", t):
            h = dt.datetime.now().hour
            if t.startswith(("добр", "привіт", "здоров", "вітаю", "хай", "hello", "hi", "здрав")):
                part = "Доброго ранку" if 5 <= h < 12 else "Добрий день" if h < 18 else "Добрий вечір" if h < 23 else "Доброї ночі"
                return f"{part}, {T}. Чим можу допомогти?"
            return self._f(GREET)
        if re.fullmatch(r"(як (ти|справи|твої справи|життя|ся маєш|почуваєшся|настрій)|що нового|як ся маєш)( у тебе)?", t):
            return f"Усі системи в нормі, {T}. {self.status_line()}"
        if re.fullmatch(r"(дякую|дякс|спасибі|спс|дуже дякую|красава|молодець|добре зроблено|чудово|супер|клас|топ|гарна робота)( тобі)?( джарвіс)?", t):
            return self._f(THANKS)
        if re.fullmatch(r"(хто ти|ти хто|що ти таке|представся|як тебе звати)", t):
            return f"Джарвіс, {T}. Just A Rather Very Intelligent System — ваш особистий асистент. Керую комп'ютером, музикою, вікнами й OBS, стежу за системою і відповідаю на питання."
        if re.fullmatch(r"(що ти (вмієш|можеш)|які в тебе (можливості|функції)|допомога|що ти робиш)", t):
            return (f"Можу відкривати й закривати програми, розставляти вікна на моніторах, вмикати музику, керувати OBS, "
                    f"шукати в інтернеті, ставити нагадування, показувати погоду й стан системи, {T}. Просто скажіть.")
        if re.search(r"\b(розкажи|скажи|знаєш)\b.*\bжарт|\bпожартуй|\bрозсміши", t) or t in ("жарт", "анекдот", "розкажи анекдот"):
            return self._f(JOKES)
        if re.fullmatch(r"(добраніч|на добраніч|бувай|до побачення|відпочивай|поки)", t):
            return f"Добраніч, {T}. Я на варті." if "добр" in t else f"До зв'язку, {T}. Я на варті."

        # стан комп'ютера
        if re.search(r"(стан|статус|діагностик\w*|як почувається|як там|перевір)\s*(систем\w*|комп'ютер\w*|пк|компа|ноутбук\w*|залізо)|^(діагностика|звіт системи|стан системи)$", t):
            return f"Діагностику завершено, {T}. {self.status_line(full=True)}"
        if re.search(r"(процесор|цп|cpu)", t) and re.search(r"(завантаж|навантаж|скільки|відсот|як)", t):
            return f"Процесор завантажено на {cpu_now():.0f} відсотків, {T}."
        if re.search(r"(оперативк|оперативн\w* пам'ят|ram|пам'ят\w* (зайнят|вільн|скільки))|скільки (вільної )?пам'яті", t):
            vm = psutil.virtual_memory()
            return f"Зайнято {vm.percent:.0f} відсотків оперативної пам'яті: {vm.used/2**30:.1f} з {vm.total/2**30:.0f} гігабайт, {T}."
        if re.search(r"(місц\w* на диск|диск\w* (вільн|заповн|скільки)|скільки (вільного )?місця)", t):
            out = []
            for p in psutil.disk_partitions(all=False):
                try:
                    u = psutil.disk_usage(p.mountpoint)
                    if u.total > 2**30:
                        dev = p.device.rstrip(":\\/")
                        out.append(f"на {dev} вільно {_gb(u.free)} з {_gb(u.total)} гігабайт")
                except Exception:
                    pass
            return ("Зараз " + "; ".join(out[:4]) + f", {T}.") if out else None
        if re.search(r"(заряд|батаре|акумулятор)", t):
            b = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
            if not b:
                return f"Батареї не бачу, {T} — схоже, це стаціонарний комп'ютер."
            left = f", вистачить приблизно на {_dur(b.secsleft)}" if b.secsleft and b.secsleft > 0 and not b.power_plugged else ""
            return f"Заряд {b.percent:.0f} відсотків{' і заряджається' if b.power_plugged else ''}{left}, {T}."
        if re.search(r"(скільки|як довго|відколи) (вже )?(працює|увімкнен\w*|ввімкнен\w*) (комп'ютер|пк|система|комп)", t):
            return f"Комп'ютер працює {_dur(time.time() - psutil.boot_time())}, {T}."
        if re.search(r"(що|які програми|які вікна|що в мене) (зараз )?(відкрит\w*|запущен\w*)|що працює на (комп'ютері|пк)", t):
            apps = self.open_apps()
            if not apps:
                return f"Окрім мене, відкритих програм не бачу, {T}."
            return f"Зараз відкрито: {', '.join(apps[:10])}, {T}."
        if re.search(r"(що|яка пісня|який трек|хто) (зараз )?(грає|співає|звучить)|що це за (пісня|трек)", t):
            np = self.now_playing()
            return f"Зараз грає {np}, {T}." if np else f"Зараз у мене нічого не грає, {T}."
        if re.search(r"(що|які) (в мене |у мене )?(за )?нагадуван|мої нагадування|що я (просив|казав) нагадати", t):
            r = self.tb.run("list_reminders", {})
            if r.startswith("Активних"):
                return f"Активних нагадувань немає, {T}."
            items = [re.sub(r"^#\d+ ", "", x) for x in r.splitlines()][:5]
            return "Нагадування: " + "; ".join(items) + "."
        if re.search(r"що ти (про мене )?(знаєш|пам'ятаєш)( про мене)?$", t):
            mem = getattr(self.tb, "memory", None)
            facts = [f.get("text", "") if isinstance(f, dict) else str(f) for f in (getattr(mem, "facts", None) or [])][-6:]
            if not facts:
                return f"Поки що нічого особливого, {T}. Скажіть «запам'ятай…» — і я запам'ятаю."
            return f"Ось що я пам'ятаю, {T}: " + "; ".join(facts[:6]) + "."
        m = re.fullmatch(r"запам'ятай(?: що| що я)? (.{4,120})", t)
        if m and not re.match(r"(фраз|розташуван|вікна|робоче місце)", m.group(1)):
            mem = getattr(self.tb, "memory", None)
            if mem is not None:
                self.tb.run("remember", {"fact": m.group(1)})
                return f"Запам'ятав, {T}."
        if re.fullmatch(r"(який|яка) (сьогодні )?день тижня|який сьогодні день", t):
            from .quick import WEEKDAYS
            now = dt.datetime.now()
            return f"Сьогодні {WEEKDAYS[now.weekday()]}, {now:%d.%m}, {T}."
        return None
