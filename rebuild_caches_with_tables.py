#!/usr/bin/env python3
"""
One-shot: rebuild all docling caches with do_table_structure=True.

For each item that has an existing _docling.json.gz produced before
table-structure was enabled, this script:
  1. Confirms the cache lacks populated table cells (i.e. it's pre-table).
  2. Re-downloads the PDF if it's not on disk.
  3. Invokes segment_issue_docling.py, which auto-detects the stale
     cache, re-runs docling with table structure, and writes a fresh
     cache + TOC.

Items whose existing cache already has structured tables are skipped.
Items whose page count exceeds SEGART_MAX_PAGES (default 500) are
skipped with a note — they won't have been segmented anyway.

Runs serially (parallel=1) and uses CPU mode by default to avoid the
unified-memory pressure issues that crashed the earlier sweep.
"""
import argparse
import gzip
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ITEMS = Path.home() / "tmp" / "segart" / "tmp" / "items"
TOCS = Path.home() / "tmp" / "segart" / "tmp" / "tocs"
SCRIPT = Path(__file__).parent / "segment_issue_docling.py"


def cache_has_structured_tables(cache_path):
    """True if any docling table in this cache has populated cell text."""
    try:
        with gzip.open(cache_path, "rt") as fh:
            d = json.load(fh)
    except Exception:
        return False
    tables = d.get("tables") or []
    if not tables:
        return True  # no tables to populate; cache is fine
    for tbl in tables:
        for c in (tbl.get("data") or {}).get("table_cells") or []:
            if (c.get("text") or "").strip():
                return True
    return False


def page_count(pn_path):
    try:
        with open(pn_path) as fh:
            d = json.load(fh)
        return len(d.get("pages") or [])
    except Exception:
        return 0


def fetch_pdf(item):
    item_dir = ITEMS / item
    pdf = item_dir / f"{item}.pdf"
    if pdf.exists():
        return pdf
    item_dir.mkdir(parents=True, exist_ok=True)
    print(f"  downloading PDF for {item}...", flush=True)
    subprocess.run(
        ["ia", "download", item, "--glob", "*.pdf",
         "--destdir", str(ITEMS)],
        check=True, capture_output=True,
    )
    if not pdf.exists():
        raise FileNotFoundError(pdf)
    return pdf


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--max-pages", type=int,
                   default=int(os.environ.get("SEGART_MAX_PAGES", 500)))
    p.add_argument("--device", choices=("cpu", "mps"), default="cpu")
    p.add_argument("--keep-pdf", action="store_true",
                   help="Don't delete PDF after re-conversion")
    p.add_argument("--limit", type=int,
                   help="Process at most N items (for testing)")
    args = p.parse_args()

    candidates = []
    for d in sorted(ITEMS.glob("sim_*/")):
        item = d.name
        cache = d / f"{item}_docling.json.gz"
        if not cache.exists():
            continue
        if cache_has_structured_tables(cache):
            continue
        pn = d / f"{item}_page_numbers.json"
        if not pn.exists():
            continue
        pages = page_count(pn)
        if pages > args.max_pages:
            print(f"SKIP {item} ({pages} pages > {args.max_pages})")
            continue
        candidates.append((item, pages))

    print(f"\n{len(candidates)} items to rebuild "
          f"({sum(p for _, p in candidates)} total pages)\n")
    if args.limit is not None:
        candidates = candidates[: args.limit]
        print(f"--limit set; processing {len(candidates)} items\n")

    n_ok = 0
    n_fail = 0
    t_start = time.time()
    for i, (item, pages) in enumerate(candidates, 1):
        elapsed = time.time() - t_start
        print(f"[{i}/{len(candidates)}] {item} ({pages} pages, "
              f"elapsed {elapsed/60:.1f}m)", flush=True)
        try:
            fetch_pdf(item)
            cmd = [
                str(SCRIPT), item,
                "--cache-dir", str(ITEMS),
                "-o", str(TOCS / f"{item}_toc.json"),
                "--device", args.device,
            ]
            if args.keep_pdf:
                cmd.append("--keep-pdf")
            t0 = time.time()
            r = subprocess.run(cmd, capture_output=True, text=True)
            dt = time.time() - t0
            if r.returncode == 0:
                n_ok += 1
                print(f"  OK in {dt:.0f}s", flush=True)
            else:
                n_fail += 1
                print(f"  FAIL exit={r.returncode} in {dt:.0f}s",
                      flush=True)
                err_tail = "\n".join(r.stderr.splitlines()[-3:])
                if err_tail:
                    print(f"  stderr: {err_tail}", flush=True)
        except Exception as e:
            n_fail += 1
            print(f"  EXCEPTION {e}", flush=True)
    print(f"\ndone: {n_ok} ok, {n_fail} fail "
          f"in {(time.time()-t_start)/60:.1f}m")


if __name__ == "__main__":
    main()
