"""Post-audit analysis:
  1. Tier classification (clean ≥95%, near 80-94, mid 50-79, bad <50)
     with confidence threshold (>=30 anchors).
  2. Date-onset transition detection per journal: find earliest year
     where a 5-issue rolling window stays ≥95% match.
  3. Publisher clustering: query Crossref /journals/{issn} for clean
     candidates, group by publisher.
"""
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict, Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

AUDIT = Path("/tmp/journal_xref_audit.jsonl")
PUB_OUT = Path("/tmp/journal_publishers.jsonl")
HEADERS = {"User-Agent": "segart-audit-analysis/0.1 (mailto:brewster@archive.org)"}
MIN_ANCHORS = 30


def load_audit():
    return [json.loads(l) for l in AUDIT.read_text().splitlines() if l.strip()]


# ---------- Q1: tier classification ----------

def tier(pct, anchors):
    if pct is None or anchors < MIN_ANCHORS: return "unknown"
    if pct >= 95: return "clean"
    if pct >= 80: return "near"
    if pct >= 50: return "mid"
    if pct > 0:   return "bad"
    return "zero"


def report_tiers(rows):
    by_tier = Counter()
    for r in rows:
        by_tier[tier(r.get("overall_match_pct"),
                     r.get("anchors_seen", 0))] += 1
    print(f"\n=== Q1: tier classification (n={len(rows)}, min anchors {MIN_ANCHORS}) ===")
    total = sum(by_tier.values())
    for t in ("clean", "near", "mid", "bad", "zero", "unknown"):
        n = by_tier.get(t, 0)
        print(f"  {t:>10}: {n:>5}  ({100*n/max(total,1):.1f}%)")


# ---------- Q2: date-onset transitions ----------

def per_year_match(per_issue):
    """Aggregate per_issue to (year -> (anchors, hits))."""
    by_year = defaultdict(lambda: [0, 0])
    for x in per_issue or []:
        try: y = int(x.get("year"))
        except: continue
        by_year[y][0] += x.get("anchors", 0)
        by_year[y][1] += x.get("either_hits", 0)
    return by_year


def detect_transition(per_issue, window=5, threshold=0.95):
    """Return (status, transition_year, summary) where status is one of
    always_clean, transitioned_at_YYYY, never_clean, mixed_unstable, no_data.
    Walks per_issue chronologically with rolling window of N issues.
    """
    issues = sorted([x for x in (per_issue or []) if x.get("year","").isdigit()],
                    key=lambda x: int(x["year"]))
    if not issues: return ("no_data", None, "no per_issue rows")
    if len(issues) < window: return ("no_data", None, f"only {len(issues)} issues")
    # rolling window
    n = len(issues)
    rolling = []
    for i in range(window - 1, n):
        chunk = issues[i - window + 1: i + 1]
        a = sum(x.get("anchors", 0) for x in chunk)
        h = sum(x.get("either_hits", 0) for x in chunk)
        if a == 0:
            rolling.append((int(chunk[-1]["year"]), None))
        else:
            rolling.append((int(chunk[-1]["year"]), h / a))
    measurable = [(y, p) for (y, p) in rolling if p is not None]
    if not measurable: return ("no_data", None, "no measurable windows")
    # Always clean: all measurable windows >= threshold
    if all(p >= threshold for _, p in measurable):
        return ("always_clean", measurable[0][0], f"{len(measurable)} windows all clean")
    # Never clean: no measurable window >= threshold
    if not any(p >= threshold for _, p in measurable):
        return ("never_clean", None, f"max window pct = {max(p for _,p in measurable):.2f}")
    # Find earliest sustained-clean: from year Y onward, every measurable window >= threshold
    for i, (y, p) in enumerate(measurable):
        rest = measurable[i:]
        if all(pp >= threshold for _, pp in rest):
            return ("transitioned", y, f"clean from window ending {y}")
    # Otherwise mixed/unstable
    return ("mixed_unstable", None,
            f"{sum(1 for _,p in measurable if p>=threshold)}/{len(measurable)} windows clean")


def report_transitions(rows):
    print(f"\n=== Q2: date-onset transitions (≥30 anchors only) ===")
    bucket = Counter()
    transitions = []
    for r in rows:
        if r.get("anchors_seen", 0) < MIN_ANCHORS: continue
        status, year, _ = detect_transition(r.get("per_issue"))
        bucket[status] += 1
        if status == "transitioned" and year:
            transitions.append((r["issn"], year, r.get("overall_match_pct")))
    total = sum(bucket.values())
    for k in ("always_clean", "transitioned", "mixed_unstable",
              "never_clean", "no_data"):
        n = bucket.get(k, 0)
        print(f"  {k:>20}: {n:>5}  ({100*n/max(total,1):.1f}%)")
    if transitions:
        years = Counter(y // 5 * 5 for _, y, _ in transitions)
        print(f"\n  transition-year histogram (5y bins):")
        for y in sorted(years):
            print(f"    {y:>4}-{y+4}: {'#' * years[y]}  ({years[y]})")
        print(f"\n  earliest 8 transitions:")
        for issn, y, pct in sorted(transitions, key=lambda x: (x[1], -x[2] or 0))[:8]:
            print(f"    {y}  {issn}  overall {pct}%")
        print(f"\n  latest 8 transitions:")
        for issn, y, pct in sorted(transitions, key=lambda x: (-x[1], -x[2] or 0))[:8]:
            print(f"    {y}  {issn}  overall {pct}%")
    return transitions


# ---------- Q3: publisher clustering ----------

def fetch_publisher(issn):
    url = (f"https://api.crossref.org/journals/{issn}"
           f"?mailto=brewster@archive.org")
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=30) as fh:
            d = json.load(fh)
        msg = d.get("message", {})
        return {
            "issn": issn,
            "title": msg.get("title"),
            "publisher": msg.get("publisher"),
            "issns": msg.get("ISSN") or [],
        }
    except Exception as e:
        return {"issn": issn, "error": str(e)}


def report_publishers(rows, candidate_tiers=("clean", "near")):
    candidates = []
    for r in rows:
        t = tier(r.get("overall_match_pct"), r.get("anchors_seen", 0))
        if t in candidate_tiers:
            candidates.append(r["issn"])
    print(f"\n=== Q3: publisher clustering for {len(candidates)} {candidate_tiers} candidates ===")
    # cache publisher lookups
    pub_cache = {}
    if PUB_OUT.exists():
        for line in PUB_OUT.read_text().splitlines():
            try: d = json.loads(line)
            except: continue
            pub_cache[d["issn"]] = d
    todo = [i for i in candidates if i not in pub_cache]
    print(f"  fetching publishers for {len(todo)} (cached: {len(pub_cache)})...")
    fout = open(PUB_OUT, "a")
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(fetch_publisher, i): i for i in todo}
        for fut in as_completed(futs):
            d = fut.result()
            pub_cache[d["issn"]] = d
            fout.write(json.dumps(d) + "\n"); fout.flush()
    fout.close()
    by_pub = Counter()
    by_pub_clean = Counter()
    by_pub_near = Counter()
    issn_tier = {r["issn"]: tier(r.get("overall_match_pct"), r.get("anchors_seen",0))
                 for r in rows}
    for issn in candidates:
        d = pub_cache.get(issn) or {}
        pub = d.get("publisher") or "?"
        by_pub[pub] += 1
        if issn_tier[issn] == "clean": by_pub_clean[pub] += 1
        else: by_pub_near[pub] += 1
    # also count total journals per publisher across ALL audited journals (so we can compute "fraction of this publisher's journals that are clean")
    by_pub_total = Counter()
    issn_to_pub = {}
    for issn, d in pub_cache.items():
        if d.get("publisher"):
            issn_to_pub[issn] = d["publisher"]
    # we only have publishers for candidates; need full audit for denominator
    # but that requires fetching publisher for ALL audited journals
    print(f"\n  Top publishers among clean+near candidates ({len(candidates)} total):")
    print(f"  {'publisher':<60} {'clean':>5} {'near':>5} {'total':>5}")
    for pub, n in by_pub.most_common(25):
        c = by_pub_clean.get(pub, 0)
        nr = by_pub_near.get(pub, 0)
        print(f"  {pub[:60]:<60} {c:>5} {nr:>5} {n:>5}")


def main():
    rows = load_audit()
    print(f"loaded {len(rows)} audit rows from {AUDIT}")
    report_tiers(rows)
    report_transitions(rows)
    report_publishers(rows)


if __name__ == "__main__":
    main()
