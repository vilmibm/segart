#!/usr/bin/env python3
"""Build segart's QA corpus from ILL fulfillment logs.

Reads every ill_logs_*.csv in --logs-dir, parses each row, dedupes anchors
within an item, drops obvious non-periodical identifiers (preprints,
datasets, etc.), and emits one JSONL line per anchored issue with the
article anchor records attached.

Usage:
  ./build_ill_qa_corpus.py --logs-dir /tmp/ill_logs -o /tmp/qa_corpus.jsonl

Output format (one JSONL per item):
  {"identifier": "<ia_item>", "anchors": [<anchor_record>, ...]}

Each anchor record carries: article_title, article_author, journal_title,
issn, volume, issue, year, printed_pages, leaf_ranges, unfill_reason.
"""
import argparse
import csv
import glob
import json
import os
import sys

# IA item identifier prefixes that are NOT periodical issues.
# Conservative — only drops cases we've actually observed in ILL logs.
NON_PERIODICAL_PREFIXES = (
    "biorxiv-",
    "arxiv-",
    "medrxiv-",
    "osf-",
    "ssrn-",
    "psyarxiv-",
    "chemrxiv-",
)


def normalize(s):
    if s is None:
        return None
    s = s.strip()
    return s if s else None


def parse_ill_row(row):
    try:
        ff = json.loads(row["full_form"])
    except (json.JSONDecodeError, KeyError, TypeError):
        return None
    identifier = ff.get("identifier") or row.get("source_identifier")
    if not identifier:
        return None
    starts = ff.get("start") or ff.get("normalized_orig_start") or []
    stops = ff.get("stop") or ff.get("normalized_orig_stop") or []
    if not starts or len(starts) != len(stops):
        return None
    params = ff.get("original_request_params") or {}
    return {
        "identifier": identifier,
        "article_title": normalize(params.get("article_title")),
        "article_author": normalize(params.get("article_author")),
        "journal_title": normalize(params.get("journal_title")),
        "issn": normalize(params.get("standard_number")),
        "volume": normalize(params.get("journal_volume")),
        "issue": normalize(params.get("journal_issue")),
        "year": normalize(params.get("journal_year")),
        "printed_pages": normalize(params.get("journal_pages")),
        "leaf_ranges": [[s, e] for s, e in zip(starts, stops)],
        "unfill_reason": ff.get("unfill_reason"),
    }


def is_periodical_identifier(ident):
    return not any(ident.startswith(p) for p in NON_PERIODICAL_PREFIXES)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--logs-dir", required=True)
    p.add_argument("-o", "--output", required=True)
    p.add_argument(
        "--include-unfilled",
        action="store_true",
        help="Keep records with unfill_reason set",
    )
    p.add_argument(
        "--no-prefix-filter",
        action="store_true",
        help="Skip the non-periodical-prefix filter",
    )
    args = p.parse_args()

    csv_paths = sorted(glob.glob(os.path.join(args.logs_dir, "ill_logs_*.csv")))
    print(f"reading {len(csv_paths)} CSVs", file=sys.stderr)

    by_item = {}
    n_rows = n_kept = n_skip = n_unfilled = n_nonperiodical = 0
    for path in csv_paths:
        with open(path) as f:
            for row in csv.DictReader(f):
                n_rows += 1
                rec = parse_ill_row(row)
                if rec is None:
                    n_skip += 1
                    continue
                if rec["unfill_reason"]:
                    n_unfilled += 1
                    if not args.include_unfilled:
                        continue
                if not args.no_prefix_filter and not is_periodical_identifier(
                    rec["identifier"]
                ):
                    n_nonperiodical += 1
                    continue
                slot = by_item.setdefault(rec["identifier"], [])
                key = (
                    rec["article_title"],
                    tuple(tuple(r) for r in rec["leaf_ranges"]),
                )
                if any(
                    (a["article_title"], tuple(tuple(r) for r in a["leaf_ranges"]))
                    == key
                    for a in slot
                ):
                    continue
                slot.append(rec)
                n_kept += 1

    with open(args.output, "w") as fout:
        for ident, anchors in sorted(by_item.items()):
            fout.write(
                json.dumps(
                    {"identifier": ident, "anchors": anchors},
                    ensure_ascii=False,
                )
                + "\n"
            )

    n_anchors_per_item = (
        sum(len(v) for v in by_item.values()) / len(by_item) if by_item else 0
    )
    print(
        f"\n  rows={n_rows} skipped={n_skip} unfilled={n_unfilled} "
        f"non_periodical={n_nonperiodical}\n"
        f"  kept anchors: {n_kept}\n"
        f"  unique items: {len(by_item)}\n"
        f"  avg anchors/item: {n_anchors_per_item:.2f}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
