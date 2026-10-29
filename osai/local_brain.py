"""Free, on-device brain: any OpenAI-compatible server (llama.cpp's llama-server, Ollama, LM Studio, vLLM).

It presents the same `client.beta.messages.stream(...)` surface the agent uses for Claude, translating
Anthropic-style requests to /v1/chat/completions and the streamed reply back, so the agent loop,
tools, approvals and UI work unchanged. Recommended model: Qwen3.5-4B or -9B (tool calling + vision).
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, AsyncIterator

import httpx


class LocalBrainError(Exception):
    """The local model server is unreachable or returned an error."""


class Block(SimpleNamespace):
    """A content block shaped like the Anthropic SDK's (attribute access + model_dump)."""

    def model_dump(self, exclude_none: bool = True) -> dict:
        return {k: v for k, v in vars(self).items() if v is not None or not exclude_none}


def _get(block: Any, key: str, default: Any = None) -> Any:
    return block.get(key, default) if isinstance(block, dict) else getattr(block, key, default)


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(_get(b, "text", "") for b in content or [] if _get(b, "type") == "text")


def _image_part(block: Any) -> dict | None:
    src = _get(block, "source") or {}
    if _get(src, "type") == "base64":
        return {"type": "image_url", "image_url": {"url": f"data:{_get(src, 'media_type')};base64,{_get(src, 'data')}"}}
    if _get(src, "type") == "url":
        return {"type": "image_url", "image_url": {"url": _get(src, "url")}}
    return None


def to_openai_messages(system: Any, messages: list[dict], vision: bool = True) -> list[dict]:
    out: list[dict] = [{"role": "system", "content": _text_of(system) if not isinstance(system, str) else system}]
    for m in messages:
        role, content = m["role"], m["content"]
        if role == "assistant":
            text = "".join(_get(b, "text", "") for b in content if _get(b, "type") == "text") if not isinstance(content, str) else content
            calls = [{"id": _get(b, "id"), "type": "function",
                      "function": {"name": _get(b, "name"), "arguments": json.dumps(_get(b, "input") or {})}}
                     for b in (content if not isinstance(content, str) else []) if _get(b, "type") == "tool_use"]
            msg: dict = {"role": "assistant", "content": text or None}
            if calls:
                msg["tool_calls"] = calls
            out.append(msg)
            continue
        if role == "system":  # mid-conversation operator note
            out.append({"role": "system", "content": _text_of(content)})
            continue
        if isinstance(content, str):
            out.append({"role": "user", "content": content})
            continue
        parts: list[dict] = []
        images_from_tools: list[dict] = []
        for b in content:
            kind = _get(b, "type")
            if kind == "tool_result":
                res = _get(b, "content")
                text = _text_of(res) if not isinstance(res, str) else res
                if _get(b, "is_error"):
                    text = "ERROR: " + text
                out.append({"role": "tool", "tool_call_id": _get(b, "tool_use_id"), "content": text or "(no output)"})
                if vision and not isinstance(res, str):
                    images_from_tools += [p for p in (_image_part(x) for x in res if _get(x, "type") == "image") if p]
            elif kind == "text":
                parts.append({"type": "text", "text": _get(b, "text", "")})
            elif kind == "image" and vision:
                p = _image_part(b)
                if p:
                    parts.append(p)
        # Most servers can't put images inside tool messages: show them in a user turn right after.
        if images_from_tools:
            parts = [{"type": "text", "text": "(Screenshot from the tool above.)"}] + images_from_tools + parts
        if parts:
            if all(p["type"] == "text" for p in parts):
                out.append({"role": "user", "content": "\n".join(p["text"] for p in parts)})
            else:
                out.append({"role": "user", "content": parts})
    return out


def trim_to_budget(msgs: list[dict], max_chars: int, keep_images: int = 2) -> list[dict]:
    """Small local context windows: keep the system prompt + the newest turns, and only the latest screenshots."""
    msgs = [dict(m) for m in msgs]
    seen = 0
    for m in reversed(msgs):  # drop all but the newest few images
        if isinstance(m.get("content"), list):
            kept = []
            for p in m["content"]:
                if p["type"] == "image_url":
                    seen += 1
                    if seen > keep_images:
                        kept.append({"type": "text", "text": "(older screenshot removed)"})
                        continue
                kept.append(p)
            m["content"] = kept

    def size(m: dict) -> int:
        c = m.get("content")
        if isinstance(c, list):
            return sum(len(p.get("text", "")) + (1500 * 4 if p["type"] == "image_url" else 0) for p in c)
        return len(c or "") + len(json.dumps(m.get("tool_calls", "")))

    system, rest = msgs[:1], msgs[1:]
    while rest and sum(map(size, system + rest)) > max_chars:
        rest.pop(0)
        # never start on a tool result or an assistant turn (it would lose its call / question)
        while rest and rest[0]["role"] in ("tool", "assistant"):
            rest.pop(0)
    return system + rest


def to_openai_tools(tools: list[dict]) -> list[dict]:
    out = []
    for t in tools:
        if "input_schema" not in t:  # Anthropic server tools (web search) have no local equivalent
            continue
        out.append({"type": "function", "function": {"name": t["name"], "description": t.get("description", ""),
                                                     "parameters": t["input_schema"]}})
    return out


class _Stream:
    def __init__(self, cfg: dict, payload: dict):
        self.cfg, self.payload = cfg, payload
        self.text = ""
        self.calls: dict[int, dict] = {}
        self.finish = None
        self._client: httpx.AsyncClient | None = None
        self._resp = None

    async def __aenter__(self):
        headers = {"content-type": "application/json"}
        if self.cfg.get("api_key"):
            headers["authorization"] = f"Bearer {self.cfg['api_key']}"
        # Local servers on slow CPUs can take minutes to read a long prompt the first time.
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(600, connect=10), trust_env=False)
        url = self.cfg["base_url"].rstrip("/") + "/chat/completions"
        try:
            self._resp = await self._client.send(self._client.build_request("POST", url, json=self.payload, headers=headers),
                                                 stream=True)
        except httpx.HTTPError as e:
            await self._client.aclose()
            raise LocalBrainError(f"can't reach the local AI at {self.cfg['base_url']} ({type(e).__name__}). "
                                  "Is the local model server running?") from e
        if self._resp.status_code >= 400:
            body = (await self._resp.aread()).decode(errors="replace")[:400]
            await self.__aexit__(None, None, None)
            raise LocalBrainError(f"local AI error {self._resp.status_code}: {body}")
        return self

    async def __aexit__(self, *exc):
        if self._resp is not None:
            await self._resp.aclose()
        if self._client is not None:
            await self._client.aclose()
        return False

    def __aiter__(self) -> AsyncIterator[SimpleNamespace]:
        return self._events()

    async def _events(self):
        async for line in self._resp.aiter_lines():
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue
            if chunk.get("error"):
                raise LocalBrainError(f"local AI error: {chunk['error']}")
            for choice in chunk.get("choices", []):
                delta = choice.get("delta") or {}
                reasoning = delta.get("reasoning_content") or delta.get("reasoning")  # llama.cpp / Ollama
                if reasoning:
                    yield SimpleNamespace(type="thinking", thinking=reasoning)
                if delta.get("content"):
                    self.text += delta["content"]
                    yield SimpleNamespace(type="text", text=delta["content"])
                for tc in delta.get("tool_calls") or []:
                    slot = self.calls.setdefault(tc.get("index", len(self.calls)), {"id": None, "name": "", "args": ""})
                    slot["id"] = tc.get("id") or slot["id"]
                    fn = tc.get("function") or {}
                    slot["name"] += fn.get("name") or ""
                    slot["args"] += fn.get("arguments") or ""
                if choice.get("finish_reason"):
                    self.finish = choice["finish_reason"]

    async def get_final_message(self) -> SimpleNamespace:
        content: list[Block] = []
        if self.text.strip():
            content.append(Block(type="text", text=self.text.strip()))
        for i, c in sorted(self.calls.items()):
            try:
                args = json.loads(c["args"] or "{}")
            except json.JSONDecodeError as e:
                raise ValueError(f"tool arguments were not valid JSON: {c['args'][:200]}") from e
            content.append(Block(type="tool_use", id=c["id"] or f"call_{i}", name=c["name"], input=args))
        stop = {"tool_calls": "tool_use", "length": "max_tokens", "stop": "end_turn"}.get(self.finish or "stop", "end_turn")
        if any(b.type == "tool_use" for b in content) and stop == "end_turn":
            stop = "tool_use"  # some servers report "stop" even when they emitted tool calls
        return SimpleNamespace(content=content, stop_reason=stop)


class LocalClient:
    """Drop-in for anthropic.AsyncAnthropic as far as the agent uses it."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))

    def _stream(self, **params) -> _Stream:
        cfg = self.cfg
        msgs = to_openai_messages(params.get("system", ""), params["messages"], vision=cfg.get("vision", True))
        msgs = trim_to_budget(msgs, max_chars=int(cfg.get("context_tokens", 32768)) * 3)
        payload: dict = {
            "model": cfg.get("model", "local"),
            "messages": msgs,
            "tools": to_openai_tools(params.get("tools", [])),
            "stream": True,
            "max_tokens": min(int(params.get("max_tokens", 4096)), int(cfg.get("max_tokens", 4096))),
            "temperature": cfg.get("temperature", 0.6),
            # Qwen3.5 thinks before every answer by default; off is much faster on CPUs.
            "chat_template_kwargs": {"enable_thinking": bool(cfg.get("thinking", False))},
        }
        if not payload["tools"]:
            payload.pop("tools")
        return _Stream(cfg, payload)
