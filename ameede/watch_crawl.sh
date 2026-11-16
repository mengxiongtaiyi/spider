#!/usr/bin/env bash
# Independent watchdog: never parents the crawl process.
set -eu
cd "$(dirname "$0")"
LOG="data/ameede/full_crawl.log"
WATCHLOG="data/ameede/watchdog.log"
PIDFILE="data/ameede/crawl.pid"
STALE_SECS="${STALE_SECS:-120}"

export PYTHONUNBUFFERED=1
export PYTHONPATH="$(pwd)"
mkdir -p data/ameede/checkpoints
touch "$LOG" "$WATCHLOG"

start_crawl() {
  if [[ -f "$PIDFILE" ]]; then
    old="$(cat "$PIDFILE" || true)"
    if [[ -n "${old:-}" ]]; then
      # Kill process group if possible
      kill -9 -"$old" 2>/dev/null || kill -9 "$old" 2>/dev/null || true
      sleep 1
    fi
  fi
  pkill -9 -f 'crawl_ameede.py' 2>/dev/null || true
  echo "$(date -Is) starting crawl" | tee -a "$LOG" >>"$WATCHLOG"
  # Launch via setsid so watchdog is not the parent
  setsid nohup python3 crawl_ameede.py --min-delay 0.35 --max-delay 0.75 --xlsx-every-pages 20 \
    >>"$LOG" 2>&1 < /dev/null &
  echo $! >"$PIDFILE"
  echo "$(date -Is) crawl pid=$(cat "$PIDFILE")" >>"$WATCHLOG"
}

newest_activity() {
  local newest=0 m f
  for f in data/ameede/checkpoints/state.json data/ameede/run_summary.json data/ameede/ameede.csv; do
    if [[ -f "$f" ]]; then
      m=$(stat -c %Y "$f")
      if (( m > newest )); then newest=$m; fi
    fi
  done
  echo "$newest"
}

start_crawl

while true; do
  sleep 30
  now=$(date +%s)
  pid="$(cat "$PIDFILE" 2>/dev/null || true)"
  if [[ -z "${pid:-}" ]] || ! kill -0 "$pid" 2>/dev/null; then
    echo "$(date -Is) crawl not running; restarting" >>"$WATCHLOG"
    start_crawl
    continue
  fi
  newest=$(newest_activity)
  age=$((now - newest))
  echo "$(date -Is) watchdog ok pid=$pid age=${age}s" >>"$WATCHLOG"
  if (( age > STALE_SECS )); then
    echo "$(date -Is) activity stale ${age}s; killing pid $pid and resuming" | tee -a "$LOG" >>"$WATCHLOG"
    kill -9 -"$pid" 2>/dev/null || kill -9 "$pid" 2>/dev/null || true
    pkill -9 -f 'crawl_ameede.py' 2>/dev/null || true
    pkill -9 -f 'curl.*ameede.com' 2>/dev/null || true
    sleep 2
    start_crawl
  fi
done
