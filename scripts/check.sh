#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
python=${SPARROW_PYTHON:-.venv/bin/python}
"$python" -m compileall -q backend tests
"$python" -m pip check
"$python" -m unittest discover -s tests
cd frontend
npm run build
npm audit --audit-level=high
cd ..
./scripts/check-release-tree.sh
