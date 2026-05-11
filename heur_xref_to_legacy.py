#!/usr/bin/env python3
"""Translate `heuristic_toc_crossref.py` output (start_page_index/
end_page_index ints in BookReader's 0-indexed page-index coordinate) into the
legacy schema-v2 format (`page_index_ranges: [["nN","nM"]]`) so it can be
evaluated by `compare_toc_techniques.py` / `score_toc.py`.

Reads either v2 fields (`start_page_index`) or v1 (`start_leaf`); always
writes the v2 legacy schema with `page_index_ranges`.

Mirrors `llm_toc_to_legacy.py` (the analogous adapter for the LLM TOC).
"""
import argparse
import gzip
import json
import re
import sys
import time
from pathlib import Path

SEGART = Path("/Users/brewster/tmp/segart")
sys.path.insert(0, str(SEGART))
sys.path.insert(0, str(SEGART / "tools"))
from segart_version import software_versions  # noqa: E402

DOCLING_CACHE = SEGART / "tmp" / "items"

_TOC_HEADING_RE = re.compile(r"^(table of contents|contents)\s*$", re.IGNORECASE)


def _detect_toc_entry_from_docling(item: str, first_article_pi: int | None) -> dict | None:
    """For the clean-tier QA workflow: pull a single 'Table of Contents'
    frontmatter entry from docling, when available.

    Returns a legacy-schema entry dict or None. The page_index_range
    spans from the 'Contents' heading until just before the next
    section_header (the terminator — typically 'Related Articles' or
    the first article). Matches heurxref's convention of using
    docling page_no directly as page_index.
    """
    p = DOCLING_CACHE / item / f"{item}_docling.json.gz"
    if not p.exists():
        return None
    try:
        with gzip.open(p, "rt", encoding="utf-8") as fh:
            doc = json.load(fh)
    except Exception:
        return None
    headers = []
    for t in doc.get("texts") or []:
        if t.get("label") not in ("title", "section_header"):
            continue
        pr = t.get("prov") or []
        if not pr: continue
        pn = pr[0].get("page_no")
        if pn is None: continue
        txt = (t.get("text") or "").strip()
        headers.append((pn, txt))
    headers.sort()

    contents_pn = None
    for pn, txt in headers:
        if _TOC_HEADING_RE.match(txt):
            # Skip backmatter 'Contents' that might appear at the end
            # (cumulative indexes can use the word). Restrict to the
            # front matter — page_no must come before the first article.
            if first_article_pi is not None and pn >= first_article_pi:
                continue
            contents_pn = pn
            break
    if contents_pn is None:
        return None

    # End-page: leaf just before the next section_header after contents,
    # or just before the first article (whichever comes first).
    next_header_pn = None
    for pn, txt in headers:
        if pn <= contents_pn: continue
        next_header_pn = pn; break
    end_pn = contents_pn
    if next_header_pn is not None:
        end_pn = max(contents_pn, next_header_pn - 1)
    if first_article_pi is not None:
        end_pn = min(end_pn, first_article_pi - 1)

    return {
        "type": "frontmatter",
        "title": "Table of Contents",
        "authors": None,
        "page_index_ranges": [[f"n{contents_pn}", f"n{end_pn}"]],
        "printed_pages": None,
        "ext_ids": {},
        "confidence": 0.8,
        "evidence": ["docling_section_header"],
        "level": 1,
    }


_FRONTMATTER_PATTERNS = re.compile(
    r"^(preface|foreword|introduction|editorial board|contents"
    r"|table of contents|editor'?s note|from the editor|editorial"
    r"|publication information|masthead|in this issue)\b",
    re.IGNORECASE,
)
_BACKMATTER_PATTERNS = re.compile(
    r"^(index|subject index|author index|bibliography"
    r"|references|errata|colophon|advertisements?|back matter)\b",
    re.IGNORECASE,
)


def _classify_entry_type(title, default="article"):
    """Re-type entries whose titles match standard frontmatter / backmatter
    labels. Crossref types these as `journal-article` (Elsevier and others
    DOI even the preface/index pages), but for a TOC consumer it's useful
    to distinguish so a librarian / BookReader can deprioritize."""
    t = (title or "").strip()
    if _FRONTMATTER_PATTERNS.match(t): return "frontmatter"
    if _BACKMATTER_PATTERNS.match(t):  return "backmatter"
    return default


def _split_printed_range(s):
    """Convert a Crossref `page` string ('263-279', '263', 'S1-S4') to
    the v2 schema's [[start, end]] string-pair form. Returns None if
    nothing useful can be extracted."""
    if not s: return None
    s = str(s).strip()
    if not s: return None
    # Single page or hyphen-delimited range
    m = re.match(r"^([A-Za-z]?\d+[A-Za-z]?)\s*[-\u2013]\s*([A-Za-z]?\d+[A-Za-z]?)$", s)
    if m:
        return [[m.group(1), m.group(2)]]
    # Single page
    m = re.match(r"^([A-Za-z]?\d+[A-Za-z]?)$", s)
    if m:
        return [[m.group(1), m.group(1)]]
    # Fallback: leave as a single-pair best-guess
    parts = re.split(r"\s*[-\u2013]\s*", s, maxsplit=1)
    if len(parts) == 2:
        return [[parts[0].strip(), parts[1].strip()]]
    return [[s, s]]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input", help="path to *_toc_heur_xref.json")
    ap.add_argument("--out", help="legacy-schema output path")
    args = ap.parse_args()

    src = Path(args.input)
    d = json.loads(src.read_text())

    legacy_entries = []
    # Pre-pass: try to add a docling-detected 'Table of Contents'
    # frontmatter entry. Only fires for the clean-tier QA workflow when
    # docling is cached. Single, controlled extension of docling's role
    # (locator → also-emits-toc-frontmatter); doesn't propose articles.
    raw_entries = d.get("entries") or []
    # Use the first non-frontmatter entry as the page-index ceiling
    # for ToC detection. Otherwise an "Introduction" article (which we
    # reclassify as frontmatter) would shadow a real ToC heading that
    # appears later in the front matter.
    first_article_pi = None
    for e in raw_entries:
        pi_v = e.get("start_page_index", e.get("start_leaf"))
        if pi_v is None: continue
        klass = _classify_entry_type(e.get("title"),
                                     default=e.get("type") or "article")
        if klass == "article":
            first_article_pi = int(pi_v); break
    toc_entry = _detect_toc_entry_from_docling(d.get("item") or "",
                                                first_article_pi)
    if toc_entry:
        toc_entry["id"] = "e0_toc"
        legacy_entries.append(toc_entry)

    for i, e in enumerate(raw_entries):
        # Read either v2 (`start_page_index`) or v1 (`start_leaf`).
        s_pi = e.get("start_page_index", e.get("start_leaf"))
        e_pi = e.get("end_page_index", e.get("end_leaf"))
        if s_pi is None: continue
        # heuristic_toc_crossref emits 0-indexed BookReader page-index
        # integers (via page_index.printed_to_br), which directly become
        # the legacy `nN` string. The LLM path uses docling page_no and
        # needs -1.
        sl = max(0, int(s_pi))
        el = max(sl, int(e_pi)) if e_pi is not None else sl
        legacy_entries.append({
            "id": f"e{i+1}",
            "type": _classify_entry_type(e.get("title"),
                                         default=e.get("type") or "article"),
            "title": e.get("title") or "",
            "authors": e.get("authors") or None,
            "page_index_ranges": [[f"n{sl}", f"n{el}"]],
            "printed_pages": _split_printed_range(e.get("crossref_page")),
            "ext_ids": {"doi": e["doi"]} if e.get("doi") else {},
            "confidence": 0.7,
            "evidence": [e["_method"]] if e.get("_method") else [],
            "level": 1,
        })

    # Compute page_index_count from the item's scandata via page_index module.
    page_index_count = None
    item = d.get("item")
    if item:
        try:
            from page_index import PageIndex
            pi = PageIndex.for_item(item, fetch=True)
            page_index_count = pi.visible_count
        except Exception as e:
            print(f"  WARN: could not derive page_index_count: {e}",
                  file=sys.stderr)

    out_path = Path(args.out) if args.out else (
        SEGART / "tmp" / "tocs_compare" /
        f"{d['item']}_toc_heurxref.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({
        "schema_version": 2,
        "item": d["item"],
        "issn": d.get("issn"),
        "volume": d.get("volume"),
        "issue": d.get("issue"),
        "year": d.get("year"),
        "page_index_count": page_index_count,
        "software_versions": software_versions(),
        "entries": legacy_entries,
    }, indent=2))
    print(f"wrote {out_path}: {len(legacy_entries)} entries")


if __name__ == "__main__":
    main()
