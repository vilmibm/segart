#!/usr/bin/env python3
"""Parse Internet Archive ILL fulfillment logs into clean ground-truth records.

Each output line is a JSON object describing one article appearance in a known
IA item, suitable for testing periodical-issue segmentation.

See docs/ill_logs_schema.md for the input and output schemas.
"""
import argparse
import csv
import json
import sys


def normalize(s):
    if s is None:
        return None
    s = s.strip()
    return s if s else None


def parse_row(row):
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

    request_time = None
    raw_time = row.get("time")
    if raw_time and raw_time.isdigit():
        request_time = int(raw_time)

    return {
        "identifier": identifier,
        "article_title": normalize(params.get("article_title")),
        "article_author": normalize(params.get("article_author")),
        "journal_title": normalize(params.get("journal_title")),
        "issn": normalize(params.get("standard_number")),
        "volume": normalize(params.get("journal_volume")),
        "issue": normalize(params.get("journal_issue")),
        "year": normalize(params.get("journal_year")),
        "month": normalize(params.get("journal_month")),
        "printed_pages": normalize(params.get("journal_pages")),
        "leaf_ranges": [[s, e] for s, e in zip(starts, stops)],
        "unfill_reason": ff.get("unfill_reason"),
        "provider": ff.get("provider"),
        "ill_request_id": ff.get("filename"),
        "request_time": request_time,
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("input_csv", help="ILL fulfillment log CSV")
    p.add_argument("-o", "--output", help="Output JSONL path (default: stdout)")
    p.add_argument(
        "--drop-unfilled",
        action="store_true",
        help="Skip rows with a non-null unfill_reason",
    )
    p.add_argument(
        "--dedupe",
        action="store_true",
        help="Deduplicate by (identifier, article_title, leaf_ranges)",
    )
    args = p.parse_args()

    out = open(args.output, "w") if args.output else sys.stdout
    seen = set()
    n_in = n_out = n_skip = n_unfilled = n_dupes = 0

    with open(args.input_csv, newline="") as f:
        for row in csv.DictReader(f):
            n_in += 1
            rec = parse_row(row)
            if rec is None:
                n_skip += 1
                continue
            if rec["unfill_reason"]:
                n_unfilled += 1
                if args.drop_unfilled:
                    continue
            if args.dedupe:
                key = (
                    rec["identifier"],
                    rec["article_title"],
                    tuple(tuple(r) for r in rec["leaf_ranges"]),
                )
                if key in seen:
                    n_dupes += 1
                    continue
                seen.add(key)
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            n_out += 1

    if args.output:
        out.close()
    print(
        f"in={n_in} out={n_out} skipped={n_skip} unfilled={n_unfilled} dupes={n_dupes}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
