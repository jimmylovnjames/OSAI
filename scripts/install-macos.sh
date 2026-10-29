#!/usr/bin/env bash
# Install OSAI on macOS and start it at login.
# Uses a free local model from Ollama when one with tool calling is installed; otherwise add an
# Anthropic key in OSAI's Settings -> Brain.
set -euo pipefail
SRC=$(cd "$(dirname "$0")/.." && pwd)
PREFIX="$HOME/Library/Application Support/OSAI"

# macOS's built-in python3 can be 3.9; OSAI needs 3.10+.
PY=""
for c in python3.13 python3.12 python3.11 python3.10 /opt/homebrew/bin/python3 /usr/local/bin/python3 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
    PY=$(command -v "$c"); break
  fi
done
if [ -z "$PY" ]; then
  echo "OSAI needs Python 3.10 or newer. Install it with:  brew install python@3.12   (or from python.org), then rerun."
  exit 1
fi
echo "==> Using $($PY --version) at $PY"

mkdir -p "$PREFIX"
"$PY" -m venv "$PREFIX/venv"
"$PREFIX/venv/bin/pip" install -q --upgrade pip
"$PREFIX/venv/bin/pip" install -q "$SRC[browser,desktop]"
echo "==> Installing OSAI's browser"
"$PREFIX/venv/bin/playwright" install chromium
command -v brew >/dev/null && brew install --quiet blueutil cliclick brightness 2>/dev/null || true

echo "==> Choosing a free local AI from Ollama"
if "$PREFIX/venv/bin/python" -m osai.ollama_setup; then
  :
else
  echo "   (No usable Ollama model set up; OSAI will ask for an Anthropic key, or rerun:"
  echo "    \"$PREFIX/venv/bin/python\" -m osai.ollama_setup   after adding a model to Ollama.)"
fi

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
echo
echo "OSAI is installed and running, and starts automatically at login."
echo "macOS will ask to allow Accessibility, Screen Recording and Microphone access - allow them so OSAI can operate apps."
echo "Log: $PREFIX/osai.log"
