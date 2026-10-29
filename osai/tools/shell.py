"""Shell and process control."""
from __future__ import annotations

import os
import subprocess

import psutil

from ..osinfo import HOME, IS_WINDOWS, detach_kwargs, which
from ..safety import shell_risk
from .base import ToolError, tool


def _shell_argv(command: str) -> list[str]:
    if IS_WINDOWS:
        return [which("pwsh", "powershell") or "powershell", "-NoProfile", "-Command", command]
    return [os.environ.get("SHELL") or "/bin/bash", "-lc", command]


@tool(
    "run_command",
    "Run a shell command on this computer and return its output (bash/zsh on Linux and macOS, PowerShell on "
    "Windows). Use for anything without a dedicated tool. Set background=true for long-running or GUI programs.",
    {
        "command": {"type": "string", "description": "The command line to run."},
        "cwd": {"type": "string", "description": "Working directory (default: home)."},
        "timeout": {"type": "integer", "description": "Seconds before the command is killed (default 60, max 600)."},
        "background": {"type": "boolean", "description": "Start detached and return immediately with the pid."},
    },
    ["command"],
    risk=lambda a: shell_risk(a.get("command", "")),
    category="system",
)
def run_command(ctx, command: str, cwd: str | None = None, timeout: int = 60, background: bool = False):
    cwd = os.path.expanduser(cwd) if cwd else str(HOME)
    if not os.path.isdir(cwd):
        raise ToolError(f"cwd does not exist: {cwd}")
    argv = _shell_argv(command)
    if background:
        p = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, **detach_kwargs())
        return f"started in background, pid {p.pid}"
    timeout = max(1, min(int(timeout), 600))
    try:
        p = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout, errors="replace",
                           stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or b"")
        out = out.decode(errors="replace") if isinstance(out, bytes) else out
        return f"[timed out after {timeout}s]\n{out[-8000:]}"
    parts = [f"exit code {p.returncode}"]
    if p.stdout.strip():
        parts.append(p.stdout.rstrip())
    if p.stderr.strip():
        parts.append("[stderr]\n" + p.stderr.rstrip())
    return "\n".join(parts)


@tool(
    "list_processes",
    "List running processes, sorted by CPU or memory use. Optionally filter by name substring.",
    {
        "filter": {"type": "string", "description": "Case-insensitive substring of the process name."},
        "sort": {"type": "string", "enum": ["cpu", "memory"]},
        "limit": {"type": "integer"},
    },
    category="system",
)
def list_processes(ctx, filter: str = "", sort: str = "cpu", limit: int = 25):
    procs = []
    for p in psutil.process_iter(["pid", "name", "username", "cpu_percent", "memory_info", "cmdline"]):
        info = p.info
        name = info.get("name") or ""
        if filter and filter.lower() not in name.lower() and filter.lower() not in " ".join(info.get("cmdline") or []).lower():
            continue
        mem = info.get("memory_info")
        procs.append({
            "pid": info["pid"], "name": name, "cpu%": info.get("cpu_percent") or 0.0,
            "mem_mb": round(mem.rss / 2**20, 1) if mem else 0, "user": info.get("username"),
        })
    key = "cpu%" if sort == "cpu" else "mem_mb"
    procs.sort(key=lambda d: d[key], reverse=True)
    return procs[: max(1, min(limit, 200))]


@tool(
    "kill_process",
    "Terminate a process by pid (or force-kill it). Unsaved work in that program is lost.",
    {"pid": {"type": "integer"}, "force": {"type": "boolean"}},
    ["pid"],
    risk="confirm",
    category="system",
)
def kill_process(ctx, pid: int, force: bool = False):
    try:
        p = psutil.Process(pid)
        name = p.name()
        p.kill() if force else p.terminate()
        try:
            p.wait(timeout=5)
        except psutil.TimeoutExpired:
            return f"sent {'SIGKILL' if force else 'SIGTERM'} to {name} ({pid}); still running - retry with force=true"
        return f"{name} ({pid}) stopped"
    except psutil.NoSuchProcess:
        raise ToolError(f"no process with pid {pid}")
    except psutil.AccessDenied:
        raise ToolError(f"permission denied for pid {pid}")
