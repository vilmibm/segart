#!/bin/bash
# Run segment_issue.py over a list of IA item identifiers and score the
# resulting TOCs against the QA corpus.
#
# Usage:
#   ./run_segment_batch.sh items.txt /tmp/qa_corpus.jsonl
#
# Default args land on a sample list cached at /tmp/sample_items.txt and
# the corpus at /tmp/qa_corpus.jsonl.
set -euo pipefail
ITEMS="${1:-/tmp/sample_items.txt}"
CORPUS="${2:-/tmp/qa_corpus.jsonl}"
OUT_DIR="${SEGART_OUT_DIR:-/tmp/segart_tocs}"
mkdir -p "$OUT_DIR"

HERE="$(cd "$(dirname "$0")" && pwd)"

n=0
while IFS= read -r item; do
  [ -z "$item" ] && continue
  n=$((n+1))
  echo "[$n] $item"
  "$HERE/segment_issue.py" "$item" -o "$OUT_DIR/${item}_toc.json" 2>&1 | sed 's/^/    /'
done < "$ITEMS"

echo
echo "=== scoring $n items against $CORPUS ==="
"$HERE/score_toc.py" --corpus "$CORPUS" --toc-dir "$OUT_DIR" -o "$OUT_DIR/_scores.jsonl"
