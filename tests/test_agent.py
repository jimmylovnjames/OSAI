import asyncio

import anthropic
import httpx

from osai.agent import Agent
from osai.config import Config
from osai.memory import Memory
from tests.fakes import FakeClient, text, tool_use


def make(tmp_path, turns, errors=None, autonomy="ask"):
    events = []
    cfg = Config(tmp_path / "config.json")
    cfg.update({"autonomy": autonomy})
    client = FakeClient(turns, errors)
    agent = Agent(cfg, Memory(tmp_path / "mem.json"), events.append, {}, client_factory=lambda: client)
    return agent, client, events


async def test_plain_reply(tmp_path):
    agent, client, events = make(tmp_path, [([text("Hello there.")], "end_turn")])
    await agent.handle("hi")
    assert [e["text"] for e in events if e["type"] == "assistant_done"] == ["Hello there."]
    req = client.requests[0]
    assert req["model"] == "claude-opus-5-5"
    assert req["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert req["fallbacks"] == "default" and "server-side-fallback-2026-07-01" in req["betas"]
    assert req["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert any(t.get("name") == "web_search" for t in req["tools"])
    assert all(t.get("eager_input_streaming") for t in req["tools"] if "input_schema" in t)


async def test_tool_loop_and_avatar_change(tmp_path):
    turns = [
        ([text("On it."), tool_use("t1", "customize_avatar", {"form": "cat", "primary": "#aa00ff"})], "tool_use"),
        ([text("Meow - done.")], "end_turn"),
    ]
    agent, client, events = make(tmp_path, turns)
    await agent.handle("make yourself a purple cat")
    assert agent.config["avatar"]["form"] == "cat"
    assert any(e["type"] == "config" for e in events)
    results = client.requests[1]["messages"][-1]["content"]
    assert results[0]["type"] == "tool_result" and not results[0].get("is_error")
    # history is append-only: user, assistant(tool_use), user(tool_result), assistant
    assert [m["role"] for m in agent.messages] == ["user", "assistant", "user", "assistant"]


async def test_risky_command_needs_approval_and_can_be_denied(tmp_path):
    turns = [
        ([tool_use("t1", "run_command", {"command": "rm -rf ~/Documents/old"})], "tool_use"),
        ([text("Okay, I left it alone.")], "end_turn"),
    ]
    agent, client, events = make(tmp_path, turns)

    async def deny_when_asked():
        while not agent.pending_approval():
            await asyncio.sleep(0.01)
        agent.resolve_approval(agent.pending_approval(), False)

    await asyncio.gather(agent.handle("clean up old docs"), deny_when_asked())
    req = [e for e in events if e["type"] == "approval_request"][0]
    assert "deletes files" in req["reason"]
    result = client.requests[1]["messages"][-1]["content"][0]
    assert result["is_error"] and "declined" in result["content"]


async def test_full_autonomy_skips_approval(tmp_path):
    turns = [([tool_use("t1", "run_command", {"command": "kill -0 1 || true"})], "tool_use"), ([text("done")], "end_turn")]
    agent, client, events = make(tmp_path, turns, autonomy="full")
    await asyncio.wait_for(agent.handle("x"), 10)
    assert not any(e["type"] == "approval_request" for e in events)


async def test_invalid_tool_input_returns_error_to_model(tmp_path):
    turns = [([tool_use("t1", "set_volume", {"level": "loud"})], "tool_use"), ([text("fixed")], "end_turn")]
    agent, client, events = make(tmp_path, turns)
    await agent.handle("volume up")
    result = client.requests[1]["messages"][-1]["content"][0]
    assert result["is_error"] and "INVALID_INPUT" in result["content"]


async def test_rejected_optional_feature_is_dropped_and_retried(tmp_path):
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    err = anthropic.BadRequestError("fallbacks: not supported", response=httpx.Response(400, request=req), body=None)
    agent, client, events = make(tmp_path, [([text("ok")], "end_turn")], errors=[err])
    await agent.handle("hi")
    assert "fallbacks" not in client.requests[1]
    assert [e["text"] for e in events if e["type"] == "assistant_done"] == ["ok"]


async def test_error_then_next_message_keeps_valid_alternation(tmp_path):
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    err = anthropic.APIConnectionError(request=req)
    agent, client, events = make(tmp_path, [([text("back")], "end_turn")], errors=[err])
    await agent.handle("first")
    assert any(e["type"] == "error" for e in events)
    await agent.handle("second")
    roles = [m["role"] for m in client.requests[-1]["messages"]]
    assert roles == ["user"]  # both texts merged into one user turn
    assert len(client.requests[-1]["messages"][0]["content"]) == 2
