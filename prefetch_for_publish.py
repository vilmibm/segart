"""Prefetch docling + pn.json + scandata for a list of items, so
publish_batch can run without each subprocess re-downloading."""
import argparse
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

SEGART = Path("/Users/brewster/tmp/segart")
ITEMS = SEGART / "tmp" / "items"


def have_all(ident):
    d = ITEMS / ident
    return (
        (d / f"{ident}_docling.json.gz").exists()
        and (d / f"{ident}_page_numbers.json").exists()
        and (d / f"{ident}_scandata.xml").exists()
    )


def fetch(ident):
    if have_all(ident):
        return ident, "cached"
    (ITEMS / ident).mkdir(parents=True, exist_ok=True)
    cmd = [
        "ia", "download", ident,
        # ia download takes globs separated by `|`, not multiple --glob flags.
        "--glob", "*_docling.json.gz|*_page_numbers.json|*_scandata.xml",
        "--destdir", str(ITEMS),
        "--retries", "3",
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired:
        return ident, "timeout"
    if r.returncode != 0:
        return ident, f"fail rc={r.returncode}"
    return ident, "ok" if have_all(ident) else "incomplete"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("items_file")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()
    items = [l.strip() for l in Path(args.items_file).read_text().splitlines() if l.strip()]
    print(f"prefetching {len(items)} items with {args.workers} workers")
    t0 = time.time()
    counts = {"ok": 0, "cached": 0, "fail": 0, "timeout": 0}
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {ex.submit(fetch, i): i for i in items}
        n = 0
        for fut in as_completed(futures):
            ident, status = fut.result()
            n += 1
            k = "ok" if status == "ok" else ("cached" if status == "cached"
                 else ("timeout" if status == "timeout" else "fail"))
            counts[k] += 1
            if n % 10 == 0:
                print(f"  {n}/{len(items)}  ok={counts['ok']} cached={counts['cached']} "
                      f"fail={counts['fail']} timeout={counts['timeout']}  "
                      f"elapsed={time.time()-t0:.0f}s", flush=True)
    print(f"\ndone in {time.time()-t0:.0f}s: {counts}")


if __name__ == "__main__":
    main()
