"""Extract a compact JSONL cache of the scanner-audit data so follow-up
reports can be built without re-downloading 1.5 GB of scandata + pn.json.

One row per item:

  {
    "item": "...",
    "scancenter": "cebu",
    "scanner": "microfilm03.cebu.archive.org",
    "publicdate": "2026-05-08 ...",
    "imagecount": 215,
    "assertions": [[5, "183"], [94, "272"]],
    "operators": [{"email": "...", "active_seconds": 213,
                    "save_count": 3, "first_date": "...", "last_date": "..."}],
    "leaf_pages": {"5": "183", "94": "272", ...}     # only leaves we care about
  }

The `leaf_pages` map keeps every leaf with a populated pageNumber from
pn.json. That's enough to recompute accuracy / mismatch outcomes for
any future query without re-fetching pn.json.

Usage:
  python3 tools/extract_scanner_audit_cache.py [--items-meta items.jsonl] [--out cache.jsonl]
"""
import argparse
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

SEGART = Path("/Users/brewster/tmp/segart")
SCANDATA_DIR = SEGART / "tmp" / "scanner_audit"

sys.path.insert(0, str(SEGART / "tools"))
from scandata_assertions import parse_assertions, parse_scan_operators


def ia_metadata_bulk(items, workers=16):
    """Fetch IA metadata for each item in parallel via `ia metadata`.
    Returns {item → {scancenter, scanner, publicdate, imagecount}}."""
    out = {}
    def one(item):
        try:
            r = subprocess.run(["ia", "metadata", item],
                               capture_output=True, text=True, timeout=30)
            if r.returncode != 0: return item, None
            d = json.loads(r.stdout).get("metadata") or {}
            return item, {
                "scancenter": (d.get("scanningcenter") or "").lower(),
                "scanner": d.get("scanner") or "",
                "publicdate": d.get("publicdate") or "",
                "imagecount": d.get("imagecount") or "",
            }
        except Exception:
            return item, None
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(one, it): it for it in items}
        for fut in as_completed(futs):
            item, md = fut.result()
            if md is not None: out[item] = md
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out",
                    default=str(SEGART / "tmp" / "audit" / "scanner_audit_cache.jsonl"),
                    help="output JSONL path (default: %(default)s)")
    ap.add_argument("--workers", type=int, default=16)
    args = ap.parse_args()

    # Walk the local scandata cache
    items = sorted(d.name for d in SCANDATA_DIR.iterdir() if d.is_dir())
    print(f"items in cache: {len(items)}", flush=True)

    # Fetch IA metadata so we have scancenter/scanner/publicdate
    print(f"fetching IA metadata for scancenter info ...", flush=True)
    meta = ia_metadata_bulk(items, workers=args.workers)
    print(f"  got metadata for {len(meta)} items", flush=True)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    n_written = 0
    with open(out_path, "w") as fh:
        for item in items:
            sd = SCANDATA_DIR / item / f"{item}_scandata.xml"
            pn = SCANDATA_DIR / item / f"{item}_page_numbers.json"
            if not sd.exists(): continue
            assertions = parse_assertions(sd)
            operators = parse_scan_operators(sd)
            leaf_pages = {}
            if pn.exists():
                try:
                    pn_data = json.load(open(pn))
                    for row in (pn_data.get("pages") or []):
                        leaf = row.get("leafNum")
                        page = row.get("pageNumber")
                        if leaf is None or page in (None, ""): continue
                        leaf_pages[str(leaf)] = page
                except Exception:
                    pass
            md = meta.get(item, {})
            obj = {
                "item": item,
                "scancenter": md.get("scancenter", ""),
                "scanner": md.get("scanner", ""),
                "publicdate": md.get("publicdate", ""),
                "imagecount": md.get("imagecount", ""),
                "assertions": [[l, p] for l, p in assertions],
                "operators": operators,
                "leaf_pages": leaf_pages,
            }
            fh.write(json.dumps(obj) + "\n")
            n_written += 1
    print(f"wrote {n_written} rows to {out_path} "
          f"({out_path.stat().st_size / 1024:.0f} KB)", flush=True)


if __name__ == "__main__":
    main()
