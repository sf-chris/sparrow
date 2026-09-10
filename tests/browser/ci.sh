#!/bin/sh
set -eu
cd "$(dirname "$0")/../.."
export SPARROW_BROWSER_STATE="${SPARROW_BROWSER_STATE:-$(mktemp -d /tmp/sparrow-browser.XXXXXX)}"
export SPARROW_BROWSER_PORT="${SPARROW_BROWSER_PORT:-8891}"
export SPARROW_BROWSER_URL="http://127.0.0.1:$SPARROW_BROWSER_PORT"
evidence="${SPARROW_BROWSER_EVIDENCE:-tests/browser/artifacts}"
mkdir -p "$evidence"
# Refuse to run against an existing preview or someone else's fixture state.
.venv/bin/python - <<'PY'
import os, socket
with socket.socket() as sock:
    sock.bind(('127.0.0.1', int(os.environ['SPARROW_BROWSER_PORT'])))
PY
.venv/bin/python tests/browser/server.py > "$evidence/server.log" 2>&1 &
fixture_pid=$!
trap 'kill "$fixture_pid" 2>/dev/null || true' EXIT INT TERM
.venv/bin/python - <<'PY'
import os,time,urllib.request
for _ in range(40):
    try:
        urllib.request.urlopen(os.environ['SPARROW_BROWSER_URL']+'/api/v1/auth/status',timeout=2)
        break
    except OSError:time.sleep(1)
else:raise RuntimeError('The browser fixture server did not start.')
PY
SPARROW_VISUAL_OUT="$evidence/setup" node tests/browser/design.cjs
SPARROW_VISUAL_OUT="$evidence/viewing" node tests/browser/check.cjs
SPARROW_VISUAL_OUT="$evidence/entry" node tests/browser/entry.cjs
SPARROW_VISUAL_OUT="$evidence/accessibility" node tests/browser/accessibility.cjs
SPARROW_VISUAL_OUT="$evidence/household" node tests/browser/household.cjs
SPARROW_VISUAL_OUT="$evidence/design" node tests/browser/design.cjs
SPARROW_VISUAL_OUT="$evidence/reimagined" node tests/browser/reimagined.cjs
SPARROW_VISUAL_OUT="$evidence/follow-up" node tests/browser/follow-up.cjs
SPARROW_SCREENSHOT_OUT="${SPARROW_SCREENSHOT_OUT:-$evidence/screenshots}" node tests/browser/screenshots.cjs
