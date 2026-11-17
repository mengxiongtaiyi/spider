#!/usr/bin/env bash
# Keep watchdog + store-flush alive. Never kills a healthy crawl.
set +e
cd "$(dirname "$0")"
SUP_LOG=data/ameede/ensure_daemons.log
mkdir -p data/ameede
touch "$SUP_LOG"
export STALE_SECS="${STALE_SECS:-75}"
export CHECK_SECS="${CHECK_SECS:-15}"
export INTERVAL_SECS="${INTERVAL_SECS:-600}"

log() { printf '%s %s\n' "$(date -Is)" "$*" >>"$SUP_LOG" 2>/dev/null; printf '%s\n' "$*"; }

alive_hb() {
  local f="$1" max_age="$2"
  python3 - "$f" "$max_age" <<'PY'
import sys, time
from pathlib import Path
p, max_age = Path(sys.argv[1]), int(sys.argv[2])
if not p.exists():
    raise SystemExit(1)
age = time.time() - p.stat().st_mtime
raise SystemExit(0 if age <= max_age else 1)
PY
}

ensure_watchdog() {
  if pgrep -f 'bash .*watchdog_only\.sh' >/dev/null 2>&1 && alive_hb /tmp/ameede_watchdog_heartbeat 45; then
    return 0
  fi
  # Heartbeat stale or process missing — restart watchdog only (it adopts healthy crawl).
  pkill -9 -f 'bash .*watchdog_only\.sh' 2>/dev/null || true
  sleep 1
  nohup bash ./watchdog_only.sh >>data/ameede/watchdog.log 2>&1 &
  disown || true
  log "restarted watchdog pid=$!"
}

ensure_flush() {
  if pgrep -f 'bash .*flush_ameede_live\.sh' >/dev/null 2>&1 && alive_hb /tmp/ameede_flush_heartbeat 720; then
    return 0
  fi
  pkill -9 -f 'bash .*flush_ameede_live\.sh' 2>/dev/null || true
  sleep 1
  nohup bash ./flush_ameede_live.sh >>data/ameede/flush_live.log 2>&1 &
  disown || true
  log "restarted flush pid=$!"
}

printf '%s\n' "$$" >/tmp/ameede_ensure.pid
log "ensure_daemons online pid=$$ STALE_SECS=$STALE_SECS INTERVAL_SECS=$INTERVAL_SECS"
ensure_watchdog
ensure_flush
while true; do
  sleep 30
  ensure_watchdog
  ensure_flush
  date -u +%Y-%m-%dT%H:%M:%SZ >/tmp/ameede_ensure_heartbeat
done
