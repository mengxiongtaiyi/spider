#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
SRC="$(cd "$ROOT/../需求3的任务2-Shorts/images" && pwd)"
DST="$ROOT/images"
mkdir -p "$DST"
echo "Copying from $SRC -> $DST"
cp -a "$SRC/." "$DST/"
echo "Done. covers=$(ls "$DST/covers" 2>/dev/null | wc -l) avatars=$(ls "$DST/avatars" 2>/dev/null | wc -l)"
