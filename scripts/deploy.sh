#!/usr/bin/env bash
#
# Safe deploy for Nemo — rsync the engine + voice code, then PROVE it imports
# and the services come back HEALTHY before trusting the deploy. Auto-rolls back
# on any failure. Catches the missing-dependency / syntax-error crash-loop class
# BEFORE restarting prod (e.g. shipping voice_brain.py without its `vision` dep).
#
# Usage:
#   ./scripts/deploy.sh [ssh-host]          # default host: chess-vps
#   REMOTE_DIR=/srv/nemo ./scripts/deploy.sh sg-vps
#
# Point it at the new Singapore box by passing its ssh host alias.

set -euo pipefail

HOST="${1:-chess-vps}"
REMOTE="${REMOTE_DIR:-/root/pyclaudir-nemo}"
UV="${REMOTE_UV:-/root/.local/bin/uv}"
BAK="$REMOTE/.deploy-bak"

say()  { printf '\n\033[1;36m== %s\033[0m\n' "$*"; }
die()  { printf '\n\033[1;31mDEPLOY FAILED: %s\033[0m\n' "$*" >&2; exit 1; }

# Restore the pre-deploy snapshot and (optionally) restart. Used on any failure.
rollback() {
  printf '\033[1;33m   rolling back to previous code...\033[0m\n'
  ssh "$HOST" "
    rsync -a --delete $BAK/pyclaudir/ $REMOTE/pyclaudir/ &&
    rsync -a --delete $BAK/server/ $REMOTE/nemo-voice/server/ &&
    cp -a $BAK/pyproject.toml $BAK/uv.lock $REMOTE/ &&
    cd $REMOTE && $UV sync >/dev/null 2>&1 ${1:+&& systemctl restart nemo.service nemo-voice.service}
  "
}

say "1/7  local pre-flight (ruff + tests)"
PY="$([ -x .venv/bin/python ] && echo .venv/bin/python || echo python3)"
$PY -m ruff check pyclaudir nemo-voice/server >/dev/null || die "ruff failed locally"
$PY -m pytest -q >/dev/null || die "engine tests failed locally"
$PY -m pytest nemo-voice/server/tests -q >/dev/null || die "voice tests failed locally"

say "2/7  snapshot current remote code (for rollback)"
ssh "$HOST" "rm -rf $BAK && mkdir -p $BAK &&
  cp -a $REMOTE/pyclaudir $BAK/ &&
  cp -a $REMOTE/nemo-voice/server $BAK/server &&
  cp -a $REMOTE/pyproject.toml $REMOTE/uv.lock $BAK/" || die "snapshot failed"

say "3/7  rsync engine + voice"
rsync -az --delete --exclude='__pycache__' --exclude='*.pyc' \
  pyclaudir/ "$HOST:$REMOTE/pyclaudir/"
rsync -az pyproject.toml uv.lock "$HOST:$REMOTE/"
rsync -az --exclude='__pycache__' --exclude='*.pyc' \
  nemo-voice/server/*.py "$HOST:$REMOTE/nemo-voice/server/"

say "4/7  uv sync (engine deps)"
ssh "$HOST" "cd $REMOTE && $UV sync >/dev/null 2>&1" || { rollback; die "uv sync failed"; }

say "5/7  IMPORT SMOKE — verify it loads BEFORE restarting anything"
if ! ssh "$HOST" "
      cd $REMOTE && .venv/bin/python -c 'import pyclaudir.tools, pyclaudir.engine.engine, pyclaudir.code_sandbox' &&
      cd $REMOTE/nemo-voice/server && python3 -c 'import voice_brain, qwen_realtime, streaming_service'
    "; then
  rollback           # old process is still live + untouched; restore disk only
  die "import smoke failed — previous code restored, services NOT restarted"
fi

say "6/7  restart services"
ssh "$HOST" "systemctl restart nemo.service nemo-voice.service"
sleep 6

say "7/7  health gate"
HEALTH=$(ssh "$HOST" "curl -sk --max-time 8 https://localhost:3001/health" 2>/dev/null || true)
ACTIVE=$(ssh "$HOST" "systemctl is-active nemo.service nemo-voice.service | tr '\n' ' '")
if [[ "$HEALTH" != *'"status": "ok"'* || "$ACTIVE" != "active active "* ]]; then
  rollback restart
  die "health gate failed (health='$HEALTH' active='$ACTIVE') — rolled back + restarted"
fi

printf '\n\033[1;32m✓ DEPLOYED OK — /health ok, both services active\033[0m\n'
