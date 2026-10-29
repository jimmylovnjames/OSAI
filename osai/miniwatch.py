"""Linux/X11: show the small always-on-top avatar only while an app covers the OSAI screen."""
from __future__ import annotations

import asyncio

from .osinfo import IS_LINUX, has_display, run, which

OSAI_CLASSES = ("OSAIShell", "OSAIMini")


def _windows() -> list[tuple[str, str]]:
    code, out = run(["wmctrl", "-lx"], timeout=3)
    if code != 0:
        return []
    rows = []
    for line in out.splitlines():
        parts = line.split(None, 4)
        if len(parts) >= 3:
            rows.append((parts[0], parts[2]))
    return rows


async def watch(interval: float = 1.0) -> None:
    if not (IS_LINUX and has_display() and which("wmctrl") and which("xdotool")):
        return
    applied: dict[str, bool] = {}  # mini window id -> shown?
    while True:
        await asyncio.sleep(interval)
        wins = await asyncio.to_thread(_windows)
        # Unmapped windows are invisible to wmctrl, so look minis up by class (includes hidden ones).
        code, out = await asyncio.to_thread(run, ["xdotool", "search", "--class", "OSAIMini"], 3)
        minis = set(out.split()) if code == 0 else set()
        minis |= {w for w, cls in wins if cls.endswith(".OSAIMini")}
        want = any(not cls.endswith(OSAI_CLASSES) for _, cls in wins)
        for m in minis:
            # New windows (e.g. a relaunched mini screen) get the current state too, not just changes.
            if applied.get(_norm(m)) != want:
                await asyncio.to_thread(run, ["xdotool", "windowmap" if want else "windowunmap", m], 3)
                applied[_norm(m)] = want
        for gone in set(applied) - {_norm(m) for m in minis}:
            applied.pop(gone)


def _norm(wid: str) -> int:
    """wmctrl prints hex ids, xdotool prints decimal ones."""
    return int(wid, 16) if wid.lower().startswith("0x") else int(wid)
