#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DATA_DIR="${SPARROW_DATA_DIR:-$ROOT/data}"
DEST="${1:-$ROOT/backups}"
STAMP="$(date +%Y%m%d-%H%M%S)"

mkdir -p "$DEST"
tar -czf "$DEST/sparrow-state-$STAMP.tar.gz" -C "$DATA_DIR" .
chmod 600 "$DEST/sparrow-state-$STAMP.tar.gz"
echo "$DEST/sparrow-state-$STAMP.tar.gz"
