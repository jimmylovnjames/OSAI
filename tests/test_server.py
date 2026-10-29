from fastapi.testclient import TestClient

from osai.config import Config
from osai.server import create_app
from tests.fakes import FakeClient, text


def app(tmp_path, turns=()):
    return create_app(Config(tmp_path / "c.json"), client_factory=lambda: FakeClient(turns))


def test_blocks_foreign_origins(tmp_path):
    c = TestClient(app(tmp_path))
    assert c.get("/api/status").status_code == 200
    assert c.get("/api/status", headers={"origin": "https://evil.example"}).status_code == 403
    assert c.get("/api/status", headers={"host": "evil.example"}).status_code == 403


def test_config_roundtrip_masks_key(tmp_path):
    c = TestClient(app(tmp_path))
    assert c.post("/api/config", json={"api_key": "sk-ant-secret-1234", "avatar": {"form": "bot"}}).status_code == 200
    cfg = c.get("/api/config").json()["config"]
    assert cfg["api_key"] == "set:1234" and cfg["avatar"]["form"] == "bot"
    assert c.post("/api/config", json={"avatar": {"form": "dragon"}}).status_code == 400
    # echoing the masked key back must not overwrite the stored one
    c.post("/api/config", json={"api_key": "set:1234"})
    assert Config(tmp_path / "c.json")["api_key"] == "sk-ant-secret-1234"


def test_websocket_conversation(tmp_path):
    c = TestClient(app(tmp_path, [([text("Hi! How can I help?")], "end_turn")]))
    with c.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "hello"
        ws.send_json({"type": "say", "text": "hello"})
        seen = []
        for _ in range(20):
            ev = ws.receive_json()
            seen.append(ev["type"])
            if ev["type"] == "assistant_done":
                assert ev["text"] == "Hi! How can I help?"
                break
        assert "user" in seen and "assistant_delta" in seen
