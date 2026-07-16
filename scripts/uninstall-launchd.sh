#!/bin/bash
set -euo pipefail

LABEL="com.sparrow.media"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$PLIST"
echo "Sparrow LaunchAgent removed. Media and Sparrow data were left untouched."
