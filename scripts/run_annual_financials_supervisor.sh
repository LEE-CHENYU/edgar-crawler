#!/bin/zsh
set -u

REPO_DIR="/Users/lichenyu/GitHub/edgar-crawler"
DATA_ROOT="/Volumes/OWC Express 1M2/datasets/MARKET_FILINGS"
ENV_FILE="$HOME/.config/edgar-crawler/market_filings.env"
LOG_DIR="$REPO_DIR/logs"

mkdir -p "$LOG_DIR"

log() {
  printf '%s %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*"
}

while true; do
  if [[ ! -d "$DATA_ROOT" ]]; then
    log "waiting for external data root: $DATA_ROOT"
    sleep 60
    continue
  fi

  if [[ ! -f "$DATA_ROOT/metadata/MARKET_FILINGS_METADATA.csv" || ! -f "$DATA_ROOT/backfill_state_dart.json" ]]; then
    log "waiting for external corpus sentinel files under: $DATA_ROOT"
    sleep 60
    continue
  fi

  if [[ ! -x "$REPO_DIR/.venv/bin/python" ]]; then
    log "waiting for repo venv: $REPO_DIR/.venv/bin/python"
    sleep 60
    continue
  fi

  if [[ ! -f "$ENV_FILE" ]]; then
    log "waiting for env file: $ENV_FILE"
    sleep 60
    continue
  fi

  cd "$REPO_DIR" || {
    log "cannot cd to repo: $REPO_DIR"
    sleep 60
    continue
  }

  . .venv/bin/activate
  log "starting annual financials supervisor"
  python -u scripts/annual_financials_queue.py supervise \
    --data-root "$DATA_ROOT" \
    --env-file "$ENV_FILE" \
    --max-workers 3 \
    --interval-seconds 300
  rc=$?
  log "annual financials supervisor exited rc=$rc; restarting after delay"
  sleep 60
done
