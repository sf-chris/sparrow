#!/bin/sh
# Sparrow Node setup for Linux and macOS. It installs Sparrow Node on this
# computer with its own media tools and download app, pairs it with the Sparrow
# server below and keeps it running in the background. Nothing needs sudo or a
# password. Running it again on a paired computer updates it and keeps its
# folders. Folders are chosen afterwards in Sparrow. Made for one pairing: the
# code expires ten minutes after Sparrow showed it.
set -eu

SERVER='__SPARROW_SERVER__'
CODE='__SPARROW_CODE__'

say() { printf '%s\n' "$*"; }
report() {
  curl -fsS -m 10 -o /dev/null -X POST -H 'Content-Type: application/json' \
    -H 'X-Sparrow-Request: 1' -d "{\"stage\":\"$1\"}" \
    "$SERVER/api/v1/node/setup/$CODE/progress" 2>/dev/null || true
}
fail() {
  report "failed:$1"
  printf '\n%s\n' "$2" >&2
  exit 1
}
has() { command -v "$1" >/dev/null 2>&1; }

case "$(uname -s)" in
  Linux) system=linux ;;
  Darwin) system=macos ;;
  *) fail system "This setup is for Linux and macOS. On Windows, copy the Windows command from Sparrow." ;;
esac
has curl || fail system "This setup needs curl."
has tar || fail system "This setup needs tar."

owner="$(id -un)"
home="$HOME"
if [ "$(id -u)" -eq 0 ]; then
  [ "$system" = macos ] && fail system "Run this without sudo on a Mac."
  if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != root ]; then
    owner="$SUDO_USER"
    home="$(getent passwd "$owner" | cut -d: -f6)"
  fi
fi
if [ "$system" = macos ]; then
  dir="$home/Library/Application Support/SparrowNode"
elif [ "$owner" = root ]; then
  dir=/var/lib/sparrow-node
else
  dir="${XDG_DATA_HOME:-$home/.local/share}/SparrowNode"
  [ "$owner" != "$(id -un)" ] && dir="$home/.local/share/SparrowNode"
fi
mkdir -p "$dir"
report started
say "Setting up Sparrow Node in $dir"

# Stop a copy that is already running, so it can be replaced.
if [ "$system" = macos ]; then
  launchctl bootout "gui/$(id -u)/com.sparrow.node" >/dev/null 2>&1 || true
elif [ "$(id -u)" -eq 0 ] && has systemctl; then
  systemctl stop sparrow-node >/dev/null 2>&1 || true
else
  if [ -z "${XDG_RUNTIME_DIR:-}" ] && [ -d "/run/user/$(id -u)" ]; then
    XDG_RUNTIME_DIR="/run/user/$(id -u)"
    export XDG_RUNTIME_DIR
  fi
  systemctl --user stop sparrow-node >/dev/null 2>&1 || true
  pkill -f "$dir/app/sparrow-node.py" >/dev/null 2>&1 || true
fi

# Media tools and the download app come from the server, built for this kind
# of computer. Without a bundle (a Mac, or a server without one) the computer
# still pairs, and Sparrow shows what it's missing.
platform=""
if [ "$system" = linux ]; then
  case "$(uname -m)" in
    x86_64|amd64) platform=linux-x86_64 ;;
    aarch64|arm64) platform=linux-aarch64 ;;
  esac
fi

report installing
say "Downloading Sparrow Node from $SERVER"
rm -rf "$dir/app.new"
mkdir -p "$dir/app.new"
curl -fsSL "$SERVER/api/v1/node/setup/$CODE/package.tar.gz" | tar -xzf - -C "$dir/app.new" \
  || fail download "Couldn't download Sparrow Node from $SERVER. If the command expired, get a fresh one in Sparrow."
rm -rf "$dir/app.old"
[ -d "$dir/app" ] && mv "$dir/app" "$dir/app.old"
mv "$dir/app.new" "$dir/app"
rm -rf "$dir/app.old"

tools=""
if [ -n "$platform" ]; then
  report tools
  say "Downloading media tools and the download app"
  rm -rf "$dir/tools.new"
  mkdir -p "$dir/tools.new"
  if curl -fsSL "$SERVER/api/v1/node/setup/$CODE/tools-$platform.tar.gz" | tar -xzf - -C "$dir/tools.new" 2>/dev/null \
    && "$dir/tools.new/bin/ffprobe" -version >/dev/null 2>&1; then
    rm -rf "$dir/tools.old"
    [ -d "$dir/tools" ] && mv "$dir/tools" "$dir/tools.old"
    mv "$dir/tools.new" "$dir/tools"
    rm -rf "$dir/tools.old"
  else
    rm -rf "$dir/tools.new"
  fi
fi
[ -x "$dir/tools/bin/ffprobe" ] && tools=yes

python=""
for candidate in python3.13 python3.12 python3.11 python3; do
  if has "$candidate" && "$candidate" -c 'import sqlite3, sys; sys.exit(sys.version_info < (3, 11))' >/dev/null 2>&1; then
    python="$(command -v "$candidate")"
    break
  fi
done
if [ -z "$python" ]; then
  report python
  say "Getting Python 3.12"
  uv=""
  if has uv; then
    uv="$(command -v uv)"
  else
    curl -LsSf https://astral.sh/uv/install.sh | env UV_UNMANAGED_INSTALL="$dir/uv" sh >/dev/null 2>&1 || true
    for candidate in "$dir/uv/uv" "$dir/uv/bin/uv"; do
      [ -x "$candidate" ] && uv="$candidate"
    done
  fi
  [ -n "$uv" ] || fail python "Couldn't get Python 3.11 or newer. Install it, then run the command again."
  export UV_PYTHON_INSTALL_DIR="$dir/python"
  "$uv" python install 3.12 >/dev/null 2>&1 || fail python "Couldn't download Python 3.12. Check this computer's internet connection."
  python="$("$uv" python find 3.12)" || fail python "Couldn't find the Python that was just installed."
fi

report pairing
set -- --config "$dir/node.json" configure "--server=$SERVER" "--code=$CODE"
case "$SERVER" in http://*) set -- "$@" --allow-private-http ;; esac
if [ -x "$dir/tools/bin/transmission-daemon" ] || has transmission-daemon \
  || [ -x /opt/homebrew/bin/transmission-daemon ] || [ -x /usr/local/bin/transmission-daemon ]; then
  set -- "$@" --managed-downloader
fi
(cd "$dir/app" && "$python" sparrow-node.py "$@") || fail pair "Pairing didn't work. Get a fresh command in Sparrow and run it again."
[ "$owner" != "$(id -un)" ] && chown -R "$owner" "$dir"

report starting
run="$dir/app/sparrow-node.py"
started=""
if [ "$system" = macos ]; then
  agents="$home/Library/LaunchAgents"
  mkdir -p "$agents" "$dir/logs"
  cat > "$agents/com.sparrow.node.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.sparrow.node</string>
  <key>ProgramArguments</key>
  <array>
    <string>$python</string><string>$run</string>
    <string>--config</string><string>$dir/node.json</string><string>run</string>
  </array>
  <key>WorkingDirectory</key><string>$dir/app</string>
  <key>EnvironmentVariables</key>
  <dict><key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string></dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$dir/logs/node.log</string>
  <key>StandardErrorPath</key><string>$dir/logs/node.log</string>
</dict>
</plist>
EOF
  launchctl bootstrap "gui/$(id -u)" "$agents/com.sparrow.node.plist" && started=launchd
elif has systemctl && [ -d /run/systemd/system ]; then
  unit="[Unit]
Description=Sparrow Node
Wants=network-online.target
After=network-online.target

[Service]
ExecStart=\"$python\" \"$run\" --config \"$dir/node.json\" run
WorkingDirectory=$dir/app
Restart=always
RestartSec=10"
  if [ "$(id -u)" -eq 0 ]; then
    printf '%s\nUser=%s\n\n[Install]\nWantedBy=multi-user.target\n' "$unit" "$owner" > /etc/systemd/system/sparrow-node.service
    systemctl daemon-reload && systemctl enable --now sparrow-node >/dev/null 2>&1 && started=systemd
  elif systemctl --user show-environment >/dev/null 2>&1; then
    # A user service stops when its owner signs out, unless they "linger".
    loginctl enable-linger "$owner" >/dev/null 2>&1 || true
    if [ "$(loginctl show-user "$owner" -p Linger --value 2>/dev/null)" = yes ]; then
      mkdir -p "$home/.config/systemd/user"
      printf '%s\n\n[Install]\nWantedBy=default.target\n' "$unit" > "$home/.config/systemd/user/sparrow-node.service"
      systemctl --user daemon-reload && systemctl --user enable --now sparrow-node >/dev/null 2>&1 && started=systemd-user
    else
      # Without lingering a user service stops at sign-out; the fallback below doesn't.
      systemctl --user disable --now sparrow-node >/dev/null 2>&1 || true
    fi
  fi
fi
if [ -z "$started" ]; then
  # No service manager: start it now and again at every restart.
  mkdir -p "$dir/logs"
  (cd "$dir/app" && nohup "$python" "$run" --config "$dir/node.json" run </dev/null >>"$dir/logs/node.log" 2>&1 &)
  if has crontab; then
    line="@reboot cd '$dir/app' && '$python' '$run' --config '$dir/node.json' run >>'$dir/logs/node.log' 2>&1"
    { crontab -l 2>/dev/null | grep -v 'sparrow-node.py' || true; echo "$line"; } | crontab - && started=cron
  fi
  [ -n "$started" ] || say "Sparrow Node is running now, but won't start again after a restart."
fi

report running
say ""
say "Sparrow Node is running. Choose its folders in Sparrow."
if [ -z "$tools" ] && ! has ffprobe; then
  if [ "$system" = macos ]; then say "Downloads and media checks need Homebrew's ffmpeg and transmission-cli on this Mac."
  else say "This server has no media tools for this computer yet. Downloads and some playback wait for them."; fi
fi
