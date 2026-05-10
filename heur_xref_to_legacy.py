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
import re
import sys
import time
from pathlib import Path

SEGART = Path("/Users/brewster/tmp/segart")
sys.path.insert(0, str(SEGART))
sys.path.insert(0, str(SEGART / "tools"))
from segart_version import software_versions  # noqa: E402


_FRONTMATTER_PATTERNS = re.compile(
    r"^(preface|foreword|introduction|editorial board|contents"
    r"|table of contents|editor'?s note|from the editor|editorial"
    r"|publication information|masthead|in this issue)\b",
    re.IGNORECASE,
)
_BACKMATTER_PATTERNS = re.compile(
    r"^(index|subject index|author index|bibliography"
    r"|references|errata|colophon|advertisements?|back matter)\b",
    re.IGNORECASE,
)


def _classify_entry_type(title, default="article"):
    """Re-type entries whose titles match standard frontmatter / backmatter
    labels. Crossref types these as `journal-article` (Elsevier and others
    DOI even the preface/index pages), but for a TOC consumer it's useful
    to distinguish so a librarian / BookReader can deprioritize."""
    t = (title or "").strip()
    if _FRONTMATTER_PATTERNS.match(t): return "frontmatter"
    if _BACKMATTER_PATTERNS.match(t):  return "backmatter"
    return default


def _split_printed_range(s):
    """Convert a Crossref `page` string ('263-279', '263', 'S1-S4') to
    the v2 schema's [[start, end]] string-pair form. Returns None if
    nothing useful can be extracted."""
    if not s: return None
    s = str(s).strip()
    if not s: return None
    # Single page or hyphen-delimited range
    m = re.match(r"^([A-Za-z]?\d+[A-Za-z]?)\s*[-\u2013]\s*([A-Za-z]?\d+[A-Za-z]?)$", s)
    if m:
        return [[m.group(1), m.group(2)]]
    # Single page
    m = re.match(r"^([A-Za-z]?\d+[A-Za-z]?)$", s)
    if m:
        return [[m.group(1), m.group(1)]]
    # Fallback: leave as a single-pair best-guess
    parts = re.split(r"\s*[-\u2013]\s*", s, maxsplit=1)
    if len(parts) == 2:
        return [[parts[0].strip(), parts[1].strip()]]
    return [[s, s]]


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
            "type": _classify_entry_type(e.get("title"),
                                         default=e.get("type") or "article"),
            "title": e.get("title") or "",
            "authors": e.get("authors") or None,
            "page_index_ranges": [[f"n{sl}", f"n{el}"]],
            "printed_pages": _split_printed_range(e.get("crossref_page")),
            "ext_ids": {"doi": e["doi"]} if e.get("doi") else {},
            "confidence": 0.7,
            "evidence": [e["_method"]] if e.get("_method") else [],
            "level": 1,
        })

    # Compute page_index_count from the item's scandata via page_index module.
    page_index_count = None
    item = d.get("item")
    if item:
        try:
            from page_index import PageIndex
            pi = PageIndex.for_item(item, fetch=True)
            page_index_count = pi.visible_count
        except Exception as e:
            print(f"  WARN: could not derive page_index_count: {e}",
                  file=sys.stderr)

    out_path = Path(args.out) if args.out else (
        SEGART / "tmp" / "tocs_compare" /
        f"{d['item']}_toc_heurxref.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({
        "schema_version": 2,
        "item": d["item"],
        "issn": d.get("issn"),
        "volume": d.get("volume"),
        "issue": d.get("issue"),
        "year": d.get("year"),
        "page_index_count": page_index_count,
        "software_versions": software_versions(),
        "entries": legacy_entries,
    }, indent=2))
    print(f"wrote {out_path}: {len(legacy_entries)} entries")


if __name__ == "__main__":
    main()
