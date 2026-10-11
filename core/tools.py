"""Інструменти, якими Jarvis керує комп'ютером. Кожен інструмент = опис для Claude + функція."""
import base64
import ctypes
import datetime as dt
import difflib
import io
import json
import os
import platform
import re
import subprocess
import threading
import time
import urllib.parse
import webbrowser
from pathlib import Path

import psutil
import requests

IS_WIN = platform.system() == "Windows"

# ---------------------------------------------------------------- утиліти

APP_ALIASES = {
    # назва -> команда/URI
    "блокнот": "notepad", "notepad": "notepad",
    "калькулятор": "calc", "calculator": "calc",
    "провідник": "explorer", "explorer": "explorer", "файли": "explorer",
    "диспетчер завдань": "taskmgr", "task manager": "taskmgr",
    "командний рядок": "cmd", "cmd": "cmd", "термінал": "wt",
    "powershell": "powershell",
    "налаштування": "ms-settings:", "settings": "ms-settings:",
    "paint": "mspaint", "пейнт": "mspaint",
    "панель керування": "control",
    "ножиці": "snippingtool",
    "браузер": "https://www.google.com",
    "youtube": "https://www.youtube.com", "ютуб": "https://www.youtube.com",
    "gmail": "https://mail.google.com", "пошта": "https://mail.google.com",
}

START_MENU_DIRS = [
    Path(os.environ.get("PROGRAMDATA", "C:/ProgramData")) / "Microsoft/Windows/Start Menu/Programs",
    Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
    Path.home() / "Desktop",
    Path(os.environ.get("PUBLIC", "C:/Users/Public")) / "Desktop",
]

TRANSLIT = {
    "хром": "chrome", "гугл хром": "chrome", "телеграм": "telegram", "стім": "steam",
    "дискорд": "discord", "спотіфай": "spotify", "ворд": "word", "ексель": "excel",
    "павер поінт": "powerpoint", "віскод": "visual studio code", "вс код": "visual studio code",
    "файрфокс": "firefox", "опера": "opera", "едж": "edge", "вайбер": "viber",
    "зум": "zoom", "обс": "obs", "епік": "epic games", "фотошоп": "photoshop",
}


_SC_CACHE = {"t": 0.0, "items": []}


def _shortcuts():
    """Ярлики з меню «Пуск» і робочого столу (кеш на 5 хвилин — сканування повільне)."""
    if time.time() - _SC_CACHE["t"] < 300 and _SC_CACHE["items"]:
        return _SC_CACHE["items"]
    items = _scan_shortcuts()
    _SC_CACHE.update(t=time.time(), items=items)
    return items


def _scan_shortcuts():
    items = []
    for d in START_MENU_DIRS:
        if d.exists():
            for p in d.rglob("*"):
                if p.suffix.lower() in (".lnk", ".url", ".exe", ".appref-ms"):
                    items.append(p)
    return items


def _open(target: str):
    if IS_WIN:
        os.startfile(target)  # noqa
    else:
        subprocess.Popen(["xdg-open", target])


def _press_key(vk: int, times: int = 1):
    if not IS_WIN:
        return
    for _ in range(times):
        ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
        ctypes.windll.user32.keybd_event(vk, 0, 2, 0)
        time.sleep(0.01)


VK_VOLUME_MUTE, VK_VOLUME_DOWN, VK_VOLUME_UP = 0xAD, 0xAE, 0xAF
VK_MEDIA_NEXT, VK_MEDIA_PREV, VK_MEDIA_STOP, VK_MEDIA_PLAY_PAUSE = 0xB0, 0xB1, 0xB2, 0xB3

WMO = {0: "ясно", 1: "переважно ясно", 2: "мінлива хмарність", 3: "хмарно", 45: "туман", 48: "паморозевий туман",
       51: "легка мряка", 53: "мряка", 55: "сильна мряка", 61: "невеликий дощ", 63: "дощ", 65: "сильний дощ",
       66: "крижаний дощ", 67: "сильний крижаний дощ", 71: "невеликий сніг", 73: "сніг", 75: "сильний сніг",
       77: "снігова крупа", 80: "короткочасні зливи", 81: "зливи", 82: "сильні зливи", 85: "снігопад",
       86: "сильний снігопад", 95: "гроза", 96: "гроза з градом", 99: "сильна гроза з градом"}


def _step_label(step):
    t, a = step.get("tool", ""), step.get("args", {}) or {}
    if t == "volume_control":
        return {"set": f"ГУЧНІСТЬ {a.get('level', '')}%", "up": "ГУЧНІСТЬ +", "down": "ГУЧНІСТЬ −"}.get(a.get("action"), "ЗВУК ВИМК/УВІМК")
    if t == "play_music":
        return "МУЗИКА: " + str(a.get("query", "")).upper()
    if t == "open_application":
        return "ЗАПУСК: " + str(a.get("name", "")).upper()
    if t == "close_application":
        return "ЗАКРИТТЯ: " + str(a.get("name", "")).upper()
    if t == "open_website":
        return "ВЕБ: " + str(a.get("url") or a.get("search_query", "")).replace("https://", "").upper()
    if t == "media_control":
        return "МЕДІА: " + str(a.get("action", "")).upper()
    if t == "youtube_music":
        act = {"liked": "МОЇ ЛАЙКИ", "playlist": "ПЛЕЙЛИСТ " + str(a.get("query", "")).upper(), "play": str(a.get("query", "")).upper()}
        return "YT MUSIC: " + act.get(a.get("action"), str(a.get("action", "")).upper())
    if t == "window_control":
        return "ВІКНА: " + str(a.get("action", "")).upper()
    if t == "get_calendar":
        return "КАЛЕНДАР"
    if t == "power_action":
        return "ЖИВЛЕННЯ: " + str(a.get("action", "")).upper()
    return t.replace("_", " ").upper()


# ---------------------------------------------------------------- інструменти

class Toolbox:
    def __init__(self, config, reminders, protocols=None, ui_notify=None, memory=None, browser=None, apps=None):
        self.browser = browser
        self.apps = apps
        self.on_quit = None
        self.config = config
        self.reminders = reminders
        self.protocols = protocols
        self.memory = memory
        self.ui_notify = ui_notify or (lambda *a, **k: None)

    _in_protocol = False

    def card(self, kind, data):
        """Показати голографічну картку в HUD (як панелі, що спливають у фільмі)."""
        if self._in_protocol and kind != "protocol":
            return
        try:
            self.ui_notify(kind, data)
        except Exception:
            pass

    # ---------- описи для Claude
    def definitions(self):
        return [
            {"name": "open_application", "description": "Відкрити програму на ПК (Chrome, Telegram, Steam, Discord, блокнот, калькулятор, провідник, налаштування тощо).",
             "input_schema": {"type": "object", "properties": {"name": {"type": "string", "description": "Назва програми"}}, "required": ["name"]}},
            {"name": "close_application", "description": "Закрити програму (процес) за назвою, напр. 'chrome', 'telegram'.",
             "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
            {"name": "open_website", "description": "Відкрити сайт у браузері або виконати пошук Google/YouTube у браузері.",
             "input_schema": {"type": "object", "properties": {
                 "url": {"type": "string", "description": "Повна адреса або домен"},
                 "search_query": {"type": "string", "description": "Пошуковий запит замість адреси"},
                 "engine": {"type": "string", "enum": ["google", "youtube"], "description": "Де шукати"}}}},
            {"name": "system_status", "description": "Стан системи: процесор, пам'ять, диски, батарея, мережа, час роботи, топ процесів.",
             "input_schema": {"type": "object", "properties": {}}},
            {"name": "volume_control", "description": "Керування гучністю: встановити рівень 0-100, збільшити, зменшити або вимкнути/увімкнути звук.",
             "input_schema": {"type": "object", "properties": {
                 "action": {"type": "string", "enum": ["set", "up", "down", "mute_toggle"]},
                 "level": {"type": "integer", "description": "0-100 для set; крок у відсотках для up/down"}}, "required": ["action"]}},
            {"name": "media_control", "description": "Керування музикою/відео: пауза/відтворення, наступний, попередній трек, стоп.",
             "input_schema": {"type": "object", "properties": {"action": {"type": "string", "enum": ["play_pause", "next", "previous", "stop"]}}, "required": ["action"]}},
            {"name": "look_at_screen", "description": "Зробити знімок екрана і подивитися на нього (щоб описати, що на екрані, прочитати текст, допомогти з помилкою). Також зберігає скріншот у Зображення/Jarvis.",
             "input_schema": {"type": "object", "properties": {"save_only": {"type": "boolean", "description": "true = лише зберегти скріншот, не аналізувати"}}}},
            {"name": "power_action", "description": "Живлення: lock (заблокувати), sleep (сон), shutdown (вимкнути через 30 с), restart (перезавантажити через 30 с), cancel (скасувати вимкнення). Для shutdown/restart спершу ОБОВ'ЯЗКОВО отримай підтвердження користувача.",
             "input_schema": {"type": "object", "properties": {"action": {"type": "string", "enum": ["lock", "sleep", "shutdown", "restart", "cancel"]}}, "required": ["action"]}},
            {"name": "get_weather", "description": "Поточна погода та прогноз на кілька днів для міста.",
             "input_schema": {"type": "object", "properties": {"city": {"type": "string", "description": "Місто; якщо не вказано — місто користувача"}, "days": {"type": "integer", "description": "1-7"}}}},
            {"name": "set_reminder", "description": "Поставити нагадування або таймер. Вкажи або через скільки хвилин, або конкретний час HH:MM (сьогодні/завтра).",
             "input_schema": {"type": "object", "properties": {
                 "text": {"type": "string", "description": "Про що нагадати"},
                 "in_minutes": {"type": "number"},
                 "at_time": {"type": "string", "description": "HH:MM"}}, "required": ["text"]}},
            {"name": "list_reminders", "description": "Список активних нагадувань.", "input_schema": {"type": "object", "properties": {}}},
            {"name": "cancel_reminder", "description": "Скасувати нагадування за id або всі.",
             "input_schema": {"type": "object", "properties": {"id": {"type": "string", "description": "id або 'all'"}}, "required": ["id"]}},
            {"name": "find_files", "description": "Знайти файли/папки на ПК за частиною назви (у домашній папці користувача або вказаній папці).",
             "input_schema": {"type": "object", "properties": {"query": {"type": "string"}, "folder": {"type": "string"}, "extension": {"type": "string", "description": "напр. .pdf"}}, "required": ["query"]}},
            {"name": "open_path", "description": "Відкрити файл або папку за повним шляхом (або 'downloads', 'documents', 'desktop', 'pictures', 'music', 'videos').",
             "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
            {"name": "clipboard", "description": "Прочитати текст з буфера обміну або записати в нього.",
             "input_schema": {"type": "object", "properties": {"action": {"type": "string", "enum": ["read", "write"]}, "text": {"type": "string"}}, "required": ["action"]}},
            {"name": "play_music", "description": "Одразу ввімкнути музику чи відео на YouTube (перший результат пошуку відтворюється автоматично). Напр. 'AC/DC Back in Black', 'lofi', 'український рок'.",
             "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
            {"name": "run_protocol", "description": "Запустити протокол — іменований сценарій з кількох дій (як «House Party Protocol» у фільмі). Напр. 'вечірка', 'робота', 'тиша'.",
             "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
            {"name": "list_protocols", "description": "Список доступних протоколів.", "input_schema": {"type": "object", "properties": {}}},
            {"name": "create_protocol", "description": "Створити або перезаписати протокол. Кроки — список викликів інших інструментів: open_application, close_application, open_website, play_music, volume_control, media_control, system_status, get_weather, get_calendar, open_path, set_reminder, power_action, window_control, press_keys.",
             "input_schema": {"type": "object", "properties": {
                 "name": {"type": "string", "description": "Назва без слова 'протокол', напр. 'ранок'"},
                 "description": {"type": "string"},
                 "steps": {"type": "array", "items": {"type": "object", "properties": {
                     "tool": {"type": "string"}, "args": {"type": "object"}}, "required": ["tool", "args"]}}},
                 "required": ["name", "steps"]}},
            {"name": "delete_protocol", "description": "Видалити протокол.",
             "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
            {"name": "update_self", "description": "Оновити Джарвіса до нової версії з GitHub (лише коли користувач прямо просить «онови себе»/«встанови оновлення»).",
             "input_schema": {"type": "object", "properties": {}}},
            {"name": "restore_session", "description": "Відкрити програми, які були відкриті минулого разу (до вимкнення/перезапуску ПК чи Джарвіса). Можна вказати only — список назв, які відкрити.",
             "input_schema": {"type": "object", "properties": {"only": {"type": "array", "items": {"type": "string"}}}}},
            {"name": "recent_apps", "description": "Програми, якими користувач користувався останнім часом (і що було відкрито минулого разу).",
             "input_schema": {"type": "object", "properties": {}}},
            {"name": "youtube_music", "description": "YouTube Music у браузері Джарвіса (з акаунтом користувача). action: play (пісня/виконавець за query), liked (вподобані пісні; shuffle), playlist (плейлист з бібліотеки за назвою в query), playlists (список плейлистів), pause, resume, next, previous, like (вподобати поточну), now_playing (що грає), volume:NN (гучність плеєра 0-100), login (відкрити вікно входу в акаунт).",
             "input_schema": {"type": "object", "properties": {
                 "action": {"type": "string"}, "query": {"type": "string"},
                 "shuffle": {"type": "boolean", "description": "перемішати (для liked/playlist)"}}, "required": ["action"]}},
            {"name": "browser_open", "description": "Відкрити сайт у браузері Джарвіса (яким він керує сам). Після цього browser_snapshot.",
             "input_schema": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}},
            {"name": "browser_snapshot", "description": "Список кнопок, посилань і полів на поточній сторінці з номерами [N].",
             "input_schema": {"type": "object", "properties": {}}},
            {"name": "browser_click", "description": "Натиснути елемент за номером з browser_snapshot (ref) або за видимим текстом (text).",
             "input_schema": {"type": "object", "properties": {"ref": {"type": "integer"}, "text": {"type": "string"}}}},
            {"name": "browser_type", "description": "Ввести текст у поле (ref з browser_snapshot або target — підпис/placeholder поля); press_enter — натиснути Enter.",
             "input_schema": {"type": "object", "properties": {"text": {"type": "string"}, "ref": {"type": "integer"},
                              "target": {"type": "string"}, "press_enter": {"type": "boolean"}}, "required": ["text"]}},
            {"name": "browser_press", "description": "Натиснути клавішу на сторінці: Enter, Escape, Tab, ArrowDown, PageDown, Space тощо.",
             "input_schema": {"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]}},
            {"name": "browser_scroll", "description": "Прокрутити сторінку: down, up, top, bottom.",
             "input_schema": {"type": "object", "properties": {"direction": {"type": "string", "enum": ["down", "up", "top", "bottom"]}}}},
            {"name": "browser_read", "description": "Прочитати текст поточної сторінки (статті, результати пошуку).",
             "input_schema": {"type": "object", "properties": {}}},
            {"name": "browser_back", "description": "Повернутися на попередню сторінку.", "input_schema": {"type": "object", "properties": {}}},
            {"name": "browser_look", "description": "Подивитися на сторінку (знімок вкладки) — коли список елементів незрозумілий.",
             "input_schema": {"type": "object", "properties": {}}},
            {"name": "browser_window", "description": "Вікно браузера Джарвіса: minimize, show, maximize, close, tabs (список вкладок).",
             "input_schema": {"type": "object", "properties": {"action": {"type": "string", "enum": ["minimize", "show", "maximize", "close", "tabs"]}}, "required": ["action"]}},
            {"name": "remember", "description": "Запам'ятати назавжди факт або вподобання користувача (коли просить «запам'ятай…» або каже щось важливе про себе, що варто пам'ятати).",
             "input_schema": {"type": "object", "properties": {"fact": {"type": "string", "description": "Короткий факт від третьої особи, напр. 'П'є каву без цукру'"}}, "required": ["fact"]}},
            {"name": "forget", "description": "Забути факт з довготривалої пам'яті (за ключовим словом) або 'все'.",
             "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
            {"name": "get_calendar", "description": "Події з календаря користувача (Google/Outlook) на сьогодні або кілька днів уперед.",
             "input_schema": {"type": "object", "properties": {
                 "days": {"type": "integer", "description": "Скільки днів (1 = сьогодні)"},
                 "start_offset": {"type": "integer", "description": "Зсув від сьогодні: 1 = завтра"}}}},
            {"name": "window_control", "description": "Керування вікнами Windows: show_desktop (показати робочий стіл), minimize, maximize, restore, close (закрити активне вікно), snap_left, snap_right, switch (Alt+Tab), task_view, list (список відкритих вікон), focus (перейти до вікна за назвою).",
             "input_schema": {"type": "object", "properties": {
                 "action": {"type": "string", "enum": ["show_desktop", "minimize", "maximize", "restore", "close", "snap_left", "snap_right", "switch", "task_view", "list", "focus"]},
                 "title": {"type": "string", "description": "Частина назви вікна для focus"}}, "required": ["action"]}},
            {"name": "type_text", "description": "Надрукувати текст в активному вікні (наприклад, продиктувати повідомлення).",
             "input_schema": {"type": "object", "properties": {"text": {"type": "string"}, "press_enter": {"type": "boolean"}}, "required": ["text"]}},
            {"name": "press_keys", "description": "Натиснути комбінацію клавіш в активному вікні, напр. 'ctrl+s', 'ctrl+z', 'f5', 'space', 'ctrl+shift+t'.",
             "input_schema": {"type": "object", "properties": {"keys": {"type": "string"}}, "required": ["keys"]}},
        ]

    # ---------- виконання
    def run(self, name, args):
        fn = getattr(self, "t_" + name, None)
        if not fn:
            return f"Невідомий інструмент {name}"
        try:
            return fn(**(args or {}))
        except Exception as e:  # повертаємо помилку Claude, щоб він пояснив
            return f"Помилка: {type(e).__name__}: {e}"

    def resolve_app(self, name):
        """Знайти програму без запуску: ('uri'|'exe'|'lnk', ціль, назва) або None. Швидко (ярлики кешуються)."""
        q = name.strip().lower().strip(" .,!?«»\"")
        q = TRANSLIT.get(q, q)
        if q in APP_ALIASES:
            return "uri", APP_ALIASES[q], name
        if self.apps:  # програма, якою вже користувались — запускаємо за відомим шляхом
            exe, a = self.apps.find(q)
            if exe:
                return "exe", exe, a["name"]
        names = {p.stem.lower(): p for p in _shortcuts()}
        hit = next((p for n, p in names.items() if n == q), None) \
            or next((p for n, p in names.items() if q in n and "uninstall" not in n and "видал" not in n), None)
        if not hit and len(q) >= 4:
            close = difflib.get_close_matches(q, list(names), n=1, cutoff=0.6)
            hit = names[close[0]] if close else None
        return ("lnk", str(hit), hit.stem) if hit else None

    def launch_resolved(self, r):
        kind, target, label = r
        self.card("launch", {"title": label.upper(), "sub": "ЗАПУСК ПРОГРАМИ"})
        if kind == "exe":
            from .apps import launch
            launch(target)
        else:
            _open(target)
        return f"Відкрито: {label}"

    def t_open_application(self, name):
        r = self.resolve_app(name)
        if r:
            return self.launch_resolved(r)
        q = TRANSLIT.get(name.strip().lower(), name.strip().lower())
        try:
            if IS_WIN:
                subprocess.Popen(f'start "" "{q}"', shell=True)
                return f"Спробував запустити '{q}'"
        except Exception:
            pass
        return f"Не знайшов програму '{name}' у меню Пуск"

    def t_close_application(self, name):
        q = TRANSLIT.get(name.lower().strip(), name.lower().strip())
        q = {"edge": "msedge", "едж": "msedge", "chrome": "chrome", "хром": "chrome"}.get(q, q)
        if len(q) < 3:
            return "Назва програми надто коротка — уточніть, що закрити"
        # власні процеси Джарвіса (HUD на WebView2, браузер Джарвіса) не чіпаємо
        mine = {os.getpid()}
        try:
            mine |= {c.pid for c in psutil.Process().children(recursive=True)}
        except Exception:
            pass
        protected = ("jarvis.exe", "python.exe", "pythonw.exe", "msedgewebview2.exe", "explorer.exe", "dwm.exe",
                     "csrss.exe", "winlogon.exe", "svchost.exe", "lsass.exe", "services.exe")
        killed = set()
        for p in psutil.process_iter(["name"]):
            n = (p.info["name"] or "").lower()
            base = n[:-4] if n.endswith(".exe") else n
            match = base == q or (len(q) >= 4 and base.startswith(q))
            if match and n not in protected and p.pid not in mine:
                try:
                    p.terminate()
                    killed.add(p.info["name"])
                except Exception:
                    pass
        return f"Закрито: {', '.join(killed)}" if killed else f"Процес '{name}' не знайдено"

    def t_open_website(self, url=None, search_query=None, engine="google"):
        if search_query:
            q = urllib.parse.quote_plus(search_query)
            url = f"https://www.youtube.com/results?search_query={q}" if engine == "youtube" else f"https://www.google.com/search?q={q}"
        if not url:
            return "Не вказано адресу"
        if not url.startswith("http"):
            url = "https://" + url
        webbrowser.open(url)
        self.card("launch", {"title": urllib.parse.urlparse(url).netloc.replace("www.", "").upper(), "sub": search_query or "ВЕБ-ЗВ'ЯЗОК"})
        return f"Відкрито {url}"

    def t_system_status(self):
        vm = psutil.virtual_memory()
        cpu = psutil.cpu_percent(interval=0.4)
        card = {"cpu": cpu, "ram": vm.percent, "disks": [], "battery": None}
        lines = [f"CPU: {cpu}% ({psutil.cpu_count()} потоків)",
                 f"RAM: {vm.percent}% ({vm.used/2**30:.1f} з {vm.total/2**30:.1f} ГБ)"]
        for part in psutil.disk_partitions(all=False):
            try:
                u = psutil.disk_usage(part.mountpoint)
                if not u.total:
                    continue
                lines.append(f"Диск {part.device}: вільно {u.free/2**30:.0f} ГБ з {u.total/2**30:.0f} ГБ")
                card["disks"].append({"name": part.device.rstrip("\\"), "pct": u.percent, "free": round(u.free / 2**30)})
            except Exception:
                pass
        b = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
        if b:
            lines.append(f"Батарея: {b.percent:.0f}% {'(заряджається)' if b.power_plugged else ''}")
            card["battery"] = round(b.percent)
        up = dt.timedelta(seconds=int(time.time() - psutil.boot_time()))
        lines.append(f"Час роботи: {up}")
        procs = sorted(psutil.process_iter(["name", "memory_info"]), key=lambda p: p.info["memory_info"].rss if p.info["memory_info"] else 0, reverse=True)[:5]
        lines.append("Найбільше пам'яті: " + ", ".join(f"{p.info['name']} {p.info['memory_info'].rss/2**20:.0f}МБ" for p in procs if p.info["memory_info"]))
        card["top"] = [[p.info["name"], round(p.info["memory_info"].rss / 2**20)] for p in procs if p.info["memory_info"]]
        card["uptime"] = str(up)
        self.card("system", card)
        return "\n".join(lines)

    def t_volume_control(self, action, level=None):
        if action == "mute_toggle":
            _press_key(VK_VOLUME_MUTE)
            return "Звук перемкнено"
        if action == "set":
            lvl = max(0, min(100, int(level or 50)))
            _press_key(VK_VOLUME_DOWN, 50)          # кожне натискання = 2%
            _press_key(VK_VOLUME_UP, round(lvl / 2))
            return f"Гучність {lvl}%"
        step = max(2, int(level or 10))
        _press_key(VK_VOLUME_UP if action == "up" else VK_VOLUME_DOWN, round(step / 2))
        return f"Гучність {'збільшено' if action == 'up' else 'зменшено'} на {step}%"

    def t_media_control(self, action):
        vk = {"play_pause": VK_MEDIA_PLAY_PAUSE, "next": VK_MEDIA_NEXT, "previous": VK_MEDIA_PREV, "stop": VK_MEDIA_STOP}[action]
        _press_key(vk)
        return "Готово"

    def t_look_at_screen(self, save_only=False):
        from PIL import ImageGrab
        img = ImageGrab.grab(all_screens=False)
        folder = Path.home() / "Pictures" / "Jarvis"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"screenshot_{dt.datetime.now():%Y%m%d_%H%M%S}.png"
        img.save(path)
        if save_only:
            return f"Скріншот збережено: {path}"
        thumb = img.copy()
        thumb.thumbnail((520, 520))
        tb = io.BytesIO()
        thumb.convert("RGB").save(tb, format="JPEG", quality=70)
        self.card("vision", {"img": base64.b64encode(tb.getvalue()).decode()})
        img.thumbnail((1568, 1568))
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=80)
        return [
            {"type": "text", "text": f"Ось знімок екрана (також збережено: {path})."},
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(buf.getvalue()).decode()}},
        ]

    def t_power_action(self, action):
        if not IS_WIN:
            return "Доступно лише на Windows"
        if action == "lock":
            ctypes.windll.user32.LockWorkStation()
        elif action == "sleep":
            subprocess.Popen("rundll32.exe powrprof.dll,SetSuspendState 0,1,0", shell=True)
        elif action == "shutdown":
            subprocess.Popen("shutdown /s /t 30", shell=True)
        elif action == "restart":
            subprocess.Popen("shutdown /r /t 30", shell=True)
        elif action == "cancel":
            subprocess.Popen("shutdown /a", shell=True)
        return f"Виконано: {action}"

    def t_get_weather(self, city=None, days=1):
        city = (city or self.config.get("city")).strip()
        g = {}
        # «у Львові» → Львів, «в Києві» → Київ, «в Одесі» → Одеса, «у Дніпрі» → Дніпро
        cands = [city]
        for end, rep in (("ові", "ів"), ("єві", "їв"), ("еві", "ів"), ("і", "а"), ("і", "о"), ("у", "")):
            if city.lower().endswith(end):
                cands.append(city[: -len(end)] + rep)
        for c in cands:
            g = requests.get("https://geocoding-api.open-meteo.com/v1/search",
                             params={"name": c, "count": 1, "language": "uk"}, timeout=10).json()
            if g.get("results"):
                break
        if not g.get("results"):
            return f"Не знайшов місто {city}"
        loc = g["results"][0]
        days = max(1, min(7, int(days or 1)))
        w = requests.get("https://api.open-meteo.com/v1/forecast", params={
            "latitude": loc["latitude"], "longitude": loc["longitude"], "timezone": "auto", "forecast_days": days,
            "current": "temperature_2m,apparent_temperature,relative_humidity_2m,wind_speed_10m,weather_code",
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code",
            "wind_speed_unit": "ms"}, timeout=10).json()
        c = w["current"]
        out = [f"{loc['name']}: зараз {c['temperature_2m']:.0f}°C (відчувається {c['apparent_temperature']:.0f}°), "
               f"{WMO.get(c['weather_code'], '')}, вітер {c['wind_speed_10m']:.0f} м/с, вологість {c['relative_humidity_2m']}%"]
        d = w["daily"]
        self.card("weather", {
            "city": loc["name"], "temp": round(c["temperature_2m"]), "feels": round(c["apparent_temperature"]),
            "desc": WMO.get(c["weather_code"], ""), "code": c["weather_code"], "wind": round(c["wind_speed_10m"]),
            "hum": c["relative_humidity_2m"],
            "daily": [{"date": d["time"][i], "min": round(d["temperature_2m_min"][i]), "max": round(d["temperature_2m_max"][i]),
                       "code": d["weather_code"][i], "p": d["precipitation_probability_max"][i]} for i in range(len(d["time"]))]})
        for i in range(len(d["time"])):
            out.append(f"{d['time'][i]}: {d['temperature_2m_min'][i]:.0f}…{d['temperature_2m_max'][i]:.0f}°C, "
                       f"{WMO.get(d['weather_code'][i], '')}, опади {d['precipitation_probability_max'][i]}%")
        return "\n".join(out)

    def t_set_reminder(self, text, in_minutes=None, at_time=None):
        now = dt.datetime.now()
        if at_time:
            h, m = map(int, at_time.replace(".", ":").split(":")[:2])
            when = now.replace(hour=h, minute=m, second=0, microsecond=0)
            if when <= now:
                when += dt.timedelta(days=1)
        elif in_minutes is not None:
            when = now + dt.timedelta(minutes=float(in_minutes))
        else:
            return "Вкажи час або кількість хвилин"
        r = self.reminders.add(text, when)
        self.card("reminder", {"text": text, "time": f"{when:%H:%M}", "date": f"{when:%d.%m}"})
        return f"Нагадування #{r['id']} на {when:%H:%M} ({when:%d.%m}): {text}"

    def t_list_reminders(self):
        items = self.reminders.list()
        if not items:
            return "Активних нагадувань немає"
        return "\n".join(f"#{r['id']} о {r['when'][11:16]} {r['when'][8:10]}.{r['when'][5:7]} — {r['text']}" for r in items)

    def t_cancel_reminder(self, id):
        return self.reminders.cancel(id)

    def t_find_files(self, query, folder=None, extension=None):
        root = Path(folder) if folder else Path.home()
        q = query.lower()
        found, t0 = [], time.time()
        skip = {"appdata", "node_modules", ".git", "$recycle.bin", "windows", "venv", ".cache"}
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d.lower() not in skip and not d.startswith(".")]
            for n in filenames + dirnames:
                if q in n.lower() and (not extension or n.lower().endswith(extension.lower())):
                    found.append(os.path.join(dirpath, n))
            if len(found) >= 25 or time.time() - t0 > 8:
                break
        if found:
            self.card("files", {"query": query, "items": found[:8]})
        return "\n".join(found[:25]) if found else f"Нічого не знайдено за '{query}' у {root}"

    def t_open_path(self, path):
        special = {"downloads": "Downloads", "завантаження": "Downloads", "documents": "Documents", "документи": "Documents",
                   "desktop": "Desktop", "робочий стіл": "Desktop", "pictures": "Pictures", "зображення": "Pictures",
                   "music": "Music", "музика": "Music", "videos": "Videos", "відео": "Videos"}
        p = Path.home() / special[path.lower()] if path.lower() in special else Path(path).expanduser()
        if not p.exists():
            return f"Шлях не існує: {p}"
        _open(str(p))
        return f"Відкрито {p}"

    def t_clipboard(self, action, text=""):
        if not IS_WIN:
            return "Доступно лише на Windows"
        utf8 = "[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
        if action == "read":
            r = subprocess.run(["powershell", "-NoProfile", "-Command", utf8 + "Get-Clipboard -Raw"], capture_output=True,
                               encoding="utf-8", errors="replace", creationflags=0x08000000)
            return (r.stdout or "").strip()[:4000] or "Буфер порожній"
        b64 = base64.b64encode(text.encode("utf-8")).decode()
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        f"Set-Clipboard -Value ([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{b64}')))"],
                       creationflags=0x08000000)
        return "Скопійовано в буфер"

    # ---------- музика
    @staticmethod
    def _yt_first(query):
        """Перший результат пошуку YouTube (id відео + назва) — ті самі id працюють і в YouTube Music."""
        try:
            html = requests.get("https://www.youtube.com/results", params={"search_query": query}, timeout=8,
                                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126 Safari/537.36",
                                         "Accept-Language": "uk,en;q=0.8"},
                                cookies={"CONSENT": "YES+cb", "SOCS": "CAI"}).text
            m = re.search(r'"videoRenderer":\{"videoId":"([\w-]{11})".*?"title":\{"runs":\[\{"text":"(.*?)"\}', html)
            if m:
                try:
                    return m.group(1), json.loads('"' + m.group(2) + '"')
                except Exception:
                    return m.group(1), m.group(2)
        except Exception as e:
            print("YouTube search error:", repr(e))
        return None, query

    def _music_card(self, info, query=""):
        title = (info or {}).get("title") or query
        artist = (info or {}).get("artist") or ""
        self.card("music", {"title": f"{artist} — {title}" if artist else title, "query": query,
                            "service": "YOUTUBE MUSIC" if self.config.get("music_service") == "ytmusic" else "YOUTUBE"})
        return title, artist

    def _after_music(self):
        if self.config.get("browser_minimize_music"):
            try:
                self.browser.window("minimized")
            except Exception:
                pass

    def t_play_music(self, query):
        if self.config.get("music_service") == "ytmusic":
            vid, title = self._yt_first(query)
            if self.browser:
                try:
                    info = self.browser.ytm_play_video(vid) if vid else self.browser.ytm_search_play(query)
                    t, a = self._music_card(info, query)
                    self._after_music()
                    state = "грає" if info.get("playing") else "відкрито, але відтворення не підтверджено"
                    return f"YouTube Music: {a + ' — ' if a else ''}{t} ({state})"
                except Exception as e:
                    print("YT Music browser error:", repr(e))
            # запасний варіант — звичайний браузер за замовчуванням
            url = (f"https://music.youtube.com/watch?v={vid}" if vid
                   else "https://music.youtube.com/search?q=" + urllib.parse.quote_plus(query))
            webbrowser.open(url)
            self._music_card({"title": title}, query)
            return f"Відкрив YouTube Music: {title}"
        vid, title = self._yt_first(query)
        if vid:
            webbrowser.open(f"https://www.youtube.com/watch?v={vid}")
            self._music_card({"title": title}, query)
            return f"Відтворюю: {title}"
        webbrowser.open("https://www.youtube.com/results?search_query=" + urllib.parse.quote_plus(query))
        self._music_card({"title": query}, query)
        return f"Відкрив пошук YouTube: {query}"

    NOT_SIGNED = ("Користувач не увійшов у YouTube Music у браузері Джарвіса. Виклич youtube_music з action=login — "
                  "відкриється вікно входу; попроси користувача увійти в Google-акаунт і закрити це вікно, після чого повтори.")

    def t_youtube_music(self, action, query="", shuffle=True):
        b = self.browser
        if not b:
            return "Браузер недоступний"
        try:
            if action == "login":
                b.login_window()
                self.card("launch", {"title": "YOUTUBE MUSIC", "sub": "УВІЙДІТЬ В АКАУНТ І ЗАКРИЙТЕ ВІКНО"})
                return ("Відкрито вікно браузера Джарвіса. Користувач має увійти в акаунт Google на music.youtube.com "
                        "і закрити вікно — далі музика вмикатиметься з його бібліотеки.")
            if action == "play":
                return self.t_play_music(query)
            if action == "liked":
                info = b.ytm_liked(shuffle=bool(shuffle))
                t, a = self._music_card(info, "Ваші вподобані")
                self._after_music()
                return f"Грають вподобані пісні{' (перемішано)' if shuffle else ''}: зараз {a} — {t}"
            if action == "playlists":
                items = b.ytm_playlists()
                return "Плейлисти: " + ", ".join(i["title"] for i in items) if items else "Плейлистів немає"
            if action == "playlist":
                info = b.ytm_playlist(query, shuffle=bool(shuffle))
                t, a = self._music_card(info, info.get("playlist", query))
                self._after_music()
                return f"Плейлист «{info.get('playlist')}» грає: {a} — {t}"
            if action in ("pause", "resume", "next", "previous", "like", "shuffle", "repeat") or action.startswith("volume:"):
                info = b.ytm_control(action)
                if action in ("next", "previous"):
                    self._music_card(info)
                return f"{action}: {'ok' if info.get('ok') else 'кнопку не знайдено'}; зараз: {info.get('artist')} — {info.get('title')} ({'грає' if info.get('playing') else 'пауза'})"
            if action == "now_playing":
                info = b.ytm_now()
                if not info.get("title"):
                    return "Зараз нічого не грає"
                self._music_card(info)
                return f"Зараз {'грає' if info.get('playing') else 'на паузі'}: {info.get('artist')} — {info.get('title')}"
            return f"Невідома дія {action}"
        except PermissionError:
            return self.NOT_SIGNED
        except LookupError as e:
            return f"Не знайшов такого плейлиста. {e}"

    # ---------- безкоштовний пошук в інтернеті (для Gemini/Groq/Ollama)
    def t_web_search(self, query):
        import html as _html
        results = []
        try:
            r = requests.post("https://html.duckduckgo.com/html/", data={"q": query, "kl": "ua-uk"}, timeout=10,
                              headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126 Safari/537.36"})
            for m in re.finditer(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?class="result__snippet"[^>]*>(.*?)</a>',
                                 r.text, re.S):
                url = urllib.parse.unquote(re.sub(r"^.*?uddg=([^&]+).*$", r"\1", m.group(1)))
                clean = lambda x: _html.unescape(re.sub(r"<[^>]+>", "", x)).strip()
                results.append(f"• {clean(m.group(2))} — {clean(m.group(3))} ({url})")
                if len(results) >= 6:
                    break
        except Exception as e:
            print("DDG error:", repr(e))
        if not results:  # запасний варіант — Google News RSS (добре для «новин»)
            try:
                r = requests.get("https://news.google.com/rss/search", timeout=10,
                                 params={"q": query, "hl": "uk", "gl": "UA", "ceid": "UA:uk"})
                for m in re.finditer(r"<item>.*?<title>(.*?)</title>.*?<pubDate>(.*?)</pubDate>", r.text, re.S):
                    results.append(f"• {_html.unescape(m.group(1))} ({m.group(2)[:16]})")
                    if len(results) >= 8:
                        break
            except Exception as e:
                print("News RSS error:", repr(e))
        self.card("intel", {"query": query})
        return "\n".join(results) if results else "Пошук нічого не дав. Можна відкрити сайт через browser_open."

    # ---------- самооновлення
    def t_update_self(self):
        from . import updater
        from .version import VERSION
        new, url = updater.check(VERSION)
        if not new:
            return f"Встановлена версія {VERSION} — найновіша (або репозиторій приватний і перевірка недоступна)."
        if not url:
            return f"Є версія {new}, але в релізі немає Jarvis-Setup.exe."
        self.card("protocol", {"name": f"оновлення до {new}", "steps": ["ЗАВАНТАЖЕННЯ", "ВСТАНОВЛЕННЯ", "ПЕРЕЗАПУСК"], "done": 0})
        updater.install(url, on_progress=lambda p: None)
        self.card("protocol", {"name": f"оновлення до {new}", "steps": ["ЗАВАНТАЖЕННЯ", "ВСТАНОВЛЕННЯ", "ПЕРЕЗАПУСК"], "done": 2})
        if self.on_quit:
            threading.Thread(target=self.on_quit, daemon=True).start()
        return f"Завантажено версію {new}. Інсталятор зараз закриє мене й запустить оновленого. До зустрічі за хвилину."

    # ---------- програми з минулого разу
    def t_restore_session(self, only=None):
        from .apps import launch, pretty, running_exes
        if not self.apps:
            return "Відстеження програм недоступне"
        exes, when = self.apps.last_session()
        if not exes:
            return "Я ще не бачив, які програми були відкриті минулого разу (запис ведеться раз на хвилину)."
        if only:
            wanted = [o.lower() for o in only]
            exes = [e for e in exes if any(w in pretty(e).lower() or w in os.path.basename(e).lower() for w in wanted)]
        running = running_exes()
        opened, already = [], []
        for exe in exes[:12]:
            if exe.lower() in running:
                already.append(pretty(exe))
                continue
            try:
                launch(exe)
                opened.append(pretty(exe))
                time.sleep(0.4)
            except Exception as e:
                print("Restore error:", exe, repr(e))
        self.card("protocol", {"name": "відновлення сесії", "steps": [n.upper() for n in opened + already],
                               "done": len(opened) + len(already)})
        msg = f"Відкрито: {', '.join(opened)}." if opened else "Нічого нового відкривати не довелось."
        if already:
            msg += f" Уже працювали: {', '.join(already)}."
        return msg + (f" (сесія від {when[:16].replace('T', ' ')})" if when else "")

    def t_recent_apps(self):
        from .apps import pretty
        if not self.apps:
            return "Відстеження програм недоступне"
        last, when = self.apps.last_session()
        lines = []
        if last:
            lines.append("Минулого разу були відкриті: " + ", ".join(pretty(e) for e in last))
        rec = self.apps.recent(12)
        if rec:
            lines.append("Останні програми: " + ", ".join(
                f"{a['name']} ({a.get('last', '')[11:16]} {a.get('last', '')[8:10]}.{a.get('last', '')[5:7]})" for _, a in rec))
        return "\n".join(lines) or "Поки що немає даних — я записую відкриті програми раз на хвилину."

    # ---------- браузер
    def t_browser_open(self, url):
        r = self.browser.open(url)
        self.card("launch", {"title": urllib.parse.urlparse(url if "://" in url else "https://" + url).netloc.upper(),
                             "sub": "БРАУЗЕР ДЖАРВІСА"})
        return r + "\nДалі виклич browser_snapshot, щоб побачити кнопки й поля."

    def t_browser_snapshot(self):
        return self.browser.snapshot()

    def t_browser_click(self, ref=None, text=None):
        return self.browser.click(ref, text)

    def t_browser_type(self, text, ref=None, target=None, press_enter=False):
        return self.browser.type(text, ref, target, bool(press_enter))

    def t_browser_press(self, key):
        return self.browser.press(key)

    def t_browser_scroll(self, direction="down"):
        return self.browser.scroll(direction)

    def t_browser_read(self):
        return self.browser.read()

    def t_browser_back(self):
        return self.browser.back()

    def t_browser_look(self):
        img = self.browser.screenshot()
        self.card("vision", {"img": img})
        return [{"type": "text", "text": "Знімок вкладки браузера Джарвіса."},
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": img}}]

    def t_browser_window(self, action):
        if action == "close":
            return self.browser.close()
        if action == "tabs":
            return self.browser.tabs()
        self.browser.window({"minimize": "minimized", "show": "normal", "maximize": "maximized"}[action])
        return "Готово"

    # ---------- протоколи
    def t_list_protocols(self):
        if not self.protocols or not self.protocols.items:
            return "Протоколів немає"
        return "\n".join(f"{k}: {v.get('description', '')} ({len(v['steps'])} кроків)" for k, v in self.protocols.items.items())

    def t_create_protocol(self, name, steps, description=""):
        return self.protocols.create(name, description, steps)

    def t_delete_protocol(self, name):
        return self.protocols.delete(name)

    def t_run_protocol(self, name):
        key, p = self.protocols.find(name)
        if not p:
            return f"Протокол «{name}» не знайдено. Доступні: {', '.join(self.protocols.items)}"
        labels = [_step_label(s) for s in p["steps"]]
        self.card("protocol", {"name": key, "steps": labels, "done": 0})
        results = []
        self._in_protocol = True
        try:
            results = self._run_steps(key, p, labels)
        finally:
            self._in_protocol = False
        return f"Протокол «{key}» виконано:\n" + "\n".join(results)

    def _run_steps(self, key, p, labels):
        results = []
        for i, step in enumerate(p["steps"]):
            if step["tool"] == "run_protocol":
                continue  # без рекурсії
            out = self.run(step["tool"], step.get("args", {}))
            results.append(f"{step['tool']}: {out if isinstance(out, str) else 'ok'}")
            self.card("protocol", {"name": key, "steps": labels, "done": i + 1})
            time.sleep(0.4)
        return results

    # ---------- пам'ять
    def t_remember(self, fact):
        r = self.memory.add(fact)
        self.card("memory", {"text": fact})
        return r

    def t_forget(self, query):
        return self.memory.forget(query)

    # ---------- календар
    def t_get_calendar(self, days=1, start_offset=0):
        from . import calendar_ics
        urls = self.config.get("calendar_ics")
        if not urls:
            return ("Календар не підключено. Попроси користувача вставити секретну iCal-адресу календаря в налаштуваннях "
                    "(Google Календар → Налаштування календаря → Секретна адреса у форматі iCal).")
        start = dt.date.today() + dt.timedelta(days=int(start_offset or 0))
        evs = calendar_ics.events(urls, int(days or 1), start)
        self.card("calendar", {"date": start.isoformat(), "days": int(days or 1), "events": evs[:8]})
        if not evs:
            return "Подій немає"
        return "\n".join(f"{e['start'][:10]} {'весь день' if e['all_day'] else e['start'][11:16] + '–' + e['end'][11:16]}: "
                         f"{e['title']}{' (' + e['location'] + ')' if e['location'] else ''}" for e in evs)

    # ---------- вікна та клавіатура
    def t_window_control(self, action, title=None):
        if not IS_WIN:
            return "Доступно лише на Windows"
        u = ctypes.windll.user32
        if action == "list":
            return "\n".join(t for _, t in _windows()) or "Відкритих вікон немає"
        if action == "focus":
            hit = next(((h, t) for h, t in _windows() if (title or "").lower() in t.lower()), None)
            if not hit:
                return f"Вікно '{title}' не знайдено"
            ok = _focus(hit[0])
            return f"Перейшов до: {hit[1]}" if ok else f"Windows не дозволив перемкнутися на «{hit[1]}»"
        keys = {"show_desktop": "windows+d", "switch": "alt+tab", "task_view": "windows+tab"}
        if action in keys:
            _send_keys(keys[action])
            return "Готово"
        hwnd, name = _target_window()
        if not hwnd:
            return "Немає активного вікна"
        if action == "close":
            u.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
        elif action in ("minimize", "maximize", "restore"):
            u.ShowWindow(hwnd, {"minimize": 6, "maximize": 3, "restore": 9}[action])
        elif action in ("snap_left", "snap_right"):
            _focus(hwnd)
            time.sleep(0.15)
            _send_keys("windows+left" if action == "snap_left" else "windows+right")
        return f"{action}: {name}"

    def t_type_text(self, text, press_enter=False):
        hwnd, name = _target_window()
        if IS_WIN and hwnd:
            _focus(hwnd)
            time.sleep(0.25)
        import keyboard
        keyboard.write(text, delay=0.005)
        if press_enter:
            keyboard.send("enter")
        return f"Надруковано у вікні «{name}»"

    def t_press_keys(self, keys):
        hwnd, name = _target_window()
        if IS_WIN and hwnd:
            _focus(hwnd)
            time.sleep(0.2)
        _send_keys(keys.lower().replace(" ", ""))
        return f"Натиснуто {keys} у «{name}»"


JARVIS_TITLE = "J.A.R.V.I.S."

if IS_WIN:  # явні типи, щоб 64-бітні дескриптори вікон не обрізались
    from ctypes import wintypes as _wt
    _u = ctypes.windll.user32
    _u.GetForegroundWindow.restype = _wt.HWND
    _u.GetWindow.argtypes, _u.GetWindow.restype = [_wt.HWND, ctypes.c_uint], _wt.HWND
    _u.IsWindowVisible.argtypes = [_wt.HWND]
    _u.GetWindowTextLengthW.argtypes = [_wt.HWND]
    _u.GetWindowTextW.argtypes = [_wt.HWND, ctypes.c_wchar_p, ctypes.c_int]
    _u.ShowWindow.argtypes = [_wt.HWND, ctypes.c_int]
    _u.SetForegroundWindow.argtypes = [_wt.HWND]
    _u.PostMessageW.argtypes = [_wt.HWND, ctypes.c_uint, _wt.WPARAM, _wt.LPARAM]
    _u.IsIconic.argtypes = [_wt.HWND]
    _u.GetWindowLongW.argtypes = [_wt.HWND, ctypes.c_int]
    _u.keybd_event.argtypes = [ctypes.c_ubyte, ctypes.c_ubyte, ctypes.c_uint, ctypes.c_size_t]


def _cloaked(h):
    """Приховані UWP-вікна (Магазин, налаштування у фоні) — DwmGetWindowAttribute(DWMWA_CLOAKED)."""
    try:
        val = ctypes.c_int(0)
        ctypes.windll.dwmapi.DwmGetWindowAttribute(ctypes.c_void_p(h), 14, ctypes.byref(val), ctypes.sizeof(val))
        return val.value != 0
    except Exception:
        return False


def _focus(h):
    """Вивести вікно наперед. Windows не дає фоновій програмі красти фокус — обходимо натисканням Alt."""
    u = ctypes.windll.user32
    if u.IsIconic(h):
        u.ShowWindow(h, 9)  # SW_RESTORE лише для згорнутих — розгорнуті не зменшуємо
    if u.SetForegroundWindow(h):
        return True
    u.keybd_event(0x12, 0, 0, 0)        # Alt down
    u.keybd_event(0x12, 0, 2, 0)        # Alt up
    return bool(u.SetForegroundWindow(h))


def _send_keys(combo):
    import keyboard
    keyboard.send(combo)


def _windows():
    """Видимі вікна з заголовками (без самого Джарвіса), у порядку від верхнього."""
    if not IS_WIN:
        return []
    u = ctypes.windll.user32
    res = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def cb(h, _):
        h = h or 0
        if u.IsWindowVisible(h) and not u.GetWindow(h, 4) and not (u.GetWindowLongW(h, -20) & 0x80) \
                and not _cloaked(h):  # без власника, не tool-window (WS_EX_TOOLWINDOW), не приховане UWP
            n = u.GetWindowTextLengthW(h)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                u.GetWindowTextW(h, buf, n + 1)
                t = buf.value
                if t and t != JARVIS_TITLE and t not in ("Program Manager", "Settings", "Параметри"):
                    res.append((h, t))
        return True

    u.EnumWindows(cb, 0)
    return res


def _target_window():
    """Вікно, з яким працюємо: активне, а якщо активний сам Джарвіс — наступне під ним."""
    if not IS_WIN:
        return None, ""
    u = ctypes.windll.user32
    fg = u.GetForegroundWindow()
    n = u.GetWindowTextLengthW(fg)
    buf = ctypes.create_unicode_buffer(n + 1)
    u.GetWindowTextW(fg, buf, n + 1)
    if fg and buf.value and buf.value != JARVIS_TITLE:
        return fg, buf.value
    wins = _windows()
    return wins[0] if wins else (None, "")
