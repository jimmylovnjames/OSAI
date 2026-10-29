"""System settings and status: hardware info, volume, brightness, network, bluetooth, power, media, clipboard."""
from __future__ import annotations

import datetime as dt
import platform
import re
import time

import psutil

from ..osinfo import IS_LINUX, IS_MAC, IS_WINDOWS, as_quote, osascript, powershell, ps_quote, run, which
from .base import ToolError, tool


def status_snapshot() -> dict:
    """Small status used by the UI's top bar."""
    bat = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
    return {
        "time": dt.datetime.now().isoformat(timespec="seconds"),
        "cpu": psutil.cpu_percent(interval=None),
        "mem": psutil.virtual_memory().percent,
        "battery": None if not bat else {"percent": round(bat.percent), "plugged": bat.power_plugged},
        "online": any(s.isup for n, s in psutil.net_if_stats().items() if not n.startswith(("lo", "Loopback"))),
    }


@tool("system_info", "Current date/time, OS, CPU, memory, disks, battery, network interfaces and uptime.", category="system")
def system_info(ctx):
    vm = psutil.virtual_memory()
    disks = []
    for part in psutil.disk_partitions(all=False):
        try:
            u = psutil.disk_usage(part.mountpoint)
            disks.append(f"{part.mountpoint} {u.used / 2**30:.0f}/{u.total / 2**30:.0f}GB ({u.percent}%)")
        except (PermissionError, OSError):
            pass
    bat = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
    addrs = {n: [a.address for a in al if a.family.name in ("AF_INET",)] for n, al in psutil.net_if_addrs().items()}
    return {
        "now": dt.datetime.now().strftime("%A %d %B %Y, %H:%M:%S"),
        "timezone": time.tzname[time.daylight] if time.daylight else time.tzname[0],
        "os": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "cpu": f"{psutil.cpu_count()} cores, {psutil.cpu_percent(interval=0.3)}% busy",
        "memory": f"{vm.used / 2**30:.1f}/{vm.total / 2**30:.1f}GB ({vm.percent}%)",
        "disks": disks,
        "battery": None if not bat else f"{bat.percent:.0f}% {'charging' if bat.power_plugged else 'on battery'}",
        "network": {k: v for k, v in addrs.items() if v},
        "uptime_hours": round((time.time() - psutil.boot_time()) / 3600, 1),
    }


# ---------- audio ----------

def _get_volume() -> str:
    if IS_LINUX:
        if which("pactl"):
            _, v = run(["pactl", "get-sink-volume", "@DEFAULT_SINK@"])
            _, m = run(["pactl", "get-sink-mute", "@DEFAULT_SINK@"])
            pct = re.search(r"(\d+)%", v)
            return f"{pct.group(1) if pct else '?'}%{' (muted)' if 'yes' in m else ''}"
        if which("amixer"):
            _, out = run(["amixer", "get", "Master"])
            pct = re.search(r"\[(\d+)%\]", out)
            return f"{pct.group(1) if pct else '?'}%{' (muted)' if '[off]' in out else ''}"
    if IS_MAC:
        return osascript("output volume of (get volume settings)")[1] + "%"
    return "unknown"


@tool(
    "set_volume",
    "Set or change the speaker volume, or mute/unmute. Give level (0-100) for absolute, or change (+/-) for relative.",
    {"level": {"type": "integer"}, "change": {"type": "integer"}, "mute": {"type": "boolean"}},
    category="settings",
)
def set_volume(ctx, level: int | None = None, change: int | None = None, mute: bool | None = None):
    if IS_LINUX:
        if which("pactl"):
            if mute is not None:
                run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "1" if mute else "0"])
            if level is not None:
                run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{max(0, min(level, 150))}%"])
            elif change:
                run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{change:+d}%"])
        elif which("amixer"):
            if mute is not None:
                run(["amixer", "-q", "set", "Master", "mute" if mute else "unmute"])
            if level is not None:
                run(["amixer", "-q", "set", "Master", f"{level}%"])
            elif change:
                run(["amixer", "-q", "set", "Master", f"{abs(change)}%{'+' if change > 0 else '-'}"])
        else:
            raise ToolError("no audio control found (install pulseaudio-utils/pipewire-pulse or alsa-utils)")
    elif IS_MAC:
        if mute is not None:
            osascript(f"set volume output muted {'true' if mute else 'false'}")
        if level is not None:
            osascript(f"set volume output volume {level}")
        elif change:
            osascript(f"set volume output volume ((output volume of (get volume settings)) + {change})")
    elif IS_WINDOWS:
        # Without extra libraries: send volume keys (each step is 2%).
        keys = []
        if mute is not None:
            keys.append("[char]173")
        if level is not None:
            keys += ["[char]174"] * 50 + ["[char]175"] * (level // 2)
        elif change:
            keys += [("[char]175" if change > 0 else "[char]174")] * (abs(change) // 2)
        if keys:
            powershell("$w = New-Object -ComObject WScript.Shell; " + "; ".join(f"$w.SendKeys({k})" for k in keys))
        return "volume adjusted"
    return f"volume now {_get_volume()}"


# ---------- display ----------

@tool(
    "set_brightness",
    "Set screen brightness 0-100 (absolute) or change it relatively.",
    {"level": {"type": "integer"}, "change": {"type": "integer"}},
    category="settings",
)
def set_brightness(ctx, level: int | None = None, change: int | None = None):
    if level is None and change is None:
        raise ToolError("give level or change")
    if IS_LINUX:
        if which("brightnessctl"):
            arg = f"{max(1, level)}%" if level is not None else (f"+{change}%" if change > 0 else f"{-change}%-")
            code, out = run(["brightnessctl", "set", arg])
            if code == 0:
                return "brightness: " + out.splitlines()[-1] if out else "brightness set"
        if which("xrandr") and level is not None:
            _, out = run(["xrandr", "--listmonitors"])
            outputs = re.findall(r"\s(\S+)$", out, re.M)
            for o in outputs:
                run(["xrandr", "--output", o, "--brightness", f"{max(0.1, level / 100):.2f}"])
            return f"software brightness {level}% on {', '.join(outputs)}"
        raise ToolError("brightness control needs brightnessctl (laptops) or xrandr")
    if IS_MAC:
        if which("brightness") and level is not None:
            run(["brightness", f"{level / 100:.2f}"])
            return f"brightness {level}%"
        key = 144 if (change or 0) > 0 else 145
        for _ in range(max(1, abs(change or 10) // 6)):
            osascript(f'tell application "System Events" to key code {key}')
        return "brightness adjusted"
    target = level if level is not None else f"((Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness).CurrentBrightness + {change})"
    code, out = powershell(f"(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods).WmiSetBrightness(1, {target})")
    if code != 0:
        raise ToolError("this display does not support software brightness: " + out[:200])
    return "brightness set"


@tool(
    "set_dark_mode",
    "Switch the operating system between dark and light appearance.",
    {"dark": {"type": "boolean"}},
    ["dark"],
    category="settings",
)
def set_dark_mode(ctx, dark: bool):
    if IS_LINUX and which("gsettings"):
        run(["gsettings", "set", "org.gnome.desktop.interface", "color-scheme", "prefer-dark" if dark else "default"])
        run(["gsettings", "set", "org.gnome.desktop.interface", "gtk-theme", "Adwaita-dark" if dark else "Adwaita"])
    elif IS_MAC:
        osascript(f'tell application "System Events" to tell appearance preferences to set dark mode to {str(dark).lower()}')
    elif IS_WINDOWS:
        v = 0 if dark else 1
        key = r"HKCU:\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        powershell(f"Set-ItemProperty -Path '{key}' -Name AppsUseLightTheme -Value {v}; Set-ItemProperty -Path '{key}' -Name SystemUsesLightTheme -Value {v}")
    else:
        raise ToolError("no supported appearance setting found")
    return f"system appearance: {'dark' if dark else 'light'}"


# ---------- network ----------

@tool(
    "wifi",
    "Wi-Fi control: 'status', 'scan' for networks, 'connect' to an SSID (with password if needed), 'on', 'off'.",
    {"action": {"type": "string", "enum": ["status", "scan", "connect", "on", "off"]},
     "ssid": {"type": "string"}, "password": {"type": "string"}},
    ["action"],
    category="settings",
)
def wifi(ctx, action: str, ssid: str = "", password: str = ""):
    if IS_LINUX:
        if not which("nmcli"):
            raise ToolError("needs NetworkManager (nmcli)")
        argv = {
            "status": ["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device"],
            "scan": ["nmcli", "-f", "IN-USE,SSID,SIGNAL,SECURITY", "device", "wifi", "list", "--rescan", "yes"],
            "connect": ["nmcli", "device", "wifi", "connect", ssid] + (["password", password] if password else []),
            "on": ["nmcli", "radio", "wifi", "on"],
            "off": ["nmcli", "radio", "wifi", "off"],
        }[action]
        code, out = run(argv, timeout=45)
        return out or f"exit {code}"
    if IS_MAC:
        iface = "en0"
        if action == "status":
            return run(["networksetup", "-getairportnetwork", iface])[1]
        if action == "scan":
            return run(["system_profiler", "SPAirPortDataType"], timeout=40)[1][:4000]
        if action == "connect":
            return run(["networksetup", "-setairportnetwork", iface, ssid] + ([password] if password else []), timeout=45)[1] or "connected"
        return run(["networksetup", "-setairportpower", iface, action])[1] or f"wifi {action}"
    if action == "status":
        return run(["netsh", "wlan", "show", "interfaces"])[1]
    if action == "scan":
        return run(["netsh", "wlan", "show", "networks", "mode=bssid"])[1]
    if action == "connect":
        return run(["netsh", "wlan", "connect", f"name={ssid}"])[1]
    return run(["netsh", "interface", "set", "interface", "Wi-Fi", "enabled" if action == "on" else "disabled"])[1]


@tool(
    "bluetooth",
    "Bluetooth: 'status', 'on', 'off', 'devices' (paired), 'scan', 'connect'/'disconnect' a device by MAC or name.",
    {"action": {"type": "string", "enum": ["status", "on", "off", "devices", "scan", "connect", "disconnect"]},
     "device": {"type": "string"}},
    ["action"],
    category="settings",
)
def bluetooth(ctx, action: str, device: str = ""):
    if not IS_LINUX or not which("bluetoothctl"):
        if IS_MAC and which("blueutil"):
            m = {"status": ["--power"], "on": ["--power", "1"], "off": ["--power", "0"], "devices": ["--paired"],
                 "scan": ["--inquiry", "8"], "connect": ["--connect", device], "disconnect": ["--disconnect", device]}
            return run(["blueutil", *m[action]], timeout=30)[1] or "ok"
        raise ToolError("bluetooth control needs bluetoothctl (Linux) or blueutil (macOS)")
    if device and not re.fullmatch(r"([0-9A-F]{2}:){5}[0-9A-F]{2}", device, re.I):
        _, out = run(["bluetoothctl", "devices"])
        m = next((l.split()[1] for l in out.splitlines() if device.lower() in l.lower()), None)
        if not m:
            raise ToolError(f"no known device named {device}; scan first")
        device = m
    argv = {
        "status": ["bluetoothctl", "show"], "on": ["bluetoothctl", "power", "on"], "off": ["bluetoothctl", "power", "off"],
        "devices": ["bluetoothctl", "devices"], "scan": ["bluetoothctl", "--timeout", "10", "scan", "on"],
        "connect": ["bluetoothctl", "connect", device], "disconnect": ["bluetoothctl", "disconnect", device],
    }[action]
    return run(argv, timeout=30)[1] or "ok"


# ---------- power ----------

@tool(
    "power",
    "Lock the screen, sleep, log out, restart, or shut down the computer.",
    {"action": {"type": "string", "enum": ["lock", "sleep", "logout", "restart", "shutdown"]}},
    ["action"],
    risk=lambda a: None if a.get("action") == "lock" else f"This will {a.get('action')} the computer",
    category="settings",
)
def power(ctx, action: str):
    if IS_LINUX:
        cmds = {
            "lock": [["loginctl", "lock-session"], ["xdg-screensaver", "lock"], ["dm-tool", "lock"]],
            "sleep": [["systemctl", "suspend"]],
            "logout": [["loginctl", "terminate-user", __import__("getpass").getuser()]],
            "restart": [["systemctl", "reboot"]],
            "shutdown": [["systemctl", "poweroff"]],
        }[action]
        for c in cmds:
            if which(c[0]):
                code, out = run(c)
                if code == 0:
                    return f"{action}: ok"
        raise ToolError(f"could not {action}")
    if IS_MAC:
        script = {
            "lock": 'tell application "System Events" to keystroke "q" using {control down, command down}',
            "sleep": 'tell application "System Events" to sleep',
            "logout": 'tell application "System Events" to log out',
            "restart": 'tell application "System Events" to restart',
            "shutdown": 'tell application "System Events" to shut down',
        }[action]
        osascript(script)
        return f"{action}: ok"
    cmd = {
        "lock": ["rundll32.exe", "user32.dll,LockWorkStation"],
        "sleep": ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"],
        "logout": ["shutdown", "/l"], "restart": ["shutdown", "/r", "/t", "0"], "shutdown": ["shutdown", "/s", "/t", "0"],
    }[action]
    run(cmd)
    return f"{action}: ok"


# ---------- media ----------

@tool(
    "media_control",
    "Control whatever music/video is playing: play, pause, toggle, next, previous, stop, or 'status' (now playing).",
    {"action": {"type": "string", "enum": ["play", "pause", "toggle", "next", "previous", "stop", "status"]}},
    ["action"],
    category="media",
)
def media_control(ctx, action: str):
    if IS_LINUX:
        if not which("playerctl"):
            raise ToolError("needs playerctl")
        if action == "status":
            return run(["playerctl", "metadata", "--format", "{{status}}: {{artist}} - {{title}} ({{playerName}})"])[1]
        return run(["playerctl", {"toggle": "play-pause"}.get(action, action)])[1] or f"{action}: ok"
    if IS_MAC:
        verb = {"play": "play", "pause": "pause", "toggle": "playpause", "next": "next track",
                "previous": "previous track", "stop": "stop"}
        for app in ("Spotify", "Music"):
            code, running = osascript(f'application "{app}" is running')
            if running == "true":
                if action == "status":
                    return osascript(f'tell application "{app}" to (get artist of current track) & " - " & (get name of current track)')[1]
                osascript(f'tell application "{app}" to {verb[action]}')
                return f"{app}: {action}"
        raise ToolError("no Music or Spotify running")
    if action == "status":
        raise ToolError("now-playing status is not available on Windows yet")
    key = {"play": 179, "pause": 179, "toggle": 179, "next": 176, "previous": 177, "stop": 178}[action]
    powershell(f"(New-Object -ComObject WScript.Shell).SendKeys([char]{key})")
    return f"{action}: ok"


# ---------- clipboard & notifications ----------

@tool(
    "clipboard",
    "Read the clipboard, or write text to it.",
    {"action": {"type": "string", "enum": ["read", "write"]}, "text": {"type": "string"}},
    ["action"],
    category="system",
)
def clipboard(ctx, action: str, text: str = ""):
    try:
        import pyperclip  # type: ignore
        if action == "read":
            return pyperclip.paste() or "(clipboard empty)"
        pyperclip.copy(text)
        return "copied to clipboard"
    except Exception:
        pass
    if IS_MAC:
        return run(["pbpaste"])[1] if action == "read" else (run(["pbcopy"], input=text), "copied")[1]
    if IS_WINDOWS:
        if action == "read":
            return powershell("Get-Clipboard")[1]
        powershell(f"Set-Clipboard -Value {ps_quote(text)}")
        return "copied"
    for read_cmd, write_cmd in ((["wl-paste"], ["wl-copy"]), (["xclip", "-o", "-selection", "clipboard"], ["xclip", "-selection", "clipboard"]),
                                (["xsel", "-ob"], ["xsel", "-ib"])):
        if which(read_cmd[0]):
            if action == "read":
                return run(read_cmd)[1]
            run(write_cmd, input=text)
            return "copied"
    raise ToolError("no clipboard tool (install wl-clipboard or xclip)")


@tool(
    "os_notification",
    "Show a native desktop notification (outside OSAI's own screen).",
    {"title": {"type": "string"}, "message": {"type": "string"}},
    ["title", "message"],
    category="system",
)
def os_notification(ctx, title: str, message: str):
    if IS_LINUX and which("notify-send"):
        run(["notify-send", title, message])
    elif IS_MAC:
        osascript(f'display notification {as_quote(message)} with title {as_quote(title)}')
    elif IS_WINDOWS:
        powershell(
            "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null;"
            "$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
            f"$x = $t.GetElementsByTagName('text'); $x.Item(0).InnerText = {ps_quote(title)}; $x.Item(1).InnerText = {ps_quote(message)};"
            "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('OSAI').Show([Windows.UI.Notifications.ToastNotification]::new($t))")
    else:
        raise ToolError("no notification service found")
    return "notification shown"

