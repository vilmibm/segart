#!/usr/bin/env python3
"""For each cached item under tmp/items/, fetch IA metadata and report
whether `periodicals` is in the item's `collection` field.

Caches results to tmp/in_periodicals.json so re-runs are free.

Usage:
  ./check_in_periodicals.py [--refresh]
"""
import argparse
import concurrent.futures
import json
import sys
import time
import urllib.request
from pathlib import Path

SEGART = Path("/Users/brewster/tmp/segart")
ITEMS = SEGART / "tmp" / "items"
CACHE = SEGART / "tmp" / "in_periodicals.json"
HEADERS = {"User-Agent": "segart-periodicals-check/0.1 (mailto:brewster@archive.org)"}


def fetch_collections(ident):
    url = f"https://archive.org/metadata/{ident}/metadata"
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=15) as fh:
            d = json.load(fh)
    except Exception as e:
        return ident, None, str(e)
    md = d.get("result") or d  # /metadata/X/metadata returns {"result": {...}}
    coll = md.get("collection")
    if isinstance(coll, str):
        coll = [coll]
    return ident, coll or [], None


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--refresh", action="store_true",
                    help="Refetch all items, ignore cache")
    args = ap.parse_args()

    cache = {}
    if CACHE.exists() and not args.refresh:
        cache = json.loads(CACHE.read_text())

    items = sorted(p.name for p in ITEMS.iterdir() if p.is_dir())
    todo = [i for i in items if i not in cache]
    print(f"items: {len(items)}  cached: {len(items)-len(todo)}  to fetch: {len(todo)}",
          file=sys.stderr)

    if todo:
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
            futs = {ex.submit(fetch_collections, i): i for i in todo}
            for fut in concurrent.futures.as_completed(futs):
                ident, coll, err = fut.result()
                cache[ident] = {"collection": coll, "error": err}
                if err:
                    print(f"  ERR  {ident}: {err}", file=sys.stderr)
        CACHE.write_text(json.dumps(cache, indent=2, sort_keys=True))

    in_per, not_in_per, errs = [], [], []
    for ident, rec in cache.items():
        if rec.get("error"):
            errs.append(ident)
        elif "periodicals" in (rec.get("collection") or []):
            in_per.append(ident)
        else:
            not_in_per.append(ident)

    print(f"\nIn periodicals:     {len(in_per)}")
    print(f"NOT in periodicals: {len(not_in_per)}")
    print(f"Errors:             {len(errs)}")

    if not_in_per:
        print("\n--- not in periodicals ---")
        for i in sorted(not_in_per):
            coll = cache[i].get("collection") or []
            print(f"  {i:<70}  collection={coll}")
    if errs:
        print("\n--- errors ---")
        for i in sorted(errs):
            print(f"  {i}: {cache[i]['error']}")


if __name__ == "__main__":
    main()
