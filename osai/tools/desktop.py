"""See and drive the desktop like a person: screenshots, mouse, keyboard.

This is how OSAI operates apps that have no command-line or API surface.
"""
from __future__ import annotations

import io
import os
import tempfile
import time

from ..osinfo import IS_LINUX, IS_MAC, IS_WINDOWS, run, which
from .base import ToolError, tool
from .files import image_block

MAX_W = 1366  # screenshots are scaled to this width; coordinates are reported in the scaled space


def _grab_png() -> bytes:
    try:
        import mss  # type: ignore
        import mss.tools  # type: ignore
        with mss.mss() as s:
            shot = s.grab(s.monitors[0])
            return mss.tools.to_png(shot.rgb, shot.size)
    except Exception:
        pass
    try:
        from PIL import ImageGrab
        im = ImageGrab.grab(all_screens=True)
        buf = io.BytesIO()
        im.save(buf, "PNG")
        return buf.getvalue()
    except Exception:
        pass
    path = os.path.join(tempfile.gettempdir(), f"osai-shot-{os.getpid()}.png")
    for argv in (["grim", path], ["scrot", "-o", path], ["gnome-screenshot", "-f", path],
                 ["spectacle", "-b", "-n", "-o", path], ["import", "-window", "root", path], ["screencapture", "-x", path]):
        if which(argv[0]):
            code, _ = run(argv, timeout=15)
            if code == 0 and os.path.exists(path):
                data = open(path, "rb").read()
                os.unlink(path)
                return data
    raise ToolError("cannot capture the screen (install mss, or grim/scrot)")


def _screen_size() -> tuple[int, int]:
    from PIL import Image
    return Image.open(io.BytesIO(_grab_png())).size


_scale = {"factor": 1.0}


@tool(
    "see_screen",
    "Take a screenshot of the whole desktop and look at it. Coordinates you later pass to mouse tools are in "
    "this screenshot's pixel space.",
    category="desktop",
)
def see_screen(ctx):
    from PIL import Image
    data = _grab_png()
    im = Image.open(io.BytesIO(data))
    w, h = im.size
    factor = 1.0
    if w > MAX_W:
        factor = w / MAX_W
        im = im.resize((MAX_W, round(h / factor)))
    _scale["factor"] = factor
    buf = io.BytesIO()
    im.convert("RGB").save(buf, "JPEG", quality=80)
    return [image_block(buf.getvalue(), "image/jpeg", max_side=10_000),
            {"type": "text", "text": f"screenshot {im.size[0]}x{im.size[1]} (real {w}x{h})"}]


def _real(x: int, y: int) -> tuple[int, int]:
    f = _scale["factor"]
    return round(x * f), round(y * f)


def _pyautogui():
    try:
        import pyautogui  # type: ignore
        pyautogui.FAILSAFE = True
        return pyautogui
    except Exception:
        return None


@tool(
    "mouse",
    "Move, click, double-click, right-click, drag, or scroll the mouse at screenshot coordinates.",
    {
        "action": {"type": "string", "enum": ["move", "click", "double_click", "right_click", "drag", "scroll"]},
        "x": {"type": "integer"}, "y": {"type": "integer"},
        "to_x": {"type": "integer", "description": "drag destination x"},
        "to_y": {"type": "integer", "description": "drag destination y"},
        "amount": {"type": "integer", "description": "scroll clicks; positive = down"},
    },
    ["action"],
    category="desktop",
)
def mouse(ctx, action: str, x: int | None = None, y: int | None = None, to_x: int | None = None,
          to_y: int | None = None, amount: int = 3):
    if action != "scroll" and (x is None or y is None):
        raise ToolError("x and y are required")
    rx, ry = _real(x or 0, y or 0) if x is not None else (None, None)
    pg = _pyautogui()
    if pg:
        if action == "move":
            pg.moveTo(rx, ry, duration=0.15)
        elif action == "click":
            pg.click(rx, ry)
        elif action == "double_click":
            pg.doubleClick(rx, ry)
        elif action == "right_click":
            pg.rightClick(rx, ry)
        elif action == "drag":
            tx, ty = _real(to_x or 0, to_y or 0)
            pg.moveTo(rx, ry)
            pg.dragTo(tx, ty, duration=0.4)
        elif action == "scroll":
            if rx is not None:
                pg.moveTo(rx, ry)
            pg.scroll(-amount * 3)
        return f"{action} ok"
    if IS_LINUX and which("xdotool"):
        if rx is not None:
            run(["xdotool", "mousemove", str(rx), str(ry)])
        if action in ("click", "double_click", "right_click"):
            run(["xdotool", "click", "--repeat", "2" if action == "double_click" else "1", "3" if action == "right_click" else "1"])
        elif action == "drag":
            tx, ty = _real(to_x or 0, to_y or 0)
            run(["xdotool", "mousedown", "1", "mousemove", str(tx), str(ty), "mouseup", "1"])
        elif action == "scroll":
            run(["xdotool", "click", "--repeat", str(abs(amount)), "5" if amount > 0 else "4"])
        return f"{action} ok"
    if IS_MAC and which("cliclick"):
        verb = {"move": "m", "click": "c", "double_click": "dc", "right_click": "rc"}.get(action)
        if verb:
            run(["cliclick", f"{verb}:{rx},{ry}"])
            return f"{action} ok"
    raise ToolError("mouse control needs pyautogui (pip install pyautogui) or xdotool")


_XDO_KEYS = {"enter": "Return", "return": "Return", "esc": "Escape", "escape": "Escape", "tab": "Tab", "space": "space",
             "backspace": "BackSpace", "delete": "Delete", "up": "Up", "down": "Down", "left": "Left", "right": "Right",
             "ctrl": "ctrl", "control": "ctrl", "alt": "alt", "shift": "shift", "super": "super", "win": "super",
             "cmd": "super", "home": "Home", "end": "End", "pageup": "Prior", "pagedown": "Next"}


@tool(
    "keyboard",
    "Type text into the focused window, or press a key combination like 'ctrl+s', 'alt+tab', 'enter', 'cmd+space'.",
    {"text": {"type": "string", "description": "Text to type."},
     "keys": {"type": "string", "description": "Key combo, '+'-separated."}},
    category="desktop",
)
def keyboard(ctx, text: str = "", keys: str = ""):
    if not text and not keys:
        raise ToolError("give text or keys")
    pg = _pyautogui()
    if pg:
        if text:
            pg.write(text, interval=0.01)
        if keys:
            pg.hotkey(*[k.strip().lower().replace("cmd", "command") for k in keys.split("+")])
        return "typed"
    if IS_LINUX and which("xdotool"):
        if text:
            run(["xdotool", "type", "--delay", "8", "--", text], timeout=120)
        if keys:
            combo = "+".join(_XDO_KEYS.get(k.strip().lower(), k.strip()) for k in keys.split("+"))
            run(["xdotool", "key", combo])
        return "typed"
    if IS_LINUX and which("wtype"):
        if text:
            run(["wtype", text])
        return "typed" if not keys else "typed (key combos need xdotool or pyautogui on Wayland)"
    raise ToolError("keyboard control needs pyautogui or xdotool")


@tool("wait", "Pause for a few seconds (e.g. to let an app finish opening).", {"seconds": {"type": "number"}}, ["seconds"],
      category="desktop")
def wait(ctx, seconds: float):
    time.sleep(max(0.0, min(float(seconds), 30.0)))
    return f"waited {seconds}s"
