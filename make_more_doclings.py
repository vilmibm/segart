#!/usr/bin/env python3
"""Process new periodical items into docling caches.

Picks items from the ILL logs that:
  - have ≥1 post-2024-04 ILL anchor (active, well-documented requests)
  - look like a periodical (sim_* / journal-* / pub_* / dated-vol-issue shape)
  - DO NOT yet have a docling cache on disk

Sorts by post-2024-04 anchor count (most-requested first) and processes
sequentially. Stops gracefully if disk space drops below a threshold.

Output: tmp/items/<item>/<item>_docling.json.gz for each processed item.
Logs to tmp/make_more.log.
"""
import csv
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

SEGART = Path("/Users/brewster/tmp/segart")
ITEMS = SEGART / "tmp" / "items"
TOCS = SEGART / "tmp" / "tocs"
SCRIPT = SEGART / "segment_issue_docling.py"
CUTOFF = int(datetime(2024, 4, 1, tzinfo=timezone.utc).timestamp())
LEAF_RE = re.compile(r"^n\d+$")
BOOK_SUFFIX = re.compile(r"\d{4}[a-z]+(_[a-z0-9]+)*$")
PERIODICAL_SHAPES = [
    re.compile(r"^sim_"),
    re.compile(r"^pub_"),
    re.compile(r"_\d{4}.*_\d+_\d+(?:-\d+)?$"),
    re.compile(r"_\d{4}-\d{2}_\d+_\d+(?:-\d+)?$"),
    re.compile(r"_(spring|summer|fall|autumn|winter|january|february|march|april|may|june|july|august|september|october|november|december)[\w-]*_\d+_\d+", re.I),
]

# Stop processing if free disk drops below this many GB.
MIN_FREE_GB = 2
# Max items to process in this run. Resumes from where we stopped on next run
# (since we skip items that already have caches).
MAX_ITEMS = 500
# Default per-item page cap honored by segment_issue_docling.
MAX_PAGES = int(os.environ.get("SEGART_MAX_PAGES", 500))


def is_periodical_id(ident):
    if not ident: return False
    if BOOK_SUFFIX.search(ident): return False
    return any(p.search(ident) for p in PERIODICAL_SHAPES)


def disk_free_gb():
    s = shutil.disk_usage(str(SEGART))
    return s.free / (1024 ** 3)


def find_candidates():
    """Scan ILL CSVs and return [(ident, anchor_count)] for processable items."""
    counts = Counter()
    for path in sorted(SEGART.glob("tmp/ill_logs/*.csv")):
        with open(path, newline='') as fh:
            for row in csv.DictReader(fh):
                ident = row.get("source_identifier") or ""
                if not is_periodical_id(ident): continue
                t = row.get("time")
                if not t or not t.isdigit() or int(t) < CUTOFF: continue
                try:
                    ff = json.loads(row.get("full_form") or "{}")
                except json.JSONDecodeError:
                    continue
                if not (ff.get("start") and ff.get("stop")): continue
                # Require leaf-shaped pair
                def all_leaves(arr):
                    return arr and all(LEAF_RE.match(str(x).strip()) for x in arr)
                raw_s, raw_e = ff.get("start") or [], ff.get("stop") or []
                norm_s, norm_e = ff.get("normalized_orig_start") or [], ff.get("normalized_orig_stop") or []
                if not (all_leaves(raw_s) or all_leaves(norm_s)):
                    continue
                counts[ident] += 1
    # Skip items that already have a docling cache
    candidates = []
    for ident, n in counts.most_common():
        cache = ITEMS / ident / f"{ident}_docling.json.gz"
        if cache.exists(): continue
        candidates.append((ident, n))
    return candidates


def process_one(ident):
    """Run segment_issue_docling.py for one item; return (rc, secs)."""
    out_toc = TOCS / f"{ident}_toc.json"
    cmd = [
        sys.executable, str(SCRIPT), ident,
        "--cache-dir", str(ITEMS),
        "-o", str(out_toc),
        "--device", "cpu",
    ]
    t0 = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True)
    dt = time.time() - t0
    if r.returncode != 0:
        # Surface a few lines of stderr for debugging.
        tail = "\n".join((r.stderr or "").splitlines()[-3:])
        print(f"  FAIL exit={r.returncode} in {dt:.0f}s",
              flush=True)
        if tail: print(f"    stderr: {tail}", flush=True)
    return r.returncode, dt


def prefetch_pdf(ident):
    """Start a background `ia download` for `ident`. Returns a Popen handle
    or None if the PDF already exists. Errors are silently dropped here —
    segment_issue_docling.py's own fetch_pdf will retry when it runs."""
    item_dir = ITEMS / ident
    pdf = item_dir / f"{ident}.pdf"
    if pdf.exists():
        return None
    item_dir.mkdir(parents=True, exist_ok=True)
    return subprocess.Popen(
        ["ia", "download", ident, "--glob", "*.pdf",
         "--destdir", str(ITEMS)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def main():
    TOCS.mkdir(parents=True, exist_ok=True)
    candidates = find_candidates()
    print(f"\n{len(candidates)} candidate items "
          f"(periodicals with post-2024-04 anchors, no docling cache yet)\n",
          flush=True)
    if not candidates: return

    n_ok = n_fail = 0
    t_start = time.time()
    pool = candidates[:MAX_ITEMS]
    prefetch = None  # Popen for the CURRENT iteration's item PDF (started in prior iteration)
    for i, (ident, anchor_count) in enumerate(pool, 1):
        free = disk_free_gb()
        if free < MIN_FREE_GB:
            if prefetch and prefetch.poll() is None:
                prefetch.terminate()
            print(f"\n[{i}/{MAX_ITEMS}] disk-low ({free:.1f}GB free) — stopping",
                  flush=True)
            break

        # Wait for the prefetch of *this* item (started one iteration earlier).
        # On i=1 there's nothing to wait for; segment_issue will fetch.
        if prefetch is not None:
            try: prefetch.wait()
            except Exception: pass
            prefetch = None

        elapsed_m = (time.time() - t_start) / 60
        print(f"[{i}/{MAX_ITEMS}] {ident} (anchors={anchor_count}, "
              f"disk={free:.1f}GB free, elapsed={elapsed_m:.1f}m)",
              flush=True)

        # Kick off prefetch for the NEXT item so its download overlaps
        # with this item's CPU conversion. Skip if disk is tight.
        if i < len(pool) and free > MIN_FREE_GB + 1:
            next_ident = pool[i][0]  # pool is 0-indexed; current=pool[i-1], next=pool[i]
            try: prefetch = prefetch_pdf(next_ident)
            except Exception as e:
                print(f"  WARN prefetch start failed for {next_ident}: {e}",
                      flush=True)
                prefetch = None

        try:
            rc, dt = process_one(ident)
        except Exception as e:
            n_fail += 1
            print(f"  EXCEPTION {e}", flush=True)
            continue
        if rc == 0:
            n_ok += 1
            print(f"  OK in {dt:.0f}s", flush=True)
        else:
            n_fail += 1

    # Clean up any straggler prefetch.
    if prefetch and prefetch.poll() is None:
        prefetch.terminate()

    print(f"\ndone: {n_ok} ok, {n_fail} fail in "
          f"{(time.time() - t_start)/60:.1f}m", flush=True)


if __name__ == "__main__":
    main()
