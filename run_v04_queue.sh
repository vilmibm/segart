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
set -eo pipefail
# Note: -u dropped because bash 3.2 trips on `${arr[@]}` when arr is empty.
TMP="${HOME}/tmp/segart/tmp"
ITEMS_DIR="${SEGART_CACHE:-${TMP}/items}"
OUT_DIR="${SEGART_OUT_DIR:-${TMP}/tocs}"
LOG="${1:-${TMP}/v04_queue.log}"
MAX_PAGES="${SEGART_MAX_PAGES:-500}"
PARALLEL="${SEGART_PARALLEL:-2}"
RAM_FLOOR="${SEGART_RAM_FLOOR_MB:-200}"
SWAP_FLOOR="${SEGART_SWAP_FLOOR_MB:-512}"
WAIT_MARGIN="${SEGART_WAIT_MARGIN_MB:-256}"
BIG_PAGES="${SEGART_BIG_PAGES:-250}"
LOCK="${TMP}/v04_queue.lock"
mkdir -p "$OUT_DIR"
HERE="$(cd "$(dirname "$0")" && pwd)"
export SEGART_CACHE="$ITEMS_DIR"

if ! mkdir "$LOCK" 2>/dev/null; then
  echo "$(date +%H:%M:%S) another v04_queue is already running ($LOCK exists); exiting" >> "$LOG"
  exit 0
fi

# Watchdog: poll free+inactive RAM and swap-free every 5s. If either drops
# below its floor, kill the most-recently-spawned docling worker (highest
# PID). Swap floor was added after the 2026-05-05 panic, where compressor
# segments hit 100% before the RAM floor tripped.
ram_watchdog() {
  while sleep 5; do
    avail=$(vm_stat | awk '/Pages free/ {f=$3} /Pages inactive/ {i=$3} END {printf "%d", (f+i)*16384/1024/1024}')
    swap_free=$(sysctl -n vm.swapusage 2>/dev/null | sed -n 's/.*free = \([0-9.]*\)M.*/\1/p' | awk '{printf "%d", $1}')
    reason=""
    if (( avail < RAM_FLOOR )); then
      reason="ram avail=${avail}MB < floor=${RAM_FLOOR}MB"
    elif [[ -n "$swap_free" ]] && (( swap_free < SWAP_FLOOR )); then
      reason="swap free=${swap_free}MB < floor=${SWAP_FLOOR}MB"
    fi
    if [[ -n "$reason" ]]; then
      victim=$(ps -ax -o pid,command | awk '/segment_issue_docling/ && !/awk/ && !/grep/ {print $1}' | sort -n | tail -1)
      if [[ -n "$victim" ]]; then
        echo "  WATCHDOG $reason → kill youngest docling pid $victim"
        kill -TERM "$victim" 2>/dev/null || true
      fi
    fi
  done
}

# Pre-launch gate: block until both RAM and swap are above floor + margin.
# Without this, the watchdog can kill worker N, then the queue immediately
# starts worker N+1 before the kernel reclaims memory — cascading FAILs.
wait_for_headroom() {
  local warned=""
  while true; do
    local avail swap_free
    avail=$(vm_stat | awk '/Pages free/ {f=$3} /Pages inactive/ {i=$3} END {printf "%d", (f+i)*16384/1024/1024}')
    swap_free=$(sysctl -n vm.swapusage 2>/dev/null | sed -n 's/.*free = \([0-9.]*\)M.*/\1/p' | awk '{printf "%d", $1}')
    if (( avail >= RAM_FLOOR + WAIT_MARGIN )) && [[ -n "$swap_free" ]] && (( swap_free >= SWAP_FLOOR + WAIT_MARGIN )); then
      [[ -n "$warned" ]] && echo "  PRE-LAUNCH cleared: avail=${avail}MB swap_free=${swap_free}MB"
      return 0
    fi
    if [[ -z "$warned" ]]; then
      echo "  PRE-LAUNCH waiting: avail=${avail}MB swap_free=${swap_free}MB (need >=$(( RAM_FLOOR + WAIT_MARGIN ))MB ram / >=$(( SWAP_FLOOR + WAIT_MARGIN ))MB swap)"
      warned=1
    fi
    sleep 10
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
  local item="$1" pages="$2"
  local device_args=()
  local mode_note=""
  if (( pages > BIG_PAGES )); then
    device_args+=(--device cpu)
    mode_note=" (cpu, $pages pages)"
  fi
  echo "=== START $item $(date +%H:%M:%S)${mode_note} ==="
  if "$HERE/segment_issue_docling.py" "$item" \
       -o "$OUT_DIR/${item}_toc.json" "${device_args[@]}" 2>&1 | tail -5; then
    echo "=== END   $item $(date +%H:%M:%S) ==="
  else
    echo "=== FAIL  $item $(date +%H:%M:%S) (exit $?)  ==="
  fi
}

{
  ram_watchdog &
  WATCHDOG_PID=$!
  trap 'kill $WATCHDOG_PID 2>/dev/null || true; rmdir "$LOCK" 2>/dev/null || true' EXIT

  echo "=== v0.5 queue starting $(date +%H:%M:%S) parallel=$PARALLEL ram_floor=${RAM_FLOOR}MB swap_floor=${SWAP_FLOOR}MB wait_margin=${WAIT_MARGIN}MB big_pages=${BIG_PAGES} ==="

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

      wait_for_headroom
      run_one "$item" "$pages" &
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
