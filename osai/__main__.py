"""Entry point.

  osai                 start the server and open the OSAI screen (fullscreen with --kiosk)
  osai --no-browser    server only (open http://127.0.0.1:8765 yourself)
  osai --cli           talk to OSAI in the terminal (no UI)
"""
from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import subprocess
import sys
import threading
import time
import webbrowser


def _wait_for_server(url: str, timeout: float = 120) -> None:
    """Block until the local server answers. Slow machines can take far longer than a fixed delay."""
    import urllib.request
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url + "api/status", timeout=2).close()
            return
        except Exception:
            time.sleep(0.5)


def _server_running(url: str) -> bool:
    import urllib.request
    try:
        urllib.request.urlopen(url + "api/status", timeout=2).close()
        return True
    except Exception:
        return False


def _open_screen(url: str, kiosk: bool, mini: bool) -> None:
    _wait_for_server(url)
    chrome = os.environ.get("OSAI_CHROMIUM") or shutil.which("chromium") or shutil.which("chromium-browser") or shutil.which("google-chrome") \
        or shutil.which("microsoft-edge") or (r"C:\Program Files\Google\Chrome\Application\chrome.exe"
                                              if os.path.exists(r"C:\Program Files\Google\Chrome\Application\chrome.exe") else None) \
        or ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
            if os.path.exists("/Applications/Google Chrome.app") else None)
    if chrome:
        base = [chrome, f"--user-data-dir={os.path.expanduser('~/.osai/shell-profile')}", "--no-first-run",
                "--autoplay-policy=no-user-gesture-required", "--use-fake-ui-for-media-stream",
                "--disable-infobars", "--no-default-browser-check", "--test-type", "--disable-features=Translate,MediaRouter"]
        base += os.environ.get("OSAI_CHROMIUM_FLAGS", "").split()
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            base.append("--no-sandbox")  # Chromium refuses to start as root otherwise
        log_path = os.path.join(os.environ.get("OSAI_HOME", os.path.expanduser("~/.osai")), "shell.log")
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        log = open(log_path, "ab")
        subprocess.Popen(base + ["--class=OSAIShell"] + (["--kiosk", url] if kiosk else [f"--app={url}", "--start-maximized"]),
                         stdout=log, stderr=log, start_new_session=os.name != "nt")
        if mini:
            subprocess.Popen(base[:1] + base[2:] + [f"--user-data-dir={os.path.expanduser('~/.osai/mini-profile')}", "--class=OSAIMini",
                                                    f"--app={url}?mini=1", "--window-size=240,300"],
                             stdout=log, stderr=log, start_new_session=os.name != "nt")
    else:
        webbrowser.open(url)


async def _cli() -> None:
    from .agent import Agent
    from .config import Config
    from .memory import Memory
    from .scheduler import Scheduler

    config = Config()
    agent: Agent

    async def emit(e: dict) -> None:
        t = e.get("type")
        if t == "assistant_delta":
            print(e["text"], end="", flush=True)
        elif t == "assistant_done":
            print()
        elif t == "tool_start":
            print(f"\n  \033[2m→ {e['name']} {str(e.get('input'))[:120]}\033[0m")
        elif t == "tool_end" and not e.get("ok"):
            print(f"  \033[31m✗ {e.get('summary')}\033[0m")
        elif t == "approval_request":
            ans = await asyncio.to_thread(input, f"\n  \033[33m⚠ {e['reason']}\n  Allow? [y/N] \033[0m")
            agent.resolve_approval(e["id"], ans.strip().lower() in ("y", "yes"))
        elif t in ("error", "notify", "speak", "notice"):
            print(f"\n[{t}] {e.get('text')}")

    services: dict = {}
    agent = Agent(config, Memory(), emit, services)

    async def on_fire(item):
        if item["kind"] == "task":
            await agent.handle(item["label"], source="scheduled")
        else:
            print(f"\n⏰ {item['label']}")

    services["scheduler"] = Scheduler(on_fire)
    services["scheduler"].start()
    print(f"{config['assistant_name']} ready. Type a request (Ctrl-D to quit).")
    while True:
        try:
            line = await asyncio.to_thread(input, "\nyou › ")
        except EOFError:
            break
        if line.strip():
            print(f"{config['assistant_name'].lower()} › ", end="", flush=True)
            await agent.handle(line.strip())


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="osai", description="OSAI - an AI that runs your computer")
    ap.add_argument("--host", default=os.environ.get("OSAI_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("OSAI_PORT", "8765")))
    ap.add_argument("--kiosk", action="store_true", help="open the OSAI screen fullscreen (desktop session mode)")
    ap.add_argument("--mini", action="store_true", help="also open the small always-visible avatar window")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--cli", action="store_true", help="terminal chat instead of the graphical shell")
    args = ap.parse_args(argv)

    if args.cli:
        asyncio.run(_cli())
        return
    if args.host not in ("127.0.0.1", "localhost", "::1") and not os.environ.get("OSAI_TOKEN"):
        sys.exit("Refusing to listen on a network interface without OSAI_TOKEN set: anyone reaching the port would control this computer.")

    import uvicorn
    from .server import create_app

    url = f"http://{'127.0.0.1' if args.host in ('0.0.0.0', '::') else args.host}:{args.port}/"
    if _server_running(url):
        # Already running (e.g. the desktop session restarted): just open the screens on it.
        if not args.no_browser:
            _open_screen(url, args.kiosk, args.mini)
        print(f"OSAI already running at {url}")
        return
    if not args.no_browser:
        threading.Thread(target=_open_screen, args=(url, args.kiosk, args.mini), daemon=True).start()
    print(f"OSAI running at {url}")
    uvicorn.run(create_app(), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
