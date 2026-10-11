"""Вікна на кількох моніторах: знайти вікно програми, поставити його на потрібний екран
(на весь екран, ліворуч, праворуч…), запам'ятати й відновити розташування («робочі місця»).
Екран 1 — головний, далі — зліва направо."""
import ctypes
import json
import os
import platform
import threading
import time
from ctypes import wintypes

import psutil

from .config import app_dir

IS_WIN = platform.system() == "Windows"

if IS_WIN:
    _u = ctypes.WinDLL("user32")  # окремий екземпляр: власні argtypes не впливають на pywebview

    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", RECT), ("rcWork", RECT), ("dwFlags", wintypes.DWORD)]

    class WINDOWPLACEMENT(ctypes.Structure):
        _fields_ = [("length", wintypes.UINT), ("flags", wintypes.UINT), ("showCmd", wintypes.UINT),
                    ("ptMinPosition", wintypes.POINT), ("ptMaxPosition", wintypes.POINT), ("rcNormalPosition", RECT)]

    _MONENUM = ctypes.WINFUNCTYPE(ctypes.c_int, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(RECT), wintypes.LPARAM)
    _u.EnumDisplayMonitors.argtypes = [wintypes.HDC, ctypes.c_void_p, _MONENUM, wintypes.LPARAM]
    _u.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MONITORINFO)]
    _u.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    _u.MonitorFromWindow.restype = wintypes.HMONITOR
    _u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
    _u.GetWindowPlacement.argtypes = [wintypes.HWND, ctypes.POINTER(WINDOWPLACEMENT)]
    _u.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
    _u.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    _u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]

SW_MAXIMIZE, SW_RESTORE, SW_MINIMIZE = 3, 9, 6
SWP_NOZORDER, SWP_SHOWWINDOW = 0x0004, 0x0040

ALIASES = {"обс": "obs", "обс студіо": "obs", "хром": "chrome", "гугл хром": "chrome", "телеграм": "telegram",
           "дискорд": "discord", "стім": "steam", "едж": "msedge", "edge": "msedge", "спотіфай": "spotify",
           "віскод": "code", "vs code": "code", "провідник": "explorer", "блокнот": "notepad", "ворд": "winword",
           "ексель": "excel", "вайбер": "viber", "фотошоп": "photoshop"}


def enable_dpi_awareness():
    """Справжні піксельні координати на моніторах з масштабом 125–200% (інакше вікна стануть криво)."""
    if not IS_WIN:
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            _u.SetProcessDPIAware()
        except Exception:
            pass


def monitors():
    """[{'i': 1, 'work': (l,t,r,b), 'full': (...), 'primary': True}, ...] — головний перший, інші зліва направо."""
    if not IS_WIN:
        return [{"i": 1, "work": (0, 0, 1920, 1040), "full": (0, 0, 1920, 1080), "primary": True, "h": 0}]
    out = []

    def cb(hmon, hdc, rc, lp):
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        _u.GetMonitorInfoW(hmon, ctypes.byref(mi))
        w, f = mi.rcWork, mi.rcMonitor
        out.append({"work": (w.left, w.top, w.right, w.bottom), "full": (f.left, f.top, f.right, f.bottom),
                    "primary": bool(mi.dwFlags & 1), "h": hmon})
        return 1

    _u.EnumDisplayMonitors(None, None, _MONENUM(cb), 0)
    out.sort(key=lambda m: (not m["primary"], m["full"][0], m["full"][1]))
    for i, m in enumerate(out, 1):
        m["i"] = i
    return out


def monitor_of(hwnd):
    h = _u.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
    for m in monitors():
        if m["h"] == h:
            return m["i"]
    return 1


def _exe_of(hwnd):
    pid = wintypes.DWORD(0)
    _u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    try:
        return psutil.Process(pid.value).exe()
    except Exception:
        return ""


def find_windows(query=None, exe=None):
    """Вікна, де назва або програма містить query (або exe точно збігається). → [(hwnd, title, exe)]"""
    from .tools import _windows
    q = (query or "").lower().strip()
    q = ALIASES.get(q, q)
    out = []
    for h, title in _windows():
        path = _exe_of(h)
        base = os.path.splitext(os.path.basename(path))[0].lower()
        if exe:
            if path.lower() == exe.lower():
                out.append((h, title, path))
        elif q and (q in title.lower() or q in base or base.startswith(q[:4])) and len(q) >= 2:
            out.append((h, title, path))
    return out


def placement(hwnd):
    wp = WINDOWPLACEMENT()
    wp.length = ctypes.sizeof(WINDOWPLACEMENT)
    _u.GetWindowPlacement(hwnd, ctypes.byref(wp))
    r = RECT()
    _u.GetWindowRect(hwnd, ctypes.byref(r))
    return {"rect": [r.left, r.top, r.right, r.bottom], "max": wp.showCmd == SW_MAXIMIZE,
            "min": wp.showCmd == 2, "monitor": monitor_of(hwnd)}


def _target_rect(mon, position):
    l, t, r, b = mon["work"]
    w, h = r - l, b - t
    return {"left": (l, t, w // 2, h), "right": (l + w // 2, t, w - w // 2, h), "top": (l, t, w, h // 2),
            "bottom": (l, t + h // 2, w, h - h // 2), "center": (l + w // 6, t + h // 8, w * 2 // 3, h * 3 // 4),
            "full": (l, t, w, h)}.get(position, (l, t, w, h))


def place(hwnd, monitor=1, position="full"):
    """Поставити вікно на екран monitor (1, 2, …) у позицію full/left/right/top/bottom/center."""
    mons = monitors()
    mon = next((m for m in mons if m["i"] == int(monitor or 1)), mons[0])
    _u.ShowWindow(hwnd, SW_RESTORE)
    x, y, w, h = _target_rect(mon, position)
    _u.SetWindowPos(hwnd, None, x, y, w, h, SWP_NOZORDER | SWP_SHOWWINDOW)
    if position == "full":
        _u.ShowWindow(hwnd, SW_MAXIMIZE)
    return mon["i"]


def restore_placement(hwnd, p):
    """Повернути вікно туди, де воно було (з урахуванням того, що монітор могли відключити)."""
    mons = monitors()
    if p.get("monitor", 1) > len(mons):
        return place(hwnd, 1, "full" if p.get("max") else "center")
    _u.ShowWindow(hwnd, SW_RESTORE)
    l, t, r, b = p["rect"]
    if p.get("max"):
        mon = next(m for m in mons if m["i"] == p.get("monitor", 1))
        x, y, w, h = _target_rect(mon, "center")
        _u.SetWindowPos(hwnd, None, x, y, w, h, SWP_NOZORDER | SWP_SHOWWINDOW)
        _u.ShowWindow(hwnd, SW_MAXIMIZE)
    else:
        _u.SetWindowPos(hwnd, None, l, t, r - l, b - t, SWP_NOZORDER | SWP_SHOWWINDOW)
    return p.get("monitor", 1)


def wait_window(exe=None, query=None, timeout=25):
    t0 = time.time()
    while time.time() - t0 < timeout:
        w = find_windows(query=query, exe=exe)
        if w:
            return w[0]
        time.sleep(0.5)
    return None


def capture():
    """Знімок: де стоїть головне вікно кожної програми користувача."""
    from .apps import snapshot
    out, seen = [], set()
    for exe, title in snapshot():
        if exe in seen:
            continue
        w = find_windows(exe=exe)
        if not w:
            continue
        seen.add(exe)
        p = placement(w[0][0])
        if p["min"]:
            continue
        out.append({"exe": exe, "title": title[:80], **p})
    return out


class Layouts:
    """Збережені «робочі місця»: «стрім», «робота»… → де стоїть кожна програма."""

    def __init__(self):
        self._file = app_dir() / "layouts.json"
        self._lock = threading.Lock()
        try:
            self.items = json.loads(self._file.read_text(encoding="utf-8"))
        except Exception:
            self.items = {}

    def save(self, name, entries):
        with self._lock:
            self.items[name.lower().strip()] = entries
            self._file.write_text(json.dumps(self.items, ensure_ascii=False, indent=1), encoding="utf-8")

    def find(self, name):
        import difflib
        k = name.lower().strip()
        if k in self.items:
            return k, self.items[k]
        m = difflib.get_close_matches(k, list(self.items), n=1, cutoff=0.6)
        return (m[0], self.items[m[0]]) if m else (None, None)


def apply(entries, launch_missing=True, on_progress=None):
    """Відкрити відсутні програми й розставити всі вікна. → (розставлено, не вийшло)"""
    from .apps import launch, pretty, running_exes
    running = running_exes()
    for e in entries:  # спершу запускаємо все разом — так швидше
        if launch_missing and e["exe"].lower() not in running and os.path.exists(e["exe"]):
            try:
                launch(e["exe"])
            except Exception as ex:
                print("Layout launch error:", repr(ex))
    done, failed = [], []
    for e in entries:
        w = wait_window(exe=e["exe"], timeout=25)
        if not w:
            failed.append(pretty(e["exe"]))
            continue
        restore_placement(w[0], e)
        done.append(pretty(e["exe"]))
        if on_progress:
            on_progress(done, failed)
    return done, failed
