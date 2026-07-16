#!/bin/bash
set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# ─── Python env ──────────────────────────────────────────────────────────────
if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi

source .venv/bin/activate
pip install -q -r requirements.txt

if [ ! -f ".env" ]; then
  cp .env.example .env
fi

# ─── Frontend deps ───────────────────────────────────────────────────────────
cd frontend
if [ ! -d "node_modules" ]; then
  echo -e "${YELLOW}Installing frontend dependencies...${NC}"
  npm install --silent
fi
cd ..

# ─── Start both in parallel ──────────────────────────────────────────────────
echo -e "${GREEN}Starting Sparrow in dev mode...${NC}"
echo -e "  Backend:  http://localhost:8888"
echo -e "  Frontend: http://localhost:3000"
echo ""

# Start backend
source .venv/bin/activate
python -m backend.main &
BACKEND_PID=$!

# Start frontend dev server
cd frontend
npm run dev &
FRONTEND_PID=$!
cd ..

trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit" INT TERM
wait
