"""Probe IA metadata for sim_* Elsevier clean-tier items, looking for
format:Docling. Saves hits incrementally to tmp/audit/elsevier_docling.jsonl
so the probe is resumable and interruptible.
"""
import argparse
import json
import os
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

SEGART = Path("/Users/brewster/tmp/segart")
HITS_FILE = SEGART / "tmp" / "audit" / "elsevier_docling.jsonl"
PROBED_FILE = SEGART / "tmp" / "audit" / "elsevier_docling_probed.txt"


def load_clean_elsevier():
    clean_issns = set()
    with open(SEGART / "tmp" / "audit" / "journal_xref_audit.jsonl") as f:
        for line in f:
            try: r = json.loads(line)
            except: continue
            if (r.get("overall_match_pct") or 0) >= 95 and (r.get("anchors_seen") or 0) >= 20:
                clean_issns.add(r["issn"])
    elsevier_issns = set()
    with open(SEGART / "tmp" / "audit" / "journal_publishers.jsonl") as f:
        for line in f:
            try: r = json.loads(line)
            except: continue
            if r.get("error"): continue
            pub = r.get("publisher") or ""
            if r["issn"] in clean_issns and "lsevier" in pub.lower():
                elsevier_issns.add(r["issn"])
    return elsevier_issns


def load_candidates(elsevier_issns):
    sim = set()
    with open(SEGART / "tmp" / "qa_corpus.jsonl") as f:
        for line in f:
            try: row = json.loads(line)
            except: continue
            ident = row.get("identifier") or ""
            if not ident.startswith("sim_"): continue
            if "contents" in ident.lower() or "index" in ident.lower(): continue
            for a in row.get("anchors") or []:
                if (a.get("issn") or "").upper() in elsevier_issns:
                    sim.add(ident); break
    return sim


def probe(ident):
    try:
        req = urllib.request.Request(
            f"https://archive.org/metadata/{ident}/files",
            headers={"User-Agent": "segart-probe/0.1"})
        with urllib.request.urlopen(req, timeout=15) as fh:
            d = json.load(fh)
        for f in (d.get("result") or []):
            if f.get("format") == "Docling":
                return ident, True
        return ident, False
    except Exception:
        return ident, None


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=2000,
                    help="Max items to probe this run (default 2000)")
    ap.add_argument("--workers", type=int, default=10)
    args = ap.parse_args()

    elsevier_issns = load_clean_elsevier()
    candidates = load_candidates(elsevier_issns)
    published = {d.replace("pilot_", "")
                 for d in os.listdir(SEGART / "tmp" / "audit")
                 if d.startswith("pilot_sim_")}
    probed = set()
    if PROBED_FILE.exists():
        probed = set(PROBED_FILE.read_text().splitlines())
    pool = sorted(candidates - published - probed)
    if args.limit and len(pool) > args.limit:
        pool = pool[:args.limit]
    print(f"clean Elsevier ISSNs: {len(elsevier_issns)}")
    print(f"sim candidates: {len(candidates)}")
    print(f"  already published: {len(candidates & published)}")
    print(f"  already probed: {len(candidates & probed)}")
    print(f"  probing this run: {len(pool)}")

    hits = []
    if HITS_FILE.exists():
        for line in HITS_FILE.open():
            try: hits.append(json.loads(line))
            except: pass
    print(f"existing hits: {len(hits)}")
    print()

    t0 = time.time()
    n_done = 0
    new_hits = 0
    with HITS_FILE.open("a") as hits_fh, PROBED_FILE.open("a") as probed_fh:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futures = {ex.submit(probe, i): i for i in pool}
            for fut in as_completed(futures):
                ident, has = fut.result()
                n_done += 1
                probed_fh.write(ident + "\n")
                if has:
                    hits_fh.write(json.dumps({"ident": ident}) + "\n")
                    new_hits += 1
                if n_done % 250 == 0:
                    print(f"  {n_done}/{len(pool)}  new_hits={new_hits}  "
                          f"elapsed={time.time()-t0:.0f}s", flush=True)
    print(f"\ndone: {new_hits} new hits in {time.time()-t0:.0f}s")
    print(f"total hits in {HITS_FILE}: {len(hits) + new_hits}")


if __name__ == "__main__":
    main()
