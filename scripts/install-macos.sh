#!/usr/bin/env bash
# Install OSAI on macOS and start it at login. Requires Python 3.10+ (python.org or `brew install python`).
set -euo pipefail
SRC=$(cd "$(dirname "$0")/.." && pwd)
PREFIX="$HOME/Library/Application Support/OSAI"
mkdir -p "$PREFIX"
python3 -m venv "$PREFIX/venv"
"$PREFIX/venv/bin/pip" install -q --upgrade pip
"$PREFIX/venv/bin/pip" install -q "$SRC[browser,desktop]"
"$PREFIX/venv/bin/playwright" install chromium
command -v brew >/dev/null && brew install --quiet blueutil cliclick brightness 2>/dev/null || true

PLIST="$HOME/Library/LaunchAgents/ai.osai.plist"
mkdir -p "$(dirname "$PLIST")"
cat > "$PLIST" <<EOT
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>ai.osai</string>
  <key>ProgramArguments</key><array><string>$PREFIX/venv/bin/osai</string><string>--mini</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
  <key>StandardOutPath</key><string>$PREFIX/osai.log</string>
  <key>StandardErrorPath</key><string>$PREFIX/osai.log</string>
</dict></plist>
EOT
launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
echo "OSAI installed and running. It starts automatically at login."
echo "macOS will ask to allow Accessibility, Screen Recording and Microphone access for Terminal/Python - allow them so OSAI can operate apps."
