#!/usr/bin/env python3
"""How well does each LLM TOC capture the articles Crossref lists for the
same (journal, vol, issue, year)?

Crossref returns a definitive list of articles published in an issue (with
DOIs). For each Crossref article, we ask: did our LLM TOC emit a matching
entry? This measures TOC recall against an external, citation-backed
ground truth — independent of ILL traffic.

Output:
  - aggregate stats per item (matched / total / extras / missing)
  - per-item examples of missing entries (Crossref says exists, TOC missed)
"""
import csv, glob, json, os, re, sys
from datetime import datetime, timezone
from difflib import SequenceMatcher
from collections import Counter, defaultdict

CUTOFF = int(datetime(2024, 4, 1, tzinfo=timezone.utc).timestamp())
SEGART = "/Users/brewster/tmp/segart"

STOPWORDS = {"the", "a", "an", "of", "and", "in", "on", "for", "to",
             "la", "le", "les", "der", "die", "das", "el", "los", "il"}


def normalize_title(s):
    if not s: return ""
    s = re.sub(r"[^a-z0-9]+", " ", s.lower())
    return " ".join(w for w in s.split() if w not in STOPWORDS)


def title_similarity(a, b):
    na, nb = normalize_title(a), normalize_title(b)
    if not na or not nb: return 0.0
    if na == nb: return 1.0
    if na in nb or nb in na: return 0.95
    ta, tb = set(na.split()), set(nb.split())
    if not ta or not tb: return 0.0
    overlap = len(ta & tb) / max(1, min(len(ta), len(tb)))
    ratio = SequenceMatcher(None, na, nb).ratio()
    return max(overlap, ratio)


def derive_metadata(item):
    """Pull issn/vol/issue/year from any post-2024-04 ILL row for this item."""
    for path in sorted(glob.glob(f"{SEGART}/tmp/ill_logs/*.csv")):
        with open(path, newline='') as fh:
            for row in csv.DictReader(fh):
                if row.get("source_identifier") != item: continue
                t = row.get("time")
                if not t or not t.isdigit() or int(t) < CUTOFF: continue
                try: ff = json.loads(row.get("full_form") or "{}")
                except: continue
                p = ff.get("original_request_params") or {}
                issn = (p.get("standard_number") or "").strip().split(";")[0].strip()
                vol = (p.get("journal_volume") or "").strip()
                iss = (p.get("journal_issue") or "").strip()
                yr = (p.get("journal_year") or "").strip()
                if issn and vol and iss and yr:
                    return issn, yr, vol, iss
    return None


def crossref_cache_path(issn, year, vol, issue):
    return f"{SEGART}/tmp/crossref_cache/{issn}_{year}_{vol}_{issue}.json"


def load_llm_toc(item):
    p = f"{SEGART}/tmp/tocs/{item}_toc_llm.json"
    if not os.path.exists(p): return None
    return json.load(open(p))


def main():
    llm_items = sorted(
        os.path.basename(p).replace("_toc_llm.json", "")
        for p in glob.glob(f"{SEGART}/tmp/tocs/*_toc_llm.json")
    )

    print(f"{'item':<60} {'XR':>4} {'matched':>7} {'recall':>7} {'extras':>7}")
    print("-" * 95)
    aggregate_xr = 0
    aggregate_match = 0
    aggregate_extras = 0
    per_item_misses = {}
    per_item_extras = {}

    for item in llm_items:
        md = derive_metadata(item)
        if md is None:
            print(f"  {item[:58]:<58} {'??':>4}  (no metadata)")
            continue
        issn, yr, vol, iss = md
        cache = crossref_cache_path(issn, yr, vol, iss)
        if not os.path.exists(cache):
            print(f"  {item[:58]:<58} {'-':>4}  (no crossref cache: {issn}/{vol}/{iss}/{yr})")
            continue
        xr_articles = json.load(open(cache))
        if not xr_articles:
            print(f"  {item[:58]:<58} {0:>4}  (cache empty)")
            continue

        toc = load_llm_toc(item)
        toc_entries = toc.get("entries") or []
        toc_titles = [(e.get("title") or "") for e in toc_entries]
        toc_matched_idx = set()  # entries that got matched to ≥1 xr article

        matched = 0
        misses = []
        for xr in xr_articles:
            xr_title = xr.get("title")
            if isinstance(xr_title, list): xr_title = xr_title[0] if xr_title else ""
            if not xr_title: continue
            best_idx = -1; best_sim = 0
            for i, tt in enumerate(toc_titles):
                sim = title_similarity(xr_title, tt)
                if sim > best_sim:
                    best_idx, best_sim = i, sim
            if best_sim >= 0.6:
                matched += 1
                toc_matched_idx.add(best_idx)
            else:
                misses.append({"xr_title": xr_title, "best_match": toc_titles[best_idx] if best_idx >= 0 else None,
                               "best_sim": round(best_sim, 2)})

        # TOC entries with no Crossref match — could be real entries Crossref
        # missed (e.g., editorials, book reviews not indexed) OR our extras.
        # For "real article"-typed entries this is more meaningful.
        extras_real = []
        for i, e in enumerate(toc_entries):
            if i in toc_matched_idx: continue
            if e.get("type") not in ("article", "review"): continue
            extras_real.append(e.get("title") or "")

        n_xr = len(xr_articles)
        recall = f"{100*matched//max(n_xr,1)}%"
        print(f"  {item[:58]:<58} {n_xr:>4} {matched:>7} {recall:>7} "
              f"{len(extras_real):>7}")

        aggregate_xr += n_xr
        aggregate_match += matched
        aggregate_extras += len(extras_real)
        per_item_misses[item] = misses
        per_item_extras[item] = extras_real[:5]

    print("-" * 95)
    if aggregate_xr:
        print(f"  {'TOTAL':<58} {aggregate_xr:>4} {aggregate_match:>7} "
              f"{100*aggregate_match//aggregate_xr:>6}% {aggregate_extras:>7}")

    # Per-item misses (Crossref articles our TOC missed)
    print(f"\n=== Crossref articles our LLM TOC missed (best fuzzy ≤0.6) ===")
    for item, misses in per_item_misses.items():
        if not misses: continue
        print(f"\n{item} — {len(misses)} miss(es):")
        for m in misses[:8]:
            print(f"    XR: {m['xr_title'][:70]}")
            print(f"    →   best={m['best_sim']} '{(m['best_match'] or '')[:70]}'")

    # Per-item extras (TOC entries Crossref doesn't list)
    print(f"\n=== TOC article-type entries with no Crossref match (top 5 each) ===")
    for item, extras in per_item_extras.items():
        if not extras: continue
        print(f"\n{item}:")
        for e in extras:
            print(f"    {e[:90]}")


if __name__ == "__main__":
    main()
