#!/usr/bin/env bash
# Independent watchdog — never parents the crawl; survives set -e arithmetic traps.
# Heartbeat goes to local /tmp so a hung workspace FS cannot freeze the loop.
set +e
cd "$(dirname "$0")"
PIDFILE=data/ameede/crawl.pid
WATCHLOG=data/ameede/watchdog.log
HB=/tmp/ameede_watchdog_heartbeat
LOG=data/ameede/full_crawl.log
STALE_SECS="${STALE_SECS:-75}"
CHECK_SECS="${CHECK_SECS:-15}"
CRAWL_CMD=(python3 crawl_ameede.py --min-delay 0.12 --max-delay 0.28 --xlsx-every-pages 50)
mkdir -p data/ameede/checkpoints
touch "$WATCHLOG" "$LOG"
export PYTHONUNBUFFERED=1
export PYTHONPATH="$(pwd)"

log() {
  local msg="$(date -Is) $*"
  # Prefer local disk; best-effort workspace log (timeout so NFS hang cannot freeze us).
  printf '%s\n' "$msg" >>"$HB" 2>/dev/null
  timeout 3 bash -c "printf '%s\n' $(printf '%q' "$msg") >>$(printf '%q' "$WATCHLOG")" 2>/dev/null || true
}

activity_age() {
  python3 - <<'PY'
import time
from pathlib import Path
files = [
    Path('data/ameede/checkpoints/state.json'),
    Path('data/ameede/run_summary.json'),
    Path('data/ameede/ameede.csv'),
]
newest = 0
for f in files:
    try:
        if f.exists():
            newest = max(newest, f.stat().st_mtime)
    except OSError:
        pass
print(int(time.time() - newest) if newest else 99999)
PY
}

rebuild_checkpoint() {
  timeout 60 python3 - <<'PY' 2>/dev/null || true
import json, csv
from pathlib import Path
csv_path = Path('data/ameede/ameede.csv')
if not csv_path.exists():
    raise SystemExit(0)
rows = list(csv.DictReader(csv_path.open(encoding='utf-8')))
urls = list(dict.fromkeys(r['detailUrl'] for r in rows if r.get('detailUrl')))
Path('data/ameede/checkpoints').mkdir(parents=True, exist_ok=True)
Path('data/ameede/checkpoints/processed_urls.txt').write_text('\n'.join(urls) + ('\n' if urls else ''), encoding='utf-8')
p = Path('data/ameede/checkpoints/state.json')
s = json.loads(p.read_text(encoding='utf-8')) if p.exists() else {}
s['success_count'] = len(rows)
s['processed_count'] = len(urls)
s['listed_count'] = max(int(s.get('listed_count') or 0), len(urls))
p.write_text(json.dumps(s, separators=(',', ':')), encoding='utf-8')
print(f"ckpt rows={len(rows)} urls={len(urls)} next={s.get('next_page')}")
PY
}

start_one() {
  # Kill only crawl + stuck curl helpers — never this watchdog / sync.
  pkill -9 -f 'python3 crawl_ameede.py' 2>/dev/null || true
  pkill -9 -f 'curl.*(ameede\.com)|timeout.*curl' 2>/dev/null || true
  sleep 1
  rebuild_checkpoint
  log "starting crawl (url-only) STALE_SECS=${STALE_SECS}"
  nohup "${CRAWL_CMD[@]}" >>"$LOG" 2>&1 &
  echo $! >"$PIDFILE"
  disown || true
  log "started pid=$(cat "$PIDFILE" 2>/dev/null)"
}

# Ensure crawl is up once at boot; do not restart if already healthy.
pid="$(cat "$PIDFILE" 2>/dev/null || true)"
if [[ -z "${pid:-}" ]] || ! kill -0 "$pid" 2>/dev/null; then
  if pgrep -f 'python3 crawl_ameede.py' >/dev/null 2>&1; then
    pgrep -f 'python3 crawl_ameede.py' | head -1 >"$PIDFILE"
    log "adopted existing crawl pid=$(cat "$PIDFILE")"
  else
    start_one
  fi
fi

printf '%s\n' "$$" >/tmp/ameede_watchdog.pid
log "watchdog online pid=$$ STALE_SECS=${STALE_SECS} CHECK_SECS=${CHECK_SECS}"

while true; do
  sleep "$CHECK_SECS"
  date -u +%Y-%m-%dT%H:%M:%SZ >"$HB"
  pid="$(cat "$PIDFILE" 2>/dev/null || true)"
  if [[ -z "${pid:-}" ]] || ! kill -0 "$pid" 2>/dev/null; then
    # Re-adopt if another launcher started crawl; else restart.
    if pgrep -f 'python3 crawl_ameede.py' >/dev/null 2>&1; then
      pgrep -f 'python3 crawl_ameede.py' | head -1 >"$PIDFILE"
      log "re-adopted crawl pid=$(cat "$PIDFILE")"
      continue
    fi
    log "crawl missing; restarting"
    start_one
    continue
  fi
  age="$(activity_age)"
  log "ok pid=$pid age=${age}s"
  if [[ -n "$age" ]] && (( age > STALE_SECS )); then
    log "stale ${age}s kill $pid"
    kill -9 "$pid" 2>/dev/null || true
    pkill -9 -f 'curl.*(ameede\.com)|timeout.*curl' 2>/dev/null || true
    sleep 2
    start_one
  fi
done
