#!/usr/bin/env bash
# Restart ameede full crawl if crawl activity stalls (hung HTTPS sockets).
set -eu
cd "$(dirname "$0")"
LOG="data/ameede/full_crawl.log"
WATCHLOG="data/ameede/watchdog.log"
PIDFILE="data/ameede/crawl.pid"
HEARTBEAT="data/ameede/checkpoints/state.json"
SUMMARY="data/ameede/run_summary.json"
CSV="data/ameede/ameede.csv"
STALE_SECS="${STALE_SECS:-120}"

export PYTHONUNBUFFERED=1
export PYTHONPATH="$(pwd)"

mkdir -p data/ameede data/ameede/checkpoints
touch "$LOG" "$WATCHLOG"

start_crawl() {
  if [[ -f "$PIDFILE" ]]; then
    old="$(cat "$PIDFILE" || true)"
    if [[ -n "${old}" ]]; then
      kill "$old" 2>/dev/null || true
      sleep 1
      kill -9 "$old" 2>/dev/null || true
    fi
  fi
  echo "$(date -Is) starting crawl" | tee -a "$LOG" >>"$WATCHLOG"
  nohup python3 crawl_ameede.py --min-delay 0.35 --max-delay 0.75 --xlsx-every-pages 20 >>"$LOG" 2>&1 &
  echo $! >"$PIDFILE"
  disown || true
  echo "$(date -Is) crawl pid=$(cat "$PIDFILE")" >>"$WATCHLOG"
}

newest_activity() {
  local newest=0 m f
  for f in "$HEARTBEAT" "$SUMMARY" "$CSV"; do
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
  if [[ -z "${pid}" ]] || ! kill -0 "$pid" 2>/dev/null; then
    echo "$(date -Is) crawl not running; restarting" >>"$WATCHLOG"
    start_crawl
    continue
  fi
  newest=$(newest_activity)
  if (( newest == 0 )); then
    echo "$(date -Is) no activity files yet" >>"$WATCHLOG"
    continue
  fi
  age=$((now - newest))
  echo "$(date -Is) watchdog ok pid=$pid age=${age}s" >>"$WATCHLOG"
  if (( age > STALE_SECS )); then
    echo "$(date -Is) activity stale ${age}s; killing pid $pid and resuming" | tee -a "$LOG" >>"$WATCHLOG"
    kill "$pid" 2>/dev/null || true
    sleep 2
    kill -9 "$pid" 2>/dev/null || true
    start_crawl
  fi
done
