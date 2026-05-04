#!/bin/bash
# Run filter_toc.py over every TOC in tmp/tocs/, then score against the
# corpus. Two passes: raw (no filter) and filtered, to compare.
#
# Usage:
#   ./eval_pipeline.sh
set -euo pipefail
TMP="${HOME}/tmp/segart/tmp"
TOCS_DIR="${TMP}/tocs"
CORPUS="${TMP}/qa_corpus.jsonl"
HERE="$(cd "$(dirname "$0")" && pwd)"

# Stage raw copies aside so we can score raw vs filtered side by side.
RAW_DIR="${TMP}/tocs_raw_snapshot"
FILTERED_DIR="${TMP}/tocs_filtered"
mkdir -p "$RAW_DIR" "$FILTERED_DIR"

# If a _raw.json exists for an item, prefer that as the raw input (it
# bypasses the segmenter's internal denylist/dedup); otherwise fall back
# to copying the _toc.json. We also re-emit raw_candidates as a TOC-shaped
# file so score_toc and filter_toc can consume it uniformly.
python3 - <<PY
import json, os, glob
TOCS = "${TOCS_DIR}"
RAW = "${RAW_DIR}"
for path in sorted(glob.glob(os.path.join(TOCS, "*_toc.json"))):
    item_toc = os.path.basename(path)
    item = item_toc.replace("_toc.json", "")
    raw_path = os.path.join(TOCS, f"{item}_raw.json")
    out_path = os.path.join(RAW, item_toc)
    if os.path.exists(raw_path):
        rd = json.load(open(raw_path))
        leaf_count = rd.get("leaf_count") or 0
        cands = rd.get("raw_candidates") or []
        cands.sort(key=lambda c: c.get("page", 0))
        entries = []
        for i, c in enumerate(cands):
            start = (c.get("page") or 1) - 1
            end = (cands[i+1]["page"] - 2) if i+1 < len(cands) else max(leaf_count - 1, start)
            if end < start: end = start
            entries.append({
                "id": f"e{i+1}",
                "type": "article",
                "title": c.get("title"),
                "authors": [{"name": a["name"], "affiliation": None} for a in (c.get("authors") or [])],
                "leaf_ranges": [[f"n{start}", f"n{end}"]],
                "printed_pages": None,
                "ext_ids": {},
                "confidence": 0.7,
                "evidence": ["ocr-raw"],
                "level": 1,
            })
        toc = {
            "schema_version": 1,
            "item": item,
            "leaf_count": leaf_count,
            "generator": {"name": "segart", "version": rd.get("generator_version") or "raw", "method": "raw"},
            "entries": entries,
        }
        json.dump(toc, open(out_path, "w"), indent=2)
    else:
        # No raw dump — just copy the _toc.json as-is.
        with open(path) as f, open(out_path, "w") as g:
            g.write(f.read())
PY

# Filter: pull from raw snapshot, write to filtered dir.
echo "=== filtering ==="
python3 - <<PY
import json, os, glob, shutil
src = "${RAW_DIR}"
dst = "${FILTERED_DIR}"
for fn in os.listdir(src):
    shutil.copy(os.path.join(src, fn), os.path.join(dst, fn))
PY
"$HERE/filter_toc.py" "$FILTERED_DIR"/*_toc.json --in-place 2>&1 | tail -10

echo
echo "=== RAW scores ==="
"$HERE/score_toc.py" --corpus "$CORPUS" --toc-dir "$RAW_DIR" -o "$RAW_DIR/_scores.jsonl" 2>&1 | tail -20

echo
echo "=== FILTERED scores ==="
"$HERE/score_toc.py" --corpus "$CORPUS" --toc-dir "$FILTERED_DIR" -o "$FILTERED_DIR/_scores.jsonl" 2>&1 | tail -20
