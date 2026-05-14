"""Evaluate scanner page-number assertions on recently-scanned periodicals.

GitHub issue #5: scanner operators are expected to assert observed page
numbers at the beginning and end of each scanned issue. These land in
scandata.xml as <pageNumData><assertion>. This script:

  1. Lists recently-scanned periodicals via authenticated `ia search`
     (publicdate >= NOW - N days, collection:periodicals, not microfilm).
  2. Downloads each item's scandata.xml + page_numbers.json.
  3. For each item, scores PRESENCE / COVERAGE / ACCURACY of the
     assertions vs. pn.json.
  4. Writes a single combined Markdown report grouped by scancenter and
     operator.

Usage:
  python3 evaluate_scanner_assertions.py --days 30 --limit 2000
  python3 evaluate_scanner_assertions.py --scancenter cebu --days 7 --limit 50
  python3 evaluate_scanner_assertions.py --identifier sim_textile-history_2000-11_31_2

No writes to IA. Read-only audit.
"""
import argparse
import json
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

SEGART = Path("/Users/brewster/tmp/segart")
SCANDATA_DIR = SEGART / "tmp" / "scanner_audit"
REPORT_DIR = SEGART / "tmp" / "audit"

sys.path.insert(0, str(SEGART / "tools"))
from scandata_assertions import parse_assertions, parse_scan_operators  # noqa
from pn_health import load_pn_for_item  # noqa


def ia_search(query, fields, limit, scope_all=True):
    """Stream results from `ia search` as dicts. Authenticated via the
    user's ia.ini; scope=all surfaces privileged items."""
    cmd = ["ia", "search", query]
    for f in fields:
        cmd += ["--field", f]
    if scope_all:
        cmd += ["--parameters", "scope=all"]
    if limit:
        cmd += ["--num-found"]  # warm-up (some clients need a separate count call)
    # Stream stdout one JSON line per result.
    proc = subprocess.Popen(["ia", "search", query] +
                            sum([["--field", f] for f in fields], []) +
                            (["--parameters", "scope=all"] if scope_all else []),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    out = []
    for line in proc.stdout:
        line = line.strip()
        if not line: continue
        try: out.append(json.loads(line))
        except Exception: continue
        if limit and len(out) >= limit: break
    proc.terminate()
    return out


def fetch_files(item, glob, destdir):
    """ia download with --glob; returns True on success."""
    destdir.mkdir(parents=True, exist_ok=True)
    cmd = ["ia", "download", item, "--glob", glob,
           "--destdir", str(destdir), "--retries", "2"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return False
    return r.returncode == 0


def fetch_one(item, destdir):
    """Fetch scandata.xml + page_numbers.json for an item.
    Returns (item, scandata_path_or_None, pn_path_or_None).
    """
    item_dir = destdir / item
    sd = item_dir / f"{item}_scandata.xml"
    pn = item_dir / f"{item}_page_numbers.json"
    if not sd.exists():
        fetch_files(item, f"{item}_scandata.xml", destdir)
    if not pn.exists():
        fetch_files(item, f"{item}_page_numbers.json", destdir)
    return item, (sd if sd.exists() else None), (pn if pn.exists() else None)


def normalize_page(s):
    """For accuracy match: lowercase, strip whitespace + trailing punct."""
    if s is None: return ""
    return re.sub(r"[^\w]+", "", str(s).lower())


def pn_at_leaf(pn_data, leaf):
    """Return the printed-pageNumber string at a given leafNum from pn.json.
    pn.json is a dict with `pages` → list[{leafNum, pageNumber, confidence}].
    """
    if not pn_data: return None
    pages = pn_data.get("pages") if isinstance(pn_data, dict) else pn_data
    if not pages: return None
    for row in pages:
        if row.get("leafNum") == leaf:
            v = row.get("pageNumber")
            return v if v not in (None, "") else None
    return None


def score_item(item, sd_path, pn_path, imagecount):
    """Compute per-item assertions + outcome list. Returns dict ready for
    aggregation.
    """
    assertions = parse_assertions(sd_path) if sd_path else []
    operators = parse_scan_operators(sd_path) if sd_path else []
    pn_data = None
    if pn_path and pn_path.exists():
        try: pn_data = json.load(open(pn_path))
        except Exception: pn_data = None

    # Score each assertion
    outcomes = []  # list of (leaf, asserted, observed, outcome)
    for leaf, asserted in assertions:
        observed = pn_at_leaf(pn_data, leaf)
        if observed is None:
            outc = "no_ocr"
        elif normalize_page(asserted) == normalize_page(observed):
            outc = "match"
        else:
            outc = "mismatch"
        outcomes.append((leaf, asserted, observed, outc))

    # Presence flags
    flags = []
    if not assertions:
        flags.append("no_assertions")
    elif len(assertions) == 1:
        flags.append("single_assertion")

    # Coverage flags (only meaningful when imagecount > 0 and ≥1 assertion)
    if assertions and imagecount:
        leafs = sorted(l for l, _ in assertions)
        if leafs[0] > imagecount * 0.1:
            flags.append("missing_start")
        if leafs[-1] < imagecount * 0.9:
            flags.append("missing_end")

    primary_operator = operators[0]["email"] if operators else ""

    return {
        "item": item,
        "n_assertions": len(assertions),
        "outcomes": outcomes,
        "flags": flags,
        "primary_operator": primary_operator,
        "operators": [o["email"] for o in operators],
        "has_pn_json": pn_data is not None,
    }


def scancenter_of(item_meta):
    """Return the scancenter slug; fall back to scanner hostname leaf."""
    sc = (item_meta.get("scanningcenter") or "").strip().lower()
    if sc: return sc
    scanner = (item_meta.get("scanner") or "").strip().lower()
    if "." in scanner:
        # microfilm03.cebu.archive.org → cebu
        parts = scanner.split(".")
        if len(parts) >= 3 and parts[-1] == "org" and parts[-2] == "archive":
            return parts[-3]
    return "unknown"


def aggregate(scored, items_meta):
    """Group scored items by scancenter and by operator."""
    by_center = defaultdict(lambda: Counter())
    by_operator = defaultdict(lambda: Counter())
    op_to_center = {}

    for s in scored:
        meta = items_meta.get(s["item"], {})
        center = scancenter_of(meta)
        op = s["primary_operator"] or "unknown"
        op_to_center[op] = center

        for bucket in (by_center[center], by_operator[op]):
            bucket["n_items"] += 1
            if "no_assertions" in s["flags"]: bucket["n_no_assertions"] += 1
            if "single_assertion" in s["flags"]: bucket["n_single_assertion"] += 1
            if "missing_start" in s["flags"]: bucket["n_missing_start"] += 1
            if "missing_end" in s["flags"]: bucket["n_missing_end"] += 1
            for _, _, _, outc in s["outcomes"]:
                bucket[f"n_{outc}"] += 1
            bucket["n_assertions_total"] += len(s["outcomes"])
            if not s["has_pn_json"]:
                bucket["n_no_pn_json"] += 1
    return by_center, by_operator, op_to_center


def fmt_pct(n, d):
    if not d: return "—"
    return f"{100*n/d:.0f}%"


def write_report(scored, items_meta, args, out_path):
    by_center, by_operator, op_to_center = aggregate(scored, items_meta)

    L = []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    L.append(f"# Scanner page-number assertion report")
    L.append("")
    L.append(f"Generated **{now}** — filter: `{args.query}`, "
             f"last **{args.days}** days, limit **{args.limit}** items.")
    L.append("")
    L.append(f"- Items audited: **{len(scored)}**")
    L.append(f"- Scancenters: **{len(by_center)}**")
    L.append(f"- Items missing pn.json (no accuracy check possible): "
             f"**{sum(1 for s in scored if not s['has_pn_json'])}**")
    L.append("")

    # Table 1: per-scancenter summary
    L.append("## By scancenter")
    L.append("")
    L.append("| scancenter | items | no asserts | single | miss start | miss end | asserts | match | mismatch | no-ocr |")
    L.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for center, c in sorted(by_center.items(), key=lambda kv: -kv[1].get("n_mismatch", 0)):
        nitems = c["n_items"]
        L.append(f"| `{center}` | {nitems} "
                 f"| {c['n_no_assertions']} ({fmt_pct(c['n_no_assertions'], nitems)}) "
                 f"| {c['n_single_assertion']} ({fmt_pct(c['n_single_assertion'], nitems)}) "
                 f"| {c['n_missing_start']} ({fmt_pct(c['n_missing_start'], nitems)}) "
                 f"| {c['n_missing_end']} ({fmt_pct(c['n_missing_end'], nitems)}) "
                 f"| {c['n_assertions_total']} "
                 f"| {c['n_match']} ({fmt_pct(c['n_match'], c['n_assertions_total'])}) "
                 f"| {c['n_mismatch']} ({fmt_pct(c['n_mismatch'], c['n_assertions_total'])}) "
                 f"| {c['n_no_ocr']} ({fmt_pct(c['n_no_ocr'], c['n_assertions_total'])}) |")
    L.append("")

    # Table 2: per-operator (≥3 items)
    L.append("## By operator (≥3 items in window)")
    L.append("")
    L.append("Primary operator = the email with the most `activeTime` in `scanLog/userMetrics`.")
    L.append("")
    L.append("| operator | center | items | no asserts | miss start | miss end | match | mismatch | no-ocr |")
    L.append("|---|---|---:|---:|---:|---:|---:|---:|---:|")
    eligible = [(op, c) for op, c in by_operator.items() if c["n_items"] >= 3]
    for op, c in sorted(eligible, key=lambda kv: (-kv[1]["n_items"],
                                                   -kv[1].get("n_mismatch", 0))):
        nitems = c["n_items"]
        L.append(f"| `{op}` | `{op_to_center.get(op,'?')}` | {nitems} "
                 f"| {c['n_no_assertions']} ({fmt_pct(c['n_no_assertions'], nitems)}) "
                 f"| {c['n_missing_start']} ({fmt_pct(c['n_missing_start'], nitems)}) "
                 f"| {c['n_missing_end']} ({fmt_pct(c['n_missing_end'], nitems)}) "
                 f"| {c['n_match']} ({fmt_pct(c['n_match'], c['n_assertions_total'])}) "
                 f"| {c['n_mismatch']} ({fmt_pct(c['n_mismatch'], c['n_assertions_total'])}) "
                 f"| {c['n_no_ocr']} ({fmt_pct(c['n_no_ocr'], c['n_assertions_total'])}) |")
    L.append("")

    # Section 3: flagged items
    L.append("## Flagged items")
    L.append("")
    L.append("Items with any of: `no_assertions`, `single_assertion`, "
             "`missing_start`, `missing_end`, or ≥1 `mismatch`.")
    L.append("")
    flagged_by_center = defaultdict(list)
    for s in scored:
        center = scancenter_of(items_meta.get(s["item"], {}))
        n_mm = sum(1 for _, _, _, o in s["outcomes"] if o == "mismatch")
        if s["flags"] or n_mm:
            flagged_by_center[center].append((s, n_mm))
    for center, items in sorted(flagged_by_center.items()):
        L.append(f"### `{center}` ({len(items)} flagged)")
        L.append("")
        for s, n_mm in sorted(items, key=lambda x: -x[1])[:50]:
            item = s["item"]
            flags = list(s["flags"])
            if n_mm: flags.append(f"{n_mm}_mismatch")
            link_leaf = 0
            if s["outcomes"]:
                link_leaf = s["outcomes"][0][0]
            L.append(f"- [{item}](https://archive.org/details/{item}/page/n{link_leaf}/mode/1up) "
                     f"— op: `{s['primary_operator'] or '?'}`, "
                     f"n_assertions={s['n_assertions']}, "
                     f"flags: {', '.join(flags) or '—'}")
        if len(items) > 50:
            L.append(f"- … and {len(items) - 50} more")
        L.append("")

    # Section 4: top mismatch pairs
    L.append("## Top mismatch patterns (asserted → observed)")
    L.append("")
    pair_counts = Counter()
    for s in scored:
        for _, asserted, observed, outc in s["outcomes"]:
            if outc == "mismatch":
                pair_counts[(asserted, observed)] += 1
    if not pair_counts:
        L.append("_No mismatches in this window._")
    else:
        L.append("| asserted | observed | count |")
        L.append("|---|---|---:|")
        for (a, o), n in pair_counts.most_common(40):
            L.append(f"| `{a}` | `{o}` | {n} |")
    L.append("")

    out_path.write_text("\n".join(L) + "\n")
    return out_path


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=30,
                    help="publicdate >= NOW - this many days (default 30)")
    ap.add_argument("--limit", type=int, default=2000,
                    help="cap on items pulled (default 2000)")
    ap.add_argument("--scancenter",
                    help="narrow to one scancenter (e.g. cebu)")
    ap.add_argument("--identifier",
                    help="audit a single item (overrides search)")
    ap.add_argument("--workers", type=int, default=8,
                    help="concurrent ia downloads (default 8)")
    ap.add_argument("--out",
                    help="output report path (default: tmp/audit/scanner_assertion_report_<date>.md)")
    args = ap.parse_args()

    SCANDATA_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    # Build search query
    if args.identifier:
        # Single-item mode — skip search, fetch metadata directly
        from urllib.request import urlopen, Request
        url = f"https://archive.org/metadata/{args.identifier}/metadata"
        try:
            md = json.load(urlopen(url, timeout=15)).get("result") or {}
        except Exception as e:
            print(f"metadata fetch failed: {e}", file=sys.stderr); sys.exit(1)
        md["identifier"] = args.identifier
        items_meta = {args.identifier: md}
        args.query = f"identifier:{args.identifier}"
    else:
        since = (datetime.now(timezone.utc) - timedelta(days=args.days)).strftime("%Y-%m-%d")
        clauses = [
            "collection:periodicals",
            "NOT microfilm",
            "format:pdf",
            f"publicdate:[{since} TO NOW]",
        ]
        if args.scancenter:
            clauses.append(f"scanningcenter:{args.scancenter}")
        query = " AND ".join(clauses)
        args.query = query
        print(f"search: {query}", flush=True)
        fields = ["identifier", "publicdate", "scanningcenter", "scanner",
                  "date", "volume", "issue", "issn", "imagecount", "collection"]
        results = ia_search(query, fields, args.limit)
        print(f"found {len(results)} items", flush=True)
        items_meta = {r["identifier"]: r for r in results if r.get("identifier")}

    if not items_meta:
        print("no items matched.", file=sys.stderr); sys.exit(0)

    # Fetch scandata.xml + pn.json
    items = list(items_meta.keys())
    print(f"fetching scandata + pn.json for {len(items)} items "
          f"(workers={args.workers})", flush=True)
    t0 = time.time()
    fetched = {}
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {ex.submit(fetch_one, it, SCANDATA_DIR): it for it in items}
        n = 0
        for fut in as_completed(futures):
            try:
                item, sd, pn = fut.result()
                fetched[item] = (sd, pn)
            except Exception:
                pass
            n += 1
            if n % 50 == 0:
                print(f"  {n}/{len(items)}  elapsed={time.time()-t0:.0f}s", flush=True)
    print(f"fetch done in {time.time()-t0:.0f}s", flush=True)

    # Score
    scored = []
    for item in items:
        sd, pn = fetched.get(item, (None, None))
        if not sd:
            continue  # skip items where we couldn't get scandata
        meta = items_meta.get(item, {})
        try: imagecount = int(meta.get("imagecount") or 0)
        except (ValueError, TypeError): imagecount = 0
        scored.append(score_item(item, sd, pn, imagecount))
    print(f"scored {len(scored)} items", flush=True)

    # Report
    today = datetime.now().strftime("%Y-%m-%d")
    out_path = Path(args.out) if args.out else (REPORT_DIR / f"scanner_assertion_report_{today}.md")
    write_report(scored, items_meta, args, out_path)
    print(f"wrote report: {out_path}", flush=True)


if __name__ == "__main__":
    main()
