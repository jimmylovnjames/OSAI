"""Timers, reminders, and scheduled tasks (an instruction OSAI carries out later, optionally daily)."""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import uuid
from pathlib import Path
from typing import Awaitable, Callable

from .config import HOME


class Scheduler:
    def __init__(self, on_fire: Callable[[dict], Awaitable[None]], path: Path | None = None):
        self.path = path or HOME / "schedule.json"
        self.on_fire = on_fire
        try:
            self.items: list[dict] = json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError):
            self.items = []
        self._task: asyncio.Task | None = None

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.items, indent=1))

    def add(self, when: dt.datetime, label: str, kind: str = "reminder", repeat: str = "none") -> dict:
        item = {"id": uuid.uuid4().hex[:8], "when": when.isoformat(timespec="seconds"), "label": label,
                "kind": kind, "repeat": repeat}
        self.items.append(item)
        self.items.sort(key=lambda i: i["when"])
        self._save()
        return item

    def cancel(self, item_id: str) -> bool:
        before = len(self.items)
        self.items = [i for i in self.items if i["id"] != item_id and i["label"].lower() != item_id.lower()]
        self._save()
        return len(self.items) < before

    def start(self) -> None:
        if not self._task:
            self._task = asyncio.create_task(self._loop())

    async def _loop(self) -> None:
        while True:
            now = dt.datetime.now()
            due = [i for i in self.items if dt.datetime.fromisoformat(i["when"]) <= now]
            for item in due:
                self.items.remove(item)
                if item["repeat"] == "daily":
                    nxt = dt.datetime.fromisoformat(item["when"])
                    while nxt <= now:
                        nxt += dt.timedelta(days=1)
                    self.items.append({**item, "when": nxt.isoformat(timespec="seconds")})
                self._save()
                try:
                    await self.on_fire(item)
                except Exception as e:  # never let one bad item kill the loop
                    print(f"[osai] scheduled item failed: {e}")
            await asyncio.sleep(1)
