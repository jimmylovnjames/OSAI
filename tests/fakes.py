"""A scripted stand-in for anthropic.AsyncAnthropic used by the agent tests."""
from __future__ import annotations

from types import SimpleNamespace


class Block(SimpleNamespace):
    def model_dump(self, exclude_none=True):
        return {k: v for k, v in vars(self).items() if v is not None or not exclude_none}


def text(t):
    return Block(type="text", text=t)


def tool_use(id, name, input):
    return Block(type="tool_use", id=id, name=name, input=input)


class FakeStream:
    def __init__(self, content, stop_reason):
        self.content, self.stop_reason = content, stop_reason

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def __aiter__(self):
        async def gen():
            for b in self.content:
                if b.type == "text":
                    yield SimpleNamespace(type="text", text=b.text)
        return gen()

    async def get_final_message(self):
        return SimpleNamespace(content=self.content, stop_reason=self.stop_reason)


class FakeClient:
    """Each call to stream() pops the next scripted (content, stop_reason) turn."""

    def __init__(self, turns, errors=None):
        self.turns = list(turns)
        self.errors = list(errors or [])
        self.requests = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))

    def _stream(self, **params):
        self.requests.append({**params, "messages": list(params["messages"])})
        if self.errors:
            raise self.errors.pop(0)
        content, stop = self.turns.pop(0)
        return FakeStream(content, stop)
