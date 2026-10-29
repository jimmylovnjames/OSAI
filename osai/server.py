"""OSAI's local server: serves the shell UI and connects it to the agent over a WebSocket.

Binds to 127.0.0.1 by default. Anyone who can reach this port controls the computer,
so only expose it (OSAI_HOST=0.0.0.0) on a network you fully trust, with OSAI_TOKEN set.
"""
from __future__ import annotations

import asyncio
import json
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import miniwatch, voice
from .agent import Agent
from .config import Config
from .memory import Memory
from .scheduler import Scheduler
from .tools.system import status_snapshot

WEB = Path(__file__).parent / "web"


class Hub:
    """Fan-out of agent events to every connected screen."""

    def __init__(self):
        self.clients: set[WebSocket] = set()
        self.mini: set[WebSocket] = set()  # small always-on-top avatar windows
        self.recent: list[dict] = []  # replay buffer so a reconnecting screen catches up

    async def emit(self, event: dict) -> None:
        if event.get("type") in ("user", "assistant_done", "card", "error", "notify", "approval_request", "approval_closed"):
            self.recent = (self.recent + [event])[-40:]
        data = json.dumps(event, default=str)
        dead = []
        for ws in list(self.clients):
            try:
                await ws.send_text(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)
            self.mini.discard(ws)

    async def send_one(self, event: dict) -> bool:
        """Send to a single screen, preferring the always-visible mini avatar (used for the listen hotkey)."""
        for ws in list(self.mini) + [c for c in self.clients if c not in self.mini]:
            try:
                await ws.send_text(json.dumps(event))
                return True
            except Exception:
                self.clients.discard(ws)
                self.mini.discard(ws)
        return False


def create_app(config: Config | None = None, client_factory=None) -> FastAPI:
    config = config or Config()
    hub = Hub()
    memory = Memory()
    services: dict = {}
    agent = Agent(config, memory, hub.emit, services, client_factory=client_factory)
    token = os.environ.get("OSAI_TOKEN", "")

    async def on_fire(item: dict) -> None:
        if item["kind"] == "task":
            await hub.emit({"type": "notify", "text": f"Scheduled: {item['label']}"})
            asyncio.create_task(agent.handle(item["label"], source="scheduled"))
        else:
            await hub.emit({"type": "notify", "text": item["label"], "alarm": True})
            await hub.emit({"type": "speak", "text": f"Reminder: {item['label']}"})
        await hub.emit({"type": "schedule", "items": scheduler.items})

    scheduler = Scheduler(on_fire)
    services["scheduler"] = scheduler

    @asynccontextmanager
    async def lifespan(app):
        scheduler.start()
        watcher = asyncio.create_task(miniwatch.watch())
        yield
        watcher.cancel()
        b = services.get("browser")
        if b:
            await b.close()

    app = FastAPI(title="OSAI", lifespan=lifespan)
    app.state.agent, app.state.hub, app.state.config = agent, hub, config

    def origin_ok(headers) -> bool:
        """Block other websites (and DNS-rebinding) from driving this computer through the local port.

        WebSockets ignore CORS, so without this any page open in any browser could connect to /ws.
        """
        host = (headers.get("host") or "").rsplit(":", 1)[0].strip("[]")
        allowed_hosts = {"127.0.0.1", "localhost", "::1"} | set(filter(None, os.environ.get("OSAI_ALLOWED_HOSTS", "").split(",")))
        if host not in allowed_hosts:
            return False
        origin = headers.get("origin")
        if origin is None:
            return True  # non-browser client (CLI, curl)
        o = origin.split("://", 1)[-1].rsplit(":", 1)[0].strip("[]")
        return o in allowed_hosts

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if not origin_ok(request.headers):
            return JSONResponse({"error": "forbidden origin"}, status_code=403)
        return await call_next(request)

    def check(request_token: str | None) -> None:
        if token and not secrets.compare_digest(request_token or "", token):
            raise HTTPException(401, "bad token")

    @app.get("/")
    async def index():
        return FileResponse(WEB / "index.html", headers={"Cache-Control": "no-cache"})

    app.mount("/static", StaticFiles(directory=WEB), name="static")

    @app.get("/api/config")
    async def get_config(request: Request):
        check(request.headers.get("x-osai-token"))
        return {"config": config.public(), "voice": {"server_stt": voice.stt_available(),
                                                     "server_tts": voice.tts_backend(config["voice"].get("piper_model", ""))}}

    @app.post("/api/config")
    async def set_config(request: Request):
        check(request.headers.get("x-osai-token"))
        patch = await request.json()
        if not isinstance(patch, dict):
            raise HTTPException(400, "expected an object")
        if "api_key" in patch and (not patch["api_key"] or str(patch["api_key"]).startswith("set:")):
            patch.pop("api_key")  # masked value echoed back; keep the stored key
        if "avatar" in patch:
            errs = config.avatar_patch_errors(patch["avatar"])
            if errs:
                raise HTTPException(400, "; ".join(errs))
        config.update(patch)
        await hub.emit({"type": "config", "config": config.public()})
        return {"ok": True}

    @app.get("/api/status")
    async def status():
        return status_snapshot() | {"busy": agent.busy, "schedule": scheduler.items}

    @app.post("/api/listen")
    async def listen(request: Request):
        """Global hotkey target: start listening on one screen (bind e.g. Super+Space to `curl -X POST .../api/listen`)."""
        check(request.headers.get("x-osai-token"))
        return {"ok": await hub.send_one({"type": "listen"})}

    @app.post("/api/stt")
    async def stt(request: Request):
        check(request.headers.get("x-osai-token"))
        if not voice.stt_available():
            raise HTTPException(501, "server speech recognition not installed (pip install faster-whisper)")
        audio = await request.body()
        ctype = request.headers.get("content-type", "")
        suffix = ".wav" if "wav" in ctype else ".ogg" if "ogg" in ctype else ".webm"
        text = await asyncio.to_thread(voice.transcribe, audio, config["voice"].get("whisper_model", "base.en"), suffix)
        return {"text": text}

    @app.post("/api/tts")
    async def tts(request: Request):
        check(request.headers.get("x-osai-token"))
        body = await request.json()
        v = config["voice"]
        try:
            wav = await asyncio.to_thread(voice.synthesize, str(body.get("text", ""))[:4000], v.get("piper_model", ""), float(v.get("rate", 1.0)))
        except RuntimeError as e:
            raise HTTPException(501, str(e))
        return Response(wav, media_type="audio/wav")

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket):
        if not origin_ok(ws.headers):
            await ws.close(code=4403)
            return
        if token and not secrets.compare_digest(ws.query_params.get("token", ""), token):
            await ws.close(code=4401)
            return
        await ws.accept()
        hub.clients.add(ws)
        if ws.query_params.get("mini") == "1":
            hub.mini.add(ws)
        await ws.send_text(json.dumps({"type": "hello", "config": config.public(), "busy": agent.busy,
                                       "recent": hub.recent, "schedule": scheduler.items}, default=str))
        try:
            while True:
                msg = json.loads(await ws.receive_text())
                kind = msg.get("type")
                if kind == "say":
                    text = str(msg.get("text", "")).strip()
                    if not text:
                        continue
                    pending = agent.pending_approval()
                    if pending and text.lower().strip(" .!") in ("yes", "yeah", "yep", "do it", "go ahead", "ok", "okay", "sure", "confirm", "approve"):
                        agent.resolve_approval(pending, True)
                    elif pending and text.lower().strip(" .!") in ("no", "nope", "don't", "stop", "cancel", "deny"):
                        agent.resolve_approval(pending, False)
                    elif agent.busy:
                        if text.lower().strip(" .!") in ("stop", "cancel", "never mind", "nevermind"):
                            agent.cancel()
                        else:
                            await ws.send_text(json.dumps({"type": "notice", "text": "Still working on the last request - say 'stop' to cancel it."}))
                    else:
                        asyncio.create_task(agent.handle(text, source=msg.get("source", "typed")))
                elif kind == "approve":
                    agent.resolve_approval(str(msg.get("id")), bool(msg.get("allow")))
                elif kind == "cancel":
                    agent.cancel()
                elif kind == "reset":
                    if not agent.busy:
                        agent.reset()
                        hub.recent = []
                        await hub.emit({"type": "reset"})
                elif kind == "ping":
                    await ws.send_text('{"type":"pong"}')
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            hub.clients.discard(ws)
            hub.mini.discard(ws)

    @app.exception_handler(Exception)
    async def on_error(request, exc):
        return JSONResponse({"error": str(exc)}, status_code=500)

    return app
