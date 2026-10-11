"""Спільні налаштування тестів: окрема тимчасова папка замість %APPDATA%\\Jarvis, щоб тести не чіпали ваші дані."""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="jarvis-test-")
os.environ.setdefault("JARVIS_HEADLESS", "1")

import pytest  # noqa: E402

IS_WIN = sys.platform == "win32"
windows_only = pytest.mark.skipif(not IS_WIN, reason="лише Windows")


@pytest.fixture
def config():
    from core.config import Config
    c = Config()
    c.data["api_key"] = "sk-test"
    return c


@pytest.fixture
def toolbox(config):
    from core.protocols import Protocols
    from core.reminders import Reminders
    from core.tools import Toolbox
    cards = []
    tb = Toolbox(config, Reminders(lambda r: None), Protocols(), ui_notify=lambda k, d: cards.append((k, d)))
    tb.cards = cards
    return tb
