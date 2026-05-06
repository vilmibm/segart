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
import gzip
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
SEGMENTER_VERSION = "0.14-docling"

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


def docling_convert(pdf_path, device="mps"):
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.datamodel.accelerator_options import AcceleratorOptions, AcceleratorDevice

    # CPU mode trades speed for lower peak unified-memory pressure on M2:
    # the layout model's weights and activations don't sit in GPU RAM
    # alongside everything else competing for the same pool.
    dev = AcceleratorDevice.CPU if device == "cpu" else AcceleratorDevice.MPS
    opts = PdfPipelineOptions()
    # Keep OCR off — IA SIM PDFs are "Text PDFs" with embedded text from
    # the original scan OCR pass; running RapidOCR again is ~10x slower
    # for identical results (validated on academic-medicine_1989-02:
    # 1791s with OCR vs 179s without, byte-identical document_index
    # cells).
    opts.do_ocr = False
    # Enable layout-aware table structure parsing. Populates
    # data.table_cells[].text with row/column offsets on tables docling
    # detects (esp. document_index = TOCs), which is what makes recall
    # extraction reliable. Adds a few minutes per item over plain
    # layout, well worth it.
    opts.do_table_structure = True
    opts.accelerator_options = AcceleratorOptions(device=dev, num_threads=4)
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

    Hierarchy (most-trusted first):
      1. Explicit "by " prefix → True
      2. Credential present (M.D., Ph.D., R.N., ...) → True
      3. Strong name patterns (initials, surname-comma-initial,
         all-caps name) → True
      4. The looser two-cap-word pattern (`^Sue Buckley`, `^Anthony
         Joyce`) only counts as a byline IF the text ALSO has a
         multi-author connector (`,`, ` and `, ` & `, `;`) OR is short
         (≤30 chars) — without that, things like `Asian American
         Women's Movement in California` get classified as bylines
         when they're really subtitles.
    """
    if not text or len(text) < 3 or len(text) > 300:
        return False
    body = re.sub(r"^by\s+", "", text, flags=re.IGNORECASE).strip()
    if body != text.strip():
        return True  # had explicit "by " prefix → trust it
    if CRED_RE.search(body):
        return True
    # Strong patterns (0..3) — trust on their own.
    for r in NAME_SHAPE_RES[:4]:
        if r.search(body):
            return True
    # Loose two-cap-word patterns (4..) — gated on connector OR brevity.
    has_connector = bool(re.search(r",|\s+and\s+|\s*&\s*|;", body))
    short = len(body) <= 30
    if has_connector or short:
        for r in NAME_SHAPE_RES[4:]:
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
        "references", "reference", "bibliography",
        "additional references", "further reading", "related articles",
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
        "indications and usage", "clinical pharmacology",
        # generic journal section labels
        "communications", "letters to the editor", "letters", "correspondence",
        "editorial", "editorials", "errata", "erratum",
        "in this issue", "from the editor", "editor's note", "editor",
        "book reviews", "book review", "publications received",
        "address changes", "address correction", "subscriptions",
        "announcements", "notices", "calendar", "official publication",
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

# Trademark / registered-mark / service-mark reject. Drug ads and product
# headlines in medical journals overwhelmingly carry one of these symbols
# (or the OCR variant "(R)" / "(TM)"); real article titles essentially
# never do. Profile run 2026-05 found this pattern on >50% of dropped
# entries in psychosocial-nursing-mental-health-services_2007.
TRADEMARK_RE = re.compile(r"[™®©℠]|\(\s*R\s*\)|\(\s*TM\s*\)", re.IGNORECASE)

# Reject titles that contain a credential token preceded by comma/period
# — i.e., the listing pattern "Name, R.N., Ph.D." that editorial-board
# pages, contributor lists, and bare-name byline-as-title false positives
# exhibit. Requiring the comma/period prefix avoids false positives on
# topical content like "MD-LD Method" where "MD" is part of a phrase.
CRED_LIST_RE = re.compile(
    r"[,.]\s*(?:M\.?\s*D\.?|Ph\.?\s*D\.?|MA|MS|MPH|MSc|R\.?\s*N\.?|D\.?\s*O\.?|"
    r"RD|PhD|Sc\.?\s*D\.?|Jr\.?|Sr\.?|II|III|FRCP|FRCS|FACP|MHA|EdD|DSc|"
    r"FAAN|CNS|CNAA|APRN|BC|CTN|FACOG|FACS|FACP)\.?\b",
    re.IGNORECASE,
)

# Drug-ad inserts often print a section header like "DOSAGE AND
# ADMINISTRATION:" followed by long product-information copy. The exact
# string "dosage and administration" is in SUBSECTION_DENYLIST, but the
# full ad-leading-line includes trailing punctuation + body text that
# breaks the exact match. This pattern catches the leading-keyword form.
DRUG_AD_PREFIX_RE = re.compile(
    r"^(?:dosage\s+and\s+administration|"
    r"indications(?:\s+and\s+usage)?|"
    r"contraindications|"
    r"adverse\s+reactions|"
    r"precautions|"
    r"warnings|"
    r"how\s+supplied|"
    r"prescribing\s+information|"
    r"clinical\s+pharmacology)\s*[:.\-]",
    re.IGNORECASE,
)

# v0.6: Section-label prefixes that frequently get glued to the front of
# article titles when docling treats them as a separate header block
# stacked above the title. We strip the prefix so the remaining text is
# a clean article title. Order matters — longest first so we strip
# multi-word labels before single-word ones.
TITLE_LABEL_PREFIXES = sorted(
    [
        "introduction to special topic forum",
        "national policy perspectives",
        "editorial team essay",
        "invited articles",
        "special topic forum",
        "research articles",
        "research article",
        "original articles",
        "original article",
        "book reviews",
        "book review",
        "essays",
        "essay",
        "features",
        "departments",
        "articles",
        "editorial",
        "editorials",
        "perspectives",
        "perspective",
        "letters to the editor",
        "letters",
        "communications",
        "communication",
        "news",
        "review",
        "note",
        "notes",
        "short paper",
        "short papers",
        "report",
        "reports",
    ],
    key=lambda s: -len(s),
)


def _strip_label_prefix(title):
    """Remove a leading section-label phrase from `title`, if present.

    Compares case-insensitively against TITLE_LABEL_PREFIXES. The label
    must be followed by whitespace and at least one more word — we
    don't want to nuke titles that genuinely ARE just "Editorial".
    """
    if not title:
        return title
    norm = re.sub(r"\s+", " ", title.strip())
    low = norm.lower()
    for label in TITLE_LABEL_PREFIXES:
        if low.startswith(label + " "):
            remainder = norm[len(label):].lstrip(" :;,-")
            # Require remainder to be substantial — at least 3 words.
            if len(remainder.split()) >= 3:
                return remainder
    return title


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


def _height(t):
    """Height of a text item's bbox (Docling y is bottom-up: top − bottom)."""
    bb = bbox_of(t) or (0, 0, 0, 0)
    if bb[1] is None or bb[3] is None:
        return 0.0
    return abs(bb[1] - bb[3])


def _looks_titleish(text):
    """A 'text' item promoted to candidate-header must look title-like:
    short-ish, no terminal sentence punctuation, has multiple words."""
    if not text:
        return False
    s = text.strip()
    if len(s) < 10 or len(s) > 200:
        return False
    if s.endswith(".") and not s.endswith(("Inc.", "Co.", "Jr.", "Sr.", "Ltd.", "ed.")):
        return False
    if s.count(" ") < 2:  # need ≥3 words
        return False
    return True


def _promote_text_to_header(t, median_h, text_label_str):
    """v0.8 trial: promote large 'text' items to candidate-header. The
    intuition is that some article titles are labeled `text` instead of
    `section_header` by docling, especially in older medical journals.
    Empirically (32-item evaluation 2026-05-05) this kept hit-rate flat
    at 22% (filtered) but worsened undershoot from 11% → 16% — most
    'tall text' items turned out to be in-article subsections that
    weren't previously emitted, so promoting them split real articles.
    Currently DISABLED. To re-enable: lower the False return below.
    """
    return False


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

    def _emit(p, title, byline_text, header_start_idx, ordered, header_ev=None):
        """Apply per-candidate filters; append to article_starts/raw if kept.

        `header_ev` records *why* this candidate was emitted — which docling
        label triggered it, whether multiple header lines were merged, and
        whether a font-size promotion fired. Used downstream by
        augment_evidence.py and for heuristic profiling.
        """
        # Collapse whitespace once at the boundary: docling joins per-line
        # text with stray spaces, so titles arrive as "A  Model  for  Theory".
        title = re.sub(r"\s+", " ", title or "").strip()
        byline_text = re.sub(r"\s+", " ", byline_text or "").strip()
        if not title or len(title) < 5:
            return
        # v0.6: strip leading section-label prefixes ("ESSAYS Beyond
        # Florence Nightingale" → "Beyond Florence Nightingale") before
        # the dedupe and denylist checks.
        title = _strip_label_prefix(title)
        if not title or len(title) < 5:
            return
        ttl_norm = title.lower()
        if ttl_norm in seen_titles:
            return
        if ttl_norm in SUBSECTION_DENYLIST:
            return
        if TABLE_FIGURE_RE.match(title):
            return
        if TRADEMARK_RE.search(title) or TRADEMARK_RE.search(byline_text):
            return
        if CRED_LIST_RE.search(title):
            return
        if DRUG_AD_PREFIX_RE.match(title):
            return
        # Reject if the header is followed by ad order-form widgets nearby.
        nearby = ordered[header_start_idx: header_start_idx + 8]
        if sum(1 for t in nearby if text_label(t) in (
            "checkbox_unselected", "checkbox_selected"
        )) >= 2:
            return
        seen_titles.add(ttl_norm)
        ev = list(header_ev or [])
        ev.append("byline_match")
        rec = {
            "page": p,
            "title": title,
            "authors": parse_authors(byline_text),
            "byline_text": byline_text,
            "evidence": ev,
        }
        article_starts.append(rec)
        if raw is not None:
            raw.append({**rec, "kept": True})

    for p in sorted(page_texts):
        ordered = sorted(page_texts[p], key=y_sort)
        # v0.8: compute median height of items labeled 'text' on this
        # page, so we can identify font-size outliers (likely article
        # titles that docling didn't label as section_header).
        text_heights = [
            _height(t) for t in ordered
            if text_label(t) == "text" and _height(t) > 0
        ]
        text_heights.sort()
        median_h = (
            text_heights[len(text_heights) // 2] if text_heights else 0
        )
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
        cur_header_ev = []
        i = 0
        while i < len(ordered):
            t = ordered[i]
            label = text_label(t)
            promoted = _promote_text_to_header(t, median_h, label)
            if label in HEADER_LABELS or promoted:
                merged, end_idx = _merge_stacked_headers(ordered, i)
                cur_header_start = i
                cur_header_text = merged
                cur_header_is_starter = label in ARTICLE_START_LABELS or promoted
                cur_header_ev = ["promoted_font_size"] if promoted else [label]
                if end_idx > i:
                    cur_header_ev.append("merged_multiline")
                i = end_idx + 1
                continue
            if (
                label == "text"
                and cur_header_start is not None
                and cur_header_is_starter
            ):
                txt = (t.text or "").strip()
                if looks_like_byline(txt):
                    _emit(p, cur_header_text or "", txt, cur_header_start,
                          ordered, header_ev=cur_header_ev)
                    cur_header_start = None
                    cur_header_text = None
                    cur_header_is_starter = False
                    cur_header_ev = []
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
    p.add_argument("--keep-pdf", action="store_true",
                   help="Don't delete PDF after caching docling output")
    p.add_argument("--device", choices=("mps", "cpu"), default="mps",
                   help="Docling accelerator. CPU is slower but uses less "
                        "unified RAM — pick it for big PDFs on M-series.")
    p.add_argument("--verbose", "-v", action="store_true")
    args = p.parse_args()

    cache_dir = Path(args.cache_dir)
    item_dir = cache_dir / args.item
    cache_doc_gz = item_dir / f"{args.item}_docling.json.gz"
    cache_doc_legacy = item_dir / f"{args.item}_docling.json"

    # Page-number JSON is small; always fetch.
    pn = fetch_page_numbers(args.item, cache_dir)
    leaf_to_page, pn_data = leaf_to_page_map(pn)
    leaf_count = max((p["leafNum"] for p in pn_data.get("pages", [])), default=0)

    # Cache-first flow: if a cached docling exists we load it and SKIP
    # fetching the PDF entirely. Cache is gzip-compressed (~7x smaller);
    # legacy uncompressed .json is read once then re-saved as .gz.
    doc = None
    pdf = None
    cache_src = cache_doc_gz if cache_doc_gz.exists() else (cache_doc_legacy if cache_doc_legacy.exists() else None)
    if cache_src is not None:
        try:
            from docling_core.types.doc import DoclingDocument
            opener = gzip.open if cache_src.suffix == ".gz" else open
            with opener(cache_src, "rt", encoding="utf-8") as fh:
                doc = DoclingDocument.model_validate_json(fh.read())
            print(f"  loaded docling cache {cache_src.name}", file=sys.stderr)
            # Cache-validity check: a cache built before
            # do_table_structure=True won't have populated cell text on
            # any table. Detect that by looking for tables-without-text
            # and force a re-conversion. (Items without any tables look
            # the same either way; we leave them alone — they're
            # accepted as fine since structured tables wouldn't have
            # changed anything for them.)
            tables = list(getattr(doc, "tables", None) or [])
            if tables:
                has_text = any(
                    (getattr(c, "text", "") or "").strip()
                    for tbl in tables
                    for c in getattr(getattr(tbl, "data", None), "table_cells", None) or []
                )
                if not has_text:
                    print("  cache predates do_table_structure; "
                          "re-converting to populate table cells",
                          file=sys.stderr)
                    doc = None
            if doc is not None and cache_src is cache_doc_legacy:
                try:
                    with gzip.open(cache_doc_gz, "wt", encoding="utf-8") as fh:
                        fh.write(doc.model_dump_json())
                    cache_doc_legacy.unlink()
                    print(f"  migrated legacy cache → {cache_doc_gz.name}", file=sys.stderr)
                except Exception as e:
                    print(f"  WARN: legacy migration failed: {e}", file=sys.stderr)
        except Exception as e:
            print(f"  cache load failed ({e}); will re-run docling", file=sys.stderr)
            doc = None
    if doc is None:
        pdf = fetch_pdf(args.item, cache_dir)
        print(f"converting {pdf} via Docling...", file=sys.stderr, flush=True)
        import time
        t0 = time.time()
        doc = docling_convert(pdf, device=args.device)
        print(f"  conversion took {time.time() - t0:.1f}s", file=sys.stderr)
        try:
            with gzip.open(cache_doc_gz, "wt", encoding="utf-8") as fh:
                fh.write(doc.model_dump_json())
            print(f"  wrote docling cache {cache_doc_gz.name}", file=sys.stderr)
        except Exception as e:
            print(f"  WARN: docling cache write failed: {e}", file=sys.stderr)

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
            "evidence": s.get("evidence") or ["ocr"],
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

    # Disk hygiene: once the docling cache is on disk, the PDF is no
    # longer needed for re-segmentation. Future segmentation passes load
    # from the cache. The PDF can be re-fetched from IA if ever needed.
    if pdf is not None and cache_doc_gz.exists() and pdf.exists() and not args.keep_pdf:
        try:
            sz = pdf.stat().st_size
            pdf.unlink()
            print(f"  deleted pdf to free {sz//1024//1024}MB", file=sys.stderr)
        except Exception as e:
            print(f"  WARN: pdf delete failed: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
