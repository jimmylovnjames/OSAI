"""Long-term memory: small facts about the user that OSAI keeps across sessions."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from .config import HOME


class Memory:
    def __init__(self, path: Path | None = None):
        self.path = path or HOME / "memory.json"
        self._lock = threading.Lock()
        try:
            self.facts: list[dict] = json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError):
            self.facts = []

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.facts, indent=1))

    def add(self, fact: str) -> None:
        with self._lock:
            if not any(f["fact"].lower() == fact.lower() for f in self.facts):
                self.facts.append({"fact": fact, "at": time.strftime("%Y-%m-%d")})
                self._save()

    def remove(self, text: str) -> int:
        with self._lock:
            before = len(self.facts)
            self.facts = [f for f in self.facts if text.lower() not in f["fact"].lower()]
            self._save()
            return before - len(self.facts)

    def render(self) -> str:
        return "\n".join(f"- {f['fact']}" for f in self.facts) or "(nothing yet)"
