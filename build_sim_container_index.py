#!/usr/bin/env python3
"""Build a compact index of fatcat containers that have IA SIM coverage.

Downloads (or reads) the latest fatcat container_export.json.gz and emits
JSONL — one line per SIM-bearing container — keyed by ISSN-L.

This is the SIM (microfilm) slice specifically. SIM is one of several IA
source collections backing periodicals; for the full segart scope, see
`match_pub_to_fatcat.py`, which joins IA pub_* collections to fatcat
containers across all sources.

Source: https://archive.org/details/fatcat_snapshots_and_exports
        (fatcat_bulk_exports_<date>/container_export.json.gz, ~25 MB)

Usage:
  ./build_sim_container_index.py                              # download default snapshot
  ./build_sim_container_index.py --snapshot 2024-02-18 -o sim_containers.jsonl
  ./build_sim_container_index.py --input container_export.json.gz
"""
import argparse
import gzip
import json
import sys
import urllib.request

DEFAULT_SNAPSHOT = "2024-02-18"
URL_TEMPLATE = (
    "https://archive.org/download/"
    "fatcat_bulk_exports_{date}/container_export.json.gz"
)


def open_source(args):
    if args.input:
        return gzip.open(args.input, "rt", encoding="utf-8")
    url = args.url or URL_TEMPLATE.format(date=args.snapshot)
    print(f"streaming {url}", file=sys.stderr)
    resp = urllib.request.urlopen(url, timeout=60)
    return gzip.open(resp, "rt", encoding="utf-8")


def project(container):
    """Return a compact dict for a SIM-bearing container, or None."""
    extra = container.get("extra") or {}
    sim = (extra.get("ia") or {}).get("sim") or {}
    sim_pubid = sim.get("sim_pubid")
    if not sim_pubid:
        return None
    return {
        "ident": container.get("ident"),
        "issnl": container.get("issnl"),
        "issne": container.get("issne"),
        "issnp": container.get("issnp"),
        "name": container.get("name"),
        "publisher": container.get("publisher"),
        "container_type": container.get("container_type"),
        "wikidata_qid": container.get("wikidata_qid"),
        "sim_pubid": sim_pubid,
        "sim_year_spans": sim.get("year_spans"),
        "sim_peer_reviewed": sim.get("peer_reviewed"),
        "kbart": extra.get("kbart"),
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--input",
        help="Local container_export.json.gz (skip download)",
    )
    p.add_argument(
        "--snapshot",
        default=DEFAULT_SNAPSHOT,
        help=f"Snapshot date for the bulk export (default {DEFAULT_SNAPSHOT})",
    )
    p.add_argument("--url", help="Override the download URL entirely")
    p.add_argument("-o", "--output", help="Output JSONL path (default: stdout)")
    args = p.parse_args()

    fout = open(args.output, "w") if args.output else sys.stdout
    n_in = n_out = 0
    with open_source(args) as fin:
        for line in fin:
            n_in += 1
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            proj = project(rec)
            if proj is None:
                continue
            fout.write(json.dumps(proj, ensure_ascii=False) + "\n")
            n_out += 1
            if n_in % 50000 == 0:
                print(
                    f"  scanned {n_in} containers, kept {n_out}",
                    file=sys.stderr,
                )

    if args.output:
        fout.close()
    print(
        f"in={n_in} out={n_out}  "
        f"({n_out / n_in * 100:.1f}% of containers have IA SIM coverage)",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
