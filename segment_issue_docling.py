#!/usr/bin/env python3
"""segart segmenter v0.3 — Docling layout-aware article segmentation.

Uses IBM/Docling's layout model (with MPS acceleration on Apple Silicon)
to identify SECTION_HEADER blocks in an issue PDF, treats the upper-most
section_header on each page as a candidate article start, and pulls the
byline from the text item immediately below.

Why Docling over the v0.2 hOCR font heuristic: the layout model emits
typed blocks (section_header, list_item, page_footer, ...) so we get a
direct article-start signal instead of inferring it from font size.
First-run is slow because model weights download (~500 MB). Subsequent
runs are MPS-accelerated and take ~3 sec/page on M2.

Usage:
  ./segment_issue_docling.py sim_academic-medicine_1989-02_64_2
  ./segment_issue_docling.py <item> -o /tmp/foo.json
"""
import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

CACHE = Path(os.environ.get("SEGART_CACHE", "/tmp/segart_items"))
SCHEMA_VERSION = 1
SEGMENTER_VERSION = "0.5-docling"

# Academic / clinical credentials that often follow a byline. `Dr.` was
# previously here but matched any sentence starting "Dr. Smith said ..."
# in body text, so it caused frequent false-positive byline matches; we
# rely on the explicit name-shape patterns below to catch "Dr. <Name>".
CRED_RE = re.compile(
    r"\b(?:M\.?\s*D\.?|Ph\.?\s*D\.?|MA|MS|MPH|MSc|R\.?\s*N\.?|D\.?\s*O\.?|RD|"
    r"PhD|Sc\.?\s*D\.?|Jr\.?|Sr\.?|II|III|FRCP|FRCS|FACP|MHA|EdD|DSc)"
    r"\.?\b",
    re.IGNORECASE,
)


def fetch_pdf(item, cache_dir):
    item_dir = cache_dir / item
    item_dir.mkdir(parents=True, exist_ok=True)
    pdf = item_dir / f"{item}.pdf"
    if not pdf.exists():
        subprocess.run(
            ["ia", "download", item, "--glob", "*.pdf", "--destdir", str(cache_dir)],
            check=True, capture_output=True,
        )
    return pdf


def fetch_page_numbers(item, cache_dir):
    item_dir = cache_dir / item
    pn = item_dir / f"{item}_page_numbers.json"
    if not pn.exists():
        subprocess.run(
            ["ia", "download", item, "--glob", "*page_numbers.json", "--destdir", str(cache_dir)],
            check=True, capture_output=True,
        )
    return pn


def docling_convert(pdf_path):
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.datamodel.accelerator_options import AcceleratorOptions, AcceleratorDevice

    opts = PdfPipelineOptions()
    opts.do_ocr = False
    opts.do_table_structure = False
    opts.accelerator_options = AcceleratorOptions(
        device=AcceleratorDevice.MPS, num_threads=4
    )
    conv = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
    )
    return conv.convert(str(pdf_path)).document


def text_label(t):
    label = getattr(t, "label", None)
    if label is None:
        return ""
    s = str(label)
    if "." in s:
        s = s.split(".")[-1].rstrip("'>")
    return s.lower()


def page_no_of(t):
    if hasattr(t, "prov") and t.prov:
        p = t.prov[0]
        if hasattr(p, "page_no"):
            return p.page_no
        if isinstance(p, dict):
            return p.get("page_no")
    return None


def bbox_of(t):
    if hasattr(t, "prov") and t.prov:
        p = t.prov[0]
        if hasattr(p, "bbox"):
            b = p.bbox
            return (b.l, b.t, b.r, b.b) if hasattr(b, "l") else None
        if isinstance(p, dict):
            b = p.get("bbox") or {}
            return (b.get("l"), b.get("t"), b.get("r"), b.get("b"))
    return None


# Explicit name-shape patterns. A line is byline-shaped if it contains at
# least one of these:
#   - "First M. Surname" or "FIRST M. SURNAME"   ("Lois A. Pounds")
#   - "Surname, F."                              ("Pounds, L A")
#   - "First Surname"  (two adjacent capitalized name-shape words, both ≥3
#     letters and not common English nouns) — looser; usually paired with
#     credentials elsewhere on the line
NAME_SHAPE_RES = [
    re.compile(r"\b[A-Z][a-z]+\s+[A-Z]\.\s*[A-Z][a-z]+\b"),
    re.compile(r"\b[A-Z]{2,}\s+[A-Z]\.?\s*[A-Z]{2,}\b"),
    re.compile(r"\b[A-Z][a-z]+,\s+[A-Z](?:\s|\.|$)"),
    re.compile(r"\b[A-Z]{2,},\s+[A-Z](?:\s|\.|$)"),
    # Two adjacent capitalized words at the START of the byline string
    # ("Sue Buckley", "Anthony Joyce"). Anchored at ^ so coincidental
    # mentions of 'Duke University' or 'New York' inside body text
    # don't trigger.
    re.compile(r"^[A-Z][a-z]+\s+[A-Z][a-z]+\b"),
    # OCR-tolerant variant ("Anthony 5. Joyce" where '5' was 'S').
    re.compile(r"^[A-Z][a-z]+\s+\S{1,4}\s+[A-Z][a-z]+\b"),
]


def looks_like_byline(text):
    """Return True if `text` plausibly is an author byline.

    Tighter rule (v0.3.2): require an explicit credential
    (M.D./Ph.D./R.N./...) or a recognized name-shape pattern, or a `by `
    prefix. Subtitles and abstract intros that happen to be title-cased no
    longer pass — those don't have name shapes.
    """
    if not text or len(text) < 3 or len(text) > 300:
        return False
    body = re.sub(r"^by\s+", "", text, flags=re.IGNORECASE).strip()
    if body != text.strip():
        return True  # had explicit "by " prefix → trust it
    if CRED_RE.search(body):
        return True
    for r in NAME_SHAPE_RES:
        if r.search(body):
            return True
    return False


# Section-header texts that are NEVER article starts — they are subsection
# headers within an article, end-of-issue indices, or article apparatus.
SUBSECTION_DENYLIST = {
    re.sub(r"\s+", " ", s).lower()
    for s in [
        "methods", "method", "materials and methods",
        "results", "result", "findings", "results in children",
        "discussion", "conclusion", "conclusions",
        "introduction", "background", "summary",
        "abstract",
        "references", "bibliography",
        "acknowledgments", "acknowledgements", "acknowledgment", "acknowledgement",
        "appendix", "appendices",
        "tables", "figures",
        "subject index", "author index", "index of authors",
        "index of first authors or sources",
        "index of authors or sources-continued from page iv",
        "table of contents", "contents",
        # drug-ad inserts: titles printed in the leading slot of an ad
        "precautions", "adverse reactions", "dosage and administration",
        "contraindications", "indications", "warnings",
        "how supplied", "prescribing information",
        # generic journal section labels
        "communications", "letters to the editor", "letters", "correspondence",
        "editorial", "editorials", "errata", "erratum",
        "in this issue", "from the editor", "editor's note",
        "book reviews", "book review", "publications received",
        "address changes", "address correction", "subscriptions",
        "announcements", "notices", "calendar",
        "articles", "research articles", "original articles", "invited articles",
        "features", "departments", "news", "abstracts",
        "acknowledgment of reviewers", "past editors",
    ]
}
# Also reject pure "Table N", "Figure N" headers (digits or Roman numerals).
TABLE_FIGURE_RE = re.compile(
    r"^(table|figure|fig\.?)\s*(\d+|[ivxlc]+)\b",
    re.IGNORECASE,
)


def parse_authors(text):
    body = re.sub(r"^by\s+", "", text, flags=re.IGNORECASE).strip()
    parts = re.split(r"\s*,\s*|\s+and\s+|\s*&\s*|\s*;\s*", body)
    out = []
    for part in parts:
        name = CRED_RE.sub("", part).strip(" ,.;-")
        if name and len(name) > 1 and any(c.isalpha() for c in name):
            if name.isupper():
                name = name.title()
            out.append({"name": name, "affiliation": None})
    return out


# Labels docling assigns to the title-shaped blocks at article starts.
# `section_header` is the dominant signal; `title` shows up on cover-style
# pages. `paragraph_header` is excluded from STARTERS — it commonly tags
# within-article subsections (Methods, Results, History, Background ...)
# and was responsible for the v0.5-pre-fix "two articles per Pounds page"
# duplicate where docling labeled the in-article 'History of Nursing
# Education' as paragraph_header. We still allow paragraph_header to
# CONTINUE a stack (so multi-line subtitles still merge) but not to start.
ARTICLE_START_LABELS = ("section_header", "title")
HEADER_LABELS = ("section_header", "title", "paragraph_header")


def _y_top(t):
    """Top y of a text item (Docling y is bottom-up)."""
    bb = bbox_of(t) or (0, 0, 0, 0)
    return bb[1] or 0


def _merge_stacked_headers(ordered, header_idx, max_gap_y=80):
    """Walk down from `header_idx`, gathering subsequent header items that
    sit immediately below (small y-gap) — these are line continuations of
    a multi-line title. Returns the merged title text.

    Why: docling sometimes emits a single article title as 2-3 separate
    section_header items (one per visual line). Without merging we keep
    only the last line — e.g. "A Reformed Curriculum at the / University
    of Michigan: The / Michigan Program" becomes just "Michigan Program".
    """
    parts = [(ordered[header_idx].text or "").strip()]
    i = header_idx + 1
    last_y = _y_top(ordered[header_idx])
    while i < len(ordered):
        t = ordered[i]
        if text_label(t) not in HEADER_LABELS:
            break
        y = _y_top(t)
        # Docling y is bottom-up; the next visual line has a SMALLER y.
        gap = abs(last_y - y)
        if gap > max_gap_y:
            break
        parts.append((t.text or "").strip())
        last_y = y
        i += 1
    return " ".join(p for p in parts if p), i - 1  # last merged idx


def detect_articles(doc, raw=None):
    """Walk pages top-down and emit article starts.

    Article-start rule (v0.5):
      - A header item (section_header, title, paragraph_header) acts as
        a title candidate. Stacked consecutive headers with a small
        y-gap are merged into one multi-line title.
      - When a byline-shaped text item follows under the current header
        chunk, emit (chunk, byline) as an article start, then RESET the
        chunk so the next header on the same page can spawn another
        article. This handles multi-article pages (Letters, Brief
        Communications, Screening Guidelines, ...).
      - Subsection headers (Methods, Results, ADVERSE REACTIONS, ...)
        are filtered via SUBSECTION_DENYLIST.
      - Category headers (ESSAYS, RESEARCH REPORTS) sit above the
        title; the per-page reset means we only emit when we've
        actually seen a byline, so a lone category header is dropped.

    `raw` (optional list) — when provided, every kept candidate is
    appended in order, including the byline_text it was emitted for,
    for post-hoc debugging without re-running docling.
    """
    page_texts = {}
    for t in doc.texts:
        p = page_no_of(t)
        if p is None:
            continue
        page_texts.setdefault(p, []).append(t)

    def y_sort(t):
        return -_y_top(t)

    article_starts = []
    seen_titles = set()

    def _emit(p, title, byline_text, header_start_idx, ordered):
        """Apply per-candidate filters; append to article_starts/raw if kept."""
        if not title or len(title) < 5:
            return
        ttl_norm = re.sub(r"\s+", " ", title.lower())
        if ttl_norm in seen_titles:
            return
        if ttl_norm in SUBSECTION_DENYLIST:
            return
        if TABLE_FIGURE_RE.match(title):
            return
        # Reject if the header is followed by ad order-form widgets nearby.
        nearby = ordered[header_start_idx: header_start_idx + 8]
        if sum(1 for t in nearby if text_label(t) in (
            "checkbox_unselected", "checkbox_selected"
        )) >= 2:
            return
        seen_titles.add(ttl_norm)
        rec = {
            "page": p,
            "title": title,
            "authors": parse_authors(byline_text),
            "byline_text": byline_text,
        }
        article_starts.append(rec)
        if raw is not None:
            raw.append({**rec, "kept": True})

    for p in sorted(page_texts):
        ordered = sorted(page_texts[p], key=y_sort)
        # Walk top-down. Maintain "current header chunk" (most recent
        # merged headers above the cursor with no intervening text). When
        # we hit a byline-shaped text item, emit (chunk, byline) as an
        # article start AND reset the chunk so the next header on this
        # page can spawn a second article. This is what lets us pick up
        # multiple short articles on a page (Letters, Brief
        # Communications, Screening Guidelines, ...).
        cur_header_start = None
        cur_header_text = None
        cur_header_is_starter = False
        i = 0
        while i < len(ordered):
            t = ordered[i]
            label = text_label(t)
            if label in HEADER_LABELS:
                merged, end_idx = _merge_stacked_headers(ordered, i)
                cur_header_start = i
                cur_header_text = merged
                cur_header_is_starter = label in ARTICLE_START_LABELS
                i = end_idx + 1
                continue
            if (
                label == "text"
                and cur_header_start is not None
                and cur_header_is_starter
            ):
                txt = (t.text or "").strip()
                if looks_like_byline(txt):
                    _emit(p, cur_header_text or "", txt, cur_header_start, ordered)
                    cur_header_start = None
                    cur_header_text = None
                    cur_header_is_starter = False
            i += 1
    return article_starts


def leaf_to_page_map(pn_path):
    data = json.load(open(pn_path))
    out = {}
    for entry in data.get("pages", []):
        ppage = (entry.get("pageNumber") or "").strip() or None
        leaf = entry.get("leafNum")
        if leaf is not None:
            out[leaf] = ppage
    return out, data


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("item", help="IA item identifier")
    p.add_argument("-o", "--output", help="Output path (default <item>_toc.json)")
    p.add_argument("--raw-output", help="Also dump raw candidates as JSON "
                                        "(default <output>.raw.json)")
    p.add_argument("--cache-dir", default=str(CACHE))
    p.add_argument("--verbose", "-v", action="store_true")
    args = p.parse_args()

    cache_dir = Path(args.cache_dir)
    pdf = fetch_pdf(args.item, cache_dir)
    pn = fetch_page_numbers(args.item, cache_dir)
    leaf_to_page, pn_data = leaf_to_page_map(pn)
    leaf_count = max((p["leafNum"] for p in pn_data.get("pages", [])), default=0)

    print(f"converting {pdf} via Docling...", file=sys.stderr, flush=True)
    import time
    t0 = time.time()
    doc = docling_convert(pdf)
    print(f"  conversion took {time.time() - t0:.1f}s", file=sys.stderr)

    raw = []
    starts = detect_articles(doc, raw=raw)
    if args.verbose:
        for s in starts:
            print(
                f"  p{s['page']} (leaf n{s['page']-1}): {s['title'][:80]!r} "
                f"authors={[a['name'] for a in s['authors']]}",
                file=sys.stderr,
            )

    # Convert Docling 1-indexed page → IA 0-indexed leaf, then build TOC
    entries = []
    for i, s in enumerate(starts):
        start_leaf = s["page"] - 1
        end_leaf = (
            starts[i + 1]["page"] - 2 if i + 1 < len(starts)
            else max(leaf_count - 1, start_leaf)
        )
        if end_leaf < start_leaf:
            end_leaf = start_leaf
        printed = leaf_to_page.get(start_leaf)
        entries.append({
            "id": f"e{i+1}",
            "type": "article",
            "title": s["title"],
            "authors": s["authors"] if s["authors"] else None,
            "leaf_ranges": [[f"n{start_leaf}", f"n{end_leaf}"]],
            "printed_pages": printed,
            "ext_ids": {},
            "confidence": 0.7,
            "evidence": ["ocr"],
            "level": 1,
        })

    toc = {
        "schema_version": SCHEMA_VERSION,
        "item": args.item,
        "leaf_count": leaf_count,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generator": {
            "name": "segart",
            "version": SEGMENTER_VERSION,
            "method": "docling-layout-section-headers",
        },
        "entries": entries,
    }
    out = args.output or f"{args.item}_toc.json"
    with open(out, "w") as f:
        json.dump(toc, f, indent=2)
    print(f"  wrote {out}: {len(entries)} entries", file=sys.stderr)

    raw_out = args.raw_output or (
        out.replace("_toc.json", "_raw.json")
        if out.endswith("_toc.json") else f"{out}.raw.json"
    )
    with open(raw_out, "w") as f:
        json.dump({
            "item": args.item,
            "leaf_count": leaf_count,
            "generator_version": SEGMENTER_VERSION,
            "raw_candidates": raw,
        }, f, indent=2)
    print(f"  wrote {raw_out}: {len(raw)} raw candidates", file=sys.stderr)


if __name__ == "__main__":
    main()
