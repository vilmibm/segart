#!/usr/bin/env python3
"""
Compare a v0.13-style docling cache (OCR off, table_structure off)
against an OCR + table_structure variant produced by
test_docling_with_ocr.py.

Reports per-item:
  - whether document_index tables have populated cell.text
  - the table-cell-derived TOC row count (the "real" structured count)
  - the anchored-row extraction count from each cache
  - diff samples
"""
import argparse
import gzip
import json
import re
import sys
from pathlib import Path

CACHE = Path.home() / "tmp" / "segart" / "tmp" / "items"


def load_cache(item, suffix):
    path = CACHE / item / f"{item}_docling{suffix}.json.gz"
    if not path.exists():
        return None
    with gzip.open(path, "rt") as fh:
        return json.load(fh)


def table_summary(d):
    """Return list of dicts describing each document_index table."""
    rows = []
    for tbl in d.get("tables", []):
        if tbl.get("label") != "document_index":
            continue
        prov = (tbl.get("prov") or [{}])[0]
        page = prov.get("page_no")
        data = tbl.get("data") or {}
        cells = data.get("table_cells") or []
        nr = data.get("num_rows", 0)
        nc = data.get("num_cols", 0)
        with_text = sum(1 for c in cells if (c.get("text") or "").strip())
        rows.append({
            "page": page,
            "n_cells": len(cells),
            "n_cells_with_text": with_text,
            "num_rows": nr,
            "num_cols": nc,
            "cells_sample": [c.get("text") for c in cells[:8]],
        })
    return rows


def extract_table_cells_as_rows(d):
    """If table cells have populated text, reassemble each table into
    visual rows by row_offset_idx. Returns list of (page, row_strings)."""
    out = []
    for tbl in d.get("tables", []):
        if tbl.get("label") != "document_index":
            continue
        page = ((tbl.get("prov") or [{}])[0]).get("page_no")
        data = tbl.get("data") or {}
        cells = data.get("table_cells") or []
        rows = {}
        for c in cells:
            txt = (c.get("text") or "").strip()
            if not txt:
                continue
            r = c.get("row_offset_idx") or c.get("start_row_offset_idx")
            col = c.get("col_offset_idx") or c.get("start_col_offset_idx") or 0
            if r is None:
                continue
            rows.setdefault(r, []).append((col, txt))
        for r in sorted(rows):
            cells_sorted = sorted(rows[r])
            joined = " | ".join(t for _, t in cells_sorted)
            out.append((page, joined))
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("item")
    args = p.parse_args()

    base = load_cache(args.item, "")
    aug = load_cache(args.item, "_ocrtbl")
    if base is None:
        print(f"baseline cache (no suffix) missing for {args.item}", file=sys.stderr)
    if aug is None:
        print(f"OCR+tables cache (_ocrtbl) missing for {args.item}", file=sys.stderr)
        return 1

    print(f"=== {args.item} ===")
    print()
    print("baseline (do_ocr=False, do_tables=False):")
    if base:
        for s in table_summary(base):
            print(f"  page {s['page']:3}: {s['n_cells']} cells, "
                  f"{s['n_cells_with_text']} with text "
                  f"({s['num_rows']}r × {s['num_cols']}c)")
            for c in s["cells_sample"]:
                if c: print(f"    cell: {c[:60]!r}")
    print()
    print("OCR+tables (do_ocr=True, do_table_structure=True):")
    for s in table_summary(aug):
        print(f"  page {s['page']:3}: {s['n_cells']} cells, "
              f"{s['n_cells_with_text']} with text "
              f"({s['num_rows']}r × {s['num_cols']}c)")
        for c in s["cells_sample"]:
            if c: print(f"    cell: {c[:60]!r}")

    print()
    print("=== full table-cell rows (OCR+tables) ===")
    for page, row in extract_table_cells_as_rows(aug):
        print(f"  p{page}  {row[:120]!r}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
