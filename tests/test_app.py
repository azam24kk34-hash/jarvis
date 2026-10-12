"""Димовий тест: увесь Джарвіс створюється й API для HUD відповідає (ловить помилки ініціалізації)."""
import sys
import types


def test_jarvis_constructs_and_api_works():
    try:
        import webview  # noqa: F401
    except ImportError:  # на Linux без GUI — заглушка
        sys.modules["webview"] = types.SimpleNamespace(screens=[], create_window=lambda *a, **k: None, start=lambda *a, **k: None)
    import main
    j = main.Jarvis()
    api = main.Api(j)
    st = api.stats()
    assert "cpu" in st and "usage" in st
    s = api.get_settings()
    assert s["provider"] and "api_key" in s
    assert api.protocols() and isinstance(api.phrases_list(), list)
    assert callable(j.voice.music_playing) and j.voice.music_playing() is False


def test_js_never_blocks_voice_or_brain():
    """Навіть якщо вікно «зависло», виклики інтерфейсу з голосу/мозку повертаються миттєво."""
    import threading
    import time
    try:
        import webview  # noqa: F401
    except ImportError:
        sys.modules["webview"] = types.SimpleNamespace(screens=[], create_window=lambda *a, **k: None, start=lambda *a, **k: None)
    import main
    j = main.Jarvis()
    gate = threading.Event()
    calls = []

    class FrozenWindow:
        def evaluate_js(self, code):
            calls.append(code)
            gate.wait(5)

    j.window = FrozenWindow()
    t0 = time.time()
    for i in range(50):
        j.js("HUD.setState", "listening")
        j.js("HUD.mic", i / 50)
    assert time.time() - t0 < 0.5
    time.sleep(0.2)
    assert calls and j._js_busy_since > 0  # сторож бачить, що інтерфейс зайнятий
    gate.set()
