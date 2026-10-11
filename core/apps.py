"""Пам'ять про програми: які були відкриті минулого разу і якими ви користуєтесь останнім часом.
Раз на хвилину Джарвіс дивиться, які вікна відкриті, і записує програми в %APPDATA%\\Jarvis\\apps.json.
Після перезапуску ПК можна сказати «відкрий програми з минулого разу» — і він їх відновить."""
import ctypes
import datetime as dt
import json
import os
import platform
import subprocess
import threading
import time

import psutil

from .config import app_dir

IS_WIN = platform.system() == "Windows"

# системні та службові процеси, які не вважаємо «програмами користувача»
SKIP = {
    "explorer.exe", "applicationframehost.exe", "msedgewebview2.exe", "textinputhost.exe", "systemsettings.exe",
    "shellexperiencehost.exe", "startmenuexperiencehost.exe", "searchhost.exe", "searchapp.exe", "lockapp.exe",
    "jarvis.exe", "python.exe", "pythonw.exe", "taskmgr.exe", "dwm.exe", "ctfmon.exe", "rundll32.exe",
    "nvidia share.exe", "nvidia overlay.exe", "gamebar.exe", "widgets.exe", "phoneexperiencehost.exe",
    "securityhealthsystray.exe", "cmd.exe", "conhost.exe", "openconsole.exe", "windowsterminal.exe",
}

PRETTY = {"msedge": "Microsoft Edge", "chrome": "Google Chrome", "firefox": "Firefox", "telegram": "Telegram",
          "steam": "Steam", "discord": "Discord", "spotify": "Spotify", "code": "Visual Studio Code",
          "winword": "Word", "excel": "Excel", "powerpnt": "PowerPoint", "outlook": "Outlook",
          "notepad": "Блокнот", "viber": "Viber", "obs64": "OBS", "zoom": "Zoom", "teams": "Teams", "ms-teams": "Teams"}


def pretty(exe):
    base = os.path.splitext(os.path.basename(exe))[0]
    return PRETTY.get(base.lower(), base)


def _window_pids():
    """PID процесів, що мають видимі звичайні вікна (Windows)."""
    from .tools import _windows
    u = ctypes.windll.user32
    pids = {}
    for h, title in _windows():
        pid = ctypes.c_ulong(0)
        u.GetWindowThreadProcessId(ctypes.c_void_p(h), ctypes.byref(pid))
        if pid.value:
            pids.setdefault(pid.value, title)
    return pids


def snapshot():
    """[(exe_path, вікно)] програм користувача, відкритих просто зараз."""
    if not IS_WIN:
        return []
    # виключаємо лише власний браузер Джарвіса (Playwright: node → msedge/chrome); програми,
    # які Джарвіс відкрив для вас, теж дочірні процеси — їх треба пам'ятати
    mine = {os.getpid()}
    try:
        for c in psutil.Process().children(recursive=True):
            if c.name().lower() in ("node.exe", "msedge.exe", "chrome.exe", "msedgewebview2.exe"):
                mine.add(c.pid)
    except Exception:
        pass
    out = {}
    for pid, title in _window_pids().items():
        if pid in mine:
            continue
        try:
            p = psutil.Process(pid)
            exe = p.exe()
        except Exception:
            continue
        name = os.path.basename(exe).lower()
        # програми з WindowsApps (Магазин) напряму не запускаються — їх пропускаємо; system32 лишаємо (Блокнот, Paint)
        if not exe or name in SKIP or "\\windowsapps\\" in exe.lower() or "\\systemapps\\" in exe.lower():
            continue
        out.setdefault(exe, title)
    return list(out.items())


class AppTracker:
    def __init__(self, interval=60, snap=snapshot):
        self._file = app_dir() / "apps.json"
        self._lock = threading.Lock()
        self.interval = interval
        self._snap = snap
        try:
            self.data = json.loads(self._file.read_text(encoding="utf-8"))
        except Exception:
            self.data = {}
        self.data.setdefault("apps", {})
        # що було відкрите наприкінці МИНУЛОГО запуску — це і є «минулий раз»
        prev = self.data.get("current_session") or {}
        if prev.get("apps"):
            self.data["last_session"] = prev
        self.data["current_session"] = {"started": dt.datetime.now().isoformat(timespec="seconds"), "apps": []}

    def start(self):
        threading.Thread(target=self._loop, daemon=True, name="apps").start()

    def _save(self):
        try:
            self._file.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception as e:
            print("Apps save error:", repr(e))

    def tick(self):
        items = self._snap()
        now = dt.datetime.now().isoformat(timespec="seconds")
        with self._lock:
            apps = self.data["apps"]
            for exe, title in items:
                a = apps.setdefault(exe, {"name": pretty(exe), "first": now, "minutes": 0})
                a["last"] = now
                a["title"] = title[:120]
                a["minutes"] = a.get("minutes", 0) + self.interval / 60
            if items:  # порожній знімок (наприклад, під час вимкнення) не затирає сесію
                self.data["current_session"]["apps"] = [exe for exe, _ in items]
                self.data["current_session"]["updated"] = now
                if IS_WIN:  # де стояло вікно кожної програми — щоб відновити й розташування на моніторах
                    try:
                        from .winlayout import capture
                        self.data["current_session"]["layout"] = capture()
                    except Exception as e:
                        print("Layout capture error:", repr(e))
            # не роздуваємо файл: лишаємо 150 останніх програм
            if len(apps) > 150:
                for exe in sorted(apps, key=lambda e: apps[e].get("last", ""))[:len(apps) - 150]:
                    del apps[exe]
            self._save()

    def _loop(self):
        time.sleep(20)
        while True:
            try:
                self.tick()
            except Exception as e:
                print("App tracker error:", repr(e))
            time.sleep(self.interval)

    # ---------------------------------------------------------------- для інструментів
    def last_layout(self):
        return [e for e in (self.data.get("last_session") or {}).get("layout", []) if os.path.exists(e.get("exe", ""))]

    def last_session(self):
        s = self.data.get("last_session") or {}
        return [e for e in s.get("apps", []) if os.path.exists(e)], s.get("updated") or s.get("started")

    def recent(self, n=12):
        apps = self.data["apps"]
        order = sorted(apps.items(), key=lambda kv: kv[1].get("last", ""), reverse=True)
        return [(exe, a) for exe, a in order if os.path.exists(exe)][:n]

    def find(self, name):
        q = name.lower().strip()
        for exe, a in self.recent(150):
            if q in a["name"].lower() or q in os.path.basename(exe).lower():
                return exe, a
        return None, None


def launch(exe):
    """Запустити програму окремим процесом (не дочірнім до Джарвіса)."""
    if IS_WIN:
        flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        subprocess.Popen([exe], cwd=os.path.dirname(exe), creationflags=flags, close_fds=True)
    else:
        subprocess.Popen([exe], cwd=os.path.dirname(exe), start_new_session=True)


def running_exes():
    out = set()
    for p in psutil.process_iter(["exe"]):
        if p.info.get("exe"):
            out.add(p.info["exe"].lower())
    return out
