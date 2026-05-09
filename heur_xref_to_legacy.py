#!/usr/bin/env python3
"""Translate `heuristic_toc_crossref.py` output (start_page_index/
end_page_index ints in BookReader's 0-indexed page-index coordinate) into the
legacy schema-v2 format (`page_index_ranges: [["nN","nM"]]`) so it can be
evaluated by `compare_toc_techniques.py` / `score_toc.py`.

Reads either v2 fields (`start_page_index`) or v1 (`start_leaf`); always
writes the v2 legacy schema with `page_index_ranges`.

Mirrors `llm_toc_to_legacy.py` (the analogous adapter for the LLM TOC).
"""
import argparse
import json
import time
from pathlib import Path

SEGART = Path("/Users/brewster/tmp/segart")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input", help="path to *_toc_heur_xref.json")
    ap.add_argument("--out", help="legacy-schema output path")
    args = ap.parse_args()

    src = Path(args.input)
    d = json.loads(src.read_text())

    legacy_entries = []
    for i, e in enumerate(d.get("entries") or []):
        # Read either v2 (`start_page_index`) or v1 (`start_leaf`).
        s_pi = e.get("start_page_index", e.get("start_leaf"))
        e_pi = e.get("end_page_index", e.get("end_leaf"))
        if s_pi is None: continue
        # heuristic_toc_crossref emits 0-indexed BookReader page-index
        # integers (via page_index.printed_to_br), which directly become
        # the legacy `nN` string. The LLM path uses docling page_no and
        # needs -1.
        sl = max(0, int(s_pi))
        el = max(sl, int(e_pi)) if e_pi is not None else sl
        legacy_entries.append({
            "id": f"e{i+1}",
            "type": e.get("type") or "article",
            "title": e.get("title") or "",
            "authors": e.get("authors") or None,
            "page_index_ranges": [[f"n{sl}", f"n{el}"]],
            "printed_pages": e.get("crossref_page") or None,
            "ext_ids": {"doi": e["doi"]} if e.get("doi") else {},
            "confidence": 0.7,
            "evidence": [e["_method"]] if e.get("_method") else [],
            "level": 1,
        })

    out_path = Path(args.out) if args.out else (
        SEGART / "tmp" / "tocs_compare" /
        f"{d['item']}_toc_heurxref.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({
        "schema_version": 2,
        "item": d["item"],
        "page_index_count": None,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "generator": {
            "name": "segart",
            "version": "heur_xref_v2_repair",
            "method": "crossref+page_numbers+repair_running_headers",
        },
        "entries": legacy_entries,
    }, indent=2))
    print(f"wrote {out_path}: {len(legacy_entries)} entries")


if __name__ == "__main__":
    main()
