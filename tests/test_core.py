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
