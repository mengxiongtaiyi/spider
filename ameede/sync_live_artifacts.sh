#!/usr/bin/env bash
# Checkpoint-safe flush while crawl runs.
set -eu
ROOT="/workspace/ameede"
REPO="/workspace"
STORE="/cursor/stores/bc-2e46f76f-8f5e-477a-9c49-8ec40c4d1be3/docs/ameede-live"
BRANCH="cursor/ameede-laser-crawler-634e"
LOG="$ROOT/data/ameede/sync_live.log"
MARKER="$ROOT/data/ameede/last_sync_rows.txt"
EVERY_ROWS="${EVERY_ROWS:-500}"
INTERVAL_SECS="${INTERVAL_SECS:-1800}"

mkdir -p "$STORE" "$ROOT/data/ameede"
touch "$LOG"
export PYTHONPATH="$ROOT"
export PYTHONUNBUFFERED=1
cd "$ROOT"

row_count() {
  python3 - <<'PY'
import csv
from pathlib import Path
p=Path('/workspace/ameede/data/ameede/ameede.csv')
print(sum(1 for _ in csv.DictReader(p.open(encoding='utf-8'))) if p.exists() else 0)
PY
}

do_sync() {
  local rows
  rows="$(row_count)"
  echo "$(date -Is) sync start rows=$rows" >>"$LOG"

  cp -f "$ROOT/data/ameede/ameede.csv" /tmp/ameede_snap.csv
  if [[ -f "$ROOT/data/ameede/run_summary.json" ]]; then
    cp -f "$ROOT/data/ameede/run_summary.json" /tmp/ameede_snap_summary.json
  else
    rm -f /tmp/ameede_snap_summary.json
  fi

  PYTHONPATH="$ROOT" python3 - <<'PY'
from pathlib import Path
import csv, json, shutil
from ameede_crawler.export import write_csv, write_xlsx

rows = list(csv.DictReader(Path('/tmp/ameede_snap.csv').open(encoding='utf-8')))
for i, r in enumerate(rows, 1):
    r['seq'] = i
write_xlsx(Path('/workspace/ameede/data/ameede/ameede.xlsx'), rows)
store = Path('/cursor/stores/bc-2e46f76f-8f5e-477a-9c49-8ec40c4d1be3/docs/ameede-live')
store.mkdir(parents=True, exist_ok=True)
write_csv(store / 'ameede.csv', rows)
if Path('/tmp/ameede_snap_summary.json').exists():
    shutil.copy2('/tmp/ameede_snap_summary.json', store / 'run_summary.json')
(store / 'SYNC_META.json').write_text(json.dumps({
    'synced_rows': len(rows),
    'note': 'coverUrl URL-only; coverUrlRelative empty for new rows (local fills)',
}, ensure_ascii=False, indent=2), encoding='utf-8')
print(len(rows))
PY

  echo "$rows" >"$MARKER"

  cd "$REPO"
  git add ameede/data/ameede/ameede.csv ameede/data/ameede/ameede.xlsx ameede/data/ameede/run_summary.json || true
  # refresh csv from live for commit if newer than snap? Prefer live copy into staging
  # Use current live files (xlsx just rebuilt from snap; csv may have grown — commit live csv)
  if ! git diff --cached --quiet; then
    git commit -m "Ameede live progress snapshot (${rows} rows, URL-only coverUrl)" || true
    git push origin "$BRANCH" || true
    echo "$(date -Is) pushed rows=$rows" >>"$LOG"
  else
    # try add again after copying live csv timestamps
    git add ameede/data/ameede/ameede.csv ameede/data/ameede/ameede.xlsx ameede/data/ameede/run_summary.json || true
    if ! git diff --cached --quiet; then
      git commit -m "Ameede live progress snapshot (${rows} rows, URL-only coverUrl)" || true
      git push origin "$BRANCH" || true
      echo "$(date -Is) pushed rows=$rows" >>"$LOG"
    else
      echo "$(date -Is) no git changes rows=$rows" >>"$LOG"
    fi
  fi
  cd "$ROOT"
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
