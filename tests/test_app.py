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
