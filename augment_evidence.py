#!/usr/bin/env python3
"""
Augment TOC entries with external-source evidence.

For each entry in <item>_toc.json, this adds the following evidence flags
when supported:

  - "in_issue_toc": entry's title fuzzy-matches a row of docling's
    document_index table for that item (i.e., the issue's own printed
    table-of-contents lists this article).
  - "crossref_match": entry's title fuzzy-matches a Crossref-indexed
    article for that item's journal+volume+issue.

Crossref data is cached under tmp/crossref_cache/<issn>_<yr>_<vol>_<iss>.json
(populated lazily on demand). Supplying --no-fetch will only consult the
cache and skip network calls — useful when iterating offline.

Usage:
  ./augment_evidence.py --toc-dir tmp/tocs --items-dir tmp/items
  ./augment_evidence.py --toc tmp/tocs/sim_xyz_toc.json --no-fetch
"""
import argparse
import gzip
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

CACHE_DIR_DEFAULT = str(Path.home() / "tmp" / "segart" / "tmp" / "crossref_cache")
HEADERS = {"User-Agent": "segart-augment/0.1 (mailto:brewster@archive.org)"}

STOP = set("a an the of for in on to and or but with by at from as is are was were "
           "be that this if".split())
WORD = re.compile(r"[a-z0-9]+")


def norm(s):
    return [w for w in WORD.findall((s or "").lower())
            if w not in STOP and len(w) > 2]


def title_match(a, b, threshold=0.5):
    wa, wb = set(norm(a)), set(norm(b))
    if not wa or not wb:
        return False
    return len(wa & wb) / max(1, min(len(wa), len(wb))) >= threshold


# ------------------------------ docling TOC ----------------------------------

PG_RE = re.compile(r"(?<!\d)(\d{1,4})(?!\d)")


def _y_top(t):
    bb = (t.get("prov") or [{}])[0].get("bbox") or {}
    return bb.get("t") or 0


def _in_bbox(t_bb, T_bb):
    if not t_bb or not T_bb:
        return False
    t_top = max(t_bb.get("t") or 0, t_bb.get("b") or 0)
    t_bot = min(t_bb.get("t") or 0, t_bb.get("b") or 0)
    T_top = max(T_bb.get("t") or 0, T_bb.get("b") or 0)
    T_bot = min(T_bb.get("t") or 0, T_bb.get("b") or 0)
    return t_bot >= T_bot - 5 and t_top <= T_top + 5


def _is_pn_token(s):
    if not s:
        return False
    m = PG_RE.fullmatch(s.strip())
    if not m:
        return False
    return 1 <= int(m.group(1)) <= 2000


def docling_toc_titles(item_docling_path):
    """Read the docling cache; return list of TOC-region entry strings.

    The cache is gzipped JSON. We extract texts inside any
    `document_index` table's bbox, treat page-number-only items as
    boundaries, and return everything between them as one entry. This is
    the same heuristic used by the comparison scripts in chat history."""
    if not os.path.exists(item_docling_path):
        return []
    opener = gzip.open if item_docling_path.endswith(".gz") else open
    with opener(item_docling_path, "rt", encoding="utf-8") as fh:
        d = json.load(fh)
    by_page = {}
    for t in d.get("texts", []):
        prov = (t.get("prov") or [{}])[0]
        pn = prov.get("page_no")
        if pn is None:
            continue
        by_page.setdefault(pn, []).append(t)
    out = []
    for tbl in d.get("tables", []):
        if tbl.get("label") != "document_index":
            continue
        prov = (tbl.get("prov") or [{}])[0]
        page = prov.get("page_no")
        T_bb = prov.get("bbox")
        if page is None or not T_bb:
            continue
        cands = [
            t for t in by_page.get(page, [])
            if _in_bbox((t.get("prov") or [{}])[0].get("bbox"), T_bb)
            and t.get("content_layer") != "furniture"
        ]
        cands.sort(key=lambda x: -_y_top(x))
        cur = []
        for t in cands:
            tx = (t.get("text") or "").strip()
            if not tx:
                continue
            non_pg = re.sub(r"\d+", "", tx).strip()
            if _is_pn_token(tx) or (len(non_pg) < 3 and PG_RE.search(tx)):
                if cur:
                    s = " ".join(cur).strip()
                    if len(s) > 5 and re.search(r"[A-Za-z]{3,}", s):
                        out.append(s)
                    cur = []
                continue
            cur.append(tx)
        if cur:
            s = " ".join(cur).strip()
            if len(s) > 5 and re.search(r"[A-Za-z]{3,}", s):
                out.append(s)
    return out


# ------------------------------ Crossref -------------------------------------

def _crossref_cache_path(cache_dir, issn, vol, iss, year):
    key = f"{issn}_{year}_{vol}_{iss}".replace("/", "_").replace(" ", "_")
    return os.path.join(cache_dir, f"{key}.json")


def crossref_titles(cache_dir, issn, vol, iss, year, *, fetch=True):
    """Return list of Crossref article titles for a (journal, vol, issue).

    Reads from on-disk cache first; on miss, optionally fetches from
    Crossref, filters to matching vol+iss, and writes the cache.
    Returns [] when any of issn/vol/iss/year are missing.
    """
    if not (issn and vol and iss and year):
        return []
    cache = _crossref_cache_path(cache_dir, issn, vol, iss, year)
    if os.path.exists(cache):
        return [c["title"] for c in json.load(open(cache))]
    if not fetch:
        return []
    try:
        y = int(str(year)[:4])
    except ValueError:
        return []
    url = (
        f"https://api.crossref.org/journals/{issn}/works"
        f"?rows=200&filter=type:journal-article,from-pub-date:{y}-01,until-pub-date:{y}-12"
        f"&select=DOI,title,page,volume,issue"
    )
    out = []
    cursor = "*"
    seen = 0
    while True:
        u = f"{url}&cursor={urllib.parse.quote(cursor)}"
        try:
            req = urllib.request.Request(u, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=20) as r:
                data = json.load(r)
        except Exception as e:
            print(f"  WARN: Crossref fetch failed for {issn} {vol}/{iss}: {e}",
                  file=sys.stderr)
            break
        msg = data.get("message", {})
        items = msg.get("items", [])
        if not items:
            break
        seen += len(items)
        for r in items:
            v, i = str(r.get("volume", "")), str(r.get("issue", ""))
            if v == str(vol) and i == str(iss):
                out.append({
                    "doi": r.get("DOI", ""),
                    "title": (r.get("title") or [""])[0],
                })
        nxt = msg.get("next-cursor")
        if not nxt or nxt == cursor:
            break
        cursor = nxt
        time.sleep(0.05)
        if seen > 4000:
            break
    os.makedirs(cache_dir, exist_ok=True)
    json.dump(out, open(cache, "w"))
    return [c["title"] for c in out]


# ------------------------------ qa_corpus md lookup --------------------------

def load_issue_metadata(corpus_path):
    """Map item_id → first anchor with full (issn, vol, issue, year)."""
    md = {}
    if not os.path.exists(corpus_path):
        return md
    with open(corpus_path) as f:
        for line in f:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            ident = row.get("identifier")
            if not ident or ident in md:
                continue
            for a in row.get("anchors") or []:
                if a.get("issn") and a.get("volume") and a.get("issue") and a.get("year"):
                    md[ident] = {
                        "issn": a["issn"], "volume": a["volume"],
                        "issue": a["issue"], "year": a["year"],
                    }
                    break
    return md


# ------------------------------ main -----------------------------------------

def augment_one(toc_path, items_dir, cache_dir, issue_md, fetch):
    toc = json.load(open(toc_path))
    item = toc.get("item")
    if not item:
        return None
    docling_path = os.path.join(items_dir, item, f"{item}_docling.json.gz")
    doc_titles = docling_toc_titles(docling_path)
    md = issue_md.get(item) or {}
    cr_titles = crossref_titles(
        cache_dir, md.get("issn"), md.get("volume"),
        md.get("issue"), md.get("year"),
        fetch=fetch,
    )
    n_added_doc = 0
    n_added_cr = 0
    for e in toc.get("entries", []):
        ttl = e.get("title") or ""
        ev = list(e.get("evidence") or [])
        if doc_titles and any(title_match(ttl, dt) for dt in doc_titles):
            if "in_issue_toc" not in ev:
                ev.append("in_issue_toc")
                n_added_doc += 1
        if cr_titles and any(title_match(ttl, ct) for ct in cr_titles):
            if "crossref_match" not in ev:
                ev.append("crossref_match")
                n_added_cr += 1
        e["evidence"] = ev
    return toc, n_added_doc, n_added_cr


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--toc-dir", help="Directory of <item>_toc.json files")
    p.add_argument("--toc", help="Single TOC file to augment")
    p.add_argument("--items-dir",
                   default=str(Path.home() / "tmp" / "segart" / "tmp" / "items"))
    p.add_argument("--corpus",
                   default=str(Path.home() / "tmp" / "segart" / "tmp" / "qa_corpus.jsonl"))
    p.add_argument("--cache-dir", default=CACHE_DIR_DEFAULT,
                   help="Crossref cache directory")
    p.add_argument("--no-fetch", action="store_true",
                   help="Don't hit Crossref; only use already-cached results")
    args = p.parse_args()

    if not args.toc and not args.toc_dir:
        p.error("must supply --toc or --toc-dir")
    issue_md = load_issue_metadata(args.corpus)
    paths = [args.toc] if args.toc else sorted(
        Path(args.toc_dir).glob("*_toc.json"))

    total_doc = 0
    total_cr = 0
    n = 0
    for path in paths:
        result = augment_one(str(path), args.items_dir, args.cache_dir,
                             issue_md, fetch=not args.no_fetch)
        if result is None:
            continue
        toc, n_doc, n_cr = result
        json.dump(toc, open(path, "w"), indent=2)
        total_doc += n_doc
        total_cr += n_cr
        n += 1

    print(f"augmented {n} TOC files; "
          f"+{total_doc} in_issue_toc, +{total_cr} crossref_match",
          file=sys.stderr)


if __name__ == "__main__":
    main()
