#!/usr/bin/env bash
# Lightweight store flush every INTERVAL_SECS. No git. Cannot block on push.
# Safe while crawl is running — copies via snap files.
set +e
ROOT="/workspace/ameede"
STORE="/cursor/stores/bc-2e46f76f-8f5e-477a-9c49-8ec40c4d1be3/docs/ameede-live"
LOG="$ROOT/data/ameede/flush_live.log"
HB=/tmp/ameede_flush_heartbeat
INTERVAL_SECS="${INTERVAL_SECS:-600}"
mkdir -p "$STORE" "$ROOT/data/ameede"
touch "$LOG"
export PYTHONPATH="$ROOT"
export PYTHONUNBUFFERED=1
cd "$ROOT"
printf '%s\n' "$$" >/tmp/ameede_flush.pid

log() {
  local msg="$(date -Is) $*"
  printf '%s\n' "$msg" >>"$HB" 2>/dev/null
  timeout 3 bash -c "printf '%s\n' $(printf '%q' "$msg") >>$(printf '%q' "$LOG")" 2>/dev/null || true
}

do_flush() {
  log "flush start"
  timeout 90 python3 - <<'PY'
import csv, json, shutil, time
from pathlib import Path
from ameede_crawler.export import write_csv

root = Path('/workspace/ameede/data/ameede')
store = Path('/cursor/stores/bc-2e46f76f-8f5e-477a-9c49-8ec40c4d1be3/docs/ameede-live')
store.mkdir(parents=True, exist_ok=True)
src = root / 'ameede.csv'
if not src.exists():
    print(0)
    raise SystemExit(0)
# Snap first so we never read a half-written final line into store permanently.
snap = Path('/tmp/ameede_live_snap.csv')
shutil.copy2(src, snap)
rows = list(csv.DictReader(snap.open(encoding='utf-8')))
for i, r in enumerate(rows, 1):
    r['seq'] = i
write_csv(store / 'ameede.csv', rows)
summary = root / 'run_summary.json'
if summary.exists():
    shutil.copy2(summary, store / 'run_summary.json')
    s = json.loads(summary.read_text(encoding='utf-8'))
else:
    s = {}
meta = {
    'synced_rows': len(rows),
    'next_page': s.get('next_page'),
    'max_page': s.get('max_page'),
    'flushed_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    'note': 'coverUrl URL-only; coverUrlRelative empty for new rows',
}
(store / 'SYNC_META.json').write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8')
print(len(rows))
PY
  rc=$?
  if (( rc == 0 )); then
    log "flush ok"
  else
    log "flush fail rc=$rc"
  fi
  date -u +%Y-%m-%dT%H:%M:%SZ >"$HB"
}

log "flush daemon online pid=$$ INTERVAL_SECS=${INTERVAL_SECS}"
do_flush
while true; do
  sleep "$INTERVAL_SECS"
  do_flush
done
