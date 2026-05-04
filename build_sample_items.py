#!/usr/bin/env python3
"""Pick a sample of IA items from the QA corpus, ranked for evaluation usefulness.

Reads `qa_corpus.jsonl` and emits a list of identifiers that:
  - Have at least --min-anchors anchors with non-empty article_title (real
    titles to match against; empty-title anchors only score on leaves).
  - Match --identifier-prefix (defaults to `sim_`).

Items are ranked by named-anchor count (desc), then identifier asc for stable
ordering. Output one identifier per line.

Usage:
  ./build_sample_items.py --corpus tmp/qa_corpus.jsonl -o tmp/sample_items.txt
  ./build_sample_items.py --corpus tmp/qa_corpus.jsonl --limit 10 --min-anchors 3
"""
import argparse
import json
import sys


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--corpus", required=True)
    p.add_argument("-o", "--output", help="Output path (default stdout)")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--min-anchors", type=int, default=2,
                   help="Minimum named anchors per item (default 2)")
    p.add_argument("--identifier-prefix", default="sim_")
    args = p.parse_args()

    rows = []
    with open(args.corpus) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            ident = rec["identifier"]
            if not ident.startswith(args.identifier_prefix):
                continue
            anchors = rec.get("anchors") or []
            named = sum(1 for a in anchors if (a.get("article_title") or "").strip())
            if named >= args.min_anchors:
                rows.append((named, len(anchors), ident))

    rows.sort(key=lambda r: (-r[0], r[2]))
    rows = rows[: args.limit]

    out = open(args.output, "w") if args.output else sys.stdout
    for named, total, ident in rows:
        out.write(ident + "\n")
    if args.output:
        out.close()
    print(
        f"  wrote {len(rows)} items "
        f"(named-anchors range: {rows[-1][0]}..{rows[0][0]})",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
