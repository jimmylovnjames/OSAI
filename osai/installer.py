"""Install-to-disk backend for the OSAI live system.

The privileged work happens in linux/osai-install (run through sudo); this module finds disks,
validates the request, runs the script and turns its PROGRESS/ERROR/DONE lines into UI events.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path
from typing import Awaitable, Callable

from .osinfo import IS_LINUX, run, which

MIN_BYTES = 12 * 1024**3
USERNAME_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
HOSTNAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9-]{0,62}$")
RESERVED_USERS = {"root", "osai", "daemon", "bin", "sys", "nobody"}


def script_path() -> Path | None:
    for p in (Path(__file__).resolve().parents[1] / "linux" / "osai-install", Path("/opt/osai/src/linux/osai-install")):
        if p.is_file():
            return p
    return None


def is_live() -> bool:
    """True when running from the OSAI live USB (casper)."""
    if os.environ.get("OSAI_FAKE_LIVE"):
        return True
    try:
        return "boot=casper" in Path("/proc/cmdline").read_text()
    except OSError:
        return False


def firmware() -> str:
    return "uefi" if Path("/sys/firmware/efi").exists() else "bios"


def _boot_disks() -> set[str]:
    """Disks holding the medium the live system booted from."""
    disks = set()
    for mnt in ("/cdrom", "/run/live/medium", "/isodevice"):
        code, src = run(["findmnt", "-no", "SOURCE", mnt], timeout=5)
        if code == 0 and src.startswith("/dev/"):
            code2, parent = run(["lsblk", "-no", "PKNAME", src], timeout=5)
            disks.add(f"/dev/{parent.splitlines()[0]}" if code2 == 0 and parent.strip() else src)
    return disks


def parse_disks(lsblk_json: str, boot_disks: set[str]) -> list[dict]:
    """Turn `lsblk -J -b -d` output into installer choices, each with a reason when it can't be used."""
    out = []
    for d in json.loads(lsblk_json).get("blockdevices", []):
        if d.get("type") != "disk" or str(d.get("name", "")).startswith(("zram", "ram", "loop")):
            continue
        size = int(d.get("size") or 0)
        path = d.get("path") or f"/dev/{d['name']}"
        problem = None
        if path in boot_disks:
            problem = "This is the OSAI USB you started from"
        elif d.get("ro") in (True, "1", 1):
            problem = "Read-only"
        elif size < MIN_BYTES:
            problem = f"Too small ({size / 1024**3:.1f} GB, needs 12 GB)"
        out.append({
            "path": path,
            "name": d["name"],
            "size": size,
            "size_label": f"{size / 1000**3:.0f} GB" if size >= 1000**3 else f"{size / 1000**2:.0f} MB",
            "model": (d.get("model") or "").strip() or "Disk",
            "transport": (d.get("tran") or "").upper(),
            "removable": d.get("rm") in (True, "1", 1),
            "usable": problem is None,
            "problem": problem,
        })
    return out


def list_disks() -> list[dict]:
    if not IS_LINUX or not which("lsblk"):
        return []
    code, out = run(["lsblk", "-J", "-b", "-d", "-o", "NAME,PATH,SIZE,MODEL,TRAN,RM,RO,TYPE"], timeout=10)
    if code != 0:
        return []
    return parse_disks(out, _boot_disks())


def info() -> dict:
    return {"live": is_live(), "firmware": firmware(), "disks": list_disks() if is_live() else [],
            "script": bool(script_path())}


def validate(req: dict, disks: list[dict]) -> str | None:
    disk = next((d for d in disks if d["path"] == req.get("disk")), None)
    if not disk:
        return "Choose a disk to install on."
    if not disk["usable"]:
        return f"{disk['path']} can't be used: {disk['problem']}."
    if req.get("confirm", "").strip() != disk["name"]:
        return f"Type {disk['name']} to confirm that everything on it will be erased."
    user = str(req.get("username", ""))
    if not USERNAME_RE.match(user) or user in RESERVED_USERS:
        return "Choose a username of lowercase letters and digits (not root or osai)."
    if len(str(req.get("password", ""))) < 4:
        return "Choose a password of at least 4 characters."
    if req.get("password") != req.get("password2"):
        return "The two passwords don't match."
    if not HOSTNAME_RE.match(str(req.get("hostname", ""))):
        return "The computer name may only use letters, digits and dashes."
    tz = str(req.get("timezone", ""))
    if not tz or tz.startswith("/") or ".." in tz or not Path("/usr/share/zoneinfo", tz).is_file():
        return "Choose a valid time zone."
    return None


def build_argv(req: dict, dry_run: bool = False) -> list[str]:
    script = script_path()
    argv = ["sudo", "-n", str(script)] if os.geteuid() != 0 else [str(script)]
    argv += ["--disk", req["disk"], "--user", req["username"], "--fullname", str(req.get("fullname") or req["username"])[:80],
             "--hostname", req["hostname"], "--timezone", req["timezone"]]
    if req.get("autologin"):
        argv.append("--autologin")
    if req.get("passwordless_admin"):
        argv.append("--passwordless-admin")
    if req.get("copy_settings"):
        argv += ["--copy-settings", str(Path(os.environ.get("OSAI_HOME", Path.home() / ".osai")))]
    if dry_run:
        argv.append("--dry-run")
    return argv


class Installer:
    def __init__(self, emit: Callable[[dict], Awaitable[None]]):
        self.emit = emit
        self.running = False
        self.last: dict = {"state": "idle"}

    async def start(self, req: dict, dry_run: bool = False) -> str | None:
        """Validate and launch. Returns an error message, or None once the install is running."""
        if self.running:
            return "An installation is already running."
        if not is_live():
            return "Installing is only available when OSAI runs from its live USB."
        if not script_path():
            return "The installer script is missing from this system."
        problem = validate(req, list_disks())
        if problem:
            return problem
        # OSAI_INSTALL_DRY_ONLY forces a rehearsal whatever the request says (for demos and tests).
        dry_run = dry_run or bool(os.environ.get("OSAI_INSTALL_DRY_ONLY"))
        self.running = True
        asyncio.create_task(self._run(build_argv(req, dry_run), str(req["password"])))
        return None

    async def _report(self, **event) -> None:
        self.last = event
        await self.emit({"type": "install", **event})

    async def _run(self, argv: list[str], password: str) -> None:
        await self._report(state="running", percent=0, message="Starting")
        error = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
            proc.stdin.write((password + "\n").encode())
            await proc.stdin.drain()
            proc.stdin.close()
            done = False
            tail: list[str] = []
            async for raw in proc.stdout:
                line = raw.decode(errors="replace").strip()
                if line.startswith("PROGRESS "):
                    _, pct, msg = (line.split(" ", 2) + [""])[:3]
                    await self._report(state="running", percent=int(pct), message=msg)
                elif line.startswith("ERROR "):
                    error = error or line[6:]
                elif line == "DONE":
                    done = True
                elif line:
                    tail = (tail + [line])[-5:]
            code = await proc.wait()
            if not done and not error:
                error = f"installer exited with code {code}" + (f": {tail[-1]}" if tail else "")
                if "password is required" in " ".join(tail):
                    error = "Installing needs administrator rights (passwordless sudo, as on the live USB)."
        except Exception as e:  # never leave the UI spinning
            error = f"{type(e).__name__}: {e}"
        finally:
            self.running = False
        if error:
            await self._report(state="failed", percent=self.last.get("percent", 0), message=error)
        else:
            await self._report(state="done", percent=100, message="OSAI is installed")
