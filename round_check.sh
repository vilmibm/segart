#!/bin/bash
# End-of-round inspection: score current TOCs (raw & filtered), summarize
# per-item hit-rates, dump miss categories, save a snapshot of key outputs.
#
# Usage:
#   ./round_check.sh <round-tag>
#   ./round_check.sh v0.5
set -euo pipefail
TAG="${1:-round-$(date +%H%M)}"
TMP="${HOME}/tmp/segart/tmp"
TOCS="${TMP}/tocs"
CORPUS="${TMP}/qa_corpus.jsonl"
HERE="$(cd "$(dirname "$0")" && pwd)"
SNAP="${TMP}/rounds/${TAG}"
mkdir -p "$SNAP"

echo "=== Round $TAG snapshot — $(date +%H:%M:%S) ==="
echo "--- TOC versions present ---"
for f in "$TOCS"/*_toc.json; do
  python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print((d.get('generator') or {}).get('version','?'))" "$f"
done | sort | uniq -c

# 1. Raw scoring (no filter)
echo
echo "--- RAW scores ---"
"$HERE/score_toc.py" --corpus "$CORPUS" --toc-dir "$TOCS" -o "$SNAP/raw_scores.jsonl" 2>&1 | tail -15

# 2. Filtered scoring (apply filter to a side copy)
FILTERED="$SNAP/tocs_filtered"
mkdir -p "$FILTERED"
cp "$TOCS"/*_toc.json "$FILTERED"/ 2>/dev/null || true
"$HERE/filter_toc.py" "$FILTERED"/*_toc.json --in-place 2>&1 | tail -8

echo
echo "--- FILTERED scores ---"
"$HERE/score_toc.py" --corpus "$CORPUS" --toc-dir "$FILTERED" -o "$SNAP/filtered_scores.jsonl" 2>&1 | tail -15

# 3. Per-item summary
echo
echo "--- per-item summary (FILTERED) ---"
"$HERE/summary_report.py" --corpus "$CORPUS" --tocs-dir "$FILTERED" 2>&1 | tail -40

# 4. Miss categories on filtered
echo
echo "--- miss categories (FILTERED) ---"
"$HERE/categorize_misses.py" --scores "$SNAP/filtered_scores.jsonl" \
                              --tocs-dir "$FILTERED" --limit 0 2>&1 | tail -10

echo
echo "Snapshot saved to $SNAP"
