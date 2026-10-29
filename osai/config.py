"""Persistent OSAI settings: model, autonomy, voice, avatar design, theme.

Stored as JSON in $OSAI_HOME (default ~/.osai). The file is written 0600
because it can hold the Anthropic API key.
"""
from __future__ import annotations

import copy
import json
import os
import threading
from pathlib import Path
from typing import Any

HOME = Path(os.environ.get("OSAI_HOME", Path.home() / ".osai")).expanduser()

AVATAR_FORMS = ["orb", "face", "bot", "cat", "ghost"]
AVATAR_EYES = ["round", "visor", "sleepy", "star", "dot"]
AVATAR_ACCESSORIES = ["none", "antenna", "halo", "ears", "headphones", "crown"]
WALLPAPERS = ["aurora", "nebula", "sunset", "ocean", "mono", "matrix"]

DEFAULTS: dict[str, Any] = {
    "assistant_name": "OSAI",
    "user_name": "",
    # auto: Claude when an Anthropic key is configured, otherwise the free local model
    "provider": "auto",
    "local": {
        "base_url": "http://127.0.0.1:8080/v1",  # llama-server; Ollama is http://127.0.0.1:11434/v1
        "model": "qwen3.5-4b",
        "api_key": "",
        "thinking": False,
        "vision": True,
        "context_tokens": 32768,
        "max_tokens": 4096,
        "temperature": 0.6,
    },
    "model": "claude-opus-5-5",
    "effort": "medium",
    "autonomy": "ask",  # "ask": confirm risky actions; "full": never ask
    "api_key": "",
    "server_web_tools": True,  # Anthropic-hosted web_search / web_fetch
    "voice": {
        "tts": "auto",  # auto | browser | server | off
        "stt": "auto",  # auto | browser | server
        "voice_name": "",
        "rate": 1.0,
        "pitch": 1.0,
        "hands_free": False,
        "wake_word": "osai",
        "piper_model": "",
        "whisper_model": "base.en",
    },
    "avatar": {
        "form": "orb",
        "primary": "#7c5cff",
        "accent": "#00e5ff",
        "eyes": "round",
        "accessory": "none",
        "mouth": True,
        "glow": 0.8,
        "size": 1.0,
        "personality": "warm, quick-witted, and concise",
    },
    "theme": {"wallpaper": "aurora", "accent": "#7c5cff", "clock": True},
    "browser": {"headless": None, "profile": "browser-profile"},
}


def _merge(base: dict, patch: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


class Config:
    def __init__(self, path: Path | None = None):
        self.path = path or HOME / "config.json"
        self._lock = threading.Lock()
        self.data = copy.deepcopy(DEFAULTS)
        # System-wide defaults (e.g. the local AI the image or linux/osai-local-ai set up), then the user's own.
        system_dir = Path(os.environ.get("OSAI_SYSTEM_CONFIG", "/etc/osai"))
        for f in sorted(system_dir.glob("*.json")) if system_dir.is_dir() else []:
            try:
                self.data = _merge(self.data, json.loads(f.read_text()))
            except (OSError, json.JSONDecodeError):
                pass
        if self.path.exists():
            try:
                self.data = _merge(self.data, json.loads(self.path.read_text()))
            except (OSError, json.JSONDecodeError):
                pass

    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def update(self, patch: dict) -> dict:
        with self._lock:
            self.data = _merge(self.data, patch)
            self.save()
            return self.data

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(self.data, f, indent=2)
        os.replace(tmp, self.path)

    def public(self) -> dict:
        """Config safe to send to the UI (API key masked)."""
        d = copy.deepcopy(self.data)
        key = d.get("api_key") or ""
        d["api_key"] = ("set:" + key[-4:]) if key else ""
        d["api_key_env"] = bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
        return d

    def avatar_patch_errors(self, patch: dict) -> list[str]:
        errs = []
        allowed = set(DEFAULTS["avatar"])
        for k, v in patch.items():
            if k not in allowed:
                errs.append(f"unknown avatar field '{k}' (allowed: {sorted(allowed)})")
            elif k == "form" and v not in AVATAR_FORMS:
                errs.append(f"form must be one of {AVATAR_FORMS}")
            elif k == "eyes" and v not in AVATAR_EYES:
                errs.append(f"eyes must be one of {AVATAR_EYES}")
            elif k == "accessory" and v not in AVATAR_ACCESSORIES:
                errs.append(f"accessory must be one of {AVATAR_ACCESSORIES}")
            elif k in ("primary", "accent") and not (isinstance(v, str) and v.startswith("#") and len(v) in (4, 7)):
                errs.append(f"{k} must be a hex color like #ff8800")
            elif k in ("glow", "size") and not isinstance(v, (int, float)):
                errs.append(f"{k} must be a number")
        return errs
