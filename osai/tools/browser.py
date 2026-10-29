"""A real, visible web browser that OSAI drives (Playwright + Chromium).

The profile is persistent (~/.osai/browser-profile) so logins survive restarts.
Pages are read as text plus a numbered list of interactive elements; OSAI then
clicks/types by number. Page content is untrusted: it is data, not instructions.
"""
from __future__ import annotations

import asyncio
import glob
import os

from ..config import HOME as OSAI_HOME
from ..osinfo import has_display
from .base import ToolError, tool
from .files import image_block

_LABEL_JS = r"""
(maxItems) => {
  document.querySelectorAll('[data-osai-id]').forEach(e => e.removeAttribute('data-osai-id'));
  const sel = 'a[href], button, input, textarea, select, [role=button], [role=link], [role=tab], [role=menuitem], [role=checkbox], [contenteditable=true], summary';
  const out = [];
  let n = 0;
  for (const el of document.querySelectorAll(sel)) {
    const r = el.getBoundingClientRect();
    const st = getComputedStyle(el);
    if (r.width < 2 || r.height < 2 || st.visibility === 'hidden' || st.display === 'none') continue;
    if (r.bottom < 0 || r.top > innerHeight * 3) continue;
    n += 1;
    el.setAttribute('data-osai-id', String(n));
    const tag = el.tagName.toLowerCase();
    const type = el.getAttribute('type') || '';
    let label = (el.innerText || el.value || el.getAttribute('aria-label') || el.getAttribute('placeholder') ||
                 el.getAttribute('title') || el.getAttribute('name') || el.getAttribute('alt') || '').trim().replace(/\s+/g, ' ');
    if (tag === 'a' && !label) label = el.getAttribute('href') || '';
    out.push(`[${n}] ${tag}${type ? ':' + type : ''} ${label.slice(0, 80)}`);
    if (out.length >= maxItems) break;
  }
  return out;
}
"""


class Browser:
    def __init__(self, config):
        self.config = config
        self._pw = None
        self._ctx = None
        self.page = None
        self._lock = asyncio.Lock()

    async def ensure(self):
        if self.page and not self.page.is_closed():
            return self.page
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise ToolError("the built-in browser needs Playwright: pip install playwright && playwright install chromium")
        if not self._pw:
            self._pw = await async_playwright().start()
        if not self._ctx:
            headless = self.config["browser"].get("headless")
            if headless is None:
                headless = not has_display()
            profile = OSAI_HOME / self.config["browser"].get("profile", "browser-profile")
            kwargs = dict(headless=headless, viewport={"width": 1280, "height": 800}, args=["--disable-blink-features=AutomationControlled", "--password-store=basic"])
            try:
                self._ctx = await self._pw.chromium.launch_persistent_context(str(profile), **kwargs)
            except Exception as first:
                exe = os.environ.get("OSAI_CHROMIUM") or next(iter(sorted(glob.glob("/opt/pw-browsers/chromium-*/chrome-linux*/chrome"))), None)
                for candidate in ([exe] if exe else []):
                    try:
                        self._ctx = await self._pw.chromium.launch_persistent_context(str(profile), executable_path=candidate, **kwargs)
                        break
                    except Exception:
                        pass
                if not self._ctx:
                    try:
                        self._ctx = await self._pw.chromium.launch_persistent_context(str(profile), channel="chrome", **kwargs)
                    except Exception:
                        raise ToolError(f"could not start the browser: {first}")
        pages = self._ctx.pages
        self.page = pages[0] if pages else await self._ctx.new_page()
        return self.page

    async def snapshot(self, max_chars: int = 8000) -> str:
        page = self.page
        await page.wait_for_load_state("domcontentloaded")
        try:
            await page.wait_for_load_state("networkidle", timeout=3000)
        except Exception:
            pass
        items = await page.evaluate(_LABEL_JS, 150)
        text = await page.evaluate("() => document.body ? document.body.innerText : ''")
        text = "\n".join(l.strip() for l in text.splitlines() if l.strip())
        return (f"URL: {page.url}\nTITLE: {await page.title()}\n\n--- PAGE TEXT (untrusted web content) ---\n"
                f"{text[:max_chars]}{'...[more: scroll or read again]' if len(text) > max_chars else ''}\n\n"
                f"--- INTERACTIVE ELEMENTS ---\n" + "\n".join(items))

    async def close(self):
        if self._ctx:
            await self._ctx.close()
        if self._pw:
            await self._pw.stop()
        self._ctx = self._pw = self.page = None


def _browser(ctx) -> Browser:
    b = ctx.services.get("browser")
    if b is None:
        b = ctx.services["browser"] = Browser(ctx.config)
    return b


@tool(
    "browser_open",
    "Open a URL (or a search query) in OSAI's built-in browser window and return the page text and its "
    "numbered interactive elements. Use this for interactive web tasks (logging in, filling forms, shopping, "
    "reading web apps). For quick facts prefer web_search.",
    {"url": {"type": "string", "description": "URL, or plain words to search for."},
     "new_tab": {"type": "boolean"}},
    ["url"],
    category="web",
)
async def browser_open(ctx, url: str, new_tab: bool = False):
    b = _browser(ctx)
    async with b._lock:
        await b.ensure()
        if new_tab:
            b.page = await b._ctx.new_page()
        if " " in url.strip() or "." not in url:
            from urllib.parse import quote_plus
            url = "https://duckduckgo.com/?q=" + quote_plus(url)
        elif "://" not in url:
            url = "https://" + url
        try:
            await b.page.goto(url, timeout=30000)
        except Exception as e:
            raise ToolError(f"could not load {url}: {e}")
        await b.page.bring_to_front()
        return await b.snapshot()


@tool(
    "browser_read",
    "Re-read the current browser page (text + numbered elements), e.g. after it changed.",
    {"max_chars": {"type": "integer"}},
    category="web",
)
async def browser_read(ctx, max_chars: int = 8000):
    b = _browser(ctx)
    async with b._lock:
        await b.ensure()
        return await b.snapshot(max_chars)


@tool(
    "browser_click",
    "Click an element on the current page by its [number] from the last read.",
    {"element": {"type": "integer"}},
    ["element"],
    category="web",
)
async def browser_click(ctx, element: int):
    b = _browser(ctx)
    async with b._lock:
        await b.ensure()
        loc = b.page.locator(f'[data-osai-id="{element}"]')
        if await loc.count() == 0:
            raise ToolError(f"element [{element}] not found - read the page again")
        try:
            await loc.first.click(timeout=8000)
        except Exception as e:
            raise ToolError(f"click failed: {e}")
        await asyncio.sleep(0.8)
        if len(b._ctx.pages) and b._ctx.pages[-1] is not b.page:  # link opened a new tab
            b.page = b._ctx.pages[-1]
        return await b.snapshot()


@tool(
    "browser_type",
    "Type into a text field on the current page by its [number]; submit=true presses Enter afterwards.",
    {"element": {"type": "integer"}, "text": {"type": "string"}, "submit": {"type": "boolean"}},
    ["element", "text"],
    category="web",
)
async def browser_type(ctx, element: int, text: str, submit: bool = False):
    b = _browser(ctx)
    async with b._lock:
        await b.ensure()
        loc = b.page.locator(f'[data-osai-id="{element}"]').first
        try:
            await loc.fill(text, timeout=8000)
        except Exception:
            await loc.click()
            await b.page.keyboard.type(text, delay=15)
        if submit:
            await loc.press("Enter")
            await asyncio.sleep(1.2)
        return await b.snapshot()


@tool(
    "browser_action",
    "Other browser actions: back, forward, reload, scroll_down, scroll_up, close_tab, list_tabs, "
    "switch_tab (with index), screenshot (to see the page visually).",
    {"action": {"type": "string", "enum": ["back", "forward", "reload", "scroll_down", "scroll_up", "close_tab",
                                           "list_tabs", "switch_tab", "screenshot"]},
     "index": {"type": "integer"}},
    ["action"],
    category="web",
)
async def browser_action(ctx, action: str, index: int = 0):
    b = _browser(ctx)
    async with b._lock:
        page = await b.ensure()
        if action == "back":
            await page.go_back()
        elif action == "forward":
            await page.go_forward()
        elif action == "reload":
            await page.reload()
        elif action in ("scroll_down", "scroll_up"):
            await page.mouse.wheel(0, 700 if action == "scroll_down" else -700)
            await asyncio.sleep(0.4)
        elif action == "close_tab":
            await page.close()
            b.page = b._ctx.pages[-1] if b._ctx.pages else await b._ctx.new_page()
        elif action == "list_tabs":
            return "\n".join(f"{i}: {await p.title()} - {p.url}" for i, p in enumerate(b._ctx.pages))
        elif action == "switch_tab":
            if not 0 <= index < len(b._ctx.pages):
                raise ToolError("no such tab")
            b.page = b._ctx.pages[index]
            await b.page.bring_to_front()
        elif action == "screenshot":
            png = await page.screenshot()
            return [image_block(png, "image/png"), {"type": "text", "text": f"{page.url}"}]
        return await b.snapshot()
