"""Watch tmp/items/<item>/<item>_docling.json.gz for the 16-item batch
and upload each to its IA item as soon as it appears.

Runs alongside queue_docling_16.py rather than blocking inside it, so
the docling pass never stalls on a network upload.

Idempotent: tracks uploaded items in tmp/audit/uploaded_docling.txt and
skips re-uploads on restart.
"""
import subprocess
import sys
import time
from pathlib import Path

SEGART = Path("/Users/brewster/tmp/segart")
ITEMS_DIR = SEGART / "tmp" / "items"
LIST = Path("/tmp/items16.txt")
STATE = SEGART / "tmp" / "audit" / "uploaded_docling.txt"
LOG = SEGART / "tmp" / "audit" / "upload_docling_watcher.log"


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG, "a") as fh:
        fh.write(line + "\n")


def load_uploaded():
    if not STATE.exists():
        return set()
    return set(l.strip() for l in STATE.read_text().splitlines() if l.strip())


def mark_uploaded(item):
    with open(STATE, "a") as fh:
        fh.write(item + "\n")


def main():
    items = [l.strip() for l in LIST.read_text().splitlines() if l.strip()]
    LOG.parent.mkdir(parents=True, exist_ok=True)
    STATE.parent.mkdir(parents=True, exist_ok=True)
    log(f"watcher starts; tracking {len(items)} items")

    while True:
        uploaded = load_uploaded()
        all_done = True
        for item in items:
            if item in uploaded:
                continue
            cache = ITEMS_DIR / item / f"{item}_docling.json.gz"
            if not cache.exists():
                all_done = False
                continue
            # Wait a couple seconds in case segment_issue_docling.py is
            # mid-write (atomic-write should make this safe but a small
            # guard costs nothing).
            time.sleep(2)
            sz_mb = cache.stat().st_size / 1024 / 1024
            log(f"{item}: uploading docling cache ({sz_mb:.1f} MB)")
            cmd = ["ia", "upload", item, str(cache),
                   f"--remote-name={item}_docling.json.gz", "--no-derive"]
            t0 = time.time()
            r = subprocess.run(cmd, capture_output=True, text=True)
            dt = time.time() - t0
            if r.returncode == 0:
                mark_uploaded(item)
                log(f"  OK in {dt:.0f}s")
            else:
                err = (r.stderr or "").strip()[-200:]
                log(f"  FAIL exit={r.returncode} in {dt:.0f}s; {err}")
                all_done = False
        if all_done:
            log("watcher done: all 16 docling caches uploaded")
            return
        time.sleep(30)


if __name__ == "__main__":
    main()
