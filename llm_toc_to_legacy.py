#!/usr/bin/env python3
"""Convert llm_toc_extract.py output → legacy segart TOC schema.

The LLM script emits start_page_index/end_page_index as integer docling
page_no values (1-indexed). The legacy schema uses
`page_index_ranges: [["nN-1", "nM-1"]]` strings (0-indexed) — see
segment_issue_docling.py:739 → `start_leaf = s["page"] - 1` (note: that
file's `start_leaf` identifier is misnamed; it's a page-index).

Reads either the new schema (`llm_toc_v2`: `start_page_index` etc) or the
old (`llm_toc_v1`: `start_leaf` etc); always writes the new legacy schema
(`schema_version: 2`) with `page_index_ranges`.

Convert every `<item>_toc_llm.json` we find under tmp/tocs/ into a
`<item>_toc_llm_legacy.json` that augment_evidence.py and score_toc.py can
consume directly. Original files are left untouched.
"""
import argparse
import json
import sys
from pathlib import Path

TOCS_DEFAULT = Path.home() / "tmp" / "segart" / "tmp" / "tocs"


def convert(src):
    """Return legacy-schema dict from an llm_toc dict (v1 or v2)."""
    entries = []
    for i, e in enumerate(src.get("entries") or [], 1):
        # Read either v2 (start_page_index) or v1 (start_leaf).
        s_pi = e.get("start_page_index", e.get("start_leaf"))
        e_pi = e.get("end_page_index", e.get("end_leaf"))
        # Map 1-indexed docling page_no → 0-indexed legacy "n<N>" strings.
        sl = max(0, int(s_pi) - 1)
        el = max(sl, int(e_pi) - 1)
        sp = e.get("start_page_number", e.get("start_page"))
        ep = e.get("end_page_number", e.get("end_page"))
        # v2 printed_pages: array of [start, end] string pairs, mirroring
        # page_index_ranges. 0 is the no-printed-page sentinel.
        if sp:
            end = ep if ep else sp
            printed_pages = [[str(sp), str(end)]]
        else:
            printed_pages = None
        entries.append({
            "id": f"e{i}",
            "type": e.get("type", "article"),
            "title": e["title"],
            "authors": [{"name": a["name"], "affiliation": None}
                        for a in (e.get("authors") or [])] or None,
            "page_index_ranges": [[f"n{sl}", f"n{el}"]],
            "printed_pages": printed_pages,
            "ext_ids": {},
            "confidence": 0.9,
            "evidence": ["llm_toc"],
            "level": 1,
        })
    return {
        "schema_version": 2,
        "item": src["item"],
        "page_index_count": None,
        "generated_at": src.get("generated_at"),
        "generator": f"llm_toc_extract.py + adapter (model={src.get('model')})",
        "entries": entries,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--toc-dir", default=str(TOCS_DEFAULT))
    ap.add_argument("--src", help="Single _toc_llm.json (skip directory walk)")
    args = ap.parse_args()

    if args.src:
        srcs = [Path(args.src)]
    else:
        srcs = sorted(Path(args.toc_dir).glob("*_toc_llm.json"))

    if not srcs:
        sys.exit("no _toc_llm.json files found")

    for src in srcs:
        d = json.loads(src.read_text())
        legacy = convert(d)
        dst = src.with_name(src.name.replace("_toc_llm.json",
                                              "_toc_llm_legacy.json"))
        dst.write_text(json.dumps(legacy, indent=2))
        print(f"{src.name} → {dst.name}: {len(legacy['entries'])} entries",
              file=sys.stderr)


if __name__ == "__main__":
    main()
