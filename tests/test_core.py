"""Логіка, що не залежить від ОС."""
import time
import types

import numpy as np


def test_quick_commands(toolbox, config):
    from core.protocols import Protocols
    from core.quick import QuickCommands
    calls = []
    toolbox.run = lambda n, a: (calls.append((n, a)), "ok")[1]
    q = QuickCommands(config, toolbox, Protocols())
    assert q.handle("Джарвіс, гучність 40%")[0].startswith("Гучність 40")
    assert q.handle("котра година?")[0].startswith("Зараз")
    assert q.handle("протокол вечірку")[1] == "run_protocol"
    assert q.handle("яка погода завтра") is None
    assert q.handle("увімкни телеграм") is None
    assert ("volume_control", {"action": "set", "level": 40}) in calls


def test_protocol_matching():
    from core.protocols import Protocols
    p = Protocols()
    for name, key in [("вечірку", "вечірка"), ("Протокол «Тиша»", "тиша"), ("роботи", "робота"), ("мою музику", "моя музика")]:
        assert p.find(name)[0] == key
    assert p.find("xyz")[0] is None
    assert "не можна" in p.create("зло", "", [{"tool": "find_files", "args": {}}])


def test_memory_roundtrip():
    from core.memory import Memory
    m = Memory()
    m.add("Тата звати Олег")
    assert "вже" in m.add("тата звати олег")
    assert "Олег" in m.prompt_block()
    m.forget("олег")
    assert not m.facts


def test_usage_cost():
    from core.usage import Usage
    u = Usage()
    usage = types.SimpleNamespace(input_tokens=1000, output_tokens=500, cache_creation_input_tokens=0,
                                  cache_read_input_tokens=10000, server_tool_use=None)
    cost = u.add("claude-sonnet-5-5", usage)
    assert abs(cost - (1000 * 2 + 500 * 10 + 10000 * 0.10) / 1e6) < 1e-9
    assert u.today()["requests"] == 1


def test_speech_stream_sentences(config):
    from core.voice import Voice
    out = []
    v = Voice(config, None, lambda s: None, lambda a: time.sleep(0.01))
    v.tts = lambda t, cache=False: (out.append(t), b"x")[1]
    s = v.stream()
    for w in "Готово, сер. Температура 3.5 градуса! Далі довгий текст про погоду… Кінець".split(" "):
        s.feed(w + " ")
    s.end()
    s.wait(5)
    joined = " ".join(out)
    assert "3.5" in joined and joined.endswith("Кінець")
    assert len(out) >= 2


def test_wakeword_silence():
    from core.config import resource_path
    from core.wakeword import WakeWordDetector
    d = WakeWordDetector(resource_path("assets/wakeword"))
    score = d.process(np.zeros(16000 * 2, dtype=np.int16).tobytes())
    assert 0 <= score < 0.2


def test_brain_streaming_with_tools(config, toolbox):
    """Фальшивий Claude: спершу викликає інструмент, потім відповідає; перевіряємо стрімінг і кеш."""
    from anthropic.types import Message, TextBlock, ToolUseBlock, Usage
    from core import brain as B

    def ev(t, **k):
        return types.SimpleNamespace(type=t, **k)

    state = {"n": 0, "kw": []}

    class Stream:
        def __enter__(self):
            state["n"] += 1
            return self

        def __exit__(self, *a):
            pass

        def __iter__(self):
            yield ev("text", text="Секунду. " if state["n"] == 1 else "Готово, сер.")

        def get_final_message(self):
            u = Usage(input_tokens=1, output_tokens=1)
            if state["n"] == 1:
                return Message(id="1", type="message", role="assistant", model="x", stop_reason="tool_use", usage=u,
                               content=[TextBlock(type="text", text="Секунду."),
                                        ToolUseBlock(type="tool_use", id="t", name="list_protocols", input={})])
            return Message(id="2", type="message", role="assistant", model="x", stop_reason="end_turn", usage=u,
                           content=[TextBlock(type="text", text="Готово, сер.")])

    class Client:
        def __init__(self, **k):
            self.messages = types.SimpleNamespace(stream=lambda **kw: (state["kw"].append(kw), Stream())[1])

    B.anthropic.Anthropic = Client
    got = []
    br = B.Brain(config, toolbox)
    ans = br.ask("які є протоколи?", on_text=got.append)
    assert "Готово" in ans and "Секунду. " in got
    kw = state["kw"][0]
    assert kw["extra_body"]["cache_control"]["type"] == "ephemeral"
    assert kw["system"][0]["cache_control"]["type"] == "ephemeral"
    roles = [m["role"] for m in br.history]
    assert roles == ["user", "assistant", "user", "assistant"]


def test_calendar_parsing(config, toolbox, monkeypatch):
    import datetime as dt
    from core import calendar_ics
    d = dt.date.today().strftime("%Y%m%d")
    ics = ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:t\r\nBEGIN:VEVENT\r\nUID:1\r\nDTSTART;VALUE=DATE:" + d +
           "\r\nSUMMARY:Тест подія\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n").encode()
    monkeypatch.setattr(calendar_ics, "_fetch", lambda url: ics)
    config.data["calendar_ics"] = "https://example.com/basic.ics"
    assert "Тест подія" in toolbox.run("get_calendar", {})


def test_updater_check(monkeypatch):
    from core import updater

    class R:
        status_code = 200

        def json(self):
            return {"tag_name": "v9.1.0", "assets": [{"name": "Jarvis-Setup.exe", "browser_download_url": "https://x/s.exe"}]}

    monkeypatch.setattr(updater, "build_info", lambda: {"repo": "me/jarvis"})
    monkeypatch.setattr(updater.requests, "get", lambda *a, **k: R())
    assert updater.check("7.0.0") == ("9.1.0", "https://x/s.exe")
    assert updater.check("9.1.0") == (None, None)
    monkeypatch.setattr(updater, "build_info", lambda: {})
    assert updater.check("1.0.0") == (None, None)


def test_openai_compatible_brain_tools_and_stream(config, toolbox, monkeypatch):
    """Безкоштовний провайдер (Gemini/Groq/Ollama) через OpenAI-сумісний API: стрімінг + виклик інструмента."""
    import types as T
    import openai
    from core.brain import make_brain
    from core.brain_oai import OpenAIBrain

    def chunk(content=None, tool=None):
        delta = T.SimpleNamespace(content=content, tool_calls=[tool] if tool else None)
        return T.SimpleNamespace(choices=[T.SimpleNamespace(delta=delta)], usage=None)

    calls = {"n": 0, "msgs": []}

    def create(**kw):
        calls["n"] += 1
        calls["msgs"].append(kw["messages"])
        assert kw["tools"][0]["type"] == "function" and kw["stream"]
        if calls["n"] == 1:
            fn = T.SimpleNamespace(name="list_protocols", arguments="{}")
            return [chunk("Хвилинку. "), chunk(tool=T.SimpleNamespace(index=0, id="c1", function=fn))]
        return [chunk("Протоколи: "), chunk("вечірка, тиша.")]

    class FakeClient:
        def __init__(self, **k):
            self.chat = T.SimpleNamespace(completions=T.SimpleNamespace(create=create))

    monkeypatch.setattr(openai, "OpenAI", FakeClient)
    config.data.update(provider="gemini", gemini_key="AIza-test")
    br = make_brain(config, toolbox)
    assert isinstance(br, OpenAIBrain) and br.ready()
    got = []
    ans = br.ask("які протоколи?", on_text=got.append)
    assert "вечірка" in ans and "Хвилинку. " in got
    roles = [m["role"] for m in br.history]
    assert roles == ["user", "assistant", "tool", "assistant"]
    assert calls["msgs"][1][0]["role"] == "system"
    config.data.update(provider="ollama")
    assert make_brain(config, toolbox).ready()  # Ollama — без ключа


def test_web_search_parsing(toolbox, monkeypatch):
    from core import tools as T
    html = ('<a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa&rut=x">Курс <b>долара</b></a>'
            '<a class="result__snippet" href="x">Сьогодні 41,2 грн</a>')
    monkeypatch.setattr(T.requests, "post", lambda *a, **k: type("R", (), {"text": html})())
    r = toolbox.run("web_search", {"query": "курс долара"})
    assert "Курс долара" in r and "https://example.com/a" in r and "41,2" in r


def test_obs_actions_with_fake_websocket(config, toolbox, monkeypatch):
    import sys
    import types as T
    log = []

    class Fake:
        def __init__(self, **kw):
            assert kw["port"] == 4455
            if kw.get("password") != "secret":
                raise Exception("authentication failed")

        def get_version(self): return T.SimpleNamespace(obs_version="31")
        def get_stream_status(self): return T.SimpleNamespace(output_active=False, output_timecode="00:00:00.000")
        def get_record_status(self): return T.SimpleNamespace(output_active=False, output_timecode="00:00:00.000")
        def start_stream(self): log.append("stream")
        def get_scene_list(self): return T.SimpleNamespace(scenes=[{"sceneName": "Гра"}, {"sceneName": "Чат"}])
        def set_current_program_scene(self, n): log.append(("scene", n))
        def get_input_list(self): return T.SimpleNamespace(inputs=[{"inputName": "Mic/Aux"}])
        def set_input_mute(self, n, m): log.append(("mute", n, m))

    monkeypatch.setitem(sys.modules, "obsws_python", T.SimpleNamespace(ReqClient=Fake))
    config.data["obs_password"] = "wrong"
    assert "пароль" in toolbox.run("obs", {"action": "start_stream"})
    del toolbox._obs
    config.data["obs_password"] = "secret"
    assert toolbox.run("obs", {"action": "start_stream"}) == "Трансляцію запущено"
    assert toolbox.run("obs", {"action": "scene", "name": "гру"}) == "Сцена: Гра"
    assert "Mic/Aux" in toolbox.run("obs", {"action": "mute", "name": "мікрофон"})
    assert log == ["stream", ("scene", "Гра"), ("mute", "Mic/Aux", True)]


def test_custom_phrases(config, toolbox):
    from core.protocols import Protocols
    from core.quick import QuickCommands
    q = QuickCommands(config, toolbox, Protocols())
    r = q.handle("Джарвіс, запам'ятай фразу: коли я кажу «хто у нас дракон», відповідай «Звісно ж ви, сер»")
    assert r and "Запам'ятав" in r[0]
    assert q.handle("Джарвіс, скажи, хто у нас дракон?")[0] == "Звісно ж ви, сер"
    assert q.handle("хто у нас кіт") is None
    assert "Забув" in q.handle("забудь фразу хто у нас дракон")[0]
    assert q.handle("хто у нас дракон") is None
