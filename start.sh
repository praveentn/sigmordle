#!/usr/bin/env bash
# ============================================================
#  Sigmordle Discord Bot — macOS / Linux startup script
#  Usage:  ./start.sh
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── Colours ───────────────────────────────────────────────────
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info()  { echo -e "${GREEN}  [sigmordle]${NC} $*"; }
warn()  { echo -e "${YELLOW}  [sigmordle] WARNING:${NC} $*"; }
error() { echo -e "${RED}  [sigmordle] ERROR:${NC} $*"; exit 1; }

echo ""
echo "  =========================================="
echo "    🟩  Sigmordle Discord Bot"
echo "  =========================================="
echo ""

# ── Read PORT from .env ───────────────────────────────────────
PORT="${PORT:-}"
if [[ -z "$PORT" && -f ".env" ]]; then
    PORT=$(grep -E '^PORT=' .env 2>/dev/null | cut -d= -f2 | tr -d ' "' || true)
fi
PORT="${PORT:-8080}"
info "Port   : $PORT"

# ── Detect Python ─────────────────────────────────────────────
PYTHON_CMD=""
for cmd in python3 python; do
    if command -v "$cmd" &>/dev/null && "$cmd" -c "import sys; assert sys.version_info >= (3,10)" 2>/dev/null; then
        PYTHON_CMD="$cmd"
        break
    fi
done
[[ -z "$PYTHON_CMD" ]] && error "Python 3.10+ not found. Install from https://python.org"
info "Python : $($PYTHON_CMD --version)"

# ── Free port if occupied ─────────────────────────────────────
info "Checking port $PORT..."
if lsof -ti ":$PORT" &>/dev/null; then
    PID=$(lsof -ti ":$PORT")
    warn "Port $PORT in use by PID $PID — killing..."
    kill -9 "$PID" 2>/dev/null || true
    sleep 1
    info "OK: Port $PORT freed."
else
    info "OK: Port $PORT is free."
fi

# ── Virtual environment ───────────────────────────────────────
if [[ ! -f "venv/bin/activate" ]]; then
    info "Creating virtual environment..."
    "$PYTHON_CMD" -m venv venv || error "Failed to create venv."
    info "OK: venv created."
fi

info "Activating venv..."
# shellcheck disable=SC1091
source venv/bin/activate
info "venv   : $(python --version)"

# ── Install / sync requirements ───────────────────────────────
echo ""
info "Checking requirements..."
pip install -r requirements.txt -q --disable-pip-version-check || error "pip install failed."
info "OK: Dependencies up to date."

# ── Token check ───────────────────────────────────────────────
echo ""
TOKEN_VAL=""
if [[ -f ".env" ]]; then
    TOKEN_VAL=$(grep -E '^DISCORD_TOKEN=' .env 2>/dev/null | cut -d= -f2 | tr -d ' "' || true)
fi

if [[ -z "$TOKEN_VAL" ]]; then
    warn "DISCORD_TOKEN is not set in .env"
    warn "Bot will start in local-only mode."
    warn "Open http://localhost:$PORT/ for setup instructions."
elif [[ "$TOKEN_VAL" == "your_bot_token_here" ]]; then
    warn "DISCORD_TOKEN still has the placeholder value."
    warn "Edit .env and paste your real bot token."
else
    info "OK: Discord token found."
fi

# ── Launch ────────────────────────────────────────────────────
echo ""
info "Starting bot  -->  http://localhost:$PORT/"
info "Press Ctrl+C to stop."
echo ""

python bot.py
