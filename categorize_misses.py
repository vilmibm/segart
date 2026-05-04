#!/usr/bin/env python3
"""For every miss/leaves_only/content_only result in a scores JSONL,
print a one-line classification + the closest TOC entry, so we can scan
the failure modes quickly.

Categories assigned (in priority order):
  - 'short_anchor_range'  : anchor range is ≤2 leaves (often a single
    short note where an exact leaf match is unlikely)
  - 'no_neighbor_entry'   : no segmenter entry within 5 leaves of anchor
    start (segmenter completely missed this article)
  - 'leaf_offset'         : segmenter emitted an entry within 5 leaves of
    anchor start, but title/author didn't match (alignment drift)
  - 'wrong_title'         : nearest entry has matching author but
    different title (segmenter found article but mistitled it)
  - 'wrong_author'        : nearest entry has matching title but
    different author (segmenter found article but mis-parsed byline)
  - 'split_article'       : nearest entry's leaf range is shorter than
    the anchor range (segmenter split one article into pieces)

Usage:
  ./categorize_misses.py --scores tmp/tocs/_scores.jsonl --tocs-dir tmp/tocs/
"""
import argparse
import json
import os
import re
import sys
from collections import Counter


def leaf_int(s):
    if not isinstance(s, str):
        return None
    m = re.match(r"n(\d+)$", s)
    return int(m.group(1)) if m else None


def load_tocs(tocs_dir):
    out = {}
    for fn in os.listdir(tocs_dir):
        if not fn.endswith("_toc.json"):
            continue
        with open(os.path.join(tocs_dir, fn)) as f:
            toc = json.load(f)
        out[toc["item"]] = toc.get("entries") or []
    return out


def closest_entry(entries, anchor_start, anchor_end):
    best = None
    best_dist = None
    for e in entries:
        es = leaf_int(e.get("leaf_ranges", [["", ""]])[0][0])
        if es is None:
            continue
        d = abs(es - anchor_start)
        if best_dist is None or d < best_dist:
            best_dist = d
            best = e
    return best, best_dist


def categorize(r, entries):
    a_start = leaf_int(r["anchor_leaves"][0][0]) if r.get("anchor_leaves") else None
    a_end = leaf_int(r["anchor_leaves"][0][-1]) if r.get("anchor_leaves") else None
    if a_start is None:
        return "no_anchor_leaf", None, None
    near, dist = closest_entry(entries, a_start, a_end)
    if near is None:
        return "no_entries", None, None
    if dist is not None and dist > 5:
        return "no_neighbor_entry", near, dist
    if a_end is not None and a_start is not None and (a_end - a_start) <= 1:
        return "short_anchor_range", near, dist
    leaves = near.get("leaf_ranges", [["", ""]])[0]
    es = leaf_int(leaves[0])
    ee = leaf_int(leaves[-1])
    near_span = (ee or es) - (es or 0)
    anchor_span = a_end - a_start
    title_hit = r["reasons"].get("title")
    author_hit = r["reasons"].get("author")
    if title_hit and not author_hit:
        return "wrong_author", near, dist
    if author_hit and not title_hit:
        return "wrong_title", near, dist
    if near_span < anchor_span // 2:
        return "split_article", near, dist
    return "leaf_offset", near, dist


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--scores", required=True)
    p.add_argument("--tocs-dir", required=True)
    p.add_argument("--limit", type=int, default=80)
    args = p.parse_args()

    tocs = load_tocs(args.tocs_dir)
    cats = Counter()
    n = 0
    for line in open(args.scores):
        r = json.loads(line)
        if r.get("match") in ("exact", "soft"):
            continue
        entries = tocs.get(r["item"]) or []
        cat, near, dist = categorize(r, entries)
        cats[cat] += 1
        n += 1
        if n <= args.limit:
            print(
                f"  [{r['match']:<13}] [{cat:<22}] {r['item'][:30]:<30} "
                f"anchor={r['anchor_leaves']} {(r['anchor_title'] or '')[:50]!r}"
            )
            if near is not None:
                near_t = (near.get('title') or '')[:50]
                near_a = ', '.join(a.get('name') or '' for a in (near.get('authors') or []))[:30]
                print(f"     ↳ nearest@{dist}: {near['leaf_ranges'][0]} {near_t!r} [{near_a}]")

    print(f"\n  --- {n} non-hits categorized ---")
    for c, k in cats.most_common():
        print(f"    {c}: {k}")


if __name__ == "__main__":
    main()
