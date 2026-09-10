#!/bin/sh
set -eu
cd "$(dirname "$0")/../.."
export SPARROW_BROWSER_STATE="${SPARROW_BROWSER_STATE:-/tmp/sparrow-browser-check}"
.venv/bin/python tests/browser/server.py > /tmp/sparrow-browser-ci.log 2>&1 &
fixture_pid=$!
trap 'kill "$fixture_pid" 2>/dev/null || true' EXIT INT TERM
.venv/bin/python - <<'PY'
import time,urllib.request
for _ in range(40):
    try:
        urllib.request.urlopen('http://127.0.0.1:8891/api/v1/auth/status',timeout=2)
        break
    except OSError:time.sleep(1)
else:raise RuntimeError('The browser fixture server did not start.')
PY
node tests/browser/design.cjs
node tests/browser/check.cjs
node tests/browser/entry.cjs
node tests/browser/accessibility.cjs
node tests/browser/household.cjs
node tests/browser/design.cjs
node tests/browser/reimagined.cjs
