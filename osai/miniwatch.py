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
    visible: bool | None = None
    while True:
        await asyncio.sleep(interval)
        wins = await asyncio.to_thread(_windows)
        minis = [w for w, cls in wins if cls.endswith(".OSAIMini")]
        if not minis:
            # An unmapped window is invisible to wmctrl: look it up by class instead.
            code, out = await asyncio.to_thread(run, ["xdotool", "search", "--class", "OSAIMini"], 3)
            minis = out.split() if code == 0 else []
        apps = [w for w, cls in wins if not cls.endswith(OSAI_CLASSES)]
        want = bool(apps)
        if minis and want != visible:
            for m in minis:
                await asyncio.to_thread(run, ["xdotool", "windowmap" if want else "windowunmap", m], 3)
            visible = want
