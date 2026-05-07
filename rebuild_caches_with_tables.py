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


def pdf_is_complete(pdf):
    """Cheap integrity check: PDFs end with %%EOF in the trailer. A truncated
    download passes existence/size checks but lacks this marker, and docling
    will reject it as 'not valid'."""
    try:
        with open(pdf, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - 4096))
            return b"%%EOF" in fh.read()
    except OSError:
        return False


def fetch_pdf(item, max_attempts=5):
    item_dir = ITEMS / item
    pdf = item_dir / f"{item}.pdf"
    if pdf.exists():
        if pdf_is_complete(pdf):
            return pdf
        print(f"  existing {pdf.name} is truncated (no %%EOF); "
              f"removing and re-downloading", flush=True)
        pdf.unlink()
    item_dir.mkdir(parents=True, exist_ok=True)
    delays = [10, 30, 60, 120, 240]
    last_err = None
    for attempt in range(1, max_attempts + 1):
        print(f"  downloading PDF for {item} "
              f"(attempt {attempt}/{max_attempts})...", flush=True)
        r = subprocess.run(
            ["ia", "download", item, "--glob", "*.pdf",
             "--destdir", str(ITEMS)],
            capture_output=True, text=True,
        )
        ok_exit = r.returncode == 0
        present = pdf.exists()
        complete = present and pdf_is_complete(pdf)
        if ok_exit and complete:
            return pdf
        # Surface the failure reason so we can diagnose flakes.
        err_tail = "\n".join((r.stderr or "").splitlines()[-3:])
        if not ok_exit:
            last_err = (f"ia exit={r.returncode}: {err_tail}"
                        if err_tail else f"ia exit={r.returncode}")
        elif not present:
            last_err = f"download reported success but {pdf.name} missing"
        else:
            last_err = f"download wrote {pdf.name} but no %%EOF marker"
        print(f"  download failed: {last_err}", flush=True)
        if present and not complete:
            pdf.unlink()  # don't let a partial poison the next attempt
        if attempt < max_attempts:
            sleep_s = delays[min(attempt - 1, len(delays) - 1)]
            print(f"  retrying in {sleep_s}s...", flush=True)
            time.sleep(sleep_s)
    raise RuntimeError(
        f"PDF download for {item} failed after {max_attempts} attempts: "
        f"{last_err}"
    )


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
