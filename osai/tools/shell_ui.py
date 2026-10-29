"""Tools that control OSAI itself: its avatar, look, on-screen cards, memory, timers, settings."""
from __future__ import annotations

import datetime as dt

from ..config import AVATAR_ACCESSORIES, AVATAR_EYES, AVATAR_FORMS, WALLPAPERS
from .base import ToolError, tool


@tool(
    "customize_avatar",
    "Change how you (the on-screen avatar) look. Only include fields to change. "
    f"form: {AVATAR_FORMS}; eyes: {AVATAR_EYES}; accessory: {AVATAR_ACCESSORIES}; primary/accent: hex colors; "
    "mouth: bool; glow: 0-1.5; size: 0.5-1.6; personality: short description of how you talk.",
    {
        "form": {"type": "string", "enum": AVATAR_FORMS}, "eyes": {"type": "string", "enum": AVATAR_EYES},
        "accessory": {"type": "string", "enum": AVATAR_ACCESSORIES},
        "primary": {"type": "string"}, "accent": {"type": "string"}, "mouth": {"type": "boolean"},
        "glow": {"type": "number"}, "size": {"type": "number"}, "personality": {"type": "string"},
    },
    category="osai",
)
def customize_avatar(ctx, **changes):
    if not changes:
        raise ToolError("no changes given")
    errs = ctx.config.avatar_patch_errors(changes)
    if errs:
        raise ToolError("; ".join(errs))
    if "size" in changes:
        changes["size"] = max(0.5, min(1.6, float(changes["size"])))
    if "glow" in changes:
        changes["glow"] = max(0.0, min(1.5, float(changes["glow"])))
    data = ctx.config.update({"avatar": changes})
    ctx.notify({"type": "config", "config": ctx.config.public()})
    return f"avatar updated: {data['avatar']}"


@tool(
    "customize_screen",
    f"Change OSAI's screen: wallpaper ({WALLPAPERS}), accent color (hex), show/hide the clock, or rename yourself "
    "(assistant_name) / set what to call the user (user_name).",
    {"wallpaper": {"type": "string", "enum": WALLPAPERS}, "accent": {"type": "string"}, "clock": {"type": "boolean"},
     "assistant_name": {"type": "string"}, "user_name": {"type": "string"}},
    category="osai",
)
def customize_screen(ctx, wallpaper: str | None = None, accent: str | None = None, clock: bool | None = None,
                     assistant_name: str | None = None, user_name: str | None = None):
    patch: dict = {"theme": {}}
    if wallpaper:
        patch["theme"]["wallpaper"] = wallpaper
    if accent:
        patch["theme"]["accent"] = accent
    if clock is not None:
        patch["theme"]["clock"] = clock
    if assistant_name:
        patch["assistant_name"] = assistant_name.strip()[:24]
    if user_name is not None:
        patch["user_name"] = user_name.strip()[:40]
    ctx.config.update(patch)
    ctx.notify({"type": "config", "config": ctx.config.public()})
    return "screen updated"


@tool(
    "show_card",
    "Put a card on OSAI's screen to show information visually (lists, tables, weather, results, instructions). "
    "Body is Markdown. Cards replace the previous one with the same title.",
    {"title": {"type": "string"}, "body": {"type": "string"}, "image_url": {"type": "string"}},
    ["title", "body"],
    category="osai",
)
def show_card(ctx, title: str, body: str, image_url: str = ""):
    ctx.notify({"type": "card", "title": title, "body": body, "image_url": image_url})
    return "card shown"


@tool(
    "remember",
    "Save a lasting fact about the user or their preferences (e.g. 'prefers Firefox', 'sister is called Ana'). "
    "Use when the user tells you something worth keeping, or says 'remember'.",
    {"fact": {"type": "string"}},
    ["fact"],
    category="osai",
)
def remember(ctx, fact: str):
    ctx.services["memory"].add(fact.strip())
    return "remembered"


@tool(
    "forget",
    "Delete remembered facts containing some text.",
    {"text": {"type": "string"}},
    ["text"],
    category="osai",
)
def forget(ctx, text: str):
    n = ctx.services["memory"].remove(text)
    return f"forgot {n} fact(s)"


def _parse_when(in_seconds: float | None, at: str | None) -> dt.datetime:
    now = dt.datetime.now()
    if in_seconds is not None:
        return now + dt.timedelta(seconds=float(in_seconds))
    if at:
        try:
            t = dt.datetime.fromisoformat(at)
        except ValueError:
            try:
                hm = dt.time.fromisoformat(at)
            except ValueError:
                raise ToolError("'at' must be ISO like 2026-10-29T07:30 or 07:30")
            t = dt.datetime.combine(now.date(), hm)
            if t <= now:
                t += dt.timedelta(days=1)
        return t
    raise ToolError("give in_seconds or at")


@tool(
    "schedule",
    "Set a timer, reminder, or scheduled task. kind='reminder' announces the label at that time; kind='task' makes "
    "you carry out the label as an instruction at that time (e.g. 'read me the news headlines'). "
    "Use in_seconds for relative times or at (local ISO datetime or HH:MM). repeat='daily' repeats.",
    {"label": {"type": "string"}, "in_seconds": {"type": "number"}, "at": {"type": "string"},
     "kind": {"type": "string", "enum": ["reminder", "task"]}, "repeat": {"type": "string", "enum": ["none", "daily"]}},
    ["label"],
    category="osai",
)
def schedule(ctx, label: str, in_seconds: float | None = None, at: str | None = None, kind: str = "reminder",
             repeat: str = "none"):
    when = _parse_when(in_seconds, at)
    item = ctx.services["scheduler"].add(when, label, kind, repeat)
    ctx.notify({"type": "schedule", "items": ctx.services["scheduler"].items})
    return f"scheduled {kind} '{label}' for {when.strftime('%a %H:%M:%S')} (id {item['id']})"


@tool(
    "list_schedule",
    "List pending timers, reminders and scheduled tasks.",
    category="osai",
)
def list_schedule(ctx):
    return ctx.services["scheduler"].items or "nothing scheduled"


@tool(
    "cancel_schedule",
    "Cancel a timer/reminder/task by id or exact label.",
    {"id_or_label": {"type": "string"}},
    ["id_or_label"],
    category="osai",
)
def cancel_schedule(ctx, id_or_label: str):
    ok = ctx.services["scheduler"].cancel(id_or_label)
    ctx.notify({"type": "schedule", "items": ctx.services["scheduler"].items})
    if not ok:
        raise ToolError("nothing matched")
    return "cancelled"


@tool(
    "osai_settings",
    "Change OSAI's own behaviour: autonomy ('ask' = confirm risky actions first, 'full' = never ask), effort "
    "('low' faster, 'high' more careful), voice options (rate, pitch, hands_free, wake_word).",
    {"autonomy": {"type": "string", "enum": ["ask", "full"]},
     "effort": {"type": "string", "enum": ["low", "medium", "high", "xhigh"]},
     "voice_rate": {"type": "number"}, "voice_pitch": {"type": "number"},
     "hands_free": {"type": "boolean"}, "wake_word": {"type": "string"}},
    risk=lambda a: "Lets OSAI act without asking for risky actions" if a.get("autonomy") == "full" else None,
    category="osai",
)
def osai_settings(ctx, autonomy: str | None = None, effort: str | None = None, voice_rate: float | None = None,
                  voice_pitch: float | None = None, hands_free: bool | None = None, wake_word: str | None = None):
    patch: dict = {"voice": {}}
    if autonomy:
        patch["autonomy"] = autonomy
    if effort:
        patch["effort"] = effort
    if voice_rate is not None:
        patch["voice"]["rate"] = max(0.5, min(2.0, voice_rate))
    if voice_pitch is not None:
        patch["voice"]["pitch"] = max(0.0, min(2.0, voice_pitch))
    if hands_free is not None:
        patch["voice"]["hands_free"] = hands_free
    if wake_word:
        patch["voice"]["wake_word"] = wake_word.lower().strip()
    ctx.config.update(patch)
    ctx.notify({"type": "config", "config": ctx.config.public()})
    return "settings updated"
