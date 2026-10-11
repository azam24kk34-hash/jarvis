"""Щоб Джарвіс ніколи не «душив» комп'ютер:
- знижений пріоритет процесу (ігри, OBS і браузер завжди мають перевагу);
- сторож навантаження: якщо процесор довго завантажений — пише в журнал, ХТО саме вантажить
  (сам Джарвіс, його інтерфейс, його браузер з музикою чи інша програма), а Джарвіс сам себе пригальмовує."""
import os
import threading
import time

import psutil

IS_WIN = os.name == "nt"


def lower_priority():
    try:
        p = psutil.Process()
        p.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS if IS_WIN else 5)
        print("Пріоритет Джарвіса знижено (BELOW_NORMAL) — інші програми мають перевагу")
    except Exception as e:
        print("Priority error:", repr(e))


def _mine():
    me = psutil.Process()
    procs = [me]
    try:
        procs += me.children(recursive=True)
    except Exception:
        pass
    return procs


def _label(p):
    try:
        n = p.name().lower()
    except Exception:
        return "?"
    if p.pid == os.getpid():
        return "Джарвіс (ядро)"
    if "webview2" in n:
        return "інтерфейс (WebView2)"
    if n in ("msedge.exe", "chrome.exe"):
        return "браузер Джарвіса (музика)"
    if n == "node.exe":
        return "керування браузером (Playwright)"
    if n.startswith("flac"):
        return "кодування звуку (flac)"
    return n


class CpuWatch:
    def __init__(self, on_self_heavy=None, interval=10):
        self.interval = interval
        self.on_self_heavy = on_self_heavy or (lambda share: None)
        self.self_cpu = 0.0  # % від усього процесора, які зараз їсть Джарвіс з дочірніми процесами
        self._tracked = {}
        self._high = 0

    def start(self):
        threading.Thread(target=self._loop, daemon=True, name="cpuwatch").start()

    def _sample_mine(self):
        """{назва: % від усього процесора} для Джарвіса й дочірніх процесів."""
        ncpu = psutil.cpu_count() or 1
        out = {}
        for p in _mine():
            key = p.pid
            try:
                if key not in self._tracked:
                    self._tracked[key] = p
                    p.cpu_percent(None)
                    continue
                v = self._tracked[key].cpu_percent(None) / ncpu
            except Exception:
                self._tracked.pop(key, None)
                continue
            lbl = _label(p)
            out[lbl] = out.get(lbl, 0) + v
        return out

    def _top_system(self, n=5):
        procs = []
        for p in psutil.process_iter(["name"]):
            try:
                procs.append((p, p.cpu_percent(None)))
            except Exception:
                pass
        time.sleep(1.0)
        ncpu = psutil.cpu_count() or 1
        res = []
        for p, _ in procs:
            try:
                res.append((p.info["name"], p.cpu_percent(None) / ncpu))
            except Exception:
                pass
        res.sort(key=lambda x: -x[1])
        return [(nm, v) for nm, v in res if v >= 1][:n]

    def _loop(self):
        psutil.cpu_percent(None)
        while True:
            time.sleep(self.interval)
            try:
                total = psutil.cpu_percent(None)
                mine = self._sample_mine()
                self.self_cpu = sum(mine.values())
                if self.self_cpu >= 25:
                    print("CPU Джарвіса {:.0f}% (система {:.0f}%): {}".format(
                        self.self_cpu, total, ", ".join(f"{k} {v:.0f}%" for k, v in sorted(mine.items(), key=lambda x: -x[1]))))
                    self.on_self_heavy(self.self_cpu)
                if total >= 90:
                    self._high += 1
                    if self._high in (2, 12):  # ~20 с і ~2 хв поспіль — записуємо, хто винен
                        top = self._top_system()
                        print(f"Процесор {total:.0f}% уже {self._high * self.interval} с. Найбільше: " +
                              ", ".join(f"{nm} {v:.0f}%" for nm, v in top) +
                              f" | з них Джарвіс: {self.self_cpu:.0f}%")
                else:
                    self._high = 0
            except Exception as e:
                print("CpuWatch error:", repr(e))
