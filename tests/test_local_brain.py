"""The free local brain against a real HTTP server speaking OpenAI-style streaming (as llama-server does)."""
import asyncio
import json
import socket
import threading
import time

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

from osai.agent import Agent
from osai.config import Config
from osai.local_brain import to_openai_messages, trim_to_budget
from osai.memory import Memory


def _sse(chunks):
    for c in chunks:
        yield f"data: {json.dumps(c)}\n\n"
    yield "data: [DONE]\n\n"


def _server():
    app = FastAPI()
    app.state.requests = []

    @app.post("/v1/chat/completions")
    async def chat(req: Request):
        body = await req.json()
        app.state.requests.append(body)
        if len(app.state.requests) == 1:  # first turn: a tool call whose JSON arrives in pieces
            chunks = [
                {"choices": [{"index": 0, "delta": {"content": "Turning it up."}}]},
                {"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "id": "call_1", "type": "function",
                                                                    "function": {"name": "customize_avatar", "arguments": ""}}]}}]},
                {"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "function": {"arguments": '{"form": "c'}}]}}]},
                {"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "function": {"arguments": 'at"}'}}]}}]},
                {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
            ]
        else:
            chunks = [{"choices": [{"index": 0, "delta": {"content": "Meow, done."}}]},
                      {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}]
        return StreamingResponse(_sse(chunks), media_type="text/event-stream")

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    return app, server, port


async def test_local_brain_runs_tools_end_to_end(tmp_path):
    app, server, port = _server()
    try:
        events = []
        cfg = Config(tmp_path / "c.json")
        cfg.update({"provider": "local", "local": {"base_url": f"http://127.0.0.1:{port}/v1", "model": "qwen3.5-4b"}})
        agent = Agent(cfg, Memory(tmp_path / "m.json"), events.append, {})
        await asyncio.wait_for(agent.handle("be a cat"), 20)

        assert cfg["avatar"]["form"] == "cat"  # the tool really ran
        assert [e["text"] for e in events if e["type"] == "assistant_done"][-1] == "Meow, done."
        first, second = app.state.requests
        assert first["model"] == "qwen3.5-4b" and first["stream"] is True
        assert first["chat_template_kwargs"] == {"enable_thinking": False}
        names = {t["function"]["name"] for t in first["tools"]}
        assert "customize_avatar" in names and "web_search" not in names  # Claude-only server tools dropped
        assert first["messages"][0]["role"] == "system" and "browser_open" in first["messages"][0]["content"]
        # second request carries the assistant tool call and the tool result in OpenAI shape
        roles = [m["role"] for m in second["messages"]]
        assert roles[-2:] == ["assistant", "tool"]
        assert second["messages"][-2]["tool_calls"][0]["function"] == {"name": "customize_avatar", "arguments": '{"form": "cat"}'}
        assert second["messages"][-1]["tool_call_id"] == "call_1"
    finally:
        server.should_exit = True


async def test_unreachable_local_server_reports_clearly(tmp_path):
    events = []
    cfg = Config(tmp_path / "c.json")
    cfg.update({"provider": "local", "local": {"base_url": "http://127.0.0.1:9/v1"}})
    agent = Agent(cfg, Memory(tmp_path / "m.json"), events.append, {})
    await asyncio.wait_for(agent.handle("hi"), 20)
    err = [e for e in events if e["type"] == "error"]
    assert err and err[0]["code"] == "local" and "can't reach the local AI" in err[0]["text"]


def test_screenshots_move_out_of_tool_messages_and_old_ones_are_dropped():
    img = {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "AAAA"}}
    history = []
    for i in range(4):
        history += [
            {"role": "assistant", "content": [{"type": "tool_use", "id": f"t{i}", "name": "see_screen", "input": {}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": f"t{i}", "content": [img, {"type": "text", "text": "shot"}]}]},
        ]
    msgs = to_openai_messages("sys", [{"role": "user", "content": "look"}] + history)
    assert msgs[3]["role"] == "tool" and msgs[4]["role"] == "user"  # image follows the tool message
    trimmed = trim_to_budget(msgs, max_chars=10**9, keep_images=2)
    n_images = sum(1 for m in trimmed if isinstance(m["content"], list) for p in m["content"] if p["type"] == "image_url")
    assert n_images == 2


def test_trimming_never_starts_on_a_tool_result():
    msgs = [{"role": "system", "content": "s"}] + [
        {"role": "user", "content": "x" * 1000}, {"role": "assistant", "content": None, "tool_calls": [{"id": "a"}]},
        {"role": "tool", "tool_call_id": "a", "content": "y" * 1000}, {"role": "user", "content": "latest"}]
    out = trim_to_budget(msgs, max_chars=1500)
    assert out[0]["role"] == "system" and out[1]["role"] == "user" and out[-1]["content"] == "latest"
