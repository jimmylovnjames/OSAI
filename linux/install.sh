#!/usr/bin/env bash
# Install OSAI on an existing Linux system (Ubuntu/Debian, Fedora, Arch, openSUSE).
#   sudo linux/install.sh            -> adds an "OSAI" session to your login screen
#   sudo linux/install.sh --autostart -> also starts OSAI (mini avatar) inside your normal desktop at login
#   sudo linux/install.sh --local-ai  -> also sets up the free on-device AI (Qwen3.5, no API key needed)
set -euo pipefail
SRC=$(cd "$(dirname "$0")/.." && pwd)
PREFIX=/opt/osai
AUTOSTART=0 LOCAL_AI=0
for a in "$@"; do
  case "$a" in --autostart) AUTOSTART=1 ;; --local-ai) LOCAL_AI=1 ;; esac
done
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
TARGET_USER=${SUDO_USER:-$(logname 2>/dev/null || echo root)}

echo "==> Installing system packages"
if command -v apt-get >/dev/null; then
  apt-get update -q
  DEBIAN_FRONTEND=noninteractive apt-get install -y -q python3 python3-venv python3-pip python3-tk openbox picom dunst \
    wmctrl xdotool xclip libnotify-bin pulseaudio-utils alsa-utils playerctl brightnessctl espeak-ng poppler-utils \
    network-manager bluez x11-xserver-utils curl xterm || true
elif command -v dnf >/dev/null; then
  dnf install -y python3 python3-pip python3-tkinter openbox picom dunst wmctrl xdotool xclip libnotify pulseaudio-utils \
    alsa-utils playerctl brightnessctl espeak-ng poppler-utils NetworkManager bluez xsetroot curl xterm || true
elif command -v pacman >/dev/null; then
  pacman -Sy --noconfirm --needed python python-pip tk openbox picom dunst wmctrl xdotool xclip libnotify libpulse alsa-utils \
    playerctl brightnessctl espeak-ng poppler networkmanager bluez-utils xorg-xsetroot curl xterm || true
elif command -v zypper >/dev/null; then
  zypper -n install python3 python3-pip python3-tk openbox picom dunst wmctrl xdotool xclip libnotify-tools pulseaudio-utils \
    alsa-utils playerctl brightnessctl espeak-ng poppler-tools NetworkManager bluez curl xterm || true
else
  echo "Unknown package manager: install python3, openbox, wmctrl, xdotool, playerctl, brightnessctl, espeak-ng yourself."
fi

echo "==> Installing OSAI to $PREFIX"
mkdir -p "$PREFIX/src"
tar -C "$SRC" --exclude=./build --exclude=./.git --exclude='__pycache__' -cf - . | tar -C "$PREFIX/src" -xf -
python3 -m venv "$PREFIX/venv"
"$PREFIX/venv/bin/pip" install -q --upgrade pip
"$PREFIX/venv/bin/pip" install -q "$PREFIX/src[browser,desktop,voice]"
export PLAYWRIGHT_BROWSERS_PATH="$PREFIX/browsers"
"$PREFIX/venv/bin/playwright" install chromium
command -v apt-get >/dev/null && "$PREFIX/venv/bin/playwright" install-deps chromium || true
chmod -R a+rX "$PREFIX"
ln -sf "$PREFIX/venv/bin/osai" /usr/local/bin/osai

echo "==> Registering the OSAI login session"
mkdir -p /usr/share/xsessions
cp "$PREFIX/src/linux/osai.desktop" /usr/share/xsessions/osai.desktop

if [ "$AUTOSTART" = 1 ]; then
  H=$(getent passwd "$TARGET_USER" | cut -d: -f6)
  mkdir -p "$H/.config/autostart"
  cat > "$H/.config/autostart/osai.desktop" <<EOT
[Desktop Entry]
Type=Application
Name=OSAI
Exec=env PLAYWRIGHT_BROWSERS_PATH=$PREFIX/browsers $PREFIX/venv/bin/osai --mini
X-GNOME-Autostart-enabled=true
EOT
  chown -R "$TARGET_USER" "$H/.config/autostart"
fi

ln -sf "$PREFIX/src/linux/osai-local-ai" /usr/local/bin/osai-local-ai
if [ "$LOCAL_AI" = 1 ]; then
  echo "==> Setting up the free local AI"
  "$PREFIX/src/linux/osai-local-ai"
fi

echo
echo "Done. Log out and pick the \"OSAI\" session on the login screen, or run:  osai"
echo "Free local AI: sudo osai-local-ai (if you didn't pass --local-ai). Or add an Anthropic key in Settings for Claude."
