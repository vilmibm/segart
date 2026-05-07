#!/usr/bin/env python3
"""Convert llm_toc_extract.py output → legacy segart TOC schema.

The LLM script emits start_leaf/end_leaf as integer docling page_no values
(1-indexed). The legacy schema produced by segment_issue_docling.py uses
`leaf_ranges: [["nN-1", "nM-1"]]` strings with the -1 offset used elsewhere
(see segment_issue_docling.py:739 → `start_leaf = s["page"] - 1`).

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
    """Return legacy-schema dict from an llm_toc dict."""
    entries = []
    for i, e in enumerate(src.get("entries") or [], 1):
        # Map integer leaves → "n<N-1>" strings to match segment_issue_docling.
        sl = max(0, int(e["start_leaf"]) - 1)
        el = max(sl, int(e["end_leaf"]) - 1)
        sp, ep = e.get("start_page"), e.get("end_page")
        printed_pages = None
        if sp:  # 0 sentinel → no printed page
            printed_pages = [sp, ep] if ep and ep != sp else [sp]
        entries.append({
            "id": f"e{i}",
            "type": e.get("type", "article"),
            "title": e["title"],
            "authors": [{"name": a["name"], "affiliation": None}
                        for a in (e.get("authors") or [])] or None,
            "leaf_ranges": [[f"n{sl}", f"n{el}"]],
            "printed_pages": printed_pages,
            "ext_ids": {},
            "confidence": 0.9,
            "evidence": ["llm_toc"],
            "level": 1,
        })
    return {
        "schema_version": "v0.14_llm_legacy",
        "item": src["item"],
        "leaf_count": None,
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
