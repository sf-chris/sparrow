#!/bin/bash
set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

# ─── Colors ──────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}  ____                               ${NC}"
echo -e "${BLUE} / ___| _ __   __ _ _ __ _ __ _____    __${NC}"
echo -e "${BLUE} \\___ \\| '_ \\ / _\` | '__| '__/ _ \\ \\  / /${NC}"
echo -e "${BLUE}  ___) | |_) | (_| | |  | | | (_) \\ \\/ / ${NC}"
echo -e "${BLUE} |____/| .__/ \\__,_|_|  |_|  \\___/ \\__/  ${NC}"
echo -e "${BLUE}       |_|                               ${NC}"
echo ""

# Install only when this checkout has not been prepared. Routine restarts must
# not mutate dependencies or require Node/npm.
if [ ! -x ".venv/bin/python" ] || [ ! -f "frontend/dist/index.html" ]; then
  echo -e "${YELLOW}This checkout is not installed yet; running the installer...${NC}"
  ./scripts/install.sh
fi

if [ -f ".env" ]; then
  chmod 600 .env
fi

source .venv/bin/activate

# Fail before starting a half-working service. Torrent reachability is checked
# by the app and can recover after startup; every other production dependency is
# required here.
python -m backend.doctor --runtime-only --skip-client

# ─── Start backend ────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}Starting Sparrow...${NC}"
echo ""

source .venv/bin/activate
exec python -m backend.main
