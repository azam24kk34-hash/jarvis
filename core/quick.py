"""Миттєві команди без звернення до Claude: «пауза», «далі», «гучніше», «котра година», «протокол вечірка»…
Виконуються локально за частки секунди й нічого не коштують. Усе інше йде в мозок (Gemini / OpenAI / Claude)."""
import datetime as dt
import random
import re

ACK = ["Готово, {t}.", "Зроблено.", "Як скажете, {t}.", "Вже.", "Слушно, {t}."]

WEEKDAYS = ["понеділок", "вівторок", "середа", "четвер", "п'ятниця", "субота", "неділя"]


def _norm(text):
    t = text.lower().strip()
    t = re.sub(r"[,!?…«»\"%]", " ", t)
    t = re.sub(r"(?<!\w)\.|\.(?!\w)", " ", t)  # крапки в адресах сайтів (rozetka.com.ua) лишаємо
    t = re.sub(r"\b(будь ласка|please|будь-ласка|джарвіс|джарвис|jarvis)\b", " ", t)
    return re.sub(r"\s+", " ", t).strip()


class QuickCommands:
    def __init__(self, config, toolbox, protocols):
        self.config = config
        self.tb = toolbox
        self.protocols = protocols

    def _ack(self):
        return random.choice(ACK).format(t=self.config.get("user_title") or "сер")

    def _music(self, action):
        """Керування музикою: спершу YouTube Music у браузері Джарвіса, інакше — медіаклавіші Windows."""
        b = self.tb.browser
        if b is not None and b.running:
            try:
                info = b.ytm_control(action)
                if info.get("ok"):
                    return info
            except Exception:
                pass
        key = {"pause": "play_pause", "resume": "play_pause", "next": "next", "previous": "previous"}.get(action)
        if key:
            self.tb.run("media_control", {"action": key})
        return {}

    def handle(self, text):
        """Повертає (відповідь, tool) якщо команду виконано локально, інакше None."""
        t = _norm(text)
        if not t or len(t) > 60:
            return None
        title = self.config.get("user_title") or "сер"

        if re.fullmatch(r"(постав (на )?)?пауз[ау]|зупини( музику| пісню)?|стоп музик[ау]|вимкни музику", t):
            self._music("pause")
            return "Пауза, " + title + ".", "youtube_music"
        if re.fullmatch(r"(увімкни|включи|постав|запусти|ввімкни)( мою| якусь| мені)? музик[уи]|хочу музику", t):
            b = self.tb.browser
            if b is not None and b.ytm_is_open():
                self._music("resume")
                return self._ack(), "youtube_music"
            r = self.tb.run("youtube_music", {"action": "liked", "shuffle": True})
            if "не увійшов" in r or r.startswith("Помилка"):
                return None  # хай Claude розбереться й пояснить
            return f"Вмикаю вашу музику, {title}.", "youtube_music"
        if (re.search(r"\b(відкрий|відкрити|віднови|відновити|запусти|поверни)\b", t)
                and re.search(r"програм|вікна|сесі|робоче місце|все як було|усе як було", t)
                and re.search(r"минул|останн|було|були|вчора|сесі|перезав|робоче місце", t)):
            r = self.tb.run("restore_session", {})
            return r.split(" (сесія")[0], "restore_session"
        m = re.fullmatch(r"(?:увімкни|ввімкни|включи|постав|зіграй|запусти) (?:мені )?плейлист (.+)", t)
        if m:
            r = self.tb.run("youtube_music", {"action": "playlist", "query": m.group(1), "shuffle": False})
            if "грає" not in r:
                return None  # не знайшли / не увійшли — хай Claude пояснить
            return f"Плейлист «{m.group(1)}», {title}.", "youtube_music"
        m = re.fullmatch(r"(?:увімкни|ввімкни|включи|постав|зіграй|запусти) (?:мені )?(?:пісню|трек|музику|альбом|групу|гурт|виконавця) (.{2,})", t)
        if m and not re.fullmatch(r"(мою|якусь|мені|для .*)", m.group(1)):
            r = self.tb.run("play_music", {"query": m.group(1)})
            if r.startswith("Помилка"):
                return None
            name = r.split(":", 1)[1].split("(")[0].strip() if ":" in r else m.group(1)
            return f"Вмикаю: {name}.", "youtube_music"
        if re.fullmatch(r"(продовж(уй)?|віднови|грай( далі)?|увімкни назад|play)( музику)?", t):
            self._music("resume")
            return self._ack(), "youtube_music"
        if re.fullmatch(r"(наступн(а|у|ий|е)( пісн[яюі]| трек)?|далі|скіп|пропусти|next|інш(у|а) пісн[юя])", t):
            info = self._music("next")
            name = f" Зараз — {info['artist']}, «{info['title']}»." if info.get("title") else ""
            return "Наступна." + name, "youtube_music"
        if re.fullmatch(r"(попередн(я|ю|ій|є)( пісн[яюі]| трек)?|поверни (попередню|пісню))", t):
            self._music("previous")
            return self._ack(), "youtube_music"
        if re.fullmatch(r"(зроби )?(гучніше|голосніше)( музику)?", t):
            self.tb.run("volume_control", {"action": "up", "level": 10})
            return "Гучніше.", "volume_control"
        if re.fullmatch(r"(зроби )?(тихіше)( музику)?", t):
            self.tb.run("volume_control", {"action": "down", "level": 10})
            return "Тихіше.", "volume_control"
        m = re.fullmatch(r"(постав |зроби |встанови )?гучн[іi]сть( на)? (\d{1,3})( відсотк\w*)?", t)
        if m:
            lvl = max(0, min(100, int(m.group(3))))
            self.tb.run("volume_control", {"action": "set", "level": lvl})
            return f"Гучність {lvl} відсотків.", "volume_control"
        if re.fullmatch(r"(вимкни|прибери) звук|без звуку|mute", t):
            self.tb.run("volume_control", {"action": "mute_toggle"})
            return self._ack(), "volume_control"
        if re.fullmatch(r"(котра|яка) (зараз )?година|скільки (зараз )?(часу|годин)|котра година зараз", t):
            now = dt.datetime.now()
            return f"Зараз {now:%H:%M}, {title}.", None
        if re.fullmatch(r"(яке|який) (сьогодні )?(число|день)( сьогодні)?|яка сьогодні дата", t):
            now = dt.datetime.now()
            return f"Сьогодні {WEEKDAYS[now.weekday()]}, {now:%d.%m}.", None
        if re.fullmatch(r"згорни (все|усе|вікна)|покажи робочий стіл", t):
            self.tb.run("window_control", {"action": "show_desktop"})
            return self._ack(), "window_control"
        if re.fullmatch(r"(вподобай|лайкни)( (цю )?пісню)?|мені подобається (ця )?пісня", t):
            if not self._music("like").get("ok"):
                return "Не бачу відкритого YouTube Music у моєму браузері, " + title + ".", None
            return "Додав до вподобаних, " + title + ".", "youtube_music"
        m = re.fullmatch(r"(запусти |активуй |увімкни )?протокол (.+)", t)
        if m and self.protocols:
            key, p = self.protocols.find(m.group(2))
            if p:
                self.tb.run("run_protocol", {"name": key})
                return f"Протокол «{key}» активовано, {title}.", "run_protocol"
        # ---------- сайти й пошук
        m = re.fullmatch(r"(?:знайди|пошукай|загугли|погугли)(?: в| у)?(?: гуглі| google| інтернеті)? (.{2,})", t)
        if m:
            self.tb.run("open_website", {"search_query": m.group(1)})
            return f"Шукаю: {m.group(1)}.", "open_website"
        m = re.fullmatch(r"(?:відкрий|зайди на|перейди на) (?:сайт )?([\w-]+\.(?:com|ua|net|org|io|tv|gov\.ua|com\.ua)(?:/\S*)?)", t)
        if m:
            self.tb.run("open_website", {"url": m.group(1)})
            return self._ack(), "open_website"
        # ---------- програми: «відкрий Chrome», «запусти Телеграм», «закрий Steam»
        m = re.fullmatch(r"(?:відкрий|запусти|включи|увімкни|ввімкни|відкрити|запустити)(?: мені)?(?: програму)? (.{2,40})", t)
        if m and not re.search(r"\b(музик|пісн|плейлист|протокол|програми|вікна|сайт)\b", m.group(1)):
            r = self.tb.resolve_app(m.group(1))
            if r:
                self.tb.launch_resolved(r)
                return f"Відкриваю {r[2]}, {title}.", "open_application"
        m = re.fullmatch(r"(?:закрий|вимкни|заверши)(?: програму)? (.{3,40})", t)
        if m and not re.search(r"\b(вікно|все|усе|музик|звук|комп'ютер|пк)\b", m.group(1)):
            r = self.tb.run("close_application", {"name": m.group(1)})
            if r.startswith("Закрито"):
                return f"Закрив {m.group(1)}.", "close_application"
        # ---------- погода й нагадування
        m = re.fullmatch(r"(?:яка|що з|що за) погод[аиою](?: зараз| сьогодні)?(?: (?:в|у) (.+))?|погода(?: (?:в|у) (.+))?", t)
        if m:
            city = m.group(1) or m.group(2)
            r = self.tb.run("get_weather", {"city": city} if city else {})
            if r.startswith(("Помилка", "Не знайшов")):
                return None
            line = r.splitlines()[0].replace("°C", " градусів").replace("°", " градусів").replace("м/с", "метрів за секунду")
            return line + ".", "get_weather"
        m = re.fullmatch(r"нагадай(?: мені)? через (\d+) (хвилин\w*|годин\w*) (.{2,})", t)
        if m:
            mins = int(m.group(1)) * (60 if m.group(2).startswith("годин") else 1)
            self.tb.run("set_reminder", {"text": m.group(3), "in_minutes": mins})
            return f"Нагадаю через {m.group(1)} {m.group(2)}.", "set_reminder"
        m = re.fullmatch(r"(?:постав )?таймер на (\d+) (хвилин\w*|секунд\w*)", t)
        if m:
            n = int(m.group(1))
            mins = n if m.group(2).startswith("хвилин") else n / 60
            self.tb.run("set_reminder", {"text": "таймер", "in_minutes": mins})
            return f"Таймер на {n} {m.group(2)}.", "set_reminder"
        # ---------- OBS
        if re.fullmatch(r"(?:почни|запусти|старт|розпочни|увімкни|включи)(?: в обс| obs)? (?:трансляцію|стрім|ефір)(?: в обс| в obs)?", t):
            return self.tb.run("obs", {"action": "start_stream"}) + ".", "obs"
        if re.fullmatch(r"(?:зупини|заверши|вимкни|стоп|закінчи)(?: в обс| obs)? (?:трансляцію|стрім|ефір)(?: в обс| в obs)?", t):
            return self.tb.run("obs", {"action": "stop_stream"}) + ".", "obs"
        if re.fullmatch(r"(?:почни|запусти|увімкни|включи|старт) запис(?: в обс| в obs)?", t):
            return self.tb.run("obs", {"action": "start_record"}) + ".", "obs"
        if re.fullmatch(r"(?:зупини|заверши|вимкни|стоп) запис(?: в обс| в obs)?", t):
            return self.tb.run("obs", {"action": "stop_record"}) + ".", "obs"
        m = re.fullmatch(r"(?:перемкни на |постав |увімкни )?сцен[ауі] (.{2,30})", t)
        if m:
            return self.tb.run("obs", {"action": "scene", "name": m.group(1)}) + ".", "obs"
        # ---------- монітори й робочі місця
        m = re.fullmatch(r"(?:запам'ятай|запамʼятай|збережи) (?:розташування|вікна|робоче місце)(?: вікон)?(?: як)? (.{2,30})", t)
        if m:
            return self.tb.run("save_layout", {"name": m.group(1)}), "save_layout"
        m = re.fullmatch(r"(?:розстав|відкрий|віднови|увімкни)(?: вікна| мені)?(?: робоче місце| розташування) (.{2,30})|робоче місце (.{2,30})", t)
        if m:
            name = m.group(1) or m.group(2)
            if not self.tb.layouts.find(name)[1]:
                return None  # хай мозок пояснить, які робочі місця є
            import threading
            threading.Thread(target=self.tb.run, args=("apply_layout", {"name": name}), daemon=True).start()
            return f"Розставляю робоче місце «{name}», {title}.", "apply_layout"
        nums = {"перший": 1, "першому": 1, "головний": 1, "другий": 2, "другому": 2, "третій": 3, "третьому": 3, "1": 1, "2": 2, "3": 3}
        m = re.fullmatch(r"(?:постав|перенеси|перекинь|відкрий|запусти|кинь) (.{2,30}?) на (перший|першому|головний|другий|другому|третій|третьому|1|2|3) "
                         r"(?:екран|монітор)\w*(?: (ліворуч|праворуч|зліва|справа|вліво|вправо))?", t)
        if m:
            side = m.group(3) or ""
            pos = "left" if side.startswith(("лів", "злі", "влі")) else "right" if side else "full"
            r = self.tb.run("place_window", {"app": m.group(1), "monitor": nums[m.group(2)], "position": pos})
            return (f"Готово, {m.group(1)} на екрані {nums[m.group(2)]}." if " — екран " in r else r), "place_window"
        # ---------- натиснути кнопку в програмі
        m = re.fullmatch(r"натисни (?:в|у) (\S+) (.{2,40})", t) or re.fullmatch(r"натисни (.{2,40}?) (?:в|у) (\S+)", t)
        if m:
            app, btn = (m.group(1), m.group(2)) if t.startswith(("натисни в ", "натисни у ")) else (m.group(2), m.group(1))
            if app in ("обс", "obs") and re.search(r"трансляц|стрім", btn):
                return self.tb.run("obs", {"action": "stop_stream" if re.search(r"зупин|стоп|заверш", btn) else "start_stream"}) + ".", "obs"
            r = self.tb.run("click_in_app", {"app": app, "button": btn})
            if not r.startswith("Натиснуто"):
                return None  # хай мозок подивиться список кнопок і розбереться
            return r + ".", "click_in_app"
        return None
