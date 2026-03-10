#!/usr/bin/env bash
# Install the personal assistant as a macOS LaunchAgent.
# Runs: python main.py telegram — starts automatically at login, restarts on crash.
#
# Usage: ./scripts/install_launchagent.sh
set -euo pipefail

PLIST_NAME="com.personalassistant.plist"
PLIST_SRC="$(cd "$(dirname "$0")/.." && pwd)/$PLIST_NAME"
PLIST_DEST="$HOME/Library/LaunchAgents/$PLIST_NAME"
LOG_DIR="$HOME/Library/Logs/personalassistant"

echo "=== Personal Assistant — LaunchAgent install ==="
echo ""

# Verify .env exists
if [[ ! -f "$(dirname "$0")/../.env" ]]; then
    echo "ERROR: .env not found. Copy .env.example → .env and fill in your keys."
    exit 1
fi

# Verify venv exists
VENV_PYTHON="$(dirname "$0")/../.venv/bin/python3"
if [[ ! -f "$VENV_PYTHON" ]]; then
    echo "ERROR: .venv not found. Run: python3 -m venv .venv && .venv/bin/pip install -e ."
    exit 1
fi

# Create log directory
mkdir -p "$LOG_DIR"
echo "Log directory: $LOG_DIR"

# Unload existing agent if loaded
if launchctl list | grep -q "com.personalassistant" 2>/dev/null; then
    echo "Unloading existing agent..."
    launchctl unload "$PLIST_DEST" 2>/dev/null || true
fi

# Copy plist
cp "$PLIST_SRC" "$PLIST_DEST"
echo "Installed: $PLIST_DEST"

# Load it
launchctl load "$PLIST_DEST"
echo ""
echo "Agent loaded. The assistant will start now and on every login."
echo ""
echo "Useful commands:"
echo "  Check status:  launchctl list | grep personalassistant"
echo "  View logs:     tail -f $LOG_DIR/stdout.log"
echo "  View errors:   tail -f $LOG_DIR/stderr.log"
echo "  Uninstall:     ./scripts/uninstall_launchagent.sh"
