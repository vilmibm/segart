#!/usr/bin/env python3
"""Estimate Crossref coverage over a random sample of ILL-fulfilled
periodical issues.

For each sampled item:
  1. derive (issn, year, vol, issue) from any ILL row (prefer post-2024-04
     structured fields; fall back to cover_text parser for older rows)
  2. fetch Crossref's article list for that (journal, vol, issue) — cached
     under tmp/crossref_cache/
  3. record whether Crossref returned ≥1 article

Outputs a coverage rate plus per-bucket breakdown by decade.
"""
import argparse
import csv
import glob
import json
import os
import random
import re
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter, defaultdict

from parse_cover_text import parse as parse_cover

SEGART = "/Users/brewster/tmp/segart"
CACHE_DIR = f"{SEGART}/tmp/crossref_cache"
HEADERS = {"User-Agent": "segart-crossref-coverage/0.1 (mailto:brewster@archive.org)"}
LEAF_RE = re.compile(r"^n\d+$")
ISSN_SHAPE = re.compile(r"^\d{4}-?\d{3}[\dXx]$")
BOOK_SUFFIX = re.compile(r"\d{4}[a-z]+(_[a-z0-9]+)*$")
PERIODICAL_SHAPES = [
    re.compile(r"^sim_"),
    re.compile(r"^pub_"),
    re.compile(r"_\d{4}.*_\d+_\d+(?:-\d+)?$"),
    re.compile(r"_\d{4}-\d{2}_\d+_\d+(?:-\d+)?$"),
    re.compile(r"_(spring|summer|fall|autumn|winter|january|february|march|april|may|june|july|august|september|october|november|december)[\w-]*_\d+_\d+", re.I),
]


def is_periodical_id(ident):
    if not ident: return False
    if BOOK_SUFFIX.search(ident): return False
    return any(p.search(ident) for p in PERIODICAL_SHAPES)


def all_leaves(arr):
    return arr and all(LEAF_RE.match(str(x).strip()) for x in arr)


def derive_metadata(row):
    """From an ILL CSV row, return (issn, year, vol, issue) or None."""
    try: ff = json.loads(row.get("full_form") or "{}")
    except: return None
    if not (ff.get("start") and ff.get("stop")): return None
    p = ff.get("original_request_params") or {}
    issn = (p.get("standard_number") or "").strip().split(";")[0].strip()
    vol = (p.get("journal_volume") or "").strip()
    iss = (p.get("journal_issue") or "").strip()
    yr = (p.get("journal_year") or "").strip()
    if not (issn and vol and iss and yr):
        # Cover-text fallback for pre-upgrade rows.
        cv = parse_cover(ff.get("cover_text") or "") or {}
        issn = issn or (cv.get("issn") or "").strip()
        vol = vol or (cv.get("volume") or "").strip()
        iss = iss or (cv.get("issue") or "").strip()
        yr = yr or (cv.get("year") or "").strip()
    if not (issn and vol and iss and yr): return None
    if not ISSN_SHAPE.match(issn): return None
    return issn, yr[:4], vol, iss


def cache_path(issn, year, vol, iss):
    key = f"{issn}_{year}_{vol}_{iss}".replace("/", "_").replace(" ", "_")
    return f"{CACHE_DIR}/{key}.json"


def crossref_titles(issn, year, vol, iss, *, fetch=True, sleep=0.4):
    """Return a list of {doi, title} dicts for the (issn, vol, issue, year),
    using the local cache. Fetch from Crossref if missing and fetch=True.
    """
    p = cache_path(issn, year, vol, iss)
    if os.path.exists(p):
        try: return json.load(open(p))
        except: pass
    if not fetch: return None
    # Build URL
    try: y = int(str(year)[:4])
    except (ValueError, TypeError): return None
    url = (
        f"https://api.crossref.org/journals/{issn}/works"
        f"?rows=200&filter=type:journal-article,from-pub-date:{y}-01,until-pub-date:{y}-12"
        f"&select=DOI,title,page,volume,issue"
    )
    out = []
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=30) as fh:
            data = json.load(fh)
        for r in data.get("message", {}).get("items", []):
            v = str(r.get("volume", "")).strip()
            i = str(r.get("issue", "")).strip()
            if v == str(vol).strip() and i == str(iss).strip():
                ttl = r.get("title")
                if isinstance(ttl, list): ttl = ttl[0] if ttl else ""
                out.append({"doi": r.get("DOI"), "title": ttl})
    except Exception as e:
        print(f"  WARN crossref fetch failed for {issn} {vol}/{iss}/{y}: {e}",
              file=sys.stderr)
        return None
    os.makedirs(CACHE_DIR, exist_ok=True)
    json.dump(out, open(p, "w"))
    time.sleep(sleep)  # be polite
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=200, help="sample size")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--no-fetch", action="store_true",
                    help="only consult cache; skip API calls")
    args = ap.parse_args()

    random.seed(args.seed)

    # 1. Build pool: unique periodical items with a usable metadata-bearing row
    print("scanning ILL logs for candidate items...", file=sys.stderr)
    item_md = {}  # ident -> (issn, yr, vol, iss) — first usable row wins
    n_rows = 0
    n_periodicals = 0
    for path in sorted(glob.glob(f"{SEGART}/tmp/ill_logs/*.csv")):
        with open(path, newline='') as fh:
            for row in csv.DictReader(fh):
                ident = row.get("source_identifier") or ""
                if not is_periodical_id(ident): continue
                n_periodicals += 1
                if ident in item_md: continue  # already got metadata
                md = derive_metadata(row)
                if md: item_md[ident] = md
                n_rows += 1
    print(f"  scanned {n_rows} candidate rows; found {len(item_md)} unique "
          f"periodical items with derivable metadata", file=sys.stderr)

    # 2. Random sample
    items = sorted(item_md.keys())
    sample = random.sample(items, min(args.n, len(items)))

    # 3. Crossref lookup with rate limit
    found = 0
    not_found = 0
    fetch_failed = 0
    decade_stats = defaultdict(lambda: {"n": 0, "found": 0})
    miss_examples = []
    print(f"querying Crossref for {len(sample)} items "
          f"(use --no-fetch to skip uncached)...", file=sys.stderr)
    for i, ident in enumerate(sample, 1):
        issn, yr, vol, iss = item_md[ident]
        try: y = int(str(yr)[:4])
        except: y = 0
        decade = (y // 10) * 10 if y else 0
        articles = crossref_titles(issn, yr, vol, iss,
                                    fetch=not args.no_fetch)
        decade_stats[decade]["n"] += 1
        if articles is None:
            fetch_failed += 1
        elif articles:
            found += 1
            decade_stats[decade]["found"] += 1
        else:
            not_found += 1
            if len(miss_examples) < 6:
                miss_examples.append((ident, issn, yr, vol, iss))
        if i % 25 == 0:
            print(f"  {i}/{len(sample)}: found={found} empty={not_found} "
                  f"failed={fetch_failed}", file=sys.stderr)

    n = len(sample)
    print(f"\n=== Crossref coverage on {n} sampled periodical issues ===")
    print(f"  found ≥1 article in Crossref: {found}/{n} ({100*found//max(n,1)}%)")
    print(f"  Crossref returned 0 articles: {not_found}/{n} ({100*not_found//max(n,1)}%)")
    if fetch_failed:
        print(f"  fetch failed (network/parsing):   {fetch_failed}/{n} "
              f"({100*fetch_failed//max(n,1)}%)")
    print(f"\nby decade:")
    for d in sorted(decade_stats.keys()):
        s = decade_stats[d]
        if s["n"] == 0: continue
        pct = 100*s["found"]//s["n"]
        bar = "█" * (pct // 5)
        label = f"{d}s" if d else "unknown"
        print(f"  {label:<8} {s['found']:>4}/{s['n']:<4} ({pct:>3}%)  {bar}")

    if miss_examples:
        print(f"\nfirst 6 'no Crossref articles' examples:")
        for ident, issn, yr, vol, iss in miss_examples:
            print(f"  {ident}  issn={issn} {vol}/{iss}/{yr}")


if __name__ == "__main__":
    main()
