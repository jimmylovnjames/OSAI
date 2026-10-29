import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from osai import installer

LSBLK = json.dumps({"blockdevices": [
    {"name": "sda", "path": "/dev/sda", "size": 256 * 1024**3, "model": "Samsung SSD 860 ", "tran": "sata", "rm": False, "ro": False, "type": "disk"},
    {"name": "sdb", "path": "/dev/sdb", "size": 32 * 1024**3, "model": "USB Flash", "tran": "usb", "rm": True, "ro": False, "type": "disk"},
    {"name": "sdc", "path": "/dev/sdc", "size": 8 * 1024**3, "model": None, "tran": "usb", "rm": True, "ro": False, "type": "disk"},
    {"name": "sr0", "path": "/dev/sr0", "size": 1024**3, "model": "DVD", "tran": "sata", "rm": True, "ro": True, "type": "rom"},
    {"name": "zram0", "path": "/dev/zram0", "size": 4 * 1024**3, "model": None, "tran": None, "rm": False, "ro": False, "type": "disk"},
]})


def test_parse_disks_marks_unusable_ones():
    disks = {d["name"]: d for d in installer.parse_disks(LSBLK, boot_disks={"/dev/sdb"})}
    assert set(disks) == {"sda", "sdb", "sdc"}  # no CD drive, no zram
    assert disks["sda"]["usable"] and disks["sda"]["model"] == "Samsung SSD 860" and disks["sda"]["size_label"] == "275 GB"
    assert not disks["sdb"]["usable"] and "started from" in disks["sdb"]["problem"]
    assert not disks["sdc"]["usable"] and "Too small" in disks["sdc"]["problem"]


def _req(**over):
    base = {"disk": "/dev/sda", "confirm": "sda", "username": "ana", "fullname": "Ana", "password": "pw12",
            "password2": "pw12", "hostname": "ana-osai", "timezone": "UTC"}
    return base | over


@pytest.mark.skipif(not Path("/usr/share/zoneinfo/UTC").exists(), reason="needs tzdata")
def test_validate():
    disks = installer.parse_disks(LSBLK, boot_disks={"/dev/sdb"})
    assert installer.validate(_req(), disks) is None
    assert "Type sda" in installer.validate(_req(confirm="sdb"), disks)
    assert "can't be used" in installer.validate(_req(disk="/dev/sdb", confirm="sdb"), disks)
    assert "username" in installer.validate(_req(username="Root"), disks)
    assert "username" in installer.validate(_req(username="osai"), disks)
    assert "match" in installer.validate(_req(password2="other"), disks)
    assert "time zone" in installer.validate(_req(timezone="/etc/passwd"), disks)
    assert "time zone" in installer.validate(_req(timezone="../../etc/passwd"), disks)
    assert "computer name" in installer.validate(_req(hostname="bad name"), disks)


def test_build_argv_never_puts_password_on_command_line():
    argv = installer.build_argv(_req(autologin=True, copy_settings=True), dry_run=True)
    assert "pw12" not in argv
    assert "--autologin" in argv and "--dry-run" in argv and "--copy-settings" in argv
    assert "--passwordless-admin" not in argv
    assert argv[argv.index("--disk") + 1] == "/dev/sda"


SCRIPT = Path(__file__).resolve().parents[1] / "linux" / "osai-install"


@pytest.mark.skipif(os.name == "nt" or not shutil.which("lsblk") or os.geteuid() != 0 or not shutil.which("losetup"),
                    reason="needs root + losetup on Linux")
def test_script_dry_run_validates_without_touching_the_disk(tmp_path):
    img = tmp_path / "disk.img"
    with open(img, "wb") as f:
        f.truncate(13 * 1024**3)
    loop = subprocess.run(["losetup", "-f", "--show", str(img)], capture_output=True, text=True)
    if loop.returncode != 0:
        pytest.skip("no loop devices")
    dev = loop.stdout.strip()
    try:
        def run(*extra, pw="pw12"):
            return subprocess.run([str(SCRIPT), "--disk", dev, "--user", "ana", "--hostname", "ana-osai",
                                   "--timezone", "UTC", "--source", str(tmp_path), "--dry-run", *extra],
                                  input=pw + "\n", capture_output=True, text=True)
        ok = run()
        assert ok.returncode == 0 and "DONE" in ok.stdout, ok.stdout + ok.stderr
        assert run(pw="ab").stdout.startswith("ERROR password")
        bad = subprocess.run([str(SCRIPT), "--disk", dev, "--user", "Ana!", "--dry-run"], input="pw12\n",
                             capture_output=True, text=True)
        assert "ERROR username" in bad.stdout
        with open(img, "rb") as f:
            assert f.read(64 * 1024) == bytes(64 * 1024)  # no partition table written
    finally:
        subprocess.run(["losetup", "-d", dev])
