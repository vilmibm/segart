"""Build (publisher, year-bucket) reliability trust table from the audit.

The audit directly scores ~1300 journals with >=30 ILL anchors. The
remaining ~4400 audited journals (and most of the 6M-issue corpus
beyond) have insufficient ILL signal to score directly. This script
aggregates the direct evidence to per-(publisher, year-bucket)
reliability tiers, so we can extrapolate trust to journals lacking
direct anchors.

Output: tmp/audit/publisher_trust_table.jsonl
  Each line: {publisher, year_bucket, n_journals_scored, median_pct,
              min_pct, journals_in_bucket, tier}
"""
import json
import re
from collections import defaultdict
from pathlib import Path
from statistics import median, quantiles

SEGART = Path("/Users/brewster/tmp/segart")
AUDIT = SEGART / "tmp" / "audit" / "journal_xref_audit.jsonl"
PUBLISHERS = SEGART / "tmp" / "audit" / "journal_publishers.jsonl"
OUT = SEGART / "tmp" / "audit" / "publisher_trust_table.jsonl"

MIN_ANCHORS = 30
MIN_JOURNALS_FOR_TRUST = 3
TRUST_MEDIAN_PCT = 95.0
# Lower-quartile floor: at least 75% of journals in the cell must score
# >= this. The earlier "min" threshold was too strict — single outlier
# journals (publisher deposit failures on specific titles) ruled out
# whole buckets even when the typical journal scored 100%.
TRUST_Q1_PCT = 80.0


def tier(pct):
    if pct is None: return "unknown"
    if pct >= 95: return "clean"
    if pct >= 80: return "near"
    if pct >= 50: return "mid"
    if pct > 0:   return "bad"
    return "zero"


def main():
    # 1. Per-ISSN publisher map (from earlier publisher fetches)
    pub = {}
    if PUBLISHERS.exists():
        for line in PUBLISHERS.read_text().splitlines():
            if not line.strip(): continue
            try: r = json.loads(line)
            except: continue
            if r.get("publisher"):
                pub[r["issn"]] = r["publisher"]
    print(f"publisher cache: {len(pub)} ISSNs")

    # 2. For each audit row, expand per-bucket scores
    # bucket -> publisher -> [(issn, pct, anchors), ...]
    table = defaultdict(lambda: defaultdict(list))
    n_journals_scored = 0
    n_journals_with_pub = 0
    for line in AUDIT.read_text().splitlines():
        if not line.strip(): continue
        try: row = json.loads(line)
        except: continue
        issn = row["issn"]
        if issn not in pub: continue
        n_journals_with_pub += 1
        publisher = pub[issn]
        # per-bucket: only count buckets with >=MIN_ANCHORS_BUCKET anchors
        for bucket, stats in (row.get("year_buckets") or {}).items():
            seen = stats.get("anchors_seen") or 0
            pct = stats.get("match_pct")
            if pct is None or seen < 10:  # bucket-level min anchor
                continue
            table[bucket][publisher].append((issn, pct, seen))
        # also overall journal score
        opct = row.get("overall_match_pct")
        oanc = row.get("anchors_seen") or 0
        if opct is not None and oanc >= MIN_ANCHORS:
            n_journals_scored += 1

    print(f"audit rows with publisher info: {n_journals_with_pub}")
    print(f"direct-scored journals (>=30 anchors): {n_journals_scored}")
    print(f"unique (bucket, publisher) cells: "
          f"{sum(len(v) for v in table.values())}")

    # 3. Per (publisher, bucket): compute aggregate tier
    out = OUT
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w") as fout:
        for bucket, pub_map in sorted(table.items()):
            for publisher, journals in sorted(pub_map.items()):
                pcts = sorted([p for _, p, _ in journals])
                n_j = len(pcts)
                med = median(pcts)
                mn = pcts[0]
                mx = pcts[-1]
                # Q1 = 25th percentile; "75% of journals score at least Q1"
                if n_j >= 4:
                    q1 = quantiles(pcts, n=4)[0]
                else:
                    q1 = pcts[0]  # fallback for tiny cells
                # Decision: when is this (publisher, bucket) trusted?
                trusted = (n_j >= MIN_JOURNALS_FOR_TRUST
                           and med >= TRUST_MEDIAN_PCT
                           and q1 >= TRUST_Q1_PCT)
                fout.write(json.dumps({
                    "year_bucket": bucket,
                    "publisher": publisher,
                    "n_journals_scored": n_j,
                    "median_pct": round(med, 1),
                    "q1_pct": round(q1, 1),
                    "min_pct": round(mn, 1),
                    "max_pct": round(mx, 1),
                    "trusted": trusted,
                    "tier": tier(med),
                }) + "\n")

    # 4. Aggregate summary
    print(f"\nwrote {out}")
    # how many cells trusted?
    n_trusted = 0
    n_total = 0
    pub_trusted = defaultdict(set)  # publisher -> set of trusted buckets
    for line in out.read_text().splitlines():
        d = json.loads(line)
        n_total += 1
        if d["trusted"]:
            n_trusted += 1
            pub_trusted[d["publisher"]].add(d["year_bucket"])
    print(f"trusted (publisher, year-bucket) cells: {n_trusted} / {n_total}")
    print()
    print("Top publishers by # trusted year-buckets:")
    top = sorted(pub_trusted.items(), key=lambda kv: -len(kv[1]))[:15]
    for p, bkts in top:
        print(f"  {p[:55]:<55} {len(bkts)} buckets: {sorted(bkts)}")


if __name__ == "__main__":
    main()
