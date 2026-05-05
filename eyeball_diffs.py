#!/usr/bin/env python3
"""Print a side-by-side report of anchor vs matched-entry for every
non-trivial result in a scores JSONL — designed for human eyeballing
where the segmenter is wrong (or the corpus is) and how badly.

Columns shown:
  start    anchor_start_leaf  →  toc_start_leaf
  end      anchor_end_leaf    →  toc_end_leaf
  title    diff               (red/green word changes if rich is up)
  author   anchor_author      vs toc_author_names

Usage:
  ./eyeball_diffs.py --scores /tmp/v07_filtered_scores.jsonl
  ./eyeball_diffs.py --scores ... --only soft,content_only,leaves_only
  ./eyeball_diffs.py --scores ... --only soft --max 30
"""
import argparse
import json
import re
import sys


def short(s, n=70):
    if not s:
        return "∅"
    s = re.sub(r"\s+", " ", str(s).strip())
    return s if len(s) <= n else s[: n - 1] + "…"


def author_str(authors):
    if not authors:
        return "∅"
    if isinstance(authors, list):
        return ", ".join(short((a or {}).get("name") or "", 25) for a in authors[:4])
    return short(str(authors), 70)


def fmt_leaf(s):
    if isinstance(s, str) and s.startswith("n"):
        return s
    return f"n{s}" if s else "?"


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--scores", required=True)
    p.add_argument("--only", default="exact,soft,leaves_only,content_only,miss",
                   help="Comma-separated match categories to include")
    p.add_argument("--max", type=int, default=200, help="Max rows to print")
    p.add_argument("--per-item-max", type=int, default=4,
                   help="Max rows per item (keeps one issue from dominating)")
    args = p.parse_args()

    cats = set(args.only.split(","))
    n = 0
    seen_per_item = {}
    print(
        f"{'item':<40} {'match':<13} "
        f"{'anchor leaf':<13} {'toc leaf':<13} "
        f"end Δ"
    )
    print("-" * 120)
    for line in open(args.scores):
        r = json.loads(line)
        if r["match"] not in cats:
            continue
        item = r["item"]
        if seen_per_item.get(item, 0) >= args.per_item_max:
            continue
        seen_per_item[item] = seen_per_item.get(item, 0) + 1

        a_leaves = r.get("anchor_leaves") or [["?", "?"]]
        a_start = fmt_leaf(a_leaves[0][0])
        a_end = fmt_leaf(a_leaves[0][-1])
        t_leaves = r.get("matched_entry_leaves")
        t_start = fmt_leaf(t_leaves[0][0]) if t_leaves else "—"
        t_end = fmt_leaf(t_leaves[0][-1]) if t_leaves else "—"
        end_off = r.get("end_offset")
        end_str = f"{end_off:+d}" if end_off is not None else " "

        item_short = item.replace("sim_", "")[:40]
        print(
            f"{item_short:<40} {r['match']:<13} "
            f"{a_start}-{a_end:<10} "
            f"{t_start}-{t_end:<10} "
            f"{end_str}"
        )
        a_t = short(r.get("anchor_title") or "(no title)", 95)
        t_t = short(r.get("matched_entry_title") or "(no match)", 95)
        print(f"  anchor title : {a_t}")
        print(f"  toc title    : {t_t}")
        a_au = short(r.get("anchor_author") or "(no author)", 95)
        t_au = author_str(r.get("matched_entry_authors"))  # may not exist
        # If matched_entry_authors not in record, leave as ∅
        print(f"  anchor author: {a_au}")
        print(f"  toc author   : {t_au}")
        print()

        n += 1
        if n >= args.max:
            break

    print(f"\n  printed {n} rows")


if __name__ == "__main__":
    main()
