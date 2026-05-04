#!/usr/bin/env python3
"""Print a one-screen evaluation summary across all TOCs in a directory.

Reads <tocs-dir>/*_toc.json and the QA corpus, then prints, for each
item: leaf_count, n_entries, n_anchors, hit-rate, miss categories.
At the bottom: aggregate totals.

Usage:
  ./summary_report.py --corpus tmp/qa_corpus.jsonl --tocs-dir tmp/tocs/
"""
import argparse
import json
import os
import re
import sys
from collections import Counter


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from score_toc import score_toc  # type: ignore


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--corpus", required=True)
    p.add_argument("--tocs-dir", required=True)
    args = p.parse_args()

    corpus = {}
    with open(args.corpus) as f:
        for line in f:
            r = json.loads(line)
            corpus[r["identifier"]] = r["anchors"]

    tot = {"anchors": 0, "exact": 0, "soft": 0, "leaves_only": 0,
           "content_only": 0, "miss": 0, "entries": 0, "items": 0}

    rows = []
    for fn in sorted(os.listdir(args.tocs_dir)):
        if not fn.endswith("_toc.json"):
            continue
        toc = json.load(open(os.path.join(args.tocs_dir, fn)))
        item = toc.get("item")
        anchors = corpus.get(item, [])
        if not anchors:
            continue
        entries = toc.get("entries") or []
        results = score_toc(anchors, toc)
        cats = Counter(r["match"] for r in results)
        rows.append({
            "item": item,
            "version": (toc.get("generator") or {}).get("version", "?"),
            "leaves": toc.get("leaf_count"),
            "entries": len(entries),
            "anchors": len(anchors),
            "exact": cats["exact"],
            "soft": cats["soft"],
            "leaves_only": cats["leaves_only"],
            "content_only": cats["content_only"],
            "miss": cats["miss"],
        })
        tot["items"] += 1
        tot["anchors"] += len(anchors)
        tot["entries"] += len(entries)
        for k in ("exact", "soft", "leaves_only", "content_only", "miss"):
            tot[k] += cats[k]

    rows.sort(key=lambda r: -r["anchors"])
    h = lambda r: r["exact"] + r["soft"]
    print(f"\n{'item':<58} {'ver':<5} {'lvs':>4} {'ent':>4} {'anc':>4} "
          f"{'hit':>4} {'pct':>4}  {'miss':>4}  filt-mode")
    for r in rows:
        item = r["item"]
        if len(item) > 58:
            item = item[:55] + "..."
        pct = (h(r) * 100 // r["anchors"]) if r["anchors"] else 0
        print(f"{item:<58} {r['version'][:5]:<5} {r['leaves']:>4} "
              f"{r['entries']:>4} {r['anchors']:>4} {h(r):>4} {pct:>3}%  "
              f"{r['miss']:>4}")

    if tot["anchors"]:
        hit = tot["exact"] + tot["soft"]
        pct = hit * 100 // tot["anchors"]
        print(f"\n--- aggregate over {tot['items']} items "
              f"({tot['entries']} TOC entries, {tot['anchors']} anchors) ---")
        print(f"  hit (exact+soft): {hit}/{tot['anchors']} ({pct}%)")
        print(f"    exact: {tot['exact']}, soft: {tot['soft']}")
        print(f"  leaves_only: {tot['leaves_only']}")
        print(f"  content_only: {tot['content_only']}")
        print(f"  miss: {tot['miss']}")


if __name__ == "__main__":
    main()
