"""Проактивний Джарвіс: сам стежить за системою і звертається першим, як у фільмі
(«Сер, заряд батареї критичний», «З поверненням, сер»)."""
import ctypes
import platform
import socket
import threading
import time

import psutil

IS_WIN = platform.system() == "Windows"


def idle_seconds() -> float:
    """Скільки секунд користувач не торкався миші/клавіатури (Windows)."""
    if not IS_WIN:
        return 0.0

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

    lii = LASTINPUTINFO()
    lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
        return 0.0
    k32 = ctypes.windll.kernel32
    k32.GetTickCount.restype = ctypes.c_uint32   # інакше після ~25 днів роботи ПК число стає від'ємним
    return ((k32.GetTickCount() - lii.dwTime) & 0xFFFFFFFF) / 1000.0


def online() -> bool:
    # спершу сервери, які Джарвісу справді потрібні (публічні DNS у деяких мережах заблоковані)
    for host in (("api.anthropic.com", 443), ("www.google.com", 443), ("1.1.1.1", 53)):
        try:
            socket.create_connection(host, timeout=2).close()
            return True
        except OSError:
            continue
    return False


class Monitor:
    def __init__(self, config, on_alert, on_welcome):
        """on_alert(text, level) — level: 'info' | 'warn' | 'crit';  on_welcome(minutes_away)"""
        self.config = config
        self.on_alert = on_alert
        self.on_welcome = on_welcome
        self._bat_warned = set()
        self._was_low = False
        self._last = {}            # тип -> час останнього попередження
        self._high_cpu_since = None
        self._high_ram_since = None
        self._net_fail = 0
        self._net_up = True
        self._away = False
        self._away_since = 0.0

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()

    def _cooldown(self, key, seconds):
        now = time.time()
        if now - self._last.get(key, 0) < seconds:
            return False
        self._last[key] = now
        return True

    def _loop(self):
        tick = 0
        psutil.cpu_percent(None)
        for p in psutil.process_iter():  # «прогріваємо» лічильники процесів, щоб перше попередження називало правильний
            try:
                p.cpu_percent(None)
            except Exception:
                pass
        while True:
            time.sleep(5)
            tick += 1
            try:
                if self.config.get("welcome_back"):
                    self._check_presence()
                if not self.config.get("proactive_alerts"):
                    continue
                self._check_battery()
                self._check_load()
                if tick % 3 == 0:
                    self._check_network()
                if tick % 120 == 0:
                    self._check_disk()
            except Exception as e:
                print("Monitor error:", e)

    # ---------------------------------------------------------- присутність
    def _check_presence(self):
        idle = idle_seconds()
        limit = float(self.config.get("welcome_after_min") or 15) * 60
        if not self._away and idle >= limit:
            self._away = True
            self._away_since = time.time() - idle
        elif self._away and idle < 3:
            self._away = False
            self.on_welcome(int((time.time() - self._away_since) / 60))

    # ---------------------------------------------------------- батарея
    def _check_battery(self):
        b = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
        if not b:
            return
        if b.power_plugged:
            if self._was_low:
                self.on_alert("Живлення підключено, сер. Заряджаємось.", "info")
            self._bat_warned.clear()
            self._was_low = False
            return
        for level, text, lvl in ((20, "Сер, заряд батареї {p}%. Рекомендую підключити живлення.", "warn"),
                                 (10, "Сер, заряд батареї лише {p}%. Ми працюємо на резервах.", "crit"),
                                 (5, "Сер, заряд критичний — {p}%. Збережіть роботу, поки є можливість.", "crit")):
            if b.percent <= level and level not in self._bat_warned:
                self._bat_warned.update(l for l in (20, 10, 5) if l >= level)
                self._was_low = True
                self.on_alert(text.format(p=round(b.percent)), lvl)
                break

    # ---------------------------------------------------------- навантаження
    @staticmethod
    def _top(attr):
        procs = []
        for p in psutil.process_iter(["name", "cpu_percent", "memory_percent"]):
            n = p.info["name"] or ""
            if n.lower() in ("system idle process", "idle", "system", "registry"):
                continue
            procs.append((p.info.get(attr) or 0, n))
        procs.sort(reverse=True)
        return procs[0][1].replace(".exe", "") if procs else "невідомий процес"

    def _check_load(self):
        now = time.time()
        cpu = psutil.cpu_percent(None)
        ram = psutil.virtual_memory().percent
        self._high_cpu_since = (self._high_cpu_since or now) if cpu >= 95 else None
        self._high_ram_since = (self._high_ram_since or now) if ram >= 92 else None
        if self._high_cpu_since and now - self._high_cpu_since > 45 and self._cooldown("cpu", 900):
            self.on_alert(f"Сер, процесор завантажений на {round(cpu)}% уже понад пів хвилини. "
                          f"Найбільше навантаження дає {self._top('cpu_percent')}.", "warn")
        if self._high_ram_since and now - self._high_ram_since > 30 and self._cooldown("ram", 900):
            self.on_alert(f"Сер, пам'ять заповнена на {round(ram)}%. Найбільше займає {self._top('memory_percent')}.", "warn")

    def _check_disk(self):
        try:
            u = psutil.disk_usage("C:\\" if IS_WIN else "/")
        except Exception:
            return
        free_gb = u.free / 2**30
        if free_gb < 5 and self._cooldown("disk", 6 * 3600):
            self.on_alert(f"Сер, на системному диску залишилось лише {free_gb:.1f} гігабайта.", "warn")

    # ---------------------------------------------------------- мережа
    def _check_network(self):
        ok = online()
        if ok:
            if not self._net_up:
                self._net_up = True
                self.on_alert("З'єднання з мережею відновлено, сер.", "info")
            self._net_fail = 0
        else:
            self._net_fail += 1
            if self._net_up and self._net_fail >= 2:
                self._net_up = False
                self.on_alert("Сер, втрачено з'єднання з інтернетом. Частина моїх систем недоступна.", "crit")
