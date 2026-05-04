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
SEGMENTER_VERSION = "0.3-docling"

CRED_RE = re.compile(
    r"\b(?:M\.?\s*D\.?|Ph\.?\s*D\.?|MA|MS|MPH|MSc|R\.?\s*N\.?|D\.?\s*O\.?|RD|"
    r"PhD|Sc\.?\s*D\.?|Dr\.?|Jr\.?|Sr\.?|II|III|FRCP|FRCS|FACP|MHA|EdD|DSc)"
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


def looks_like_byline(text):
    """Return True if `text` plausibly is an author byline."""
    if not text or len(text) < 3 or len(text) > 300:
        return False
    # Strip leading "by " if present
    body = re.sub(r"^by\s+", "", text, flags=re.IGNORECASE).strip()
    # Has at least one credential or all-caps name pattern
    has_cred = bool(CRED_RE.search(body))
    # Names are capitalized words; check fraction
    words = re.findall(r"[A-Za-z][A-Za-z'\-]+", body)
    if not words:
        return False
    caps = sum(1 for w in words if w[0].isupper() or w.isupper())
    name_ratio = caps / len(words)
    return has_cred or (name_ratio >= 0.7 and 2 <= len(words) <= 30)


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


def detect_articles(doc):
    """Walk pages top-down and emit article starts.

    Article-start rule (v0.3.1): a `section_header` is treated as an
    article title only if it is immediately followed (within a few
    items) by a byline-shaped text block. Subsection headers within an
    article (Findings, Methods, Discussion, ...) don't have bylines
    under them and are dropped. Section/category headers (ESSAYS,
    RESEARCH REPORTS) sit *above* the article title; in that case the
    article title is the section_header closest to (immediately above)
    the byline.
    """
    page_texts = {}
    for t in doc.texts:
        p = page_no_of(t)
        if p is None:
            continue
        page_texts.setdefault(p, []).append(t)

    def y_of(t):
        bb = bbox_of(t) or (0, 0, 0, 0)
        # Docling y is bottom-up; invert so higher-on-page comes first.
        return -(bb[1] or 0)

    article_starts = []
    seen_titles = set()
    for p in sorted(page_texts):
        ordered = sorted(page_texts[p], key=y_of)
        # Walk top-down looking for a byline; the article title is the
        # section_header most-recently seen above it.
        last_section_header_idx = None
        byline_idx = None
        for i, t in enumerate(ordered):
            label = text_label(t)
            if label == "section_header":
                last_section_header_idx = i
            elif label == "text" and last_section_header_idx is not None:
                txt = (t.text or "").strip()
                if looks_like_byline(txt):
                    byline_idx = i
                    break

        if byline_idx is None or last_section_header_idx is None:
            continue
        sh = ordered[last_section_header_idx]
        title = (sh.text or "").strip()
        if not title or len(title) < 5:
            continue
        # Skip duplicates (same title detected on multiple pages)
        ttl_norm = re.sub(r"\s+", " ", title.lower())
        if ttl_norm in seen_titles:
            continue
        seen_titles.add(ttl_norm)

        # Reject the header is followed by ad order-form widgets nearby
        nearby = ordered[last_section_header_idx: last_section_header_idx + 8]
        if sum(1 for t in nearby if text_label(t) in (
            "checkbox_unselected", "checkbox_selected"
        )) >= 2:
            continue

        authors = parse_authors((ordered[byline_idx].text or "").strip())
        article_starts.append({
            "page": p,
            "title": title,
            "authors": authors,
        })
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

    starts = detect_articles(doc)
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


if __name__ == "__main__":
    main()
