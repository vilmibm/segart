#!/usr/bin/env python3
"""
Compare docling-with-tables extraction quality across OCR engines.

For each item that has a docling cache produced with do_table_structure
enabled, measure metrics that proxy "did docling get clean structured
tables out of this OCR's embedded text":

  - n_doc_index_tables:    how many tables docling labeled document_index
  - n_cells_total:         total table_cells across those tables
  - n_cells_with_text:     cells whose text field is non-empty
  - n_anchored_rows:       rows where a right-column page-number anchors
                           one or more title items (real-TOC pattern)

Group items by OCR engine recorded in IA metadata (loaded from
items_ocr_engine.json). Print per-engine distributions.

Usage:
  ./compare_ocr_engines.py              # use built-in mapping file
  ./compare_ocr_engines.py --items ...  # restrict to listed items
"""
import argparse
import gzip
import json
import os
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

ITEMS = Path.home() / "tmp" / "segart" / "tmp" / "items"
ENGINE_MAP = Path.home() / "tmp" / "segart" / "tmp" / "items_ocr_engine.json"

PG_RE = re.compile(r"^\s*(\d{1,4})\s*$")


def cache_metrics(cache_path):
    try:
        with gzip.open(cache_path, "rt") as fh:
            d = json.load(fh)
    except Exception:
        return None
    by_page = {}
    for t in d.get("texts", []):
        prov = (t.get("prov") or [{}])[0]
        pn = prov.get("page_no")
        if pn is None: continue
        by_page.setdefault(pn, []).append(t)
    n_tables = 0
    n_cells_total = 0
    n_cells_text = 0
    n_anchored = 0
    for tbl in d.get("tables") or []:
        if tbl.get("label") != "document_index":
            continue
        n_tables += 1
        data = tbl.get("data") or {}
        cells = data.get("table_cells") or []
        n_cells_total += len(cells)
        n_cells_text += sum(1 for c in cells if (c.get("text") or "").strip())
        # Anchored rows: rows where one cell is a small integer
        rows = defaultdict(list)
        for c in cells:
            r = c.get("row_offset_idx") or c.get("start_row_offset_idx")
            txt = (c.get("text") or "").strip()
            if r is None or not txt: continue
            rows[r].append(txt)
        for r, vals in rows.items():
            has_pn = any(PG_RE.match(v) and 1 <= int(PG_RE.match(v).group(1)) <= 2000
                         for v in vals)
            has_title = any(re.search(r"[A-Za-z]{4,}", v) for v in vals)
            if has_pn and has_title:
                n_anchored += 1
    return {
        "n_doc_index_tables": n_tables,
        "n_cells_total": n_cells_total,
        "n_cells_with_text": n_cells_text,
        "cell_text_rate": (
            n_cells_text / n_cells_total if n_cells_total else 0.0
        ),
        "n_anchored_rows": n_anchored,
    }


def normalize_engine(s):
    if not s or s == "(none)":
        return "(none)"
    s = s.lower()
    if "tesseract" in s:
        return "tesseract"
    if "abbyy" in s:
        return "abbyy"
    if "google-cloud-vision" in s:
        return "google-cloud-vision"
    return s


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--items", nargs="*",
                   help="Restrict to these item IDs (default: all cached)")
    p.add_argument("--lookup-missing", action="store_true",
                   help="Hit IA metadata for items not in the saved map")
    args = p.parse_args()

    engine_map = {}
    if ENGINE_MAP.exists():
        engine_map = json.load(open(ENGINE_MAP))

    candidates = []
    iterable = (
        [ITEMS / x for x in args.items] if args.items
        else sorted(ITEMS.glob("sim_*/")) + sorted(d for d in ITEMS.iterdir()
                                                  if d.is_dir() and not d.name.startswith("sim_"))
    )
    for d in iterable:
        if not d.exists():
            continue
        item = d.name
        cache = d / f"{item}_docling.json.gz"
        if not cache.exists():
            continue
        m = cache_metrics(cache)
        if m is None:
            continue
        # Only include items where docling actually produced structured
        # cells. Items with no document_index tables, or with tables but
        # no populated cells (pre-table_structure caches), aren't
        # comparable for the engine question — they're absent for
        # reasons unrelated to OCR quality.
        if m["n_cells_with_text"] == 0:
            continue
        engine = engine_map.get(item)
        if engine is None and args.lookup_missing:
            import urllib.request
            try:
                req = urllib.request.Request(
                    f"https://archive.org/metadata/{item}",
                    headers={"User-Agent": "segart/0.1"},
                )
                with urllib.request.urlopen(req, timeout=15) as r:
                    md = json.load(r).get("metadata") or {}
                engine = md.get("ocr") or "(none)"
                engine_map[item] = engine
            except Exception:
                engine = "(none)"
        engine_short = normalize_engine(engine)
        candidates.append((item, engine, engine_short, m))

    if args.lookup_missing and engine_map:
        json.dump(engine_map, open(ENGINE_MAP, "w"), indent=2)

    by_engine = defaultdict(list)
    for item, _eng, eng_short, m in candidates:
        by_engine[eng_short].append((item, m))

    print(f"Compared {len(candidates)} items with structured-table caches\n")
    for eng in sorted(by_engine):
        rows = by_engine[eng]
        n = len(rows)
        cell_rates = [m["cell_text_rate"] for _, m in rows]
        anchors = [m["n_anchored_rows"] for _, m in rows]
        tables = [m["n_doc_index_tables"] for _, m in rows]
        print(f"=== {eng} (n={n}) ===")
        print(f"  doc_index tables/item:     "
              f"median={statistics.median(tables):.1f}, "
              f"mean={statistics.mean(tables):.1f}")
        print(f"  cells with text rate:      "
              f"median={statistics.median(cell_rates):.2f}, "
              f"mean={statistics.mean(cell_rates):.2f}")
        print(f"  anchored TOC rows/item:    "
              f"median={statistics.median(anchors):.1f}, "
              f"mean={statistics.mean(anchors):.1f}")
        print()

    print("=== sample per-item rows ===")
    print(f"{'engine':>22} {'tab':>3} {'cells':>5} {'with':>5} {'anchor':>6}  item")
    for item, eng, eng_short, m in candidates[:25]:
        print(f"{eng_short:>22} {m['n_doc_index_tables']:>3} "
              f"{m['n_cells_total']:>5} {m['n_cells_with_text']:>5} "
              f"{m['n_anchored_rows']:>6}  {item[:50]}")


if __name__ == "__main__":
    main()
