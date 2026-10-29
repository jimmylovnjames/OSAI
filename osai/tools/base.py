"""Tool registry. Each tool is a plain function `fn(ctx, **input)` plus a JSON schema.

`risk` decides whether OSAI asks the user before running it:
  - None            -> always runs
  - "confirm"       -> always asks (in autonomy="ask" mode)
  - callable(input) -> returns a reason string when this particular call needs confirmation
"""
from __future__ import annotations

import asyncio
import inspect
import json
from dataclasses import dataclass, field
from typing import Any, Callable

MAX_RESULT_CHARS = 24_000


@dataclass
class Tool:
    name: str
    description: str
    properties: dict
    required: list[str]
    fn: Callable
    risk: Any = None
    category: str = "general"

    def definition(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "eager_input_streaming": True,
            "input_schema": {
                "type": "object",
                "properties": self.properties,
                "required": self.required,
            },
        }

    def confirmation_reason(self, args: dict) -> str | None:
        if self.risk is None:
            return None
        if self.risk == "confirm":
            return self.description.split(".")[0]
        return self.risk(args)

    def validate(self, args: Any) -> str | None:
        """Minimal schema check. Eager input streaming means the API no longer validates for us."""
        if not isinstance(args, dict):
            return "input must be a JSON object"
        for r in self.required:
            if r not in args:
                return f"missing required field '{r}'"
        types = {"string": str, "integer": int, "number": (int, float), "boolean": bool, "object": dict, "array": list}
        for k, v in args.items():
            spec = self.properties.get(k)
            if spec is None:
                return f"unknown field '{k}'"
            t = types.get(spec.get("type", ""))
            if t and not isinstance(v, t) or (spec.get("type") in ("integer", "number") and isinstance(v, bool)):
                return f"field '{k}' must be {spec['type']}"
            if "enum" in spec and v not in spec["enum"]:
                return f"field '{k}' must be one of {spec['enum']}"
        return None

    async def call(self, ctx: "ToolContext", args: dict) -> Any:
        if inspect.iscoroutinefunction(self.fn):
            return await self.fn(ctx, **args)
        return await asyncio.to_thread(self.fn, ctx, **args)


REGISTRY: dict[str, Tool] = {}


def tool(name: str, description: str, properties: dict | None = None, required: list[str] | None = None,
         risk: Any = None, category: str = "general"):
    def deco(fn):
        REGISTRY[name] = Tool(name, description, properties or {}, required or [], fn, risk, category)
        return fn
    return deco


@dataclass
class ToolContext:
    """What a tool can reach: the UI event bus, config, scheduler, browser, memory."""
    emit: Callable[[dict], Any]
    config: Any
    services: dict = field(default_factory=dict)

    def notify(self, event: dict) -> None:
        r = self.emit(event)
        if inspect.isawaitable(r):
            asyncio.ensure_future(r)


def normalize_result(result: Any) -> str | list:
    """Convert a tool's return value into tool_result content."""
    if isinstance(result, list) and result and isinstance(result[0], dict) and "type" in result[0]:
        return result  # already content blocks (e.g. an image)
    if not isinstance(result, str):
        result = json.dumps(result, indent=1, default=str)
    if len(result) > MAX_RESULT_CHARS:
        result = result[:MAX_RESULT_CHARS] + f"\n...[truncated {len(result) - MAX_RESULT_CHARS} chars]"
    return result or "(no output)"


class ToolError(Exception):
    """Raised by tools for an expected failure; message goes back to the model as is_error."""
