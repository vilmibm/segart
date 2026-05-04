#!/usr/bin/env python3
"""Download ILL log daily CSVs from the iailllogs collection.

The iailllogs collection is access-restricted, so this shells out to the
`ia` CLI (https://archive.org/developers/internetarchive/) which uses the
local user's IA credentials. Run `ia configure` once if you haven't.

Lists every monthly item in `collection:iailllogs`, then for each one
runs `ia download <item> --glob "*.csv"` into a flat output directory.
Already-present files are skipped by `ia` automatically.

Usage:
  ./download_ill_logs.py --output-dir /tmp/ill_logs
  ./download_ill_logs.py --output-dir /tmp/ill_logs --months 2026-04 2026-03
"""
import argparse
import os
import shutil
import subprocess
import sys


def list_monthly_items(months=None):
    out = subprocess.run(
        ["ia", "search", "collection:iailllogs", "--itemlist"],
        check=True,
        capture_output=True,
        text=True,
    )
    items = sorted(line.strip() for line in out.stdout.splitlines() if line.strip())
    if months:
        wanted = {f"ill_logs_{m}" for m in months}
        items = [i for i in items if i in wanted]
    return items


def download_item(item, out_dir):
    """Run `ia download` for one item; CSVs land in {out_dir}/{item}/.

    Returns (item, returncode).
    """
    proc = subprocess.run(
        [
            "ia",
            "download",
            item,
            "--glob",
            "*.csv",
            "--destdir",
            out_dir,
        ],
        capture_output=True,
        text=True,
    )
    return item, proc.returncode, proc.stderr


def flatten(out_dir):
    """Move CSVs from {out_dir}/<item>/<csv> up to {out_dir}/<csv>."""
    moved = 0
    for entry in os.listdir(out_dir):
        sub = os.path.join(out_dir, entry)
        if not os.path.isdir(sub):
            continue
        for fn in os.listdir(sub):
            if fn.endswith(".csv"):
                src = os.path.join(sub, fn)
                dst = os.path.join(out_dir, fn)
                if not os.path.exists(dst):
                    shutil.move(src, dst)
                    moved += 1
        try:
            os.rmdir(sub)
        except OSError:
            pass
    return moved


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output-dir", default="/tmp/ill_logs")
    p.add_argument(
        "--months",
        nargs="+",
        help="Restrict to specific YYYY-MM values; default = all",
    )
    args = p.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)
    items = list_monthly_items(args.months)
    print(f"{len(items)} monthly items", file=sys.stderr)

    failed = []
    for i, item in enumerate(items, 1):
        ident, rc, err = download_item(item, args.output_dir)
        marker = "ok" if rc == 0 else f"ERR rc={rc}"
        print(f"  [{i}/{len(items)}] {ident}: {marker}", file=sys.stderr)
        if rc != 0:
            failed.append((ident, err.strip().splitlines()[-1] if err else ""))

    moved = flatten(args.output_dir)
    print(
        f"\ndone: {len(items) - len(failed)} items ok, {len(failed)} failed; "
        f"{moved} CSVs flattened into {args.output_dir}/",
        file=sys.stderr,
    )
    for ident, msg in failed[:5]:
        print(f"  {ident}: {msg}", file=sys.stderr)


if __name__ == "__main__":
    main()
