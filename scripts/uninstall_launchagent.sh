#!/usr/bin/env bash
# Uninstall the personal assistant LaunchAgent.
set -euo pipefail

PLIST_NAME="com.personalassistant.plist"
PLIST_DEST="$HOME/Library/LaunchAgents/$PLIST_NAME"

echo "=== Personal Assistant — LaunchAgent uninstall ==="

if [[ -f "$PLIST_DEST" ]]; then
    launchctl unload "$PLIST_DEST" 2>/dev/null || true
    rm "$PLIST_DEST"
    echo "Removed: $PLIST_DEST"
    echo "Agent unloaded and removed. It will not start on next login."
else
    echo "Nothing to remove ($PLIST_DEST not found)."
fi
