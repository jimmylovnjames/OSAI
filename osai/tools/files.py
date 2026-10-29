"""File system tools: browse, read, write, search, move, trash, open."""
from __future__ import annotations

import base64
import fnmatch
import io
import os
import shutil
import time
from pathlib import Path

from ..osinfo import IS_LINUX, IS_MAC, IS_WINDOWS, HOME, osascript, run, spawn, which
from .base import ToolError, tool

IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".cache", ".venv", "venv", ".local/share/Trash", "Library", "AppData"}


def _p(path: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(path))).resolve()


def _human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}PB"


def image_block(data: bytes, media_type: str, max_side: int = 1568) -> dict:
    """Downscale an image (keeps token cost sane) and wrap it as an API image block."""
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(data))
        if max(im.size) > max_side:
            im.thumbnail((max_side, max_side))
            buf = io.BytesIO()
            im.convert("RGB").save(buf, "JPEG", quality=85)
            data, media_type = buf.getvalue(), "image/jpeg"
    except Exception:
        pass
    return {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": base64.b64encode(data).decode()}}


@tool(
    "list_directory",
    "List the contents of a folder with sizes and modification times. Use ~ for the home folder.",
    {"path": {"type": "string"}, "show_hidden": {"type": "boolean"}},
    ["path"],
    category="files",
)
def list_directory(ctx, path: str, show_hidden: bool = False):
    p = _p(path)
    if not p.is_dir():
        raise ToolError(f"not a folder: {p}")
    rows = []
    for e in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
        if not show_hidden and e.name.startswith("."):
            continue
        try:
            st = e.stat()
            kind = "dir " if e.is_dir() else "file"
            rows.append(f"{kind} {_human(st.st_size) if not e.is_dir() else '-':>8}  "
                        f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(st.st_mtime))}  {e.name}")
        except OSError:
            rows.append(f"?    {'?':>8}  {'?':16}  {e.name}")
        if len(rows) >= 500:
            rows.append("... (truncated at 500 entries)")
            break
    return f"{p}\n" + ("\n".join(rows) if rows else "(empty)")


@tool(
    "read_file",
    "Read a file. Text files return their contents; images are returned so you can see them.",
    {"path": {"type": "string"}, "max_chars": {"type": "integer"}},
    ["path"],
    category="files",
)
def read_file(ctx, path: str, max_chars: int = 20000):
    p = _p(path)
    if not p.is_file():
        raise ToolError(f"no such file: {p}")
    ext = p.suffix.lower()
    if ext in IMAGE_TYPES:
        return [image_block(p.read_bytes(), IMAGE_TYPES[ext]), {"type": "text", "text": f"image {p} ({_human(p.stat().st_size)})"}]
    if ext == ".pdf":
        if which("pdftotext"):
            code, out = run(["pdftotext", "-layout", str(p), "-"], timeout=60)
            if code == 0:
                return out[:max_chars]
        raise ToolError("PDF text extraction needs `pdftotext` (poppler-utils)")
    raw = p.read_bytes()[: max_chars * 4]
    if b"\x00" in raw[:4096]:
        raise ToolError(f"{p} looks like a binary file ({_human(p.stat().st_size)})")
    text = raw.decode("utf-8", errors="replace")
    return text[:max_chars] + ("\n...[truncated]" if len(text) > max_chars else "")


def _write_risk(a: dict) -> str | None:
    p = _p(a.get("path", "~"))
    if p.exists() and not a.get("append"):
        return f"Overwrites existing file {p}"
    if not str(p).startswith(str(HOME)):
        return f"Writes outside your home folder: {p}"
    return None


@tool(
    "write_file",
    "Create or overwrite a text file (parent folders are created). Set append=true to add to the end.",
    {"path": {"type": "string"}, "content": {"type": "string"}, "append": {"type": "boolean"}},
    ["path", "content"],
    risk=_write_risk,
    category="files",
)
def write_file(ctx, path: str, content: str, append: bool = False):
    p = _p(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a" if append else "w", encoding="utf-8") as f:
        f.write(content)
    return f"{'appended to' if append else 'wrote'} {p} ({_human(p.stat().st_size)})"


@tool(
    "find_files",
    "Search for files by name pattern (glob like '*.pdf' or a plain substring) under a folder, newest first. "
    "Optionally only files containing some text.",
    {
        "pattern": {"type": "string"},
        "root": {"type": "string", "description": "Folder to search (default ~)."},
        "contains": {"type": "string", "description": "Only text files containing this (case-insensitive)."},
        "limit": {"type": "integer"},
    },
    ["pattern"],
    category="files",
)
def find_files(ctx, pattern: str, root: str = "~", contains: str = "", limit: int = 50):
    base = _p(root)
    glob = pattern if any(c in pattern for c in "*?[") else f"*{pattern}*"
    hits: list[tuple[float, str]] = []
    deadline = time.time() + 20
    needle = contains.lower()
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in filenames:
            if fnmatch.fnmatch(fn.lower(), glob.lower()):
                fp = os.path.join(dirpath, fn)
                if needle:
                    try:
                        with open(fp, "rb") as f:
                            if needle not in f.read(2_000_000).decode("utf-8", "ignore").lower():
                                continue
                    except OSError:
                        continue
                try:
                    hits.append((os.path.getmtime(fp), fp))
                except OSError:
                    pass
        if time.time() > deadline:
            break
    hits.sort(reverse=True)
    out = [f"{time.strftime('%Y-%m-%d', time.localtime(m))}  {f}" for m, f in hits[: max(1, min(limit, 500))]]
    note = "" if time.time() <= deadline else "\n(search stopped after 20s; narrow the root folder for more)"
    return ("\n".join(out) or "no matches") + note


@tool(
    "move_path",
    "Move or rename a file/folder, or copy it when copy=true.",
    {"source": {"type": "string"}, "destination": {"type": "string"}, "copy": {"type": "boolean"}},
    ["source", "destination"],
    risk=lambda a: f"Replaces existing {a.get('destination')}" if _p(a.get("destination", "")).is_file() else None,
    category="files",
)
def move_path(ctx, source: str, destination: str, copy: bool = False):
    s, d = _p(source), _p(destination)
    if not s.exists():
        raise ToolError(f"no such path: {s}")
    d.parent.mkdir(parents=True, exist_ok=True)
    if copy:
        (shutil.copytree if s.is_dir() else shutil.copy2)(s, d)
    else:
        shutil.move(str(s), str(d))
    return f"{'copied' if copy else 'moved'} {s} -> {d}"


@tool(
    "make_folder",
    "Create a folder (and parents).",
    {"path": {"type": "string"}},
    ["path"],
    category="files",
)
def make_folder(ctx, path: str):
    p = _p(path)
    p.mkdir(parents=True, exist_ok=True)
    return f"created {p}"


def _trash(p: Path) -> str:
    try:
        from send2trash import send2trash  # type: ignore
        send2trash(str(p))
        return "moved to trash"
    except ImportError:
        pass
    if IS_LINUX and which("gio"):
        code, out = run(["gio", "trash", str(p)])
        if code == 0:
            return "moved to trash"
    if IS_MAC:
        code, out = osascript(f'tell application "Finder" to delete POSIX file "{p}"')
        if code == 0:
            return "moved to trash"
    raise ToolError("no trash backend available (install send2trash) - use permanent=true to delete permanently")


@tool(
    "delete_path",
    "Delete a file or folder. Goes to the trash/recycle bin by default; permanent=true deletes for good.",
    {"path": {"type": "string"}, "permanent": {"type": "boolean"}},
    ["path"],
    risk="confirm",
    category="files",
)
def delete_path(ctx, path: str, permanent: bool = False):
    p = _p(path)
    if not p.exists():
        raise ToolError(f"no such path: {p}")
    if p in (Path("/"), HOME) or len(p.parts) <= 2:
        raise ToolError(f"refusing to delete top-level path {p}")
    if not permanent:
        return f"{p}: {_trash(p)}"
    shutil.rmtree(p) if p.is_dir() and not p.is_symlink() else p.unlink()
    return f"permanently deleted {p}"


@tool(
    "open_path",
    "Open a file, folder, or URL with its default application (e.g. a PDF in the PDF viewer, a folder in the "
    "file manager, a link in the user's normal web browser).",
    {"target": {"type": "string"}},
    ["target"],
    category="files",
)
def open_path(ctx, target: str):
    is_url = "://" in target or target.startswith("mailto:")
    t = target if is_url else str(_p(target))
    if not is_url and not os.path.exists(t):
        raise ToolError(f"no such path: {t}")
    if IS_WINDOWS:
        os.startfile(t)  # type: ignore[attr-defined]
    elif IS_MAC:
        spawn(["open", t])
    else:
        opener = which("xdg-open", "gio")
        if not opener:
            raise ToolError("no opener found (install xdg-utils)")
        spawn([opener, "open", t] if opener.endswith("gio") else [opener, t])
    return f"opened {t}"
