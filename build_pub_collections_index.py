#!/usr/bin/env python3
"""Harvest the Internet Archive pub_* collection catalog.

Pulls every collection item with identifier matching pub_* (~28k) using
IA's scrape API and emits JSONL — one record per collection — with the
fields segart needs to match against fatcat containers.

Usage:
  ./build_pub_collections_index.py -o pub_collections.jsonl
"""
import argparse
import json
import sys
import time
import urllib.parse
import urllib.request

SCRAPE = "https://archive.org/services/search/v1/scrape"
FIELDS = [
    "identifier",
    "title",
    "issn",
    "sim_pubid",
    "external-identifier",
    "publisher",
    "collection",
    "pub_type",
    "peer_reviewed",
    "scholarly",
]
USER_AGENT = "segart-pub-harvest/0.1 (+https://github.com/brewsterkahle/segart)"


def fetch_page(query, cursor=None, count=10000):
    params = {"q": query, "fields": ",".join(FIELDS), "count": count}
    if cursor:
        params["cursor"] = cursor
    url = SCRAPE + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.load(resp)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "-q",
        "--query",
        default="mediatype:collection AND identifier:pub_*",
        help="IA search query (default: pub_* collections)",
    )
    p.add_argument("-o", "--output", help="Output JSONL path (default stdout)")
    p.add_argument(
        "--count", type=int, default=10000, help="Page size (default 10000)"
    )
    args = p.parse_args()

    fout = open(args.output, "w") if args.output else sys.stdout
    cursor = None
    total = 0
    while True:
        page = fetch_page(args.query, cursor=cursor, count=args.count)
        items = page.get("items", [])
        for item in items:
            fout.write(json.dumps(item, ensure_ascii=False) + "\n")
        total += len(items)
        reported = page.get("total")
        cursor = page.get("cursor")
        print(
            f"  fetched {len(items)} (total {total} of {reported})",
            file=sys.stderr,
        )
        if not cursor:
            break
        time.sleep(0.5)

    if args.output:
        fout.close()
    print(f"done: {total} records", file=sys.stderr)


if __name__ == "__main__":
    main()
