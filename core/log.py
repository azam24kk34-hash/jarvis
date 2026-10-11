"""Журнал роботи: %APPDATA%\\Jarvis\\jarvis.log. У зібраному exe немає консолі, тому всі print()
і непійманні помилки потрапляють сюди — цей файл можна надіслати для діагностики."""
import logging
import logging.handlers
import platform
import sys
import threading

from .config import app_dir

LOG_PATH = app_dir() / "jarvis.log"
log = logging.getLogger("jarvis")


class _Stream:
    def __init__(self, level, original):
        self.level, self.original = level, original

    def write(self, s):
        if self.original:
            try:
                self.original.write(s)
            except Exception:
                pass
        s = s.rstrip()
        if s:
            log.log(self.level, s)

    def flush(self):
        if self.original:
            try:
                self.original.flush()
            except Exception:
                pass

    def isatty(self):
        return False


def setup(version):
    handler = logging.handlers.RotatingFileHandler(LOG_PATH, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(threadName)s: %(message)s", "%Y-%m-%d %H:%M:%S"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    for noisy in ("httpx", "httpcore", "urllib3", "anthropic", "asyncio", "pywebview", "bottle"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    sys.stdout = _Stream(logging.INFO, sys.stdout)
    sys.stderr = _Stream(logging.ERROR, sys.stderr)

    def hook(exc_type, exc, tb):
        log.critical("Неперехоплена помилка", exc_info=(exc_type, exc, tb))

    def thook(args):
        log.error(f"Помилка в потоці {args.thread.name if args.thread else '?'}",
                  exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    sys.excepthook = hook
    threading.excepthook = thook
    log.info("=" * 60)
    log.info(f"J.A.R.V.I.S. {version} | Python {platform.python_version()} | {platform.platform()} | "
             f"frozen={getattr(sys, 'frozen', False)}")
