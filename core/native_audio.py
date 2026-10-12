"""Пряме відтворення mp3 через Windows (MCI, winmm.dll) — без браузерного рушія інтерфейсу.
Використовується, коли вікно сховане або WebView2 не дає грати звук (політика автовідтворення)."""
import ctypes
import os
import tempfile
import threading
import time

IS_WIN = os.name == "nt"
_lock = threading.Lock()
_n = [0]


def available():
    return IS_WIN


def _mci(cmd):
    buf = ctypes.create_unicode_buffer(256)
    err = ctypes.windll.winmm.mciSendStringW(cmd, buf, 255, None)
    return err, buf.value


def play(mp3: bytes, should_stop=lambda: False):
    """Програти й дочекатися кінця (або зупинки). Блокує лише потік мовлення."""
    if not IS_WIN or not mp3:
        return
    with _lock:
        _n[0] += 1
        alias = f"jv{_n[0]}"
        path = os.path.join(tempfile.gettempdir(), f"jarvis_say_{_n[0] % 4}.mp3")
        with open(path, "wb") as f:
            f.write(mp3)
        err, _ = _mci(f'open "{path}" type mpegvideo alias {alias}')
        if err:
            print("MCI open error:", err)
            return
        try:
            _mci(f"play {alias}")
            t0 = time.time()
            limit = len(mp3) / 3000 + 5  # з запасом: 48 кбіт/с ≈ 6000 байт/с
            while time.time() - t0 < limit:
                if should_stop():
                    break
                _, mode = _mci(f"status {alias} mode")
                if mode and mode != "playing" and time.time() - t0 > 0.3:
                    break
                time.sleep(0.05)
        finally:
            _mci(f"stop {alias}")
            _mci(f"close {alias}")
