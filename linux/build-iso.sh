#!/usr/bin/env bash
# Build a bootable OSAI live ISO (Ubuntu 24.04 base, boots on BIOS and UEFI PCs, USB or DVD).
#
#   sudo linux/build-iso.sh                      # full image with all hardware firmware
#   sudo FLAVOR=vm linux/build-iso.sh            # small image for virtual machines
#   sudo CHROMIUM_DIR=/path/chromium-1234 ...    # bundle an existing Playwright Chromium instead of downloading
#
# Needs: debootstrap squashfs-tools xorriso grub-pc-bin grub-efi-amd64-bin mtools (Ubuntu/Debian host), ~8 GB disk.
set -euo pipefail

SRC=$(cd "$(dirname "$0")/.." && pwd)
WORK=${WORK:-$SRC/build/iso}
OUT=${OUT:-$SRC/build/osai-$(date +%Y%m%d).iso}
SUITE=${SUITE:-noble}
MIRROR=${MIRROR:-http://archive.ubuntu.com/ubuntu}
FLAVOR=${FLAVOR:-full}
CHROMIUM_DIR=${CHROMIUM_DIR:-}
WHISPER_MODEL=${WHISPER_MODEL-base.en}  # set empty to skip
ROOT=$WORK/chroot

[ "$(id -u)" = 0 ] || { echo "run as root (sudo)"; exit 1; }
for t in debootstrap mksquashfs grub-mkrescue xorriso mformat; do
  command -v $t >/dev/null || { echo "missing $t: apt-get install debootstrap squashfs-tools xorriso grub-pc-bin grub-efi-amd64-bin mtools"; exit 1; }
done

log() { printf '\n\033[1;35m==> %s\033[0m\n' "$*"; }

umount_all() {
  for m in run dev/pts dev sys proc; do mountpoint -q "$ROOT/$m" && umount -lf "$ROOT/$m" || true; done
}
trap umount_all EXIT

if [ ! -f "$ROOT/.debootstrap-done" ]; then
  log "Bootstrapping Ubuntu $SUITE into $ROOT"
  rm -rf "$ROOT"; mkdir -p "$ROOT"
  debootstrap --arch=amd64 --components=main,universe "$SUITE" "$ROOT" "$MIRROR"
  touch "$ROOT/.debootstrap-done"
fi

log "Preparing chroot"
mount -t proc proc "$ROOT/proc"
mount -t sysfs sys "$ROOT/sys"
mount --bind /dev "$ROOT/dev"
mount --bind /dev/pts "$ROOT/dev/pts"
mount -t tmpfs tmpfs "$ROOT/run"
# Ubuntu's resolv.conf is a symlink into /run (now an empty tmpfs): replace it with a real file for the build.
rm -f "$ROOT/etc/resolv.conf"
cp -L /etc/resolv.conf "$ROOT/etc/resolv.conf"
# Make the build host's CA bundle / pip config visible inside (needed behind TLS-inspecting proxies).
for v in SSL_CERT_FILE REQUESTS_CA_BUNDLE PIP_CERT CURL_CA_BUNDLE PIP_CONFIG_FILE; do
  f=${!v:-}
  if [ -n "$f" ] && [ -f "$f" ]; then mkdir -p "$ROOT$(dirname "$f")"; cp "$f" "$ROOT$f"; fi
done

log "Copying OSAI into the image"
rm -rf "$ROOT/opt/osai/src"; mkdir -p "$ROOT/opt/osai/src"
tar -C "$SRC" --exclude=./build --exclude=./.git --exclude='__pycache__' --exclude=.pytest_cache -cf - . | tar -C "$ROOT/opt/osai/src" -xf -
if [ -n "$CHROMIUM_DIR" ]; then
  mkdir -p "$ROOT/opt/osai/browsers"
  cp -a "$CHROMIUM_DIR" "$ROOT/opt/osai/browsers/"
fi

KERNEL=linux-generic
[ "$FLAVOR" = vm ] && KERNEL=linux-image-virtual

log "Installing packages and OSAI inside the image"
chroot "$ROOT" /usr/bin/env -i HOME=/root PATH=/usr/sbin:/usr/bin:/sbin:/bin TERM=xterm LANG=C.UTF-8 \
  DEBIAN_FRONTEND=noninteractive MIRROR="$MIRROR" SUITE="$SUITE" KERNEL="$KERNEL" WHISPER_MODEL="$WHISPER_MODEL" \
  http_proxy="${http_proxy:-}" https_proxy="${https_proxy:-}" no_proxy="${no_proxy:-}" \
  HTTPS_PROXY="${HTTPS_PROXY:-}" NO_PROXY="${NO_PROXY:-}" SSL_CERT_FILE="${SSL_CERT_FILE:-}" \
  REQUESTS_CA_BUNDLE="${REQUESTS_CA_BUNDLE:-}" PIP_CERT="${PIP_CERT:-}" PIP_CONFIG_FILE="${PIP_CONFIG_FILE:-}" \
  /bin/bash -euo pipefail <<'CHROOT'
cat > /etc/apt/sources.list <<EOF
deb $MIRROR $SUITE main restricted universe multiverse
deb $MIRROR $SUITE-updates main restricted universe multiverse
deb $MIRROR $SUITE-security main restricted universe multiverse
EOF
echo osai > /etc/hostname
apt-get update -q
apt-get install -y -q --no-install-recommends \
  systemd-sysv dbus dbus-user-session udev kmod sudo locales tzdata ca-certificates curl less nano \
  casper initramfs-tools "$KERNEL" \
  xserver-xorg-core xserver-xorg-legacy xserver-xorg-input-libinput xserver-xorg-video-fbdev xserver-xorg-video-vesa \
  xinit xauth x11-xserver-utils x11-utils x11-xkb-utils openbox picom dunst wmctrl xdotool xclip libnotify-bin xterm \
  fonts-noto-core fonts-noto-color-emoji fonts-dejavu-core \
  network-manager wpasupplicant iw bluez pipewire pipewire-pulse wireplumber pulseaudio-utils alsa-utils \
  playerctl brightnessctl espeak-ng poppler-utils pcmanfm mpv flatpak \
  python3 python3-venv python3-pip python3-tk
locale-gen en_US.UTF-8 >/dev/null || true

# Chromium's sandbox needs unprivileged user namespaces, which Ubuntu 24.04 restricts by default.
echo "kernel.apparmor_restrict_unprivileged_userns=0" > /etc/sysctl.d/60-osai-chromium.conf

# Let a normal user start X from the console.
printf 'allowed_users=anybody\nneeds_root_rights=yes\n' > /etc/X11/Xwrapper.config

# OSAI itself, in its own virtualenv.
python3 -m venv /opt/osai/venv
/opt/osai/venv/bin/pip install -q --upgrade pip
/opt/osai/venv/bin/pip install -q "/opt/osai/src[browser,desktop,voice]"
export PLAYWRIGHT_BROWSERS_PATH=/opt/osai/browsers
if ! ls -d /opt/osai/browsers/chromium-* >/dev/null 2>&1; then
  /opt/osai/venv/bin/playwright install chromium || echo "WARNING: could not download Chromium; the OSAI screen needs a browser"
fi
/opt/osai/venv/bin/playwright install-deps chromium || true
chmod -R a+rX /opt/osai
# Optional: bake the speech-recognition model so voice works offline on first boot.
if [ -n "$WHISPER_MODEL" ]; then
  HF_HOME=/opt/osai/hf /opt/osai/venv/bin/python -c "from faster_whisper import WhisperModel; WhisperModel('$WHISPER_MODEL', compute_type='int8')" \
    && chmod -R a+rX /opt/osai/hf || echo "WARNING: whisper model not baked in; it downloads on first use"
fi
ln -sf /opt/osai/venv/bin/osai /usr/local/bin/osai

# Live user "osai" (created by casper at boot) logs in on tty1 and starts the OSAI session.
cat > /etc/casper.conf <<EOF
export USERNAME="osai"
export USERFULLNAME="OSAI"
export HOST="osai"
export BUILD_SYSTEM="Ubuntu"
export FLAVOUR="OSAI"
EOF
mkdir -p /etc/systemd/system/getty@tty1.service.d
cat > /etc/systemd/system/getty@tty1.service.d/autologin.conf <<EOF
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin osai --noclear %I \$TERM
EOF
cat >> /etc/skel/.profile <<'EOF'

# OSAI: start the AI desktop on the first console.
if [ -z "$DISPLAY" ] && [ "$(tty)" = /dev/tty1 ]; then
  export HF_HOME=/opt/osai/hf
  exec startx /opt/osai/src/linux/osai-session -- -nolisten tcp >"$HOME/.osai-x.log" 2>&1
fi
EOF
mkdir -p /usr/share/xsessions && cp /opt/osai/src/linux/osai.desktop /usr/share/xsessions/
# Ubuntu leaves wired devices unmanaged unless netplan hands them to NetworkManager, and points DNS at
# systemd-resolved (not in this image): without these, wired networking stays down on the live system.
touch /etc/NetworkManager/conf.d/10-globally-managed-devices.conf
printf '[main]\ndns=default\nrc-manager=file\n' > /etc/NetworkManager/conf.d/90-osai.conf
systemctl enable NetworkManager >/dev/null 2>&1 || true

update-initramfs -u
apt-get clean
rm -f /etc/resolv.conf  # NetworkManager writes the real one at boot
rm -rf /var/lib/apt/lists/* /tmp/* /root/.cache
CHROOT

umount_all

log "Building the boot media"
ISO=$WORK/iso
rm -rf "$ISO"; mkdir -p "$ISO/casper" "$ISO/boot/grub" "$ISO/.disk"
cp -L "$ROOT/boot/vmlinuz" "$ISO/casper/vmlinuz"
cp -L "$ROOT/boot/initrd.img" "$ISO/casper/initrd"
echo "OSAI $(date +%Y-%m-%d) (Ubuntu $SUITE base)" > "$ISO/.disk/info"
# -e must be the last option: everything after it is an exclude pattern.
mksquashfs "$ROOT" "$ISO/casper/filesystem.squashfs" -noappend -comp zstd -wildcards \
  -e boot .debootstrap-done 'proc/*' 'sys/*' 'dev/*' 'run/*' 'tmp/*'
du -sx --block-size=1 "$ROOT" | cut -f1 > "$ISO/casper/filesystem.size"
cat > "$ISO/boot/grub/grub.cfg" <<'EOF'
set timeout=3
set default=0
insmod all_video
menuentry "OSAI" {
  linux /casper/vmlinuz boot=casper quiet splash ---
  initrd /casper/initrd
}
menuentry "OSAI (keep my changes on this USB - needs a 'writable' partition)" {
  linux /casper/vmlinuz boot=casper persistent quiet splash ---
  initrd /casper/initrd
}
menuentry "OSAI (safe graphics)" {
  linux /casper/vmlinuz boot=casper nomodeset ---
  initrd /casper/initrd
}
EOF
mkdir -p "$(dirname "$OUT")"
grub-mkrescue -o "$OUT" "$ISO" -- -volid OSAI
log "Done: $OUT ($(du -h "$OUT" | cut -f1))"
echo "Write it to a USB stick:  sudo dd if=$OUT of=/dev/sdX bs=4M status=progress oflag=sync"
