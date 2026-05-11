#!/usr/bin/env python3
"""Run docling sequentially over the 16-item pilot batch.

Reads the item list from /tmp/items16.txt, invokes
segment_issue_docling.py for each, and logs per-item status to
tmp/audit/queue_docling_16.log.

Sequential by design — docling can balloon past 3 GB RSS on this 8 GB
Air; running two in parallel risks an OOM panic. CPU mode for the
same reason.
"""
import json
import subprocess
import sys
import time
from pathlib import Path

SEGART = Path("/Users/brewster/tmp/segart")
ITEMS_DIR = SEGART / "tmp" / "items"
SEGMENTER = SEGART / "segment_issue_docling.py"
LOG = SEGART / "tmp" / "audit" / "queue_docling_16.log"
LIST = Path("/tmp/items16.txt")


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG, "a") as fh:
        fh.write(line + "\n")


def main():
    items = [l.strip() for l in LIST.read_text().splitlines() if l.strip()]
    log(f"queue starts; {len(items)} items")
    LOG.parent.mkdir(parents=True, exist_ok=True)

    t_start = time.time()
    n_ok = n_fail = n_skip = 0
    for i, item in enumerate(items, 1):
        cache = ITEMS_DIR / item / f"{item}_docling.json.gz"
        if cache.exists():
            n_skip += 1
            log(f"[{i}/{len(items)}] {item}: skipping (cache exists)")
            continue
        log(f"[{i}/{len(items)}] {item}: starting docling")
        t0 = time.time()
        # segment_issue_docling.py downloads the PDF, runs docling, writes
        # the cache + TOC. We don't need the TOC output here, but it
        # comes for free. Don't pass --keep-pdf so it cleans up the
        # ~50-100 MB PDF after conversion.
        cmd = [
            sys.executable, str(SEGMENTER), item,
            "--cache-dir", str(ITEMS_DIR),
            "--device", "cpu",
            "-o", str(SEGART / "tmp" / "tocs" / f"{item}_toc.json"),
        ]
        r = subprocess.run(cmd, capture_output=True, text=True)
        dt = time.time() - t0
        if r.returncode == 0 and cache.exists():
            n_ok += 1
            log(f"  OK in {dt:.0f}s "
                f"({cache.stat().st_size / 1024 / 1024:.1f} MB cache)")
        else:
            n_fail += 1
            tail = "\n  | ".join(((r.stderr or "").splitlines() or [])[-3:])
            log(f"  FAIL exit={r.returncode} in {dt:.0f}s")
            if tail:
                log(f"  stderr: {tail}")
    elapsed_min = (time.time() - t_start) / 60
    log(f"queue done: ok={n_ok} fail={n_fail} skip={n_skip} "
        f"in {elapsed_min:.1f}m")


if __name__ == "__main__":
    main()
