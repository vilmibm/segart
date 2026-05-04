#!/bin/bash
# Run the segmenter over a list of IA item identifiers and score the
# resulting TOCs against the QA corpus.
#
# Usage:
#   ./run_segment_batch.sh items.txt qa_corpus.jsonl
#
# Defaults use the project-local cache layout (~/tmp/segart/tmp/...) — see
# memory note "Use ~/tmp/segart/tmp for run cache".
#
# Env vars:
#   SEGART_SEGMENTER  — segmenter script (default segment_issue_docling.py)
#   SEGART_CACHE      — IA item cache dir (PDFs, page_numbers.json)
#   SEGART_OUT_DIR    — TOC + scores output dir
set -euo pipefail
TMP="${HOME}/tmp/segart/tmp"
ITEMS="${1:-${TMP}/sample_items.txt}"
CORPUS="${2:-${TMP}/qa_corpus.jsonl}"
OUT_DIR="${SEGART_OUT_DIR:-${TMP}/tocs}"
CACHE_DIR="${SEGART_CACHE:-${TMP}/items}"
SEGMENTER="${SEGART_SEGMENTER:-segment_issue_docling.py}"
mkdir -p "$OUT_DIR" "$CACHE_DIR"

HERE="$(cd "$(dirname "$0")" && pwd)"
export SEGART_CACHE="$CACHE_DIR"

n=0
while IFS= read -r item; do
  [ -z "$item" ] && continue
  n=$((n+1))
  echo "[$n] $item"
  "$HERE/$SEGMENTER" "$item" -o "$OUT_DIR/${item}_toc.json" 2>&1 | sed 's/^/    /'
done < "$ITEMS"

echo
echo "=== scoring $n items against $CORPUS ==="
"$HERE/score_toc.py" --corpus "$CORPUS" --toc-dir "$OUT_DIR" -o "$OUT_DIR/_scores.jsonl"
