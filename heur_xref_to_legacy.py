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

# Section labels that frequently appear AS SUB-HEADINGS within a multi-page
# printed Table of Contents (the journal's ToC lists articles grouped by
# section). These shouldn't be treated as the terminator that ends the ToC
# range — the ToC continues past them.
_TOC_SUBHEADING_RE = re.compile(
    r"^(articles|book\s*reviews?|publication\s*decisions|dialogue|"
    r"related\s*articles|editorial|notes?|letters?|news|reviews?|"
    r"announcements?|in\s*this\s*issue|forthcoming(\s*issues)?|"
    r"recent\s*issues|features?|departments?|columns?)\s*$",
    re.IGNORECASE,
)


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

    # End-page: the next section_header after CONTENTS that is NOT a
    # ToC sub-section label (ARTICLES, Book Reviews, etc.) — those
    # appear as sub-headings within the ToC layout and shouldn't end
    # the ToC range. Also bounded by the first article's start.
    next_header_pn = None
    for pn, txt in headers:
        if pn <= contents_pn: continue
        if _TOC_SUBHEADING_RE.match(txt or ""): continue
        next_header_pn = pn; break
    end_pn = contents_pn
    if next_header_pn is not None:
        end_pn = max(contents_pn, next_header_pn - 1)
    if first_article_pi is not None:
        end_pn = min(end_pn, first_article_pi - 1)

    # Convert docling page_no → BR page-index
    try:
        from page_index import PageIndex
        pi = PageIndex.for_item(item, fetch=True)
        start_br = pi.scandata_to_br(contents_pn - 1)
        end_br = pi.scandata_to_br(end_pn - 1)
        if start_br is None: start_br = max(0, contents_pn - 1)
        if end_br is None: end_br = max(start_br, end_pn - 1)
    except Exception:
        start_br = max(0, contents_pn - 1)
        end_br = max(start_br, end_pn - 1)
    return {
        "type": "frontmatter",
        "title": "Table of Contents",
        "authors": None,
        "page_index_ranges": [[f"n{start_br}", f"n{end_br}"]],
        "printed_pages": None,
        "ext_ids": {},
        "confidence": 0.8,
        "evidence": ["docling_section_header"],
        "level": 1,
    }


def _docling_printed_to_pi(item: str) -> dict:
    """Walk docling page_header blocks looking for pure printed page
    numbers (e.g. "86", "87"). Returns {printed_page_str → page_index}.

    Each printed-page string is mapped to the BR page-index of the
    first docling page whose page_header starts with that number. For
    items with restart-pagination, this gives a per-section
    printed→page-index map that pn.json couldn't produce.
    """
    p = DOCLING_CACHE / item / f"{item}_docling.json.gz"
    if not p.exists(): return {}
    try:
        with gzip.open(p, "rt", encoding="utf-8") as fh:
            doc = json.load(fh)
    except Exception:
        return {}
    try:
        from page_index import PageIndex
        pi = PageIndex.for_item(item, fetch=True)
    except Exception:
        pi = None
    out = {}
    for t in doc.get("texts") or []:
        if t.get("label") != "page_header": continue
        pr = t.get("prov") or []
        if not pr: continue
        pn = pr[0].get("page_no")
        if pn is None: continue
        txt = (t.get("text") or "").strip()
        m = re.match(r"^(\d{1,4})\b", txt)
        if not m: continue
        printed = m.group(1)
        # Convert docling page_no (1-indexed) → BR page-index
        br_pi = pi.scandata_to_br(pn - 1) if pi else None
        if br_pi is None:
            br_pi = max(0, pn - 1)  # fallback for missing PageIndex
        out.setdefault(printed, br_pi)
    return out


def _find_next_section_pi_after(item: str, after_pi: int,
                                  ignore_titles: set | None = None) -> int | None:
    """Scan docling for the first section_header / title block on a page
    strictly after `after_pi`. Returns its page_no (BR page-index for
    items where docling page_no aligns) or None if no docling cache or
    no header found. Used to bound the last article's end span when
    Crossref deposited only the start page."""
    p = DOCLING_CACHE / item / f"{item}_docling.json.gz"
    if not p.exists(): return None
    try:
        with gzip.open(p, "rt", encoding="utf-8") as fh:
            doc = json.load(fh)
    except Exception:
        return None
    titles_to_ignore = {(t or "").strip().lower() for t in (ignore_titles or set())}
    best = None
    for t in doc.get("texts") or []:
        if t.get("label") not in ("title", "section_header"):
            continue
        pr = t.get("prov") or []
        if not pr: continue
        pn = pr[0].get("page_no")
        if pn is None or pn <= after_pi: continue
        txt = (t.get("text") or "").strip().lower()
        # Skip a section_header that is just a repeat of the article's
        # own title (running header on subsequent pages).
        if txt and txt in titles_to_ignore:
            continue
        if best is None or pn < best:
            best = pn
    return best


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
            "_collapsed_span": (el == sl),  # internal flag for post-pass
        })

    # Span inference: many Crossref records deposit only the start page
    # (e.g. AMR records `page: "777"` for an article that runs pp.777-782).
    # heuristic_toc_crossref leaves end==start in that case. Walk the
    # sorted entries: when an entry's span collapsed, infer end from the
    # next entry's start. Conservative — leaves 1 leaf of slack for an
    # inter-article divider page if there's room.
    #
    # Compute page_index_count up front so we have a fallback for the
    # last collapsed entry (otherwise last article would be 1-page).
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

    def _start_pi(le):
        return int(le["page_index_ranges"][0][0].lstrip("n"))
    def _end_pi(le):
        return int(le["page_index_ranges"][0][1].lstrip("n"))

    # Cache docling printed→page-index map once (used for repeated-title
    # section-internal placement and for fall-through cases).
    docling_pp_map = _docling_printed_to_pi(d.get("item") or "")

    # Build the inverse map (page-index → printed page string) so we
    # can update an entry's printed_pages metadata when we infer its
    # page-index end via the next entry. Prefer pn.json (most complete);
    # fall back to inverting docling_pp_map.
    pi_to_printed = {}
    item = d.get("item")
    if item:
        try:
            pn_path = SEGART / "tmp" / "items" / item / f"{item}_page_numbers.json"
            if pn_path.exists():
                pn_data = json.loads(pn_path.read_text())
                from page_index import PageIndex
                pi_obj = PageIndex.for_item(item, fetch=True)
                pi_to_printed = pi_obj.br_to_printed(pn_data)
        except Exception:
            pi_to_printed = {}
    # Augment with docling-derived map (covers items where pn.json failed)
    for printed, pn_pi in (docling_pp_map or {}).items():
        pi_to_printed.setdefault(pn_pi, printed)

    def _set_printed_end(le, new_el_pi):
        """Update an entry's printed_pages end to match the inferred
        page-index end. No-op if we can't determine the printed value."""
        pp = le.get("printed_pages")
        if not pp or not pp[0]: return
        printed = pi_to_printed.get(new_el_pi)
        if not printed: return
        # Only widen, don't narrow: only update when the current end
        # is the same as the start (collapsed) and the new printed is
        # numerically/lexically >= current start.
        try:
            cur_start = str(pp[0][0])
            if printed and printed != pp[0][1]:
                le["printed_pages"] = [[cur_start, printed]]
        except Exception:
            pass

    def _printed_start(le):
        """First printed page from legacy entry's printed_pages, as str."""
        pp = le.get("printed_pages")
        if not pp or not pp[0]: return None
        return str(pp[0][0]) if pp[0][0] is not None else None

    # Pass A: repeated-title relocation. When multiple entries share the
    # same start_pi AND the same title (typical for sections like
    # "Letters to the editor" or "ANS Open Forum" that Crossref deposits
    # as separate DOIs each with distinct printed page ranges), use
    # docling page_headers to relocate each entry to its actual page.
    if docling_pp_map:
        starts_to_idxs = {}
        for k, le in enumerate(legacy_entries):
            starts_to_idxs.setdefault(_start_pi(le), []).append(k)
        for sl, ks in starts_to_idxs.items():
            if len(ks) < 2: continue
            titles = {(legacy_entries[k].get("title") or "").strip().lower()
                       for k in ks}
            if len(titles) != 1: continue  # different titles → not the repeated case
            for k in ks:
                le = legacy_entries[k]
                pstart = _printed_start(le)
                if not pstart or pstart not in docling_pp_map: continue
                new_sl = docling_pp_map[pstart]
                if new_sl == sl: continue
                # Span length comes from Crossref printed range.
                old_end = _end_pi(le); span = max(0, old_end - sl)
                new_el = new_sl + span
                le["page_index_ranges"] = [[f"n{new_sl}", f"n{new_el}"]]
                le["evidence"] = (le.get("evidence") or []) + [
                    "start_from_docling_page_header"
                ]
                le["confidence"] = 0.6

    # Re-sort after Pass A — relocations may have changed order.
    sorted_idxs = sorted(range(len(legacy_entries)),
                          key=lambda j: _start_pi(legacy_entries[j]))
    inferred_count = 0

    for pos, j in enumerate(sorted_idxs):
        le = legacy_entries[j]
        if not le.get("_collapsed_span"): continue
        sl = _start_pi(le)
        # Co-located entries: when multiple collapsed entries share the
        # same start_pi, they're typically (a) short announcements on
        # the same printed page (Call for Papers + Seminars +
        # Fellowship), or (b) a repeated-title section like "ANS Open
        # Forum" where Crossref deposited multiple DOIs that share the
        # section title but have distinct sequential page ranges. For
        # case (b), docling page_headers carry the printed page number
        # per leaf; use that to relocate each entry.
        siblings = [j2 for j2 in sorted_idxs
                     if j2 != j and _start_pi(legacy_entries[j2]) == sl]
        if siblings:
            same_title_siblings = [
                j2 for j2 in siblings
                if (legacy_entries[j2].get("title") or "").strip().lower()
                    == (le.get("title") or "").strip().lower()
            ]
            relocated = False
            if same_title_siblings and docling_pp_map:
                # Repeated-title section: look up this entry's Crossref
                # printed start in the docling page_header map.
                pstart = _printed_start(le)
                if pstart and pstart in docling_pp_map:
                    new_sl = docling_pp_map[pstart]
                    if new_sl != sl:
                        le["page_index_ranges"] = [[f"n{new_sl}",
                                                     f"n{new_sl}"]]
                        le["evidence"] = (le.get("evidence") or []) + [
                            "start_from_docling_page_header"
                        ]
                        le["confidence"] = 0.6
                        relocated = True
                        inferred_count += 1
            if not relocated:
                le["needs_qa"] = True
                le["confidence"] = 0.4
                le["evidence"] = (le.get("evidence") or []) + [
                    "span_co_located_with_siblings"
                ]
                inferred_count += 1
            continue
        # Find the next entry (in document order) whose start is strictly
        # greater than this one's start.
        next_start = None
        for j2 in sorted_idxs[pos + 1:]:
            s2 = _start_pi(legacy_entries[j2])
            if s2 > sl:
                next_start = s2; break
        if next_start is None:
            # Last collapsed entry — extend to end of visible pages.
            # Over-claims into trailing backmatter; flag needs_qa.
            if page_index_count is not None and page_index_count - 1 > sl:
                new_el = page_index_count - 1
                le["page_index_ranges"] = [[f"n{sl}", f"n{new_el}"]]
                le["evidence"] = (le.get("evidence") or []) + ["span_extended_to_end"]
                le["confidence"] = 0.4
                le["needs_qa"] = True
                _set_printed_end(le, new_el)
                inferred_count += 1
            continue
        # End = next_start - 1, with 1 leaf of slack if the gap is >= 2
        # (covers chapter-divider blanks).
        gap = next_start - sl
        if gap <= 1: continue
        new_el = next_start - (2 if gap >= 3 else 1)
        new_el = max(sl, new_el)
        le["page_index_ranges"] = [[f"n{sl}", f"n{new_el}"]]
        le["evidence"] = (le.get("evidence") or []) + ["span_inferred_from_next_entry"]
        le["confidence"] = 0.5
        _set_printed_end(le, new_el)
        inferred_count += 1
    # Strip internal flag.
    for le in legacy_entries:
        le.pop("_collapsed_span", None)

    # page_index_count was already computed above for span inference.

    out_path = Path(args.out) if args.out else (
        SEGART / "tmp" / "tocs_compare" /
        f"{d['item']}_toc_heurxref.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Surface QA-relevant counts at the issue level so consumers (and a
    # downstream "items needing review" report) can find issues whose
    # spans were inferred rather than deposited.
    article_count = sum(1 for le in legacy_entries
                        if le.get("type") == "article")
    qa_summary = {
        "entries_with_inferred_spans": inferred_count,
        "entries_needing_qa": [le["id"] for le in legacy_entries
                                if le.get("needs_qa")],
    }
    out_path.write_text(json.dumps({
        "schema_version": 2,
        "item": d["item"],
        "issn": d.get("issn"),
        "volume": d.get("volume"),
        "issue": d.get("issue"),
        "year": d.get("year"),
        "page_index_count": page_index_count,
        "software_versions": software_versions(),
        "qa": qa_summary,
        "entries": legacy_entries,
    }, indent=2))
    print(f"wrote {out_path}: {len(legacy_entries)} entries")


if __name__ == "__main__":
    main()
