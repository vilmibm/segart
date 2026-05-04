#!/usr/bin/env python3
"""segart segmenter v0 — extract a TOC from one IA periodical issue.

Approach:
  1. Fetch <item>_hocr.html and <item>_page_numbers.json via the `ia` CLI
     (the iailllogs-adjacent items are access-restricted; raw HTTP gets 403s).
  2. Parse hOCR to get per-leaf OCR text. Take the first N leaves — that's
     where a printed Table of Contents typically lives in scholarly journals.
  3. Send the OCR text to Claude with a structured-extraction prompt.
  4. Resolve each entry's printed page number → leaf via page_numbers.json.
  5. Compute end_leaf for each entry as start_leaf of the next entry minus one.
  6. Emit <item>_toc.json per docs/toc_format.md.

Limitations of v0 (intentional — see how far this gets us before adding):
  - Only consults front-of-book OCR. If the printed TOC isn't in the first
    N leaves, we miss the issue entirely.
  - Doesn't use hOCR font sizes (yet) for title-page detection — that's the
    natural fallback when no printed TOC is found.
  - Doesn't look at page images, just OCR.
  - Doesn't currently fill ext_ids (DOI/PMID/fatcat) — populated in a later
    cross-reference pass against fatcat.

Usage:
  ./segment_issue.py sim_academic-medicine_2003-06_78_6
  ./segment_issue.py <item> --dry-run        # print prompt, don't call API
  ./segment_issue.py <item> -o /tmp/foo.json
"""
import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

CACHE = Path(os.environ.get("SEGART_CACHE", "/tmp/segart_items"))
MODEL = "claude-opus-4-7"
SCHEMA_VERSION = 1
SEGMENTER_VERSION = "0.1"

SYSTEM_PROMPT = """You are an expert library cataloger working on the segart \
project at the Internet Archive. The Internet Archive holds digitized scans \
of periodicals — scholarly journals and magazines — and we are building a \
machine-readable Table of Contents (TOC) for each issue.

You will be given the OCR text of the first several leaves of one issue. \
The first few leaves of a journal issue typically contain: the cover, the \
masthead/editorial board, advertisements, and a printed Table of Contents.

Your job: find the printed TOC (if present) and extract every article it \
lists — even short items like editorials, letters, news, and book reviews. \
For each article emit:

  title          — the article title, cleaned of obvious OCR errors
  authors        — list of {name, affiliation?}; affiliation null if not in TOC
  printed_page   — the printed page number from the TOC, as a string ("23",
                   "S63", "1273", etc.). Preserve any prefix (e.g. supplement
                   pages keep their "S"). Null if not shown in the TOC.
  type           — one of: article, editorial, letter, review, news,
                   advertisement, toc, frontmatter, backmatter, other

Important rules:
  - If the OCR text does NOT contain a recognizable printed Table of Contents,
    return entries: []. Do not invent entries from a cover page or masthead
    alone — the TOC is the source of truth here.
  - Skip non-content items: page numbers in editorial board lists, journal
    boilerplate, ads. Include real scholarly items even if short.
  - OCR errors are common. Fix obvious ones (e.g. "ot" → "of") but don't
    invent words.
  - Each entry must have a non-empty title.
"""

EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "found_toc": {
            "type": "boolean",
            "description": "True if a printed TOC was found in the OCR text.",
        },
        "entries": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "authors": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "affiliation": {"type": ["string", "null"]},
                            },
                            "required": ["name", "affiliation"],
                            "additionalProperties": False,
                        },
                    },
                    "printed_page": {"type": ["string", "null"]},
                    "type": {
                        "type": "string",
                        "enum": [
                            "article", "editorial", "letter", "review", "news",
                            "advertisement", "toc", "frontmatter", "backmatter",
                            "other",
                        ],
                    },
                },
                "required": ["title", "authors", "printed_page", "type"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["found_toc", "entries"],
    "additionalProperties": False,
}


def fetch_derive_files(item, cache_dir):
    """Pull hOCR and page_numbers.json into the cache via `ia` CLI."""
    item_dir = cache_dir / item
    item_dir.mkdir(parents=True, exist_ok=True)
    needed = {
        f"{item}_hocr.html": "*hocr.html",
        f"{item}_page_numbers.json": "*page_numbers.json",
    }
    for fname, glob in needed.items():
        if (item_dir / fname).exists():
            continue
        subprocess.run(
            [
                "ia", "download", item,
                "--glob", glob,
                "--destdir", str(cache_dir),
            ],
            check=True, capture_output=True,
        )
    return item_dir


PAGE_DIV_RE = re.compile(
    r"<div class=['\"]ocr_page['\"]\s+id=['\"]page_(\d+)['\"]"
)
WORD_RE = re.compile(
    r"<span class=['\"]ocrx_word['\"][^>]*>([^<]*)</span>"
)


def hocr_pages(hocr_path, max_pages):
    """Return [{leaf_num, text}, ...] for the first max_pages leaves of hOCR."""
    text = open(hocr_path, encoding="utf-8").read()
    page_starts = list(PAGE_DIV_RE.finditer(text))
    out = []
    for i, m in enumerate(page_starts[:max_pages]):
        # IA hOCR `ppageno` is unreliable (often stuck at 0); the page id
        # `page_000001` corresponds to leaf 0 in IA URL space (n0).
        leaf = int(m.group(1)) - 1
        end = (
            page_starts[i + 1].start()
            if i + 1 < len(page_starts)
            else len(text)
        )
        chunk = text[m.start():end]
        words = WORD_RE.findall(chunk)
        page_text = " ".join(w for w in words if w.strip())
        out.append({"leaf_num": leaf, "text": page_text})
    return out, len(page_starts)


def page_to_leaf_map(pn_path):
    """Return (printed_page_str -> leaf_num int) plus the raw data."""
    data = json.load(open(pn_path))
    m = {}
    for entry in data.get("pages", []):
        ppage = (entry.get("pageNumber") or "").strip()
        leaf = entry.get("leafNum")
        if not ppage or leaf is None:
            continue
        m.setdefault(ppage, leaf)
        # Also try a normalized integer form (drops any leading zeros)
        try:
            m.setdefault(str(int(re.sub(r"[^\d]", "", ppage))), leaf)
        except ValueError:
            pass
    return m, data


def resolve_printed_page(printed_page, page_to_leaf):
    """Best-effort lookup of a printed-page string to a leaf number."""
    if printed_page is None:
        return None
    candidates = [printed_page, printed_page.strip()]
    candidates.append(printed_page.strip().lstrip("S"))
    for c in candidates:
        if c in page_to_leaf:
            return page_to_leaf[c]
    digits = re.sub(r"[^\d]", "", printed_page)
    if digits and digits in page_to_leaf:
        return page_to_leaf[digits]
    return None


def build_toc(item, llm_entries, page_to_leaf, leaf_count):
    resolved = []
    for e in llm_entries:
        leaf = resolve_printed_page(e.get("printed_page"), page_to_leaf)
        if leaf is None:
            continue
        e["_start_leaf"] = leaf
        resolved.append(e)
    resolved.sort(key=lambda e: e["_start_leaf"])

    out_entries = []
    for i, e in enumerate(resolved):
        start = e["_start_leaf"]
        end = (
            resolved[i + 1]["_start_leaf"] - 1
            if i + 1 < len(resolved)
            else leaf_count - 1
        )
        if end < start:
            end = start
        authors = e.get("authors") or []
        out_entries.append({
            "id": f"e{i+1}",
            "type": e.get("type", "article"),
            "title": e["title"],
            "authors": authors if authors else None,
            "leaf_ranges": [[f"n{start}", f"n{end}"]],
            "printed_pages": e.get("printed_page"),
            "ext_ids": {},
            "confidence": 0.5,
            "evidence": ["ocr"],
            "level": 1,
        })
    return {
        "schema_version": SCHEMA_VERSION,
        "item": item,
        "leaf_count": leaf_count,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generator": {
            "name": "segart",
            "version": SEGMENTER_VERSION,
            "method": "hocr-front-toc-llm",
        },
        "entries": out_entries,
    }


def call_llm(client, item, ocr_text, n_leaves):
    user_message = (
        f"Item identifier: {item}\n"
        f"OCR text from the first {n_leaves} leaves of this issue follows. "
        f"Each leaf is delimited by a `=== leaf nN ===` header.\n\n"
        f"{ocr_text}"
    )
    response = client.messages.create(
        model=MODEL,
        max_tokens=16000,
        thinking={"type": "adaptive"},
        system=[{
            "type": "text",
            "text": SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }],
        messages=[{"role": "user", "content": user_message}],
        output_config={
            "format": {"type": "json_schema", "schema": EXTRACTION_SCHEMA},
        },
    )
    text = next(b.text for b in response.content if b.type == "text")
    return json.loads(text), response.usage


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("item", help="IA item identifier")
    p.add_argument("-o", "--output", help="Output path (default <item>_toc.json)")
    p.add_argument(
        "--n-leaves", type=int, default=10,
        help="Number of front-of-book leaves to send to the LLM (default 10)",
    )
    p.add_argument("--cache-dir", default=str(CACHE))
    p.add_argument(
        "--dry-run", action="store_true",
        help="Print the prompt and exit without calling the API",
    )
    args = p.parse_args()

    cache_dir = Path(args.cache_dir)
    item_dir = fetch_derive_files(args.item, cache_dir)
    hocr_path = item_dir / f"{args.item}_hocr.html"
    pn_path = item_dir / f"{args.item}_page_numbers.json"

    pages, total_pages = hocr_pages(hocr_path, args.n_leaves)
    page_to_leaf, pn_data = page_to_leaf_map(pn_path)
    leaf_count = (
        max((p["leafNum"] for p in pn_data.get("pages", [])), default=total_pages)
    )

    ocr_text = "\n\n".join(
        f"=== leaf n{p['leaf_num']} ===\n{p['text']}" for p in pages
    )

    print(
        f"item={args.item} leaves_seen={len(pages)}/{total_pages} "
        f"ocr_chars={len(ocr_text)} page_to_leaf_entries={len(page_to_leaf)}",
        file=sys.stderr,
    )

    if args.dry_run:
        print("--- SYSTEM ---")
        print(SYSTEM_PROMPT)
        print("\n--- USER ---")
        print(ocr_text[:6000])
        if len(ocr_text) > 6000:
            print(f"...[truncated, full size {len(ocr_text)} chars]")
        return

    import anthropic
    client = anthropic.Anthropic()
    extracted, usage = call_llm(client, args.item, ocr_text, len(pages))

    print(
        f"  llm: input={usage.input_tokens} cache_read={usage.cache_read_input_tokens} "
        f"output={usage.output_tokens} found_toc={extracted['found_toc']} "
        f"entries={len(extracted['entries'])}",
        file=sys.stderr,
    )

    toc = build_toc(args.item, extracted["entries"], page_to_leaf, leaf_count)
    out_path = args.output or f"{args.item}_toc.json"
    with open(out_path, "w") as f:
        json.dump(toc, f, indent=2)
    print(f"  wrote {out_path} ({len(toc['entries'])} entries)", file=sys.stderr)


if __name__ == "__main__":
    main()
