"""Сторож зависань. Якщо Джарвіс перестав слухати, «завис» інтерфейс чи мозок думає підозріло довго —
записує в %APPDATA%\\Jarvis\\hang.log, де саме застряг кожен потік (для діагностики),
і намагається сам відновитися: перезапускає прослуховування мікрофона."""
import faulthandler
import threading
import time

from .config import app_dir


class Watchdog:
    def __init__(self, jarvis):
        self.j = jarvis
        self._file = open(app_dir() / "hang.log", "a", encoding="utf-8", buffering=1)
        self._last_dump = 0.0
        try:
            faulthandler.enable(file=self._file, all_threads=True)  # аварійне падіння — теж у файл
        except Exception:
            pass

    def start(self):
        threading.Thread(target=self._loop, daemon=True, name="watchdog").start()

    def dump(self, reason):
        if time.time() - self._last_dump < 600:
            return
        self._last_dump = time.time()
        print("Сторож: " + reason + " — стан потоків записано в hang.log")
        try:
            self._file.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} {reason}\n")
            faulthandler.dump_traceback(file=self._file, all_threads=True)
        except Exception as e:
            print("Watchdog dump error:", repr(e))

    def _loop(self):
        beat = 0
        while True:
            # якщо зависне сам Python (усі потоки), C-сторож faulthandler через 3 хв запише стан потоків
            try:
                faulthandler.dump_traceback_later(180, repeat=False, file=self._file)
            except Exception:
                pass
            time.sleep(15)
            beat += 1
            try:
                self._check()
            except Exception as e:
                print("Watchdog error:", repr(e))
            if beat % 40 == 0:  # раз на 10 хв — коротка «ознака життя» в журналі
                v = self.j.voice
                print(f"Пульс: мікрофон {'OK' if v.mic_ok else 'немає'}, черга команд {self.j._commands.qsize()}, "
                      f"інтерфейс {self.j._jsq.qsize()}, CPU Джарвіса {getattr(self.j.cpuwatch, 'self_cpu', 0):.0f}%")

    def _check(self):
        now = time.time()
        j, v = self.j, self.j.voice
        if v.mic_ok and v.tick and not v.speaking and now - v.tick > 45:
            self.dump(f"мікрофон не слухає вже {now - v.tick:.0f} с")
        if j._js_busy_since and now - j._js_busy_since > 20:
            self.dump(f"інтерфейс не відповідає {now - j._js_busy_since:.0f} с")
        if getattr(j, "_busy_since", 0) and now - j._busy_since > 150:
            self.dump(f"відповідь готується вже {now - j._busy_since:.0f} с")
