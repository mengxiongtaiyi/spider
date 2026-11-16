#!/usr/bin/env bash
# Restart ameede full crawl if the log stalls (hung HTTPS sockets).
set -euo pipefail
cd "$(dirname "$0")"
LOG="data/ameede/full_crawl.log"
STALE_SECS="${STALE_SECS:-120}"
CMD=(python3 crawl_ameede.py --min-delay 0.35 --max-delay 0.75 --xlsx-every-pages 20)

export PYTHONUNBUFFERED=1
export PYTHONPATH="$(pwd)"

mkdir -p data/ameede

start_crawl() {
  echo "$(date -Is) starting crawl" | tee -a "$LOG"
  "${CMD[@]}" >>"$LOG" 2>&1 &
  echo $! > data/ameede/crawl.pid
}

if [[ ! -f data/ameede/crawl.pid ]] || ! kill -0 "$(cat data/ameede/crawl.pid)" 2>/dev/null; then
  start_crawl
fi

while true; do
  sleep 30
  pid="$(cat data/ameede/crawl.pid 2>/dev/null || true)"
  if [[ -z "${pid}" ]] || ! kill -0 "$pid" 2>/dev/null; then
    echo "$(date -Is) crawl not running; restarting" | tee -a "$LOG"
    start_crawl
    continue
  fi
  if [[ -f "$LOG" ]]; then
    now=$(date +%s)
    mtime=$(stat -c %Y "$LOG")
    age=$((now - mtime))
    if (( age > STALE_SECS )); then
      echo "$(date -Is) log stale ${age}s; killing pid $pid and resuming" | tee -a "$LOG"
      kill "$pid" 2>/dev/null || true
      sleep 2
      kill -9 "$pid" 2>/dev/null || true
      start_crawl
    fi
  fi
done
