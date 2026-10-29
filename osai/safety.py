"""Seatbelt for risky actions.

This is NOT a sandbox: a pattern list cannot make an LLM with shell access safe.
It catches the common destructive / irreversible / privileged commands so that in
autonomy="ask" mode the user gets a confirmation card before they run.
"""
from __future__ import annotations

import re

_RISKY_SHELL = [
    (r"(^|[;&|`(\s])(sudo|doas|pkexec|su)\s", "runs with administrator privileges"),
    (r"(^|[;&|`(\s])rm\s", "deletes files"),
    (r"(^|[;&|`(\s])(rmdir|shred|unlink|wipefs|truncate)\s", "deletes or wipes data"),
    (r"(^|[;&|`(\s])(mkfs|fdisk|parted|sfdisk|dd|diskutil|format)(\.|\s|$)", "touches disks or partitions"),
    (r"(^|[;&|`(\s])(shutdown|reboot|poweroff|halt|init\s+[06])\b", "powers off or restarts the machine"),
    (r"systemctl\s+(stop|disable|mask|poweroff|reboot|halt|suspend)", "changes system services or power state"),
    (r"(^|[;&|`(\s])(kill|pkill|killall|taskkill)\s", "kills processes"),
    (r"(^|[;&|`(\s])(chmod|chown|chgrp)\s+(-R|--recursive)", "recursively changes permissions"),
    (r"(curl|wget|iwr|Invoke-WebRequest)[^|]*\|\s*(sudo\s+)?(ba|z|da)?sh", "pipes a downloaded script into a shell"),
    (r"(^|[;&|`(\s])(apt|apt-get|dnf|yum|pacman|zypper|brew|winget|choco|snap|flatpak)\s+(install|remove|purge|uninstall|-S|-R)", "installs or removes software"),
    (r"pip3?\s+(install|uninstall)|npm\s+(i|install|uninstall)\s+-g", "installs or removes software"),
    (r"git\s+(push\s+.*(--force|-f)|reset\s+--hard|clean\s+-[a-z]*f)", "rewrites or discards git history/files"),
    (r"(Remove-Item|del\s|erase\s|rd\s|Format-Volume|Clear-Disk|Stop-Computer|Restart-Computer)", "destructive Windows command"),
    (r">\s*/dev/(sd|nvme|disk)", "writes directly to a disk device"),
    (r"(^|[;&|`(\s])crontab\s+-r", "removes scheduled jobs"),
    (r"(passwd|useradd|userdel|usermod|visudo)\b", "changes user accounts"),
    (r":\(\)\s*\{.*\};\s*:", "fork bomb"),
]

_COMPILED = [(re.compile(p, re.IGNORECASE), why) for p, why in _RISKY_SHELL]


def shell_risk(command: str) -> str | None:
    """Return a human-readable reason if a shell command needs confirmation."""
    for rx, why in _COMPILED:
        if rx.search(command):
            return f"This command {why}: `{command[:200]}`"
    return None
