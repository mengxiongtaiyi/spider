#!/usr/bin/env bash
# Periodic git snapshot. Store flush is owned by flush_ameede_live.sh (every 10min).
# Hard timeouts so git/xlsx cannot freeze this loop for 15–30min.
set +e
ROOT="/workspace/ameede"
REPO="/workspace"
BRANCH="cursor/ameede-laser-crawler-634e"
LOG="$ROOT/data/ameede/sync_live.log"
MARKER="$ROOT/data/ameede/last_sync_rows.txt"
INTERVAL_SECS="${INTERVAL_SECS:-1800}"

mkdir -p "$ROOT/data/ameede"
touch "$LOG"
export PYTHONPATH="$ROOT"
export PYTHONUNBUFFERED=1
cd "$ROOT"
printf '%s\n' "$$" >/tmp/ameede_sync.pid

log() {
  local msg="$(date -Is) $*"
  timeout 3 bash -c "printf '%s\n' $(printf '%q' "$msg") >>$(printf '%q' "$LOG")" 2>/dev/null || true
  date -u +%Y-%m-%dT%H:%M:%SZ >/tmp/ameede_sync_heartbeat 2>/dev/null || true
}

row_count() {
  timeout 30 python3 - <<'PY'
import csv
from pathlib import Path
p=Path('/workspace/ameede/data/ameede/ameede.csv')
print(sum(1 for _ in csv.DictReader(p.open(encoding='utf-8'))) if p.exists() else 0)
PY
}

do_sync() {
  local rows
  rows="$(row_count)"
  log "sync start rows=$rows"

  timeout 30 cp -f "$ROOT/data/ameede/ameede.csv" /tmp/ameede_snap.csv || true
  if [[ -f "$ROOT/data/ameede/run_summary.json" ]]; then
    timeout 10 cp -f "$ROOT/data/ameede/run_summary.json" /tmp/ameede_snap_summary.json || true
  fi

  # XLSX rebuild can be slow; never block forever.
  timeout 240 env PYTHONPATH="$ROOT" python3 - <<'PY'
from pathlib import Path
import csv
from ameede_crawler.export import write_xlsx
rows = list(csv.DictReader(Path('/tmp/ameede_snap.csv').open(encoding='utf-8')))
for i, r in enumerate(rows, 1):
    r['seq'] = i
write_xlsx(Path('/workspace/ameede/data/ameede/ameede.xlsx'), rows)
print(len(rows))
PY

  echo "$rows" >"$MARKER"

  cd "$REPO" || return 0
  timeout 60 git add ameede/data/ameede/ameede.csv ameede/data/ameede/ameede.xlsx ameede/data/ameede/run_summary.json
  if ! git diff --cached --quiet; then
    timeout 60 git commit -m "Ameede live progress snapshot (${rows} rows, URL-only coverUrl)"
    if timeout 120 git push origin "$BRANCH"; then
      log "pushed rows=$rows"
    else
      log "push failed/timeout rows=$rows"
    fi
  else
    log "no git changes rows=$rows"
  fi
  cd "$ROOT" || true
}

do_sync

while true; do
  sleep "$INTERVAL_SECS"
  if [[ -f "$ROOT/data/ameede/STOP_SYNC" ]]; then
    echo "$(date -Is) stop requested" >>"$LOG"
    do_sync
    exit 0
  fi
  do_sync
  if ! pgrep -f 'python3 crawl_ameede.py' >/dev/null 2>&1; then
    echo "$(date -Is) crawl absent after sync" >>"$LOG"
    # exit only if past last page
    done_flag=$(python3 - <<'PY'
import json
from pathlib import Path
s=json.loads(Path('/workspace/ameede/data/ameede/run_summary.json').read_text()) if Path('/workspace/ameede/data/ameede/run_summary.json').exists() else {}
nxt=int(s.get('next_page') or 0); mx=int(s.get('max_page') or 0)
print('1' if mx and nxt>mx else '0')
PY
)
    if [[ "$done_flag" == "1" ]]; then
      echo "$(date -Is) crawl complete; sync loop exiting" >>"$LOG"
      exit 0
    fi
  fi
done
