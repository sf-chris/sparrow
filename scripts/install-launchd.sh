#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.sparrow.media"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_DIR="$ROOT/data/logs"

mkdir -p "$HOME/Library/LaunchAgents" "$LOG_DIR"

# Generate the user-specific plist from fixed values. Quotes are XML-escaped by
# PlistBuddy when values are assigned.
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT
plutil -create xml1 "$TMP"
/usr/libexec/PlistBuddy -c 'Add :Label string com.sparrow.media' "$TMP"
/usr/libexec/PlistBuddy -c 'Add :ProgramArguments array' "$TMP"
/usr/libexec/PlistBuddy -c 'Add :ProgramArguments:0 string /bin/bash' "$TMP"
/usr/libexec/PlistBuddy -c "Add :ProgramArguments:1 string $ROOT/start.sh" "$TMP"
/usr/libexec/PlistBuddy -c "Add :WorkingDirectory string $ROOT" "$TMP"
/usr/libexec/PlistBuddy -c 'Add :RunAtLoad bool true' "$TMP"
/usr/libexec/PlistBuddy -c 'Add :KeepAlive bool true' "$TMP"
/usr/libexec/PlistBuddy -c 'Add :ThrottleInterval integer 10' "$TMP"
/usr/libexec/PlistBuddy -c "Add :StandardOutPath string $LOG_DIR/launchd.out.log" "$TMP"
/usr/libexec/PlistBuddy -c "Add :StandardErrorPath string $LOG_DIR/launchd.err.log" "$TMP"
/usr/libexec/PlistBuddy -c 'Add :EnvironmentVariables dict' "$TMP"
/usr/libexec/PlistBuddy -c 'Add :EnvironmentVariables:PATH string /usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin' "$TMP"
plutil -convert xml1 "$TMP" -o "$PLIST"
chmod 600 "$PLIST"

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
launchctl enable "gui/$(id -u)/$LABEL"
launchctl kickstart -k "gui/$(id -u)/$LABEL"

echo "Sparrow LaunchAgent installed: $PLIST"
echo "Logs: $LOG_DIR"
