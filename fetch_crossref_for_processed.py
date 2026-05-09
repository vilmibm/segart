#!/usr/bin/env python3
"""Fetch and cache Crossref data for every item that has a docling cache.

For each <item>_docling.json.gz on disk:
  1. Find the item's (issn, year, vol, iss) from any ILL row
     (post-2024-04 native fields preferred; cover_text fallback)
  2. Hit Crossref unless we already have it cached
  3. Cache file lives at tmp/crossref_cache/<issn>_<year>_<vol>_<iss>.json

Outputs a summary table.
"""
import csv
import glob
import json
import os
import re
import sys
import time

from crossref_coverage_sample import crossref_titles, ISSN_SHAPE, derive_metadata

SEGART = "/Users/brewster/tmp/segart"
ITEMS_DIR = f"{SEGART}/tmp/items"
CACHE_DIR = f"{SEGART}/tmp/crossref_cache"


def find_processed_items():
    """Items with a docling cache on disk."""
    out = []
    for p in sorted(glob.glob(f"{ITEMS_DIR}/*/*_docling.json.gz")):
        item = os.path.basename(p).replace("_docling.json.gz", "")
        out.append(item)
    return out


def derive_for_item(item):
    """Walk ILL CSVs and return the first usable (issn, year, vol, iss) for
    `item`. Returns None if none is derivable."""
    for path in sorted(glob.glob(f"{SEGART}/tmp/ill_logs/*.csv")):
        with open(path, newline='') as fh:
            for row in csv.DictReader(fh):
                if row.get("source_identifier") != item: continue
                md = derive_metadata(row)
                if md: return md
    return None


def main():
    items = find_processed_items()
    print(f"processed items with docling cache: {len(items)}", file=sys.stderr)

    n_already = n_fetched = n_no_md = n_failed = n_empty = 0
    rows = []
    for i, item in enumerate(items, 1):
        md = derive_for_item(item)
        if not md:
            n_no_md += 1
            rows.append((item, None, None, "no_md"))
            continue
        issn, yr, vol, iss = md
        cache = f"{CACHE_DIR}/{issn}_{yr}_{vol}_{iss}.json"
        already = os.path.exists(cache)
        if already:
            try:
                d = json.load(open(cache))
                count = len(d) if isinstance(d, list) else 0
                rows.append((item, md, count, "cached"))
                n_already += 1
                if count == 0: n_empty += 1
                continue
            except Exception:
                already = False  # fall through to refetch
        # Fetch (rate-limited by crossref_titles internally)
        if i % 5 == 0 or i == len(items):
            print(f"  {i}/{len(items)} fetched={n_fetched} cached={n_already} "
                  f"empty={n_empty} failed={n_failed}", file=sys.stderr)
        articles = crossref_titles(issn, yr, vol, iss, fetch=True)
        if articles is None:
            n_failed += 1
            rows.append((item, md, None, "fetch_failed"))
            continue
        n_fetched += 1
        if not articles: n_empty += 1
        rows.append((item, md, len(articles), "fetched"))

    print(f"\n=== summary ===")
    print(f"  total items: {len(items)}")
    print(f"  already cached: {n_already}")
    print(f"  newly fetched:  {n_fetched}")
    print(f"  empty (0 articles): {n_empty}")
    print(f"  fetch failed (404 / network): {n_failed}")
    print(f"  no derivable metadata:        {n_no_md}")

    print(f"\n=== per-item ===")
    print(f"{'item':<70} {'articles':>9} {'status':>14}")
    for item, md, count, status in rows:
        c = str(count) if count is not None else "-"
        print(f"  {item[:68]:<68} {c:>9} {status:>14}")


if __name__ == "__main__":
    main()
