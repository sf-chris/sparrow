#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

.venv/bin/python -m compileall -q backend tests
.venv/bin/python -m unittest discover -v
.venv/bin/python -m pip check

cd frontend
npm run build
cd "$ROOT"

./scripts/check-release-tree.sh
