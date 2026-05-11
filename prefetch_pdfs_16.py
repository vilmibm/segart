#!/usr/bin/env python3
"""Pre-fetch PDFs for the 16-item pilot batch.

Sequential, single-process: easier to monitor and kill than xargs.
Each PDF is checked for %%EOF marker after download; partials are
removed so subsequent docling doesn't choke on them.
"""
import subprocess
import sys
import time
from pathlib import Path

SEGART = Path("/Users/brewster/tmp/segart")
ITEMS_DIR = SEGART / "tmp" / "items"
LOG = SEGART / "tmp" / "audit" / "prefetch_pdfs_16.log"
LIST = Path("/tmp/items16.txt")


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG, "a") as fh:
        fh.write(line + "\n")


def pdf_complete(p):
    if not p.exists() or p.stat().st_size < 1024:
        return False
    with open(p, "rb") as fh:
        fh.seek(-1024, 2)
        return b"%%EOF" in fh.read()


def main():
    items = [l.strip() for l in LIST.read_text().splitlines() if l.strip()]
    LOG.parent.mkdir(parents=True, exist_ok=True)
    log(f"prefetch starts; {len(items)} items")
    n_ok = n_fail = n_skip = 0
    t_start = time.time()
    for i, item in enumerate(items, 1):
        item_dir = ITEMS_DIR / item
        item_dir.mkdir(parents=True, exist_ok=True)
        pdf = item_dir / f"{item}.pdf"
        if pdf_complete(pdf):
            n_skip += 1
            log(f"[{i}/{len(items)}] {item}: have complete PDF "
                f"({pdf.stat().st_size//1024//1024} MB)")
            continue
        if pdf.exists():
            pdf.unlink()  # partial
        log(f"[{i}/{len(items)}] {item}: downloading")
        t0 = time.time()
        # 5 min timeout per file: if a datanode is throttling us, skip
        # and come back later rather than blocking the whole queue.
        try:
            r = subprocess.run(
                ["ia", "download", item, "--glob", "*.pdf",
                 "--destdir", str(ITEMS_DIR), "--retries", "2"],
                capture_output=True, text=True, timeout=300,
            )
            timed_out = False
        except subprocess.TimeoutExpired:
            timed_out = True
            r = None
        dt = time.time() - t0
        if not timed_out and r and r.returncode == 0 and pdf_complete(pdf):
            n_ok += 1
            log(f"  OK ({pdf.stat().st_size//1024//1024} MB) in {dt:.0f}s")
        else:
            n_fail += 1
            if timed_out:
                log(f"  TIMEOUT after {dt:.0f}s (slow datanode); skipping")
            else:
                err = (r.stderr or "").strip()[-200:] if r else ""
                rc = r.returncode if r else "?"
                log(f"  FAIL exit={rc} in {dt:.0f}s; stderr={err}")
            if pdf.exists() and not pdf_complete(pdf):
                pdf.unlink()
    log(f"prefetch done: ok={n_ok} fail={n_fail} skip={n_skip} "
        f"in {(time.time()-t_start)/60:.1f}m")


if __name__ == "__main__":
    main()
