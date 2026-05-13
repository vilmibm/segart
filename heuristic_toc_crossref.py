#!/usr/bin/env python3
"""Heuristic TOC: Crossref article list + page_numbers.json → TOC with leaves.

For each article Crossref lists for the issue, translate its printed
page range into leaf coordinates via the issue's `_page_numbers.json`.
No LLM, no layout parsing.

This complements `llm_toc_extract.py` so we can give a librarian three
TOCs to compare (Opus, Sonnet, heuristic) against the actual issue's
printed table of contents.

Usage:
  ./heuristic_toc_crossref.py <item> [--out path.json] [--refetch]
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

SEGART = Path("/Users/brewster/tmp/segart")
ITEMS = SEGART / "tmp" / "items"
CACHE_DIR = SEGART / "tmp" / "crossref_cache"
HEADERS = {"User-Agent": "segart-heuristic-toc/0.1 (mailto:brewster@archive.org)"}


def load_page_numbers(item):
    """Return {printed_page_str → BookReader nN integer} or None.

    page_numbers.json is keyed by scandata leafNum (the Scribe-image
    counter, including hidden leaves); we use scandata.xml to translate
    that to BookReader's visible-only nN. See page_index.py.
    """
    p = ITEMS / item / f"{item}_page_numbers.json"
    if not p.exists(): return None
    from page_index import PageIndex
    pn_data = json.loads(p.read_text())
    try:
        pi = PageIndex.for_item(item, fetch=True)
    except Exception:
        return None
    return pi.printed_to_br(pn_data)


def load_repaired_page_numbers(item):
    """Use docling running headers to derive printed-page → BookReader nN
    as a fallback. Returns {} if no anchors survive consistency filter.

    `build_repaired_map` already emits 0-indexed BookReader-aligned
    integers (docling page_no - 1, where docling sees only visible
    pages). No further shift needed — both this map and
    `load_page_numbers` use the same BookReader nN coordinate space.
    """
    from repair_page_numbers import (
        load_docling, extract_anchors, consistency_filter, build_repaired_map,
    )
    doc = load_docling(item)
    if not doc: return {}
    anchors = consistency_filter(extract_anchors(doc))
    if not anchors: return {}
    n_leaves = len(doc.get("pages") or {})
    page_to_leaf, _, _ = build_repaired_map(anchors, n_leaves)
    return dict(page_to_leaf)


_ROMAN_VALUES = {'i': 1, 'v': 5, 'x': 10, 'l': 50, 'c': 100, 'd': 500, 'm': 1000}


def _roman_to_int(s):
    """Convert a Roman numeral string to int, or None if not valid Roman.
    Used so we can compute page-range lengths for printed-page strings
    like 'xi-xii' (Preface front matter)."""
    s = (s or "").strip().lower()
    if not s or not all(c in _ROMAN_VALUES for c in s):
        return None
    total, prev = 0, 0
    for c in reversed(s):
        v = _ROMAN_VALUES[c]
        if v < prev: total -= v
        else:        total += v
        prev = v
    return total if total > 0 else None


def _page_to_int(s):
    """Parse a printed-page string to an integer, accepting Arabic or
    Roman numerals. Returns None if neither pattern fits."""
    if not s: return None
    s = str(s).strip()
    if s.isdigit(): return int(s)
    return _roman_to_int(s)


def _page_range_length(sp, ep):
    """Number of pages in a printed-page range like ('263','279') or
    ('xi','xii'). Returns None if the range can't be parsed."""
    si = _page_to_int(sp); ei = _page_to_int(ep)
    if si is None or ei is None: return None
    n = ei - si + 1
    return n if n > 0 else None


def _label_parts(s):
    """Split a vol/iss label that may carry combined-issue notation
    ("21-22", "3/4", "21,22") into its component parts plus the literal
    full string. Used so a Crossref record with volume "21" matches our
    query of vol "21-22"."""
    s = str(s or "").strip()
    if not s: return {""}
    parts = {s}
    for sep in ("-", "/", ","):
        for p in s.split(sep):
            p = p.strip()
            if p: parts.add(p)
    return parts


def _label_matches(crossref_label, query_label):
    """True iff Crossref's vol/iss label matches our query, allowing for
    combined-issue notation on either side (e.g. query=21-22 matches
    crossref=21 or crossref=22; query=21 matches crossref=21-22)."""
    c = str(crossref_label or "").strip()
    q = str(query_label or "").strip()
    if c == q: return True
    if not c or not q: return False
    c_parts = _label_parts(c)
    q_parts = _label_parts(q)
    return bool(c_parts & q_parts)


def fetch_crossref_full(issn, year, vol, iss):
    """Fetch with full fields (DOI, title, author, page, volume, issue),
    return a list of normalized article dicts.

    Volume/issue match is tolerant of combined-issue labels: a query for
    issue "21-22" matches Crossref records with issue "21", "22", or
    "21-22". This recovers articles for combined-issue IA items where the
    publisher labels each half issue separately in Crossref. Safe to use
    only when the issue's pn.json shows continuous (not restart)
    pagination — see tools/pn_health.py; the production driver should
    check that before invoking this.
    """
    try: y = int(str(year)[:4])
    except: return None
    url = (
        f"https://api.crossref.org/journals/{issn}/works"
        f"?rows=200&filter=type:journal-article,from-pub-date:{y}-01,until-pub-date:{y}-12"
        f"&select=DOI,title,page,volume,issue,author"
    )
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as fh:
        data = json.load(fh)
    out = []
    for r in data.get("message", {}).get("items", []):
        v = str(r.get("volume", "")).strip()
        i = str(r.get("issue", "")).strip()
        if not _label_matches(v, vol) or not _label_matches(i, iss): continue
        ttl = r.get("title")
        if isinstance(ttl, list): ttl = ttl[0] if ttl else ""
        authors = []
        for a in r.get("author") or []:
            given = (a.get("given") or "").strip()
            family = (a.get("family") or "").strip()
            name = " ".join(p for p in (given, family) if p)
            if name: authors.append({"name": name})
        out.append({
            "doi": r.get("DOI"),
            "title": ttl,
            "authors": authors,
            "page": (r.get("page") or "").strip() or None,
        })
    return out


def derive_metadata(item):
    """Return (issn, year, volume, issue) for an IA periodical item.

    IA metadata is the gold standard: it's the cataloger's record and
    matches what's on the physical issue. ILL data is patron-typed and
    has typos / wrong years / format quirks ("May/Jun" instead of "3").
    So we always prefer IA's values. ILL is only used when IA is
    missing a field, and then as a per-field fill-in — never to
    override a present IA value.

    An empty `issue` is valid for annual-volume journals (Biological
    Conservation, Annual Reviews) — Crossref also deposits these with
    empty issue, so the match path tolerates it.
    """
    import csv, glob
    from parse_cover_text import parse as parse_cover
    ISSN_RE = re.compile(r"^\d{4}-?\d{3}[\dXx]$")

    # IA metadata first.
    ia_issn = ia_vol = ia_iss = ia_yr = ""
    try:
        url = f"https://archive.org/metadata/{item}/metadata"
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=15) as fh:
            md = json.load(fh).get("result") or {}
        ia_issn = (md.get("issn") or "").strip()
        if isinstance(ia_issn, list): ia_issn = ia_issn[0] if ia_issn else ""
        ia_vol = (md.get("volume") or "").strip()
        ia_iss = (md.get("issue") or "").strip()
        date = (md.get("date") or md.get("year") or "").strip()
        # IA date can be "YYYY", "YYYY-MM", "YYYY-MM-DD", or free-form
        # ("May/June 1991"); pull the four-digit year out of the string.
        m_yr = re.search(r"\b(19|20)\d{2}\b", date)
        ia_yr = m_yr.group(0) if m_yr else (date[:4] if date[:4].isdigit() else "")
    except Exception:
        pass

    # If IA gave us everything except issue and ILL has an issue, that
    # may help — but only when ILL's issue looks like a number/letter
    # (skip "May/Jun"-style ILL strings that won't match Crossref).
    need_iss_from_ill = bool(ia_issn and ia_vol and ia_yr) and not ia_iss

    # Walk ILL only if IA didn't provide a complete (issn, vol, yr).
    have_complete_ia = bool(ia_issn and ISSN_RE.match(ia_issn) and ia_vol and ia_yr)
    if have_complete_ia and not need_iss_from_ill:
        return ia_issn, ia_yr, ia_vol, ia_iss

    for path in sorted(glob.glob(str(SEGART / "tmp/ill_logs/*.csv"))):
        with open(path, newline='') as fh:
            for row in csv.DictReader(fh):
                if row.get("source_identifier") != item: continue
                try: ff = json.loads(row.get("full_form") or "{}")
                except: continue
                if not (ff.get("start") and ff.get("stop")): continue
                p = ff.get("original_request_params") or {}
                issn = (p.get("standard_number") or "").strip().split(";")[0].strip()
                vol = (p.get("journal_volume") or "").strip()
                iss = (p.get("journal_issue") or "").strip()
                yr = (p.get("journal_year") or "").strip()
                if not (issn and vol and iss and yr):
                    cv = parse_cover(ff.get("cover_text") or "") or {}
                    issn = issn or (cv.get("issn") or "").strip()
                    vol = vol or (cv.get("volume") or "").strip()
                    iss = iss or (cv.get("issue") or "").strip()
                    yr = yr or (cv.get("year") or "").strip()
                if need_iss_from_ill:
                    if iss and re.match(r"^[A-Za-z]?\d+(-\d+)?$", iss):
                        return ia_issn, ia_yr, ia_vol, iss
                    continue
                if issn and ISSN_RE.match(issn) and vol and yr:
                    # IA missing — use ILL fields, preferring IA for any
                    # field IA did supply.
                    return (ia_issn or issn,
                            ia_yr or yr[:4],
                            ia_vol or vol,
                            ia_iss or iss)

    if have_complete_ia:
        return ia_issn, ia_yr, ia_vol, ia_iss
    return None


def load_docling_blocks(item):
    """Load docling cache and return list of (page_no, label, text) for
    label in {section_header, title, paragraph_header} or 20+ char text."""
    import gzip
    p = ITEMS / item / f"{item}_docling.json.gz"
    if not p.exists(): return []
    with gzip.open(p, "rt") as fh:
        d = json.load(fh)
    out = []
    for t in d.get("texts") or []:
        label = t.get("label")
        text = (t.get("text") or "").strip()
        if label in ("section_header", "title", "paragraph_header") \
                or (label == "text" and len(text) >= 20):
            prov = (t.get("prov") or [{}])[0]
            page_no = prov.get("page_no")
            if page_no is not None:
                out.append((page_no, label, text))
    return out


STOPWORDS = {"the", "a", "an", "of", "and", "in", "on", "for", "to"}


def title_tokens(s):
    s = re.sub(r"[^a-z0-9]+", " ", (s or "").lower())
    return set(w for w in s.split() if w not in STOPWORDS and len(w) > 2)


_TOC_HEADING_TOKENS = {"contents", "table"}


def _toc_page_set(blocks):
    """Pages whose section_headers/titles indicate this IS the printed
    Table of Contents (matches "Contents" or "Table of Contents")."""
    toc_pages = set()
    for page_no, label, text in blocks:
        if label not in ("section_header", "title", "paragraph_header"):
            continue
        t = re.sub(r"[^a-z]+", " ", (text or "").lower()).strip()
        if t in ("contents", "table of contents"):
            toc_pages.add(page_no)
    return toc_pages


def find_title_in_docling(title, blocks, hint_leaf=None, toc_pages=None):
    """Search docling blocks for a leaf where the title text appears.
    Score by token overlap; prefer high-scoring AND label != 'text' AND
    earliest position. If hint_leaf is provided, prefer matches near it.

    Two false-match guards:
    - Skip pages flagged as the printed ToC (toc_pages) — those are
      listings of article titles, not the articles themselves.
    - For non-header (`text`) matches, require precision >= 0.5 too:
      the matched block must be similar in length to the title, not
      a long paragraph that happens to contain the title's words
      (e.g., "Editorial correspondence, letters to the editor and..."
      shouldn't match a "Letters to the editor" article title).

    Returns (leaf, score) or (None, 0)."""
    target = title_tokens(title)
    if not target: return None, 0
    toc_pages = toc_pages or set()
    is_header = lambda l: l in ("section_header", "title", "paragraph_header")
    best = (None, 0)
    for page_no, label, text in blocks:
        if page_no in toc_pages: continue
        toks = title_tokens(text)
        if not toks: continue
        overlap = len(target & toks) / max(1, len(target))   # recall
        precision = len(target & toks) / max(1, len(toks))   # precision
        if overlap < 0.5: continue
        # Body text needs both decent recall AND precision; a long
        # paragraph that just mentions the title's words isn't a match.
        if not is_header(label) and precision < 0.5:
            continue
        score = overlap * (1.5 if is_header(label) else 1.0) * (0.5 + 0.5 * precision)
        if hint_leaf is not None:
            score -= 0.001 * abs(page_no - hint_leaf)
        if score > best[1]:
            best = (page_no, score)
    return best


def parse_page_range(page_str):
    """'65-77' → ('65', '77'). '341' → ('341', '341'). '65-' → ('65', None).
    Also accepts Roman-numeral pages ('xi-xii' for front matter)."""
    if not page_str: return None, None
    s = page_str.strip()
    # Arabic-leaning pattern (also matches letter-prefixed like 'S1-S4')
    m = re.match(r"^([A-Za-z]?\d+)\s*[-–—]\s*([A-Za-z]?\d+)?\s*$", s)
    if m:
        return m.group(1), m.group(2) or m.group(1)
    m = re.match(r"^([A-Za-z]?\d+)\s*$", s)
    if m:
        return m.group(1), m.group(1)
    # Roman-numeral range/single (e.g., front matter: 'xi-xii', 'iii')
    m = re.match(r"^([ivxlcdmIVXLCDM]+)\s*[-–—]\s*([ivxlcdmIVXLCDM]+)?\s*$", s)
    if m:
        return m.group(1), m.group(2) or m.group(1)
    m = re.match(r"^([ivxlcdmIVXLCDM]+)\s*$", s)
    if m:
        return m.group(1), m.group(1)
    return None, None


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("item")
    ap.add_argument("--out")
    ap.add_argument("--refetch", action="store_true",
                    help="Refetch Crossref with full fields even if cached")
    args = ap.parse_args()

    md = derive_metadata(args.item)
    if not md:
        sys.exit(f"could not derive (issn, vol, iss, year) for {args.item}")
    issn, yr, vol, iss = md
    print(f"item: {args.item}", file=sys.stderr)
    print(f"  issn={issn} {vol}/{iss}/{yr}", file=sys.stderr)

    # PageIndex converts scandata leafNum (the raw Scribe image counter
    # in scandata.xml) to BookReader page-index nN (0-indexed,
    # visible-only). Docling renders the PDF page-by-page, and its
    # page_no is 1-indexed against the PDF (which is built from visible
    # leaves). So docling_page_no - 1 is the equivalent BR page-index
    # for items with no hidden leaves; for items with hidden leaves
    # inside the body, scandata_to_br accounts for the offset.
    from page_index import PageIndex as _PI
    page_idx = _PI.for_item(args.item, fetch=True)

    def docling_page_to_br(doc_page_no):
        """docling page_no (1-indexed) → BookReader page-index nN."""
        if doc_page_no is None: return None
        br = page_idx.scandata_to_br(doc_page_no - 1)
        return br if br is not None else max(0, doc_page_no - 1)

    # pn_health gate: when IA's pn.json is unreliable (restart pagination,
    # low confidence, sparse), trusting it produces wrong page-indices
    # silently. Bypass pn.json in those cases and rely on the docling
    # title-match fallback for each entry's location.
    sys.path.insert(0, str(SEGART / "tools"))
    from pn_health import assess_pn_health
    pn_raw = json.loads((ITEMS / args.item / f"{args.item}_page_numbers.json").read_text())
    pn_health = assess_pn_health(pn_raw, item=args.item)
    print(f"  pn_health: {pn_health['status']} "
          f"(cov={pn_health['coverage']} conf={pn_health['confidence_fraction']} "
          f"mono={pn_health['monotonicity']} restart={pn_health['restart_pagination']})",
          file=sys.stderr)

    if pn_health["status"] == "ok":
        pn_map = load_page_numbers(args.item)
        if pn_map is None:
            sys.exit(f"missing page_numbers.json for {args.item}")
        print(f"  page_numbers entries: {len(pn_map)}", file=sys.stderr)
        repair_map = load_repaired_page_numbers(args.item)
        n_added = sum(1 for p in repair_map if p not in pn_map)
        print(f"  repair fallback: {len(repair_map)} pages, {n_added} new",
              file=sys.stderr)
        pn_map_merged = {**repair_map, **pn_map}
    else:
        # pn.json untrustworthy → use docling-derived running-header map
        # if available, else empty. Article location then routes through
        # the docling title-match fallback per entry.
        pn_map = {}  # mark as empty so downstream code's `not in pn_map`
                     # checks fall through to repair_map / title-match
        repair_map = load_repaired_page_numbers(args.item)
        print(f"  bypassing pn.json (health={pn_health['status']}); "
              f"using repair_map only ({len(repair_map)} pages)",
              file=sys.stderr)
        pn_map_merged = dict(repair_map)

    # We want articles with `page` field — refetch unconditionally for now
    # (existing cache stored only doi+title).
    print(f"  fetching Crossref with full fields...", file=sys.stderr)
    articles = fetch_crossref_full(issn, yr, vol, iss)
    if articles is None:
        sys.exit("Crossref fetch failed")
    print(f"  Crossref returned {len(articles)} articles", file=sys.stderr)

    # Save full version under a separate suffix so we don't trample the
    # existing leaner cache.
    full_path = CACHE_DIR / f"{issn}_{yr}_{vol}_{iss}_full.json"
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_text(json.dumps(articles, indent=2))

    # Load docling blocks for the title-search fallback path.
    blocks = load_docling_blocks(args.item)
    toc_pages = _toc_page_set(blocks)
    print(f"  docling blocks (header+text≥20): {len(blocks)} "
          f"(ToC pages: {sorted(toc_pages)})", file=sys.stderr)

    # Translate to TOC. Strategy per article:
    #   1. Try page_numbers.json translation (deterministic, when it works).
    #   2. If that fails, find the title text in docling layout (token overlap
    #      ≥0.5, header labels weighted higher).
    entries = []
    method_counts = {"page_numbers": 0, "page_numbers+repair": 0,
                     "title_in_docling": 0, "page_numbers_partial": 0,
                     "failed": 0}
    for a in articles:
        sp, ep = parse_page_range(a.get("page"))
        sl_pn = pn_map_merged.get(sp) if sp else None
        el_pn = pn_map_merged.get(ep) if ep else None
        used_repair = bool(
            (sp and sp not in pn_map and sp in repair_map)
            or (ep and ep not in pn_map and ep in repair_map)
        )

        # Title search in docling. The result is a docling page_no
        # (1-indexed visible-leaf counter); convert to BR page-index.
        sl_title_doc, score = find_title_in_docling(
            a.get("title"), blocks, hint_leaf=sl_pn, toc_pages=toc_pages
        )
        sl_title = docling_page_to_br(sl_title_doc)

        # Pick the best leaf
        if sl_pn is not None and el_pn is not None:
            sl, el = sl_pn, el_pn
            method = "page_numbers+repair" if used_repair else "page_numbers"
        elif sl_title is not None:
            sl = sl_title
            # We can't infer end_leaf from title-search alone; use page_numbers'
            # end if available, else fall back to sl + a guess based on
            # crossref's page span.
            if el_pn is not None:
                el = el_pn
                method = "page_numbers_partial"
            else:
                # Estimate page count from crossref range, default to single leaf
                if sp and ep and sp.isdigit() and ep.isdigit():
                    page_span = max(0, int(ep) - int(sp))
                    el = sl + page_span
                else:
                    el = sl
                method = "title_in_docling"
        elif sl_pn is not None:
            sl = sl_pn
            el = el_pn if el_pn is not None else sl
            method = "page_numbers_partial"
        else:
            sl = el = None
            method = "failed"

        # Final-pass consistency: if we resolved a start page but the end
        # collapsed to a single leaf (couldn't translate the end of the
        # range via pn.json — e.g. roman-numeral front matter the IA OCR
        # didn't recognize), but Crossref tells us the article spans
        # multiple pages, infer end = start + (range length - 1). Safe on
        # continuous-pagination items; combined with pn_health restart
        # pagination skip upstream, no ambiguity.
        if sl is not None and el == sl:
            span = _page_range_length(sp, ep)
            if span is not None and span > 1:
                el = sl + (span - 1)
                if method == "title_in_docling":
                    method = "title_in_docling+xref_span"
                elif method == "page_numbers_partial":
                    method = "page_numbers_partial+xref_span"
                method_counts.setdefault(method, 0)

        method_counts[method] += 1

        entries.append({
            "title": a.get("title") or "",
            "authors": a.get("authors") or [],
            "type": "article",
            "start_page_index": sl,
            "end_page_index": el,
            "start_page_number": int(sp) if sp and sp.isdigit() else 0,
            "end_page_number": int(ep) if ep and ep.isdigit() else 0,
            "doi": a.get("doi"),
            "crossref_page": a.get("page"),
            "_method": method,
            "_title_match_score": round(score, 3),
        })
    entries.sort(key=lambda e: (e["start_page_index"] or 9999, e["start_page_number"] or 0))

    print(f"  method breakdown:", file=sys.stderr)
    for k, v in method_counts.items():
        print(f"    {k:<25} {v}", file=sys.stderr)

    out_path = Path(args.out) if args.out else (
        SEGART / "tmp" / "tocs" / f"{args.item}_toc_heur_xref.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({
        "schema_version": "heur_toc_xref_v2",
        "item": args.item,
        "model": "heuristic: crossref + page_numbers.json",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "issn": issn, "year": yr, "volume": vol, "issue": iss,
        "entries": entries,
    }, indent=2))
    print(f"\nwrote {out_path}: {len(entries)} entries", file=sys.stderr)


if __name__ == "__main__":
    main()
