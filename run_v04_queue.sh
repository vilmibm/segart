#!/bin/bash
# Parallel v0.5 segmenter over every cached item that doesn't yet have a
# v0.5+ TOC (detected by reading generator.version inside _toc.json).
# Skips items missing a PDF or page_numbers.json, and items larger than
# SEGART_MAX_PAGES.
#
# Concurrency: SEGART_PARALLEL workers (default 2). A watchdog
# (`ram_watchdog`) runs alongside and kills the YOUNGEST docling worker
# if free+inactive RAM drops below SEGART_RAM_FLOOR_MB (default 200).
# This is the safety net the 2026-05-04 freeze taught us we need.
#
# Lock-protected: a second instance exits immediately so two triggers
# can't fan out parallel docling workers.
set -euo pipefail
TMP="${HOME}/tmp/segart/tmp"
ITEMS_DIR="${SEGART_CACHE:-${TMP}/items}"
OUT_DIR="${SEGART_OUT_DIR:-${TMP}/tocs}"
LOG="${1:-${TMP}/v04_queue.log}"
MAX_PAGES="${SEGART_MAX_PAGES:-500}"
PARALLEL="${SEGART_PARALLEL:-2}"
RAM_FLOOR="${SEGART_RAM_FLOOR_MB:-200}"
LOCK="${TMP}/v04_queue.lock"
mkdir -p "$OUT_DIR"
HERE="$(cd "$(dirname "$0")" && pwd)"
export SEGART_CACHE="$ITEMS_DIR"

if ! mkdir "$LOCK" 2>/dev/null; then
  echo "$(date +%H:%M:%S) another v04_queue is already running ($LOCK exists); exiting" >> "$LOG"
  exit 0
fi

# Watchdog: poll free+inactive RAM every 5s. Below the floor, kill the
# most-recently-spawned docling worker (highest PID).
ram_watchdog() {
  while sleep 5; do
    avail=$(vm_stat | awk '/Pages free/ {f=$3} /Pages inactive/ {i=$3} END {printf "%d", (f+i)*16384/1024/1024}')
    if (( avail < RAM_FLOOR )); then
      victim=$(ps -ax -o pid,command | awk '/segment_issue_docling/ && !/awk/ && !/grep/ {print $1}' | sort -n | tail -1)
      if [[ -n "$victim" ]]; then
        echo "  WATCHDOG avail=${avail}MB < floor=${RAM_FLOOR}MB → kill youngest docling pid $victim"
        kill -TERM "$victim" 2>/dev/null || true
      fi
    fi
  done
}

needs_run() {
  local item="$1" toc="$OUT_DIR/${item}_toc.json"
  [[ ! -f "$toc" ]] && return 0
  # Re-run when the file's stamped version is older than what
  # segment_issue_docling.py currently exports — that way bumping
  # SEGMENTER_VERSION is the only thing needed to schedule a re-sweep.
  python3 - "$toc" "$HERE/segment_issue_docling.py" <<'PY' >/dev/null 2>&1 || return 0
import json, re, sys
toc = json.load(open(sys.argv[1]))
ver = (toc.get("generator") or {}).get("version", "")
script = open(sys.argv[2]).read()
m = re.search(r'SEGMENTER_VERSION\s*=\s*["\']([^"\']+)["\']', script)
cur = m.group(1) if m else ""
def parse(v):
    m = re.match(r"(\d+)\.(\d+)", v)
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)
sys.exit(0 if parse(ver) >= parse(cur) else 1)
PY
  return 1
}

run_one() {
  local item="$1"
  echo "=== START $item $(date +%H:%M:%S) ==="
  if "$HERE/segment_issue_docling.py" "$item" \
       -o "$OUT_DIR/${item}_toc.json" 2>&1 | tail -5; then
    echo "=== END   $item $(date +%H:%M:%S) ==="
  else
    echo "=== FAIL  $item $(date +%H:%M:%S) (exit $?)  ==="
  fi
}

{
  ram_watchdog &
  WATCHDOG_PID=$!
  trap 'kill $WATCHDOG_PID 2>/dev/null || true; rmdir "$LOCK" 2>/dev/null || true' EXIT

  echo "=== v0.5 queue starting $(date +%H:%M:%S) parallel=$PARALLEL ram_floor=${RAM_FLOOR}MB ==="

  # Bash 3.2-compatible: use temp files instead of associative arrays.
  STATE_DIR="$TMP/v04_queue_state"
  rm -rf "$STATE_DIR"
  mkdir -p "$STATE_DIR/queued" "$STATE_DIR/skipped"
  pids=()
  passes=0
  while :; do
    passes=$((passes + 1))
    found_work=0
    for d in "$ITEMS_DIR"/sim_*/; do
      item=$(basename "$d")
      [[ -e "$STATE_DIR/queued/$item" ]] && continue
      [[ -e "$STATE_DIR/skipped/$item" ]] && continue
      pdf="$d${item}.pdf"
      pn="$d${item}_page_numbers.json"
      if [[ ! -f "$pdf" || ! -f "$pn" ]]; then
        # Wait for download to complete in a later pass.
        continue
      fi
      pages=$(python3 -c "import json,sys; print(len(json.load(open(sys.argv[1])).get('pages',[])))" "$pn" 2>/dev/null || echo 0)
      if (( pages > MAX_PAGES )); then
        echo "  SKIP $item ($pages pages > $MAX_PAGES)"
        touch "$STATE_DIR/skipped/$item"
        continue
      fi
      if ! needs_run "$item"; then
        touch "$STATE_DIR/skipped/$item"
        continue
      fi

      # Wait until we have an open slot.
      while (( ${#pids[@]} >= PARALLEL )); do
        wait -n "${pids[@]}" 2>/dev/null || true
        new_pids=()
        for pp in "${pids[@]}"; do
          if kill -0 "$pp" 2>/dev/null; then new_pids+=("$pp"); fi
        done
        pids=("${new_pids[@]}")
      done

      run_one "$item" &
      pids+=("$!")
      touch "$STATE_DIR/queued/$item"
      found_work=1
    done

    # Drain currently-running workers before reglobbing.
    for pp in "${pids[@]}"; do wait "$pp" 2>/dev/null || true; done
    pids=()

    # Stop when there are no in-progress downloads AND we found nothing
    # to do this pass (i.e. the items dir is fully drained at v0.5+).
    in_flight=$(ps -ax -o command 2>/dev/null | grep "ia download" | grep -v grep | wc -l | tr -d ' ')
    if (( found_work == 0 && in_flight == 0 )); then
      echo "=== v0.5 queue done $(date +%H:%M:%S) (after $passes passes) ==="
      break
    fi
    if (( found_work == 0 )); then
      # No new work, but downloads still in flight — wait for one to land.
      echo "  IDLE pass $passes: $in_flight downloads in flight, sleeping 30s"
      sleep 30
    fi
  done
} >> "$LOG" 2>&1
