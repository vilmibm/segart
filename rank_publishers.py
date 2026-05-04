#!/usr/bin/env python3
"""Rank IA periodical publishers by total issue volume.

For each pub_* collection in the input index, queries IA for the count of
text items in that collection. Groups by publisher and emits both a
per-pub JSONL (with issue counts) and a ranked publisher summary on stderr.

Pub_* collection counts are fetched in parallel via IA's advancedsearch
endpoint with rows=0 (count-only).

Usage:
  ./rank_publishers.py --pubs pub_collections.jsonl -o pub_issue_counts.jsonl
  ./rank_publishers.py --pubs pub_collections.jsonl --limit 200 -o sample.jsonl
"""
import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

URL = "https://archive.org/advancedsearch.php"
USER_AGENT = "segart-publisher-rank/0.1 (+https://github.com/brewsterkahle/segart)"


def count_for(pub_id):
    params = [
        ("q", f'collection:"{pub_id}" AND mediatype:texts'),
        ("fl[]", "identifier"),
        ("rows", "0"),
        ("output", "json"),
    ]
    url = URL + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.load(resp)
    return data["response"]["numFound"]


def first_str(v):
    if isinstance(v, list):
        return v[0] if v else None
    return v


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--pubs",
        required=True,
        help="JSONL from build_pub_collections_index.py",
    )
    p.add_argument("-o", "--output", required=True, help="Per-pub JSONL output")
    p.add_argument(
        "--concurrency", type=int, default=20, help="Parallel HTTP workers"
    )
    p.add_argument(
        "--limit",
        type=int,
        help="Only process first N pubs (for smoke testing)",
    )
    p.add_argument(
        "--top", type=int, default=30, help="Show top-N publishers on stderr"
    )
    args = p.parse_args()

    pubs = []
    with open(args.pubs) as f:
        for line in f:
            line = line.strip()
            if line:
                pubs.append(json.loads(line))
    if args.limit:
        pubs = pubs[: args.limit]

    print(
        f"counting issues for {len(pubs)} pub_* collections with "
        f"{args.concurrency} workers...",
        file=sys.stderr,
    )

    results = {}
    start = time.time()
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = {
            pool.submit(count_for, pub["identifier"]): pub for pub in pubs
        }
        for n_done, fut in enumerate(as_completed(futures), 1):
            pub = futures[fut]
            try:
                results[pub["identifier"]] = fut.result()
            except Exception as e:
                results[pub["identifier"]] = None
                if n_done <= 5:
                    print(
                        f"  err {pub['identifier']}: {e}", file=sys.stderr
                    )
            if n_done % 1000 == 0 or n_done == len(pubs):
                elapsed = time.time() - start
                rate = n_done / elapsed if elapsed else 0
                eta = (len(pubs) - n_done) / rate if rate else 0
                print(
                    f"  {n_done}/{len(pubs)} done, {rate:.1f}/s, ETA {eta:.0f}s",
                    file=sys.stderr,
                )

    # Per-pub output
    with open(args.output, "w") as fout:
        for pub in pubs:
            row = {
                "pub_id": pub["identifier"],
                "title": first_str(pub.get("title")),
                "publisher": first_str(pub.get("publisher")),
                "issn": first_str(pub.get("issn")),
                "sim_pubid": first_str(pub.get("sim_pubid")),
                "issue_count": results.get(pub["identifier"]),
            }
            fout.write(json.dumps(row, ensure_ascii=False) + "\n")

    # Publisher aggregation
    by_pub = {}
    total_issues = 0
    for pub in pubs:
        publisher = first_str(pub.get("publisher"))
        count = results.get(pub["identifier"]) or 0
        total_issues += count
        if not publisher:
            continue
        slot = by_pub.setdefault(
            publisher, {"issues": 0, "pubs": 0, "samples": []}
        )
        slot["issues"] += count
        slot["pubs"] += 1
        if len(slot["samples"]) < 3:
            t = first_str(pub.get("title"))
            if t:
                slot["samples"].append(t)

    print(
        f"\ntotal issues across {len(pubs)} pubs: {total_issues:,}",
        file=sys.stderr,
    )
    print(f"\n=== top {args.top} publishers by issue count ===", file=sys.stderr)
    print(
        f"{'rank':>4}  {'pubs':>5}  {'issues':>9}  publisher",
        file=sys.stderr,
    )
    ranked = sorted(by_pub.items(), key=lambda kv: -kv[1]["issues"])
    for i, (publisher, info) in enumerate(ranked[: args.top], 1):
        print(
            f"{i:>4}  {info['pubs']:>5}  {info['issues']:>9}  {publisher[:80]}",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
