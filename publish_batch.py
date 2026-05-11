"""End-to-end publish pipeline for a list of items.

For each item, runs:
  1. heuristic_toc_crossref.py        → raw heurxref output
  2. heur_xref_to_legacy.py           → v2 legacy TOC (with docling ToC entry)
  3. tools/build_articles_companion.py → articles companion
  4. tools/publish_toc.py             → upload all + post IA review

Skips each step if its output already exists. Logs to
tmp/audit/publish_batch.log.

Usage:
  python3 publish_batch.py /tmp/items32.txt
"""
import argparse
import gzip
import json
import subprocess
import sys
import time
from pathlib import Path

SEGART = Path("/Users/brewster/tmp/segart")
TOCS = SEGART / "tmp" / "tocs"
PILOTS = SEGART / "tmp" / "audit"
LOG = SEGART / "tmp" / "audit" / "publish_batch.log"


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG, "a") as fh:
        fh.write(line + "\n")


def run(cmd, **kwargs):
    """Run, capture, return (rc, stdout, stderr)."""
    r = subprocess.run(cmd, capture_output=True, text=True, **kwargs)
    return r.returncode, r.stdout, r.stderr


def step_heurxref(item):
    """Generate raw heurxref output. Always re-runs — the heurxref tool
    has been improving and any cached output may be from an older
    version. Cheap (~5-30s) so just regenerate."""
    out = TOCS / f"{item}_toc_heur_xref.json"
    rc, _, err = run([sys.executable, str(SEGART / "heuristic_toc_crossref.py"),
                      item, "--out", str(out)])
    if rc != 0:
        return False, f"heurxref exit={rc}: {err.strip()[-300:]}"
    return out.exists(), "ok"


def step_legacy(item):
    """Convert raw heurxref → v2 legacy TOC."""
    src = TOCS / f"{item}_toc_heur_xref.json"
    pilot_dir = PILOTS / f"pilot_{item}"
    pilot_dir.mkdir(parents=True, exist_ok=True)
    out = pilot_dir / f"{item}_toc.json"
    rc, _, err = run([sys.executable, str(SEGART / "heur_xref_to_legacy.py"),
                      str(src), "--out", str(out)])
    if rc != 0:
        return False, f"legacy exit={rc}: {err.strip()[-300:]}"
    return out.exists(), "ok"


def step_articles(item):
    """Build articles companion."""
    pilot_dir = PILOTS / f"pilot_{item}"
    toc = pilot_dir / f"{item}_toc.json"
    out = pilot_dir / f"{item}_articles.json.gz"
    rc, _, err = run([sys.executable, str(SEGART / "tools" / "build_articles_companion.py"),
                      str(toc), str(out)])
    if rc != 0:
        return False, f"articles exit={rc}: {err.strip()[-300:]}"
    return out.exists(), "ok"


def step_publish(item):
    """Upload TOC + articles + docling cache, post review."""
    pilot_dir = PILOTS / f"pilot_{item}"
    toc = pilot_dir / f"{item}_toc.json"
    art = pilot_dir / f"{item}_articles.json.gz"
    rc, _, err = run([sys.executable, str(SEGART / "tools" / "publish_toc.py"),
                      item, "--toc", str(toc), "--articles", str(art),
                      "--method", "heurxref+docling"])
    if rc != 0:
        return False, f"publish exit={rc}: {err.strip()[-300:]}"
    return True, "ok"


def process(item):
    log(f"  step 1/4 heurxref")
    ok, msg = step_heurxref(item)
    if not ok: return False, f"heurxref FAIL: {msg}"
    log(f"  step 2/4 legacy ({msg})")
    ok, msg = step_legacy(item)
    if not ok: return False, f"legacy FAIL: {msg}"
    log(f"  step 3/4 articles ({msg})")
    ok, msg = step_articles(item)
    if not ok: return False, f"articles FAIL: {msg}"
    log(f"  step 4/4 publish ({msg})")
    ok, msg = step_publish(item)
    if not ok: return False, f"publish FAIL: {msg}"
    return True, "published"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("items_file")
    ap.add_argument("--dry-run", action="store_true",
                    help="Run steps 1-3 only, skip publish")
    args = ap.parse_args()

    items = [l.strip() for l in Path(args.items_file).read_text().splitlines()
             if l.strip()]
    LOG.parent.mkdir(parents=True, exist_ok=True)
    log(f"publish_batch starts; {len(items)} items "
        f"({'DRY-RUN' if args.dry_run else 'live'})")

    n_ok = n_fail = 0
    t_start = time.time()
    for i, item in enumerate(items, 1):
        elapsed = (time.time() - t_start) / 60
        log(f"[{i}/{len(items)}] {item} (elapsed {elapsed:.1f}m)")
        t0 = time.time()
        try:
            if args.dry_run:
                ok, msg = True, "dry-run"
                for fn in (step_heurxref, step_legacy, step_articles):
                    ok, msg = fn(item)
                    if not ok: break
            else:
                ok, msg = process(item)
        except Exception as ex:
            ok, msg = False, f"EXC: {ex}"
        dt = time.time() - t0
        if ok:
            n_ok += 1
            log(f"  ✓ {msg} in {dt:.0f}s")
        else:
            n_fail += 1
            log(f"  ✗ {msg} in {dt:.0f}s")
    log(f"batch done: ok={n_ok} fail={n_fail} "
        f"in {(time.time()-t_start)/60:.1f}m")


if __name__ == "__main__":
    main()
