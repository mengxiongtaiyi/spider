#!/usr/bin/env bash
set -eu
cd "$(dirname "$0")"
PIDFILE=data/ameede/crawl.pid
WATCHLOG=data/ameede/watchdog.log
LOG=data/ameede/full_crawl.log
STALE_SECS=${STALE_SECS:-90}
CRAWL_CMD=(python3 crawl_ameede.py --min-delay 0.2 --max-delay 0.45 --xlsx-every-pages 50)
touch "$WATCHLOG"
export PYTHONUNBUFFERED=1
export PYTHONPATH="$(pwd)"

start_one() {
  pkill -9 -f 'crawl_ameede.py' 2>/dev/null || true
  sleep 1
  echo "$(date -Is) starting crawl (url-only)" >>"$WATCHLOG"
  nohup "${CRAWL_CMD[@]}" >>"$LOG" 2>&1 &
  echo $! >"$PIDFILE"
  disown || true
  echo "$(date -Is) started pid=$(cat "$PIDFILE")" >>"$WATCHLOG"
}

if [[ ! -f "$PIDFILE" ]] || ! kill -0 "$(cat "$PIDFILE" 2>/dev/null || echo)" 2>/dev/null; then
  start_one
fi

while true; do
  sleep 30
  now=$(date +%s)
  pid=$(cat "$PIDFILE" 2>/dev/null || true)
  if [[ -z "${pid:-}" ]] || ! kill -0 "$pid" 2>/dev/null; then
    echo "$(date -Is) crawl missing; restarting" >>"$WATCHLOG"
    start_one
    continue
  fi
  newest=0
  for f in data/ameede/checkpoints/state.json data/ameede/run_summary.json data/ameede/ameede.csv; do
    [[ -f $f ]] || continue
    m=$(stat -c %Y "$f")
    (( m > newest )) && newest=$m
  done
  age=$((now - newest))
  echo "$(date -Is) ok pid=$pid age=${age}s" >>"$WATCHLOG"
  if (( age > STALE_SECS )); then
    echo "$(date -Is) stale ${age}s kill $pid" >>"$WATCHLOG"
    kill -9 "$pid" 2>/dev/null || true
    pkill -9 -f 'curl.*(ameede.com|timeout)' 2>/dev/null || true
    sleep 2
    start_one
  fi
done
