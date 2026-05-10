"""For each ILL anchor that missed in a clean-tier journal, query
Crossref by title text to categorize the cause:

  A. real_gap         — Crossref has no record matching the title anywhere
  B. wrong_vol_iss    — record exists with same ISSN but different vol/iss
                        (publisher metadata error, OR ILL anchor's
                        vol/iss is wrong)
  C. wrong_issn       — record exists under a different ISSN
                        (publisher used a different journal identifier)
  D. matcher_miss     — record exists with same ISSN, vol, iss, year —
                        my matcher's threshold rejected it. Loosen to fix.
  E. junk_anchor      — ILL anchor's article_title is essentially the
                        journal title (already filtered upstream, this
                        catches any leakage)

Per-miss output is JSONL; aggregate counts printed at end.
"""
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict, Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from difflib import SequenceMatcher
from pathlib import Path

SEGART = Path("/Users/brewster/tmp/segart")
CORPUS = SEGART / "tmp" / "qa_corpus.jsonl"
AUDIT = SEGART / "tmp" / "audit" / "journal_xref_audit.jsonl"
CACHE = SEGART / "tmp" / "crossref_journal_year_cache"
OUT = SEGART / "tmp" / "audit" / "clean_misses_diagnosis.jsonl"

STOPWORDS = {
    "the","a","an","of","and","in","on","for","to","with","by","from",
    "la","le","les","der","die","das","el","los","il","de","du",
    "is","at","as","that","this","be","or","not","but",
}
HEADERS = {"User-Agent": "segart-clean-miss-investigator/0.1 (mailto:brewster@archive.org)"}


def normalize(s):
    if not s: return []
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return [w for w in s.split() if w and w not in STOPWORDS and len(w) > 1]


def fuzzy_match(a, b, n=4, ratio=0.8):
    wa = normalize(a); wb = normalize(b)
    if not wa or not wb: return False
    if len(wa) >= 1 and len(wb) >= 1 and wa[0] == wb[0] and (len(wa) == 1 or len(wb) == 1):
        return True
    k = min(n, len(wa), len(wb))
    if k < 2: return False
    ha = " ".join(wa[:k]); hb = " ".join(wb[:k])
    if ha == hb: return True
    return SequenceMatcher(None, ha, hb).ratio() >= ratio


def loose_match(a, b):
    """Looser than fuzzy_match — used for diagnosis. Identical first
    3 content words count, or SequenceMatcher >= 0.7 on full normalized."""
    wa = normalize(a); wb = normalize(b)
    if not wa or not wb: return False
    k = min(3, len(wa), len(wb))
    if k >= 1 and " ".join(wa[:k]) == " ".join(wb[:k]):
        return True
    sa = " ".join(wa); sb = " ".join(wb)
    return SequenceMatcher(None, sa, sb).ratio() >= 0.7


def is_junk_anchor(at, jt):
    a = normalize(at or ""); j = normalize(jt or "")
    if not a or not j: return False
    sa = " ".join(a); sj = " ".join(j)
    if sa in sj or sj in sa: return True
    return SequenceMatcher(None, sa, sj).ratio() >= 0.85


def cache_path(issn, year):
    safe = re.sub(r"[^A-Za-z0-9-]", "_", issn)
    return CACHE / f"{safe}_{year}.json"


def load_year(issn, year):
    p = cache_path(issn, year)
    if not p.exists(): return None
    try: return json.loads(p.read_text()).get("items", [])
    except Exception: return None


def crossref_title_search(title, year=None):
    """Search Crossref globally by title text. Returns matching records
    with same loose_match against the query title."""
    qs = urllib.parse.urlencode({
        "query.bibliographic": title,
        "rows": 10,
        "select": "DOI,title,page,volume,issue,container-title,publisher,ISSN,issued",
        "mailto": "brewster@archive.org",
    })
    url = f"https://api.crossref.org/works?{qs}"
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=30) as fh:
            d = json.load(fh)
        items = d.get("message", {}).get("items", [])
    except Exception as e:
        return [], str(e)
    out = []
    for r in items:
        t = (r.get("title") or [""])[0]
        if loose_match(title, t):
            out.append(r)
    return out, None


def diagnose(anchor, anchor_issn, anchor_vol, anchor_iss, anchor_year):
    """Single-anchor diagnosis. Returns (category, evidence_dict)."""
    at = anchor.get("article_title") or ""
    au = anchor.get("article_author") or ""
    jt = anchor.get("journal_title") or ""

    if is_junk_anchor(at, jt):
        return "E_junk_anchor", {"reason": "article_title ≈ journal_title"}

    # Search Crossref globally for this title
    matches, err = crossref_title_search(at, year=anchor_year)
    if err:
        return "search_error", {"error": err}
    if not matches:
        return "A_real_gap", {"reason": "no record matches title text in Crossref"}

    # Find the most plausible match
    same_issn = [r for r in matches if anchor_issn in (r.get("ISSN") or [])]
    if not same_issn:
        # Different ISSN
        m = matches[0]
        return "C_wrong_issn", {
            "found_doi": m.get("DOI"),
            "found_issns": m.get("ISSN") or [],
            "found_publisher": m.get("publisher"),
            "found_title": (m.get("title") or [""])[0],
            "found_container": (m.get("container-title") or [""])[0],
        }

    # Same ISSN — check vol/issue
    same_vol_iss = [r for r in same_issn
                    if str(r.get("volume","")).strip() == anchor_vol
                    and str(r.get("issue","")).strip() == anchor_iss]
    if same_vol_iss:
        # Record exists at the same (issn, vol, iss). Why didn't audit hit it?
        m = same_vol_iss[0]
        return "D_matcher_miss", {
            "found_doi": m.get("DOI"),
            "found_title": (m.get("title") or [""])[0],
            "anchor_title": at,
        }
    # Same ISSN, different vol/iss
    m = same_issn[0]
    return "B_wrong_vol_iss", {
        "found_doi": m.get("DOI"),
        "found_title": (m.get("title") or [""])[0],
        "found_volume": m.get("volume"),
        "found_issue": m.get("issue"),
        "anchor_volume": anchor_vol,
        "anchor_issue": anchor_iss,
    }


def main():
    # 1. Pick clean journals
    clean_issns = {}
    for line in AUDIT.read_text().splitlines():
        d = json.loads(line)
        pct = d.get("overall_match_pct"); anch = d.get("anchors_seen", 0)
        if pct is not None and pct >= 95 and anch >= 30:
            clean_issns[d["issn"]] = (pct, anch)
    print(f"clean journals (≥95% match, ≥30 anchors): {len(clean_issns)}", file=sys.stderr)

    # 2. Walk corpus, find misses in clean journals
    misses = []
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
                if is_junk_anchor(t, jt): continue
                # check the cache for this issue
                year_recs = load_year(issn, yr)
                if year_recs is None: continue
                xref_issue = [r for r in year_recs
                              if str(r.get("volume","")).strip() == vol
                              and str(r.get("issue","")).strip() == iss]
                title_hit = any(fuzzy_match(t, (r.get("title") or [""])[0]) for r in xref_issue)
                if title_hit: continue
                # author also (if present): use surname intersection from the in-issue records
                if a.get("article_author"):
                    surnames_anchor = set()
                    s = a["article_author"]
                    for piece in re.split(r"\s*;\s*|\s+and\s+|\s*&\s*|\s*\u2219\s*", s):
                        if "," in piece:
                            sn = piece.split(",", 1)[0]
                            words = re.findall(r"[A-Z][a-zA-Z'\u00c0-\u017f-]+", sn)
                            if words: surnames_anchor.add(words[0].lower())
                        else:
                            words = re.findall(r"[A-Z][a-zA-Z'\u00c0-\u017f-]+", piece)
                            long_w = [w for w in words if len(w) >= 3]
                            chosen = long_w or words
                            if chosen: surnames_anchor.add(chosen[-1].lower())
                    author_hit = False
                    for r in xref_issue:
                        for ar in r.get("author") or []:
                            fam = (ar.get("family") or "").strip().lower()
                            if fam and fam in surnames_anchor:
                                author_hit = True; break
                        if author_hit: break
                    if author_hit: continue
                misses.append((item, issn, vol, iss, yr, a))
    print(f"clean-tier misses to investigate: {len(misses)}", file=sys.stderr)

    # 3. Diagnose each miss in parallel (Crossref title-search)
    out = open(OUT, "w")
    counts = Counter()
    examples = {}
    def task(args):
        item, issn, vol, iss, yr, a = args
        cat, ev = diagnose(a, issn, vol, iss, yr)
        time.sleep(0.05)
        return item, issn, vol, iss, yr, a, cat, ev

    with ThreadPoolExecutor(max_workers=4) as ex:
        for r in ex.map(task, misses):
            item, issn, vol, iss, yr, a, cat, ev = r
            counts[cat] += 1
            rec = {
                "category": cat, "item": item, "issn": issn,
                "vol": vol, "iss": iss, "year": yr,
                "anchor_title": a.get("article_title"),
                "anchor_author": a.get("article_author"),
                "anchor_pages": a.get("printed_pages"),
                "anchor_leaf_ranges": a.get("leaf_ranges"),
                "evidence": ev,
            }
            out.write(json.dumps(rec) + "\n"); out.flush()
            if cat not in examples and cat not in ("search_error",):
                examples[cat] = rec
    out.close()

    print()
    print(f"=== Diagnosis aggregate ({sum(counts.values())} misses) ===")
    for k, n in counts.most_common():
        print(f"  {k:>20}: {n:>5}  ({100*n/max(sum(counts.values()),1):.1f}%)")

    print(f"\n=== One example per category ===")
    for cat, rec in examples.items():
        print(f"\n  [{cat}]")
        print(f"    item:    {rec['item']}")
        print(f"    journal: ISSN {rec['issn']}, vol {rec['vol']} iss {rec['iss']} year {rec['year']}")
        print(f"    title:   {(rec['anchor_title'] or '')[:75]!r}")
        print(f"    author:  {(rec['anchor_author'] or '')[:55]!r}")
        print(f"    pages:   {rec.get('anchor_pages') or '?'}")
        ev = rec["evidence"]
        for k, v in ev.items():
            print(f"    {k}: {str(v)[:80]}")


if __name__ == "__main__":
    main()
