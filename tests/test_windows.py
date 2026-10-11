"""Справжні Windows API: виконується на GitHub Actions (windows-latest)."""
import os
import subprocess
import time

import pytest

from conftest import windows_only

pytestmark = windows_only


def test_clipboard_cyrillic(toolbox):
    text = "Привіт, сер! Їжак — ґанок, 2026 ✓"
    toolbox.run("clipboard", {"action": "write", "text": text})
    assert toolbox.run("clipboard", {"action": "read"}) == text


def test_autostart_registry():
    from core import autostart
    try:
        assert autostart.set_enabled(True)
        assert autostart.is_enabled()
    finally:
        autostart.set_enabled(False)
    assert not autostart.is_enabled()


def test_idle_seconds():
    from core.monitor import idle_seconds
    v = idle_seconds()
    assert isinstance(v, float) and v >= 0


def test_online():
    from core.monitor import online
    assert online()


@pytest.fixture
def notepad():
    p = subprocess.Popen(["notepad.exe"])
    time.sleep(2.5)
    yield p
    try:
        p.kill()
    except Exception:
        pass


def test_window_enum_focus_and_close(toolbox, notepad):
    from core.tools import _windows
    titles = [t for _, t in _windows()]
    print("Вікна:", titles)
    assert any("Notepad" in t or "Блокнот" in t or "Untitled" in t for t in titles)
    r = toolbox.run("window_control", {"action": "focus", "title": "Notepad"})
    print(r)
    assert "Notepad" in r or "Untitled" in r
    print(toolbox.run("window_control", {"action": "minimize"}))
    print(toolbox.run("window_control", {"action": "restore"}))


def test_apps_snapshot_sees_notepad(notepad):
    from core.apps import snapshot
    exes = [os.path.basename(e).lower() for e, _ in snapshot()]
    print("Програми:", exes)
    assert "notepad.exe" in exes


def test_close_application_protects_jarvis(toolbox):
    assert "коротка" in toolbox.run("close_application", {"name": "e"})
    p = subprocess.Popen(["notepad.exe"])
    time.sleep(1.5)
    r = toolbox.run("close_application", {"name": "notepad"})
    print(r)
    time.sleep(0.5)
    assert p.poll() is not None or "notepad" in r.lower()


def test_flac_converter_works():
    import speech_recognition as sr
    audio = sr.AudioData(b"\0\0" * 16000, 16000, 2)
    assert audio.get_flac_data()[:4] == b"fLaC"


def test_pyaudio_loads():
    import pyaudio
    pa = pyaudio.PyAudio()
    print("Аудіопристроїв:", pa.get_device_count())
    pa.terminate()


def test_volume_keys_do_not_crash(toolbox):
    assert "Гучність" in toolbox.run("volume_control", {"action": "up", "level": 2})
    toolbox.run("volume_control", {"action": "down", "level": 2})


def test_screenshot_tool(toolbox):
    r = toolbox.run("look_at_screen", {"save_only": True})
    assert "збережено" in r


@pytest.mark.network
def test_edge_tts_ukrainian(config):
    from core.voice import Voice
    v = Voice(config, None, lambda s: None, lambda a: None)
    audio = v.tts("Добрий день, сер. Усі системи в нормі.")
    assert len(audio) > 5000


@pytest.mark.network
def test_weather(toolbox):
    r = toolbox.run("get_weather", {"city": "Київ", "days": 2})
    print(r)
    assert "Київ" in r and "°C" in r


# ---------------- монітори, розташування вікон, UI Automation (v7.5)
def test_monitors_listed(toolbox):
    from core.winlayout import monitors
    ms = monitors()
    print(ms)
    assert ms and ms[0]["primary"] and ms[0]["i"] == 1
    assert "Екрани" in toolbox.run("monitors", {})


def test_place_window_left_half(toolbox, notepad):
    from core import winlayout as wl
    r = toolbox.run("place_window", {"app": "notepad", "monitor": 1, "position": "left", "launch": False})
    print(r)
    assert "екран 1" in r
    h = wl.find_windows(query="notepad")[0][0]
    p = wl.placement(h)
    l, t, rr, b = wl.monitors()[0]["work"]
    print(p, (l, t, rr, b))
    assert abs(p["rect"][0] - l) < 30 and abs(p["rect"][2] - (l + (rr - l) // 2)) < 40


def test_layout_save_and_apply(toolbox, notepad):
    from core import winlayout as wl
    toolbox.run("place_window", {"app": "notepad", "monitor": 1, "position": "right", "launch": False})
    import time
    time.sleep(0.5)
    r = toolbox.run("save_layout", {"name": "тест"})
    print(r)
    assert "Блокнот" in r or "notepad" in r.lower()
    toolbox.run("place_window", {"app": "notepad", "monitor": 1, "position": "left", "launch": False})
    r = toolbox.run("apply_layout", {"name": "тест"})
    print(r)
    h = wl.find_windows(query="notepad")[0][0]
    l, t, rr, b = wl.monitors()[0]["work"]
    assert wl.placement(h)["rect"][0] > l + (rr - l) // 3  # знову праворуч


def test_ui_automation_lists_and_clicks_notepad(toolbox, notepad):
    r = toolbox.run("app_buttons", {"app": "notepad"})
    print(r)
    assert "File" in r or "Файл" in r
    r = toolbox.run("click_in_app", {"app": "notepad", "button": "File"})
    print(r)
    assert r.startswith("Натиснуто")
    import time
    time.sleep(0.5)
    toolbox.run("press_keys", {"keys": "esc"})
