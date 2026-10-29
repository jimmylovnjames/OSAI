"""Host detection and small subprocess helpers shared by the tools."""
from __future__ import annotations

import getpass
import os
import platform
import shutil
import socket
import subprocess
from pathlib import Path

SYSTEM = platform.system().lower()  # linux | darwin | windows
IS_LINUX = SYSTEM == "linux"
IS_MAC = SYSTEM == "darwin"
IS_WINDOWS = SYSTEM == "windows"
HOME = Path.home()


def which(*names: str) -> str | None:
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    return None


def run(cmd: list[str] | str, timeout: float = 20, shell: bool = False, input: str | None = None) -> tuple[int, str]:
    """Run a command, return (returncode, combined stdout/stderr). Never raises for command failure."""
    try:
        p = subprocess.run(
            cmd, shell=shell, capture_output=True, text=True, timeout=timeout, input=input,
            errors="replace",
        )
        out = (p.stdout or "") + (("\n" + p.stderr) if p.stderr.strip() else "")
        return p.returncode, out.strip()
    except FileNotFoundError as e:
        return 127, f"command not found: {e.filename}"
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout}s"


def spawn(cmd: list[str] | str, shell: bool = False, cwd: str | None = None) -> int:
    """Start a detached process (an app) and return its pid."""
    kwargs: dict = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "cwd": cwd}
    if IS_WINDOWS:
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(cmd, shell=shell, **kwargs).pid


def powershell(script: str, timeout: float = 20) -> tuple[int, str]:
    exe = which("pwsh", "powershell") or "powershell"
    return run([exe, "-NoProfile", "-NonInteractive", "-Command", script], timeout=timeout)


def osascript(script: str, timeout: float = 20) -> tuple[int, str]:
    return run(["osascript", "-e", script], timeout=timeout)


def has_display() -> bool:
    if IS_LINUX:
        return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    return True


def session_type() -> str:
    if not IS_LINUX:
        return SYSTEM
    if os.environ.get("WAYLAND_DISPLAY"):
        return "wayland"
    if os.environ.get("DISPLAY"):
        return "x11"
    return "headless"


def describe() -> str:
    """One paragraph about the machine, for the system prompt."""
    distro = ""
    if IS_LINUX:
        try:
            for line in Path("/etc/os-release").read_text().splitlines():
                if line.startswith("PRETTY_NAME="):
                    distro = line.split("=", 1)[1].strip('"')
        except OSError:
            pass
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "")
    try:
        user = getpass.getuser()
    except Exception:
        user = "user"
    return (
        f"OS: {platform.system()} {platform.release()} {distro}".strip()
        + f"; arch {platform.machine()}; host {socket.gethostname()}; user {user}; home {HOME}"
        + f"; session {session_type()}{(' / ' + desktop) if desktop else ''}"
        + f"; shell {'powershell' if IS_WINDOWS else os.environ.get('SHELL', '/bin/sh')}."
    )


def ps_quote(s: str) -> str:
    """Quote a value as a PowerShell single-quoted literal."""
    return "'" + s.replace("'", "''") + "'"


def as_quote(s: str) -> str:
    """Quote a value as an AppleScript string literal."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
