"""Швидкість: прості розмови без ШІ, «легкі» запити без інструментів, Gemini thought_signature,
перемикання на запасний мозок, якщо основний перевантажений. Фейковий OpenAI-сумісний сервер — без інтернету."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


class FakeAI:
    def __init__(self):
        self.requests = []
        self.fail_models = set()
        self.script = []  # список відповідей: ("text", "...") або ("call", name, args, extra)
        srv = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                body = json.dumps({"object": "list", "data": [{"id": "models/gemini-9-flash", "object": "model"},
                                                              {"id": "models/gemini-9-flash-lite", "object": "model"}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                srv.requests.append(req)
                if req["model"] in srv.fail_models:
                    body = b'{"error":{"message":"overloaded"}}'
                    self.send_response(503)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                step = srv.script.pop(0) if srv.script else ("text", "Гаразд, сер.")
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                if step[0] == "text":
                    delta = {"role": "assistant", "content": step[1]}
                    finish = "stop"
                else:
                    tc = {"index": 0, "id": "call_1", "type": "function",
                          "function": {"name": step[1], "arguments": json.dumps(step[2])}}
                    if step[3]:
                        tc["extra_content"] = step[3]
                    delta = {"role": "assistant", "tool_calls": [tc]}
                    finish = "tool_calls"
                for d, f in ((delta, None), ({}, finish)):
                    chunk = {"id": "x", "object": "chat.completion.chunk", "created": 0, "model": req["model"],
                             "choices": [{"index": 0, "delta": d, "finish_reason": f}]}
                    self.wfile.write(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode())
                self.wfile.write(b"data: [DONE]\n\n")

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/v1"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()


@pytest.fixture
def fake(monkeypatch):
    from core import brain_oai
    f = FakeAI()
    for p in ("gemini", "groq"):
        monkeypatch.setitem(brain_oai.PROVIDERS, p, dict(brain_oai.PROVIDERS[p], base=f.url))
    yield f
    f.httpd.shutdown()


def make(config, toolbox, provider="gemini", model="gemini-9-flash"):
    from core.brain_oai import OpenAIBrain
    config.data.update({"provider": provider, "gemini_key": "g-test", "gemini_model": model})
    return OpenAIBrain(config, toolbox)


def test_light_request_has_no_heavy_tools(fake, config, toolbox):
    b = make(config, toolbox)
    assert b.ask("в чому сенс життя") == "Гаразд, сер."
    req = fake.requests[-1]
    assert [t["function"]["name"] for t in req["tools"]] == ["web_search"]
    assert len(req["messages"][0]["content"]) < 2000
    assert req["reasoning_effort"] == "minimal"


def test_action_request_gets_tools_and_signature_roundtrip(fake, config, toolbox):
    b = make(config, toolbox)
    sig = {"google": {"thought_signature": "SIG123"}}
    fake.script = [("call", "list_reminders", {}, sig), ("text", "Нагадувань немає, сер.")]
    out = b.ask("покажи мої нагадування")
    assert "Нагадувань немає" in out
    first, second = fake.requests[-2], fake.requests[-1]
    assert len(first["tools"]) > 10
    calls = [m for m in second["messages"] if m.get("tool_calls")]
    assert calls and calls[0]["tool_calls"][0]["extra_content"] == sig


def test_fallback_to_lite_model_when_overloaded(fake, config, toolbox):
    b = make(config, toolbox)
    fake.fail_models = {"gemini-9-flash"}
    assert b.ask("розкажи щось цікаве") == "Гаразд, сер."
    assert fake.requests[-1]["model"] == "gemini-9-flash-lite"


def test_fallback_to_other_provider_without_gemini_fields(fake, config, toolbox):
    b = make(config, toolbox)
    config.data.update({"groq_key": "q-test", "groq_model": "groq-model"})
    fake.script = [("call", "list_reminders", {}, {"google": {"thought_signature": "S"}}), ("text", "Немає, сер.")]
    b.ask("покажи нагадування")  # в історії з'явився виклик із підписом Gemini
    fake.fail_models = {"gemini-9-flash", "gemini-9-flash-lite"}
    assert b.ask("відкрий мені щось") == "Гаразд, сер."
    assert fake.requests[-1]["model"] == "groq-model"
    for m in fake.requests[-1]["messages"]:
        for tc in m.get("tool_calls") or []:
            assert "extra_content" not in tc


def test_local_talk_answers_instantly(config, toolbox):
    from core.quick import QuickCommands
    q = QuickCommands(config, toolbox, None)
    for phrase in ["Джарвіс, привіт", "як справи", "дякую", "стан системи", "скільки оперативки",
                   "що відкрито", "хто ти", "розкажи жарт", "заряд батареї"]:
        r = q.handle(phrase)
        assert r and r[0], phrase
    assert q.handle("хто виграв учорашній матч") is None
