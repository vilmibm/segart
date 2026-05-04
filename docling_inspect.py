#!/usr/bin/env python3
"""Run docling on a single item and dump every text item with its label,
bbox, page, and text — for debugging why article starts get missed.

Usage:
  ./docling_inspect.py <item> [--pages 100,101,102] [--label section_header]
"""
import argparse
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from segment_issue_docling import (
    docling_convert,
    fetch_pdf,
    fetch_page_numbers,
    text_label,
    page_no_of,
    bbox_of,
    CACHE,
)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("item")
    p.add_argument("--pages", help="Comma-separated page numbers (1-indexed)")
    p.add_argument("--label", help="Filter to only this docling label")
    p.add_argument("--cache-dir", default=str(CACHE))
    args = p.parse_args()

    cache_dir = Path(args.cache_dir)
    pdf = fetch_pdf(args.item, cache_dir)
    fetch_page_numbers(args.item, cache_dir)

    print(f"converting {pdf}...", file=sys.stderr, flush=True)
    doc = docling_convert(pdf)
    print(f"  done", file=sys.stderr)

    pages_filter = None
    if args.pages:
        pages_filter = set(int(x) for x in args.pages.split(","))

    items = []
    for t in doc.texts:
        p_ = page_no_of(t)
        if pages_filter and p_ not in pages_filter:
            continue
        label = text_label(t)
        if args.label and label != args.label:
            continue
        bb = bbox_of(t)
        text = (t.text or "").strip()
        if not text:
            continue
        items.append((p_, bb, label, text))

    items.sort(key=lambda r: (r[0] or 0, -(r[1][1] if r[1] else 0)))
    for p_, bb, label, text in items:
        bb_s = (
            f"({bb[0]:.0f},{bb[1]:.0f})-({bb[2]:.0f},{bb[3]:.0f})"
            if bb else "?"
        )
        print(f"  p{p_:>3} [{label:<20}] {bb_s:<24} {text[:80]!r}")


if __name__ == "__main__":
    main()
