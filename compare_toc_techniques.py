#!/usr/bin/env python3
"""Compare heuristic-generated vs LLM-generated TOCs on the two
external-evidence checks segart already has:

  1. Crossref check  — augment_evidence.py marks entries with title-match
     against Crossref-indexed articles for the same journal/vol/issue.
  2. ILL recall      — score_toc.py walks the qa_corpus ILL anchors and
     scores how many anchors a TOC's entries cover.

For each item, runs both pipelines on the heuristic and the LLM TOC,
then prints a side-by-side scoreboard.
"""
import json
import subprocess
import sys
from pathlib import Path

SEGART = Path("/Users/brewster/tmp/segart")
COMPARE_DIR = SEGART / "tmp" / "tocs_compare"
ITEMS_DIR = SEGART / "tmp" / "items"
CORPUS = SEGART / "tmp" / "qa_corpus.jsonl"

ITEMS = [
    "sim_amerasia-journal_1989_15_1",
    "sim_american-journal-of-clinical-nutrition_1991-07_54_1",
    "sim_journal-of-college-student-development_november-december-1995_36_6",
    "sim_academy-of-management-review_2000-10_25_4",
    "sim_journal-of-clinical-psychiatry_1983-05_44_5_0",
    "sim_behavioral-and-brain-sciences_1980-09_3_3",
    "sim_ans_1978-10_1_1",
]


def run_augment(toc_path):
    cmd = [
        sys.executable, str(SEGART / "augment_evidence.py"),
        "--toc", str(toc_path),
        "--items-dir", str(ITEMS_DIR),
        "--corpus", str(CORPUS),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=SEGART)
    if r.returncode != 0:
        print(f"  augment failed: {r.stderr}", file=sys.stderr)
        return None
    d = json.loads(toc_path.read_text())
    n_total = len(d.get("entries") or [])
    n_in_toc = sum(1 for e in d["entries"] if "in_issue_toc" in (e.get("evidence") or []))
    n_xref = sum(1 for e in d["entries"] if "crossref_match" in (e.get("evidence") or []))
    return {"total": n_total, "in_issue_toc": n_in_toc, "crossref_match": n_xref}


def run_score(toc_path):
    cmd = [
        sys.executable, str(SEGART / "score_toc.py"),
        "--corpus", str(CORPUS),
        "--toc", str(toc_path),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=SEGART)
    out = r.stdout + r.stderr
    # Lines we want (from score_toc):
    #   "  hit (exact|soft):       6/16 (37%)"
    #   "    exact leaves:         5"
    #   "    soft leaves (±1):     1"
    #   "    leaves_strict: 10/16"
    #   "    leaves_soft:   11/16"
    #   "    title:         8/16"
    #   "    author:        6/16"
    out_dict = {"hits": 0, "anchors": 0, "exact": 0, "soft": 0,
                "lstrict": 0, "lsoft": 0, "title": 0, "author": 0,
                "findable": 0, "ambiguous": 0}
    import re
    for line in out.splitlines():
        s = line.strip()
        m = re.match(r"hit \(exact\|soft\):\s+(\d+)/(\d+)", s)
        if m: out_dict["hits"] = int(m.group(1)); out_dict["anchors"] = int(m.group(2))
        m = re.match(r"findable:\s+(\d+)/\d+.*ambiguous:\s+(\d+)", s)
        if m: out_dict["findable"] = int(m.group(1)); out_dict["ambiguous"] = int(m.group(2))
        m = re.match(r"exact leaves:\s+(\d+)", s)
        if m: out_dict["exact"] = int(m.group(1))
        m = re.match(r"soft leaves \(±1\):\s+(\d+)", s)
        if m: out_dict["soft"] = int(m.group(1))
        m = re.match(r"leaves_strict:\s+(\d+)/", s)
        if m: out_dict["lstrict"] = int(m.group(1))
        m = re.match(r"leaves_soft:\s+(\d+)/", s)
        if m: out_dict["lsoft"] = int(m.group(1))
        m = re.match(r"title:\s+(\d+)/", s)
        if m: out_dict["title"] = int(m.group(1))
        m = re.match(r"author:\s+(\d+)/", s)
        if m: out_dict["author"] = int(m.group(1))
    return out_dict


def main():
    print(f"{'item / variant':<60} {'ent':>4} {'xref':>5} {'ILLhit':>7} {'find':>5} {'leaf=':>5} {'leaf±':>5} {'title':>5} {'auth':>4}")
    print("-" * 110)
    rows = []
    for item in ITEMS:
        for label, suffix in (("heur", "_toc_heur.json"),
                              ("heurxref", "_toc_heurxref.json"),
                              ("llm",  "_toc_llm.json")):
            toc = COMPARE_DIR / f"{item}{suffix}"
            if not toc.exists():
                continue
            aug = run_augment(toc)
            sco = run_score(toc)
            row = {"item": item, "kind": label,
                   "entries": aug["total"] if aug else 0,
                   "in_toc": aug["in_issue_toc"] if aug else 0,
                   "xref": aug["crossref_match"] if aug else 0,
                   **sco}
            rows.append(row)
            short = item[len("sim_"):][:55]
            ill_str = f"{sco['hits']}/{sco['anchors']}"
            print(f"{short:<55}/{label:<3} {row['entries']:>4} "
                  f"{row['xref']:>5} {ill_str:>7} "
                  f"{sco['findable']:>5} "
                  f"{sco['lstrict']:>5} {sco['lsoft']:>5} "
                  f"{sco['title']:>5} {sco['author']:>4}")
    print("-" * 110)

    # Aggregate
    print()
    for kind in ("heur", "heurxref", "llm"):
        rs = [r for r in rows if r["kind"] == kind]
        ent = sum(r["entries"] for r in rs)
        xref = sum(r["xref"] for r in rs)
        hits = sum(r["hits"] for r in rs)
        anch = sum(r["anchors"] for r in rs)
        find = sum(r["findable"] for r in rs)
        amb = sum(r["ambiguous"] for r in rs)
        lstrict = sum(r["lstrict"] for r in rs)
        lsoft = sum(r["lsoft"] for r in rs)
        title = sum(r["title"] for r in rs)
        auth = sum(r["author"] for r in rs)
        print(f"{kind:>8}: entries={ent}  crossref={xref}  "
              f"ILL hit={hits}/{anch} ({100*hits//max(anch,1)}%)  "
              f"FINDABLE={find}/{anch} ({100*find//max(anch,1)}%, ambig={amb})  "
              f"leaf=:{lstrict}  leaf±:{lsoft}  title:{title}  auth:{auth}")


if __name__ == "__main__":
    main()
