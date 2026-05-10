"""Find one (or a few) clean-tier journal issues with at least one
missed ILL anchor. Print:
  - IA item identifier (so user can look at the printed TOC at archive.org)
  - The journal's overall match% (clean tier ≥95%, ≥30 anchors)
  - All Crossref articles for that issue (from cache)
  - The missed ILL anchor: title, author, leaf range, printed pages, IA item
"""
import json
import re
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path

CORPUS = Path("/Users/brewster/tmp/segart/tmp/qa_corpus.jsonl")
AUDIT = Path("/tmp/journal_xref_audit.jsonl")
CACHE = Path("/Users/brewster/tmp/segart/tmp/crossref_journal_year_cache")

STOPWORDS = {
    "the","a","an","of","and","in","on","for","to","with","by","from",
    "la","le","les","der","die","das","el","los","il","de","du",
    "is","at","as","that","this","be","or","not","but",
}


def normalize(s):
    if not s: return []
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return [w for w in s.split() if w and w not in STOPWORDS and len(w) > 1]


def fuzzy_first_n_match(a, b, n=4, ratio=0.8):
    wa = normalize(a); wb = normalize(b)
    if not wa or not wb: return False
    if len(wa) >= 1 and len(wb) >= 1 and wa[0] == wb[0] and (len(wa) == 1 or len(wb) == 1):
        return True
    k = min(n, len(wa), len(wb))
    if k < 2: return False
    ha = " ".join(wa[:k]); hb = " ".join(wb[:k])
    if ha == hb: return True
    return SequenceMatcher(None, ha, hb).ratio() >= ratio


def is_junk_anchor(article_title, journal_title):
    a = normalize(article_title or ""); j = normalize(journal_title or "")
    if not a or not j: return False
    sa = " ".join(a); sj = " ".join(j)
    if sa in sj or sj in sa: return True
    return SequenceMatcher(None, sa, sj).ratio() >= 0.85


def extract_surnames(s):
    if not s: return set()
    surnames = set()
    for piece in re.split(r"\s*;\s*|\s+and\s+|\s*&\s*|\s*\u2219\s*", s):
        piece = piece.strip()
        if not piece: continue
        if "," in piece:
            surname_text = piece.split(",", 1)[0]
            words = re.findall(r"[A-Z][a-zA-Z'\u00c0-\u017f-]+", surname_text)
            if words: surnames.add(words[0].lower())
        else:
            words = re.findall(r"[A-Z][a-zA-Z'\u00c0-\u017f-]+", piece)
            long_words = [w for w in words if len(w) >= 3]
            chosen = long_words or words
            if chosen: surnames.add(chosen[-1].lower())
    return surnames


def author_surname_match(anchor_author, xref_records):
    anchor_set = extract_surnames(anchor_author or "")
    if not anchor_set: return False
    for r in xref_records:
        for a in r.get("author") or []:
            fam = (a.get("family") or "").strip().lower()
            if fam and fam in anchor_set: return True
    return False


def cache_path(issn, year):
    safe = re.sub(r"[^A-Za-z0-9-]", "_", issn)
    return CACHE / f"{safe}_{year}.json"


def load_year(issn, year):
    p = cache_path(issn, year)
    if not p.exists(): return None
    try: return json.loads(p.read_text()).get("items", [])
    except Exception: return None


def main():
    # 1. Pick clean journals (>=95%, >=30 anchors)
    clean_issns = {}
    for line in AUDIT.read_text().splitlines():
        d = json.loads(line)
        pct = d.get("overall_match_pct"); anch = d.get("anchors_seen", 0)
        if pct is not None and pct >= 95 and anch >= 30:
            clean_issns[d["issn"]] = (pct, anch)
    print(f"clean journals (>=95% match, >=30 anchors): {len(clean_issns)}")

    # 2. Load anchors with item identifier
    anchors_by_issue = defaultdict(list)  # (issn, vol, iss, yr) -> [(item, anchor)]
    n_loaded = 0
    n_junk = 0
    with open(CORPUS) as f:
        for line in f:
            line = line.strip()
            if not line: continue
            try: rec = json.loads(line)
            except: continue
            item = rec.get("identifier")
            for a in rec.get("anchors", []):
                issn = (a.get("issn") or "").strip()
                if issn not in clean_issns: continue
                vol = (a.get("volume") or "").strip()
                iss = (a.get("issue") or "").strip()
                yr = (a.get("year") or "").strip()[:4]
                t = (a.get("article_title") or "").strip()
                jt = (a.get("journal_title") or "").strip()
                if not (vol and iss and yr.isdigit() and t): continue
                if is_junk_anchor(t, jt):
                    n_junk += 1; continue
                anchors_by_issue[(issn, vol, iss, yr)].append((item, a))
                n_loaded += 1
    print(f"loaded {n_loaded} anchors over {len(anchors_by_issue)} issues "
          f"in clean journals (junk filtered: {n_junk})")

    # 3. Find issues with misses
    misses_found = []
    for (issn, vol, iss, yr), anchors_here in anchors_by_issue.items():
        year_recs = load_year(issn, yr)
        if year_recs is None: continue
        xref_issue = [r for r in year_recs
                      if str(r.get("volume","")).strip() == vol
                      and str(r.get("issue","")).strip() == iss]
        if not xref_issue: continue
        miss_anchors = []
        for item, a in anchors_here:
            t = a["article_title"]; au = a.get("article_author") or ""
            t_hit = any(fuzzy_first_n_match(t, (r.get("title") or [""])[0])
                        for r in xref_issue)
            au_hit = author_surname_match(au, xref_issue) if au else False
            if not (t_hit or au_hit):
                miss_anchors.append((item, a))
        if miss_anchors:
            misses_found.append((issn, vol, iss, yr, anchors_here, xref_issue, miss_anchors))

    print(f"\nclean-tier issues with at least 1 miss: {len(misses_found)}")
    if not misses_found:
        return

    # Sort by overall journal match% descending, then by miss count ascending
    # so we surface the cleanest journals' isolated misses first.
    misses_found.sort(key=lambda x: (-clean_issns[x[0]][0], len(x[6])))

    # Print up to 5 examples
    for ex_i, (issn, vol, iss, yr, anchors_here, xref_issue, misses) in enumerate(misses_found[:5]):
        pct, total_anchors = clean_issns[issn]
        # IA item identifier(s) for this issue (usually 1)
        items_for_issue = sorted({item for item, _ in anchors_here if item})
        print()
        print("=" * 88)
        print(f"JOURNAL  ISSN {issn}   journal-overall: {pct}% match across {total_anchors} anchors")
        print(f"ISSUE    vol {vol}  iss {iss}  year {yr}")
        for item in items_for_issue:
            print(f"  IA item: {item}")
            print(f"           https://archive.org/details/{item}")
        print(f"\n  Crossref returned {len(xref_issue)} article(s) for this issue:")
        for j, r in enumerate(xref_issue, 1):
            t = (r.get("title") or [""])[0]
            pp = r.get("page", "?")
            doi = r.get("DOI")
            au = ", ".join((a.get("family") or "?")
                           for a in (r.get("author") or [])[:3])
            print(f"    [{j:>2}] pp.{pp:<14} authors: {au[:60]}")
            print(f"         title: {t[:80]!r}")
            print(f"         doi:   {doi}")
        print(f"\n  ILL anchor(s) that missed ({len(misses)} of {len(anchors_here)} for this issue):")
        for item, a in misses:
            au = (a.get("article_author") or "")[:60]
            lf = a.get("leaf_ranges") or []
            lf_str = ", ".join(f"{r[0]}-{r[-1]}" for r in lf)
            pp = a.get("printed_pages") or ""
            print(f"    title:   {a['article_title'][:80]!r}")
            print(f"    author:  {au!r}")
            print(f"    leaves:  {lf_str}    printed pp.{pp}")
            print(f"    IA item: {item}")


if __name__ == "__main__":
    main()
