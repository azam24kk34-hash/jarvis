"""Справжній YouTube Music (без входу в акаунт). Перевіряє, що селектори Джарвіса ще актуальні."""
import pytest

from conftest import windows_only

pytestmark = [windows_only, pytest.mark.network]


@pytest.fixture(scope="module")
def browser():
    from core.browser import Browser
    from core.config import Config
    b = Browser(Config())
    yield b
    try:
        b.close()
    except Exception:
        pass


def test_youtube_search_finds_video():
    from core.tools import Toolbox
    vid, title = Toolbox._yt_first("AC/DC Back In Black")
    print(vid, title)
    assert vid and len(vid) == 11


def test_ytmusic_plays_video(browser):
    from core.tools import Toolbox
    vid, _ = Toolbox._yt_first("AC/DC Back In Black")
    info = browser.ytm_play_video(vid)
    print(info)
    assert info.get("title")
    assert info.get("playing"), "відео відкрилось, але не грає"


def test_ytmusic_controls(browser):
    print("pause:", browser.ytm_control("pause"))
    print("resume:", browser.ytm_control("resume"))
    info = browser.ytm_control("next")
    print("next:", info)
    assert info.get("ok"), "кнопка «Наступна» не знайдена — змінився інтерфейс YT Music"


def test_ytmusic_search_play(browser):
    info = browser.ytm_search_play("Океан Ельзи")
    print(info)
    assert info.get("playing")


def test_ytmusic_liked_requires_login(browser):
    with pytest.raises((PermissionError, RuntimeError)) as e:
        browser.ytm_liked()
    print("без входу:", repr(e.value))
