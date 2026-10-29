"""Applications and windows: discover, launch, close, focus, arrange, install."""
from __future__ import annotations

import configparser
import difflib
import glob
import os
import re
import shlex
from pathlib import Path

import psutil

from ..osinfo import IS_LINUX, IS_MAC, IS_WINDOWS, as_quote, osascript, powershell, ps_quote, run, spawn, which
from .base import ToolError, tool

_APP_CACHE: dict[str, dict] | None = None


def _linux_apps() -> dict[str, dict]:
    dirs = [
        "/usr/share/applications", "/usr/local/share/applications",
        os.path.expanduser("~/.local/share/applications"),
        "/var/lib/flatpak/exports/share/applications",
        os.path.expanduser("~/.local/share/flatpak/exports/share/applications"),
        "/var/lib/snapd/desktop/applications",
    ]
    apps: dict[str, dict] = {}
    for d in dirs:
        for f in glob.glob(os.path.join(d, "*.desktop")):
            cp = configparser.ConfigParser(interpolation=None, strict=False)
            try:
                cp.read(f, encoding="utf-8")
                e = cp["Desktop Entry"]
            except Exception:
                continue
            if e.get("NoDisplay", "false").lower() == "true" or e.get("Type", "Application") != "Application":
                continue
            name = e.get("Name")
            exe = e.get("Exec")
            if name and exe:
                apps[name] = {"name": name, "exec": exe, "id": Path(f).stem,
                              "keywords": (e.get("Keywords", "") + " " + e.get("GenericName", "")).strip()}
    return apps


def _mac_apps() -> dict[str, dict]:
    apps = {}
    for d in ["/Applications", "/Applications/Utilities", "/System/Applications",
              "/System/Applications/Utilities", os.path.expanduser("~/Applications")]:
        for a in glob.glob(os.path.join(d, "*.app")):
            name = Path(a).stem
            apps[name] = {"name": name, "path": a, "keywords": ""}
    return apps


def _windows_apps() -> dict[str, dict]:
    apps = {}
    roots = [os.path.expandvars(r"%ProgramData%\Microsoft\Windows\Start Menu\Programs"),
             os.path.expandvars(r"%AppData%\Microsoft\Windows\Start Menu\Programs")]
    for r in roots:
        for lnk in glob.glob(os.path.join(r, "**", "*.lnk"), recursive=True):
            name = Path(lnk).stem
            if "uninstall" not in name.lower():
                apps[name] = {"name": name, "path": lnk, "keywords": ""}
    return apps


def installed_apps(refresh: bool = False) -> dict[str, dict]:
    global _APP_CACHE
    if _APP_CACHE is None or refresh:
        _APP_CACHE = _linux_apps() if IS_LINUX else _mac_apps() if IS_MAC else _windows_apps() if IS_WINDOWS else {}
    return _APP_CACHE


def find_app(query: str) -> dict | None:
    apps = installed_apps()
    q = query.lower().strip()
    for name, a in apps.items():
        if name.lower() == q or a.get("id", "").lower() == q:
            return a
    for name, a in apps.items():
        if q in name.lower() or q in a.get("keywords", "").lower():
            return a
    close = difflib.get_close_matches(q, [n.lower() for n in apps], n=1, cutoff=0.6)
    if close:
        return next(a for n, a in apps.items() if n.lower() == close[0])
    return None


@tool(
    "list_apps",
    "List installed applications (optionally filtered).",
    {"filter": {"type": "string"}},
    category="apps",
)
def list_apps(ctx, filter: str = ""):
    names = sorted(installed_apps(refresh=True))
    if filter:
        names = [n for n in names if filter.lower() in n.lower()]
    return ", ".join(names) if names else "no matching apps"


@tool(
    "launch_app",
    "Open an installed application by name (fuzzy matched), optionally with files/URLs as arguments. "
    "Falls back to running it as a command.",
    {"name": {"type": "string"}, "args": {"type": "array", "items": {"type": "string"}}},
    ["name"],
    category="apps",
)
def launch_app(ctx, name: str, args: list[str] | None = None):
    args = args or []
    app = find_app(name)
    if IS_MAC:
        target = app["path"] if app else name
        code, out = run(["open", "-a", target, *args])
        if code != 0:
            raise ToolError(out or f"could not open {name}")
        return f"opened {app['name'] if app else name}"
    if IS_WINDOWS:
        if app:
            os.startfile(app["path"])  # type: ignore[attr-defined]
            return f"opened {app['name']}"
        code, out = powershell(f"Start-Process {ps_quote(name)}" + (f" -ArgumentList {','.join(ps_quote(a) for a in args)}" if args else ""))
        if code != 0:
            raise ToolError(out)
        return f"started {name}"
    if app:
        # Strip desktop-entry field codes (%u %F ...) and substitute our args.
        argv = [t for t in shlex.split(app["exec"]) if not re.fullmatch(r"%[a-zA-Z]", t)]
        pid = spawn(argv + args)
        return f"opened {app['name']} (pid {pid})"
    exe = which(name) or which(name.lower().replace(" ", "-"))
    if exe:
        pid = spawn([exe, *args])
        return f"started {exe} (pid {pid})"
    raise ToolError(f"no installed app matches '{name}'. Use list_apps to see what exists, or install_app.")


@tool(
    "close_app",
    "Close an application gracefully (like clicking its close button), so it can prompt to save.",
    {"name": {"type": "string"}},
    ["name"],
    category="apps",
)
def close_app(ctx, name: str):
    if IS_MAC:
        app = find_app(name)
        code, out = osascript(f'tell application {as_quote((app or {}).get("name", name))} to quit')
        if code != 0:
            raise ToolError(out)
        return f"asked {name} to quit"
    if IS_WINDOWS:
        code, out = powershell(
            f"$n = {ps_quote('*' + name + '*')}; $p = Get-Process | Where-Object {{ $_.MainWindowTitle -and ($_.ProcessName -like $n -or "
            f"$_.MainWindowTitle -like $n) }}; if (-not $p) {{ exit 3 }}; $p | ForEach-Object {{ $_.CloseMainWindow() | Out-Null }}")
        if code == 3:
            raise ToolError(f"no open window for {name}")
        return f"asked {name} to close"
    if which("wmctrl"):
        wins = _linux_windows()
        hits = [w for w in wins if name.lower() in w["title"].lower() or name.lower() in w["app"].lower()]
        for w in hits:
            run(["wmctrl", "-i", "-c", w["id"]])
        if hits:
            return f"closed {len(hits)} window(s): " + "; ".join(w["title"] for w in hits)
    procs = [p for p in psutil.process_iter(["name"]) if name.lower() in (p.info["name"] or "").lower()]
    if not procs:
        raise ToolError(f"{name} is not running")
    for p in procs:
        p.terminate()
    return f"sent terminate to {len(procs)} process(es) named like {name}"


def _linux_windows() -> list[dict]:
    code, out = run(["wmctrl", "-lpx"])
    wins = []
    if code != 0:
        return wins
    for line in out.splitlines():
        parts = line.split(None, 5)
        if len(parts) >= 5:
            wid, desk, pid, wmclass = parts[:4]
            title = parts[5] if len(parts) > 5 else ""
            wins.append({"id": wid, "desktop": desk, "pid": pid, "app": wmclass.split(".")[-1], "title": title})
    return wins


@tool(
    "list_windows",
    "List open windows (title, app, id).",
    category="apps",
)
def list_windows(ctx):
    if IS_LINUX:
        if not which("wmctrl"):
            raise ToolError("window control needs `wmctrl` (X11). On Wayland use the compositor's IPC via run_command.")
        return _linux_windows() or "no windows"
    if IS_MAC:
        code, out = osascript('tell application "System Events" to get name of every process whose visible is true')
        return out
    code, out = powershell("Get-Process | Where-Object {$_.MainWindowTitle} | Select-Object Id,ProcessName,MainWindowTitle | Format-Table -AutoSize | Out-String -Width 200")
    return out


@tool(
    "window_action",
    "Focus, minimize, maximize, restore, fullscreen, or close a window matched by title/app substring.",
    {"match": {"type": "string"},
     "action": {"type": "string", "enum": ["focus", "minimize", "maximize", "restore", "fullscreen", "close"]}},
    ["match", "action"],
    category="apps",
)
def window_action(ctx, match: str, action: str):
    if IS_LINUX:
        if not which("wmctrl"):
            raise ToolError("needs `wmctrl`")
        wins = [w for w in _linux_windows() if match.lower() in (w["title"] + " " + w["app"]).lower()]
        if not wins:
            raise ToolError(f"no window matches '{match}'")
        w = wins[0]["id"]
        cmds = {
            "focus": [["wmctrl", "-i", "-a", w]],
            "close": [["wmctrl", "-i", "-c", w]],
            "maximize": [["wmctrl", "-i", "-r", w, "-b", "add,maximized_vert,maximized_horz"], ["wmctrl", "-i", "-a", w]],
            "restore": [["wmctrl", "-i", "-r", w, "-b", "remove,maximized_vert,maximized_horz,fullscreen"]],
            "fullscreen": [["wmctrl", "-i", "-r", w, "-b", "toggle,fullscreen"]],
            "minimize": [["xdotool", "windowminimize", w]] if which("xdotool") else [],
        }[action]
        if not cmds:
            raise ToolError("minimize needs xdotool")
        for c in cmds:
            run(c)
        return f"{action}: {wins[0]['title']}"
    if IS_MAC:
        app = find_app(match)
        n = (app or {}).get("name", match)
        q = as_quote(n)
        script = {
            "focus": f"tell application {q} to activate",
            "close": f"tell application {q} to close front window",
            "minimize": f'tell application "System Events" to set value of attribute "AXMinimized" of front window of process {q} to true',
            "maximize": f'tell application "System Events" to tell process {q} to set value of attribute "AXFullScreen" of front window to true',
            "fullscreen": f'tell application "System Events" to tell process {q} to set value of attribute "AXFullScreen" of front window to true',
            "restore": f'tell application "System Events" to tell process {q} to set value of attribute "AXFullScreen" of front window to false',
        }[action]
        code, out = osascript(script)
        if code != 0:
            raise ToolError(out)
        return f"{action}: {n}"
    show = {"focus": 9, "minimize": 6, "maximize": 3, "restore": 9, "fullscreen": 3}
    if action == "close":
        return close_app(ctx, match)
    code, out = powershell(
        "Add-Type '[DllImport(\"user32.dll\")] public static extern bool ShowWindow(IntPtr h,int c);"
        "[DllImport(\"user32.dll\")] public static extern bool SetForegroundWindow(IntPtr h);' -Name W -Namespace U;"
        f"$n = {ps_quote('*' + match + '*')}; $p = Get-Process | Where-Object {{ $_.MainWindowTitle -like $n -or $_.ProcessName -like $n }} | Select-Object -First 1;"
        f"if (-not $p) {{ exit 3 }}; [U.W]::ShowWindow($p.MainWindowHandle, {show[action]}); [U.W]::SetForegroundWindow($p.MainWindowHandle)")
    if code == 3:
        raise ToolError(f"no window matches '{match}'")
    return f"{action}: {match}"


def _installer() -> tuple[str, list[str]] | None:
    if IS_MAC and which("brew"):
        return "brew", ["brew", "install", "--cask"]
    if IS_WINDOWS and which("winget"):
        return "winget", ["winget", "install", "--accept-package-agreements", "--accept-source-agreements", "-e", "--id"]
    if IS_LINUX:
        for name, argv in (("apt", ["sudo", "-n", "apt-get", "install", "-y"]), ("dnf", ["sudo", "-n", "dnf", "install", "-y"]),
                           ("pacman", ["sudo", "-n", "pacman", "-S", "--noconfirm"]), ("zypper", ["sudo", "-n", "zypper", "install", "-y"])):
            if which(name if name != "apt" else "apt-get"):
                return name, argv
    return None


@tool(
    "install_app",
    "Install software with the system package manager (apt/dnf/pacman/zypper, Homebrew, winget) or Flathub "
    "(source='flatpak'). Use search_packages first if unsure of the exact package id.",
    {"package": {"type": "string"}, "source": {"type": "string", "enum": ["system", "flatpak"]}},
    ["package"],
    risk="confirm",
    category="apps",
)
def install_app(ctx, package: str, source: str = "system"):
    if source == "flatpak":
        if not which("flatpak"):
            raise ToolError("flatpak is not installed")
        code, out = run(["flatpak", "install", "-y", "--noninteractive", "flathub", package], timeout=900)
    else:
        inst = _installer()
        if not inst:
            raise ToolError("no supported package manager found")
        code, out = run(inst[1] + [package], timeout=900)
        if code != 0 and "sudo" in out and "password" in out:
            raise ToolError("needs an administrator password: run `sudo -v` in a terminal, or configure passwordless "
                            "sudo for the package manager (see linux/README)")
    installed_apps(refresh=True)
    return f"exit {code}\n{out[-3000:]}"


@tool(
    "search_packages",
    "Search the package manager / Flathub for installable software.",
    {"query": {"type": "string"}},
    ["query"],
    category="apps",
)
def search_packages(ctx, query: str):
    results = []
    if which("flatpak"):
        results.append("[flathub]\n" + run(["flatpak", "search", query], timeout=60)[1][:3000])
    if IS_MAC and which("brew"):
        results.append("[brew]\n" + run(["brew", "search", query], timeout=60)[1][:3000])
    elif IS_WINDOWS and which("winget"):
        results.append("[winget]\n" + run(["winget", "search", query, "--accept-source-agreements"], timeout=60)[1][:3000])
    elif which("apt-cache"):
        results.append("[apt]\n" + run(["apt-cache", "search", query], timeout=60)[1][:3000])
    elif which("dnf"):
        results.append("[dnf]\n" + run(["dnf", "search", "-q", query], timeout=90)[1][:3000])
    elif which("pacman"):
        results.append("[pacman]\n" + run(["pacman", "-Ss", query], timeout=60)[1][:3000])
    return "\n\n".join(results) or "no package manager available"
