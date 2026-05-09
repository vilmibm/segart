#!/usr/bin/env python3
"""
Prototype: extract a TOC from a docling cache by asking Claude directly.

Replaces the heuristic stack in segment_issue_docling.py (looks_like_byline,
parse_authors, _looks_like_author_part, _strip_label_prefix, ...) for the
sub-task of "given a magazine issue's docling layout, what are the real
articles?". We're testing the quality ceiling, not optimizing cost.

Input:  docling JSON cache for an item.
Output: TOC with title, authors (name strings), start_page (printed),
        and an entry-type tag.

Run: ./llm_toc_extract.py <item> [--out path.json]
"""
import argparse
import gzip
import json
import os
import sys
import time
from pathlib import Path
from typing import List, Literal

import anthropic
from pydantic import BaseModel, Field

ITEMS = Path.home() / "tmp" / "segart" / "tmp" / "items"

# Labels worth surfacing to the model. `text` blocks are kept only when
# they're long enough to matter — short `text` items are usually OCR noise
# (page numbers, punctuation, single characters from column gutters).
HEADER_LABELS = {"section_header", "title", "paragraph_header"}
TEXT_LABEL = "text"
MIN_TEXT_LEN = 20


class Author(BaseModel):
    name: str = Field(description="Person's name as printed (no credentials, no titles).")


class TOCEntry(BaseModel):
    title: str = Field(description="Article/section title as printed, with normalized whitespace.")
    authors: List[Author] = Field(
        default_factory=list,
        description="Authors of this entry. Empty if no byline (e.g., editorials, sections).",
    )
    start_page_index: int = Field(
        description="1-indexed PDF page-index (IA's accessible page index) where "
                    "the entry begins. This is the `page` field of the layout block "
                    "carrying this entry's title. Required for every entry."
    )
    end_page_index: int = Field(
        description="1-indexed PDF page-index where the entry ends — typically the "
                    "page-index just before the next entry starts, or the issue's "
                    "last page-index for the final entry. Equal to start_page_index "
                    "for single-page entries."
    )
    start_page_number: int = Field(
        description="Printed page number (NOT page-index) where the entry begins. "
                    "Use 0 if no printed page is visible (front/back matter, ads)."
    )
    end_page_number: int = Field(
        description="Printed page number where the entry ends. Use 0 if not "
                    "determinable. May equal start_page_number for a one-page entry."
    )
    type: Literal[
        "article", "editorial", "review", "letter", "section",
        "front_matter", "back_matter", "advertisement", "other",
    ] = Field(description="Coarse category of the entry.")


class TOC(BaseModel):
    entries: List[TOCEntry]


def _strict_schema(s):
    """Anthropic's structured-outputs validator rejects object schemas that
    omit `additionalProperties: false`. Pydantic's `model_json_schema` doesn't
    set it, so we walk the schema and add it everywhere `type: object` shows
    up (also recurses into $defs, properties, items, anyOf/allOf branches)."""
    if isinstance(s, dict):
        if s.get("type") == "object" and "additionalProperties" not in s:
            s["additionalProperties"] = False
        for v in s.values():
            _strict_schema(v)
    elif isinstance(s, list):
        for v in s:
            _strict_schema(v)
    return s


def load_docling(item: str) -> dict:
    cache = ITEMS / item / f"{item}_docling.json.gz"
    if not cache.exists():
        sys.exit(f"no docling cache: {cache}")
    with gzip.open(cache, "rt") as fh:
        return json.load(fh)


def build_compact_view(doc: dict) -> List[dict]:
    """Drop OCR noise; keep labeled headings and substantive text blocks."""
    out = []
    for t in doc.get("texts") or []:
        label = t.get("label")
        text = (t.get("text") or "").strip()
        if label in HEADER_LABELS:
            pass
        elif label == TEXT_LABEL and len(text) >= MIN_TEXT_LEN:
            pass
        else:
            continue
        prov = (t.get("prov") or [{}])[0]
        bbox = prov.get("bbox") or {}
        out.append({
            "page": prov.get("page_no"),
            "y": round(bbox.get("t") or 0, 1),
            "label": label,
            "text": text,
        })
    # Sort by page, then by y descending (docling y is bottom-up, so larger y
    # means higher on the page = earlier in reading order).
    out.sort(key=lambda b: (b["page"] or 0, -(b["y"] or 0)))
    return out


SYSTEM = """You are extracting the table of contents from a periodical issue \
(an academic journal, magazine, or trade publication). You are given a \
flattened, ordered list of layout blocks from a PDF — each block has a page, \
a vertical position, a label assigned by a layout model, and the text it \
contains.

Your job: identify every distinct article, editorial, book review, letter, \
section header, or other top-level entry that would appear in a real table \
of contents — and extract its title, authors (if any), starting printed page \
number, and a coarse type.

Guidelines:
- An article entry has a title and (usually) a byline naming one or more authors.
- Author names are PEOPLE's names. Institutions, sentence fragments, narrative \
text, and bare descriptors (e.g. "president of X", "as Y has noted") are not \
authors — emit no authors in those cases.
- Editorials, book reviews, and letters often lack bylines or have only a \
single author. Include them anyway.
- Section dividers (e.g. "ESSAYS", "BOOK REVIEWS", "LETTERS TO THE EDITOR") \
should be emitted as type=section, not as articles.
- Do NOT emit subscription info, copyright pages, masthead, mailing addresses, \
calls-for-papers, or advertisements as articles. Use type=front_matter / \
back_matter / advertisement when relevant, or omit them.
- Do NOT emit in-article subsection headers (Methods, Results, Background, \
References, Acknowledgments) as separate entries — they belong to their parent \
article.
- Titles may span multiple visual lines; merge them into one clean title with \
single spaces.
- start_page_index and end_page_index are 1-indexed PDF page-index positions \
(IA's "accessible page index" — the monotonically increasing counter over \
pages included in BookReader/PDF access). The `page` field on each input \
block IS the page-index. start_page_index is the page-index containing the \
entry's title. end_page_index is the page-index just before the next entry \
begins (or the issue's last page-index for the final entry). For a one-page \
entry, set end_page_index == start_page_index. NEVER omit these — every \
entry needs them.
- start_page_number and end_page_number are the PRINTED page numbers visible \
on the page (e.g. "43" printed in the page-number gutter), NOT page-indices. \
They will usually be smaller than the page-indices because covers, contents, \
and front matter come before "page 1". If you can't see the printed page \
near the entry, use 0.

Be thorough — include every distinct entry you can identify. Quality matters \
more than brevity."""


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("item")
    ap.add_argument("--out", default=None,
                    help="Write TOC JSON here (default <item>_toc_llm.json in tmp/tocs)")
    ap.add_argument("--model", default="claude-opus-4-7")
    ap.add_argument("--max-tokens", type=int, default=32000)
    ap.add_argument("--effort", choices=("low", "medium", "high", "max"),
                    default="high",
                    help="Adaptive-thinking effort. Default 'high' matches API default; "
                         "lower for faster/cheaper at risk of quality")
    args = ap.parse_args()

    doc = load_docling(args.item)
    blocks = build_compact_view(doc)
    print(f"item: {args.item}", file=sys.stderr)
    print(f"docling texts: {len(doc.get('texts') or [])}", file=sys.stderr)
    print(f"compact blocks: {len(blocks)}", file=sys.stderr)

    user_payload = {
        "item": args.item,
        "page_count": len(doc.get("pages") or {}),
        "blocks": blocks,
    }
    user_text = (
        f"Item: {args.item}\n"
        f"Page count: {user_payload['page_count']}\n\n"
        f"Layout blocks (ordered by page, then top-to-bottom):\n\n"
        + json.dumps(blocks, separators=(",", ":"))
    )
    approx_tokens = (len(user_text) + len(SYSTEM)) // 4
    print(f"approx input tokens: {approx_tokens:,}", file=sys.stderr)

    client = anthropic.Anthropic()
    t0 = time.time()
    with client.messages.stream(
        model=args.model,
        max_tokens=args.max_tokens,
        thinking={"type": "adaptive"},
        system=SYSTEM,
        messages=[{"role": "user", "content": user_text}],
        output_config={
            "effort": args.effort,
            "format": {
                "type": "json_schema",
                "schema": _strict_schema(TOC.model_json_schema()),
            },
        },
    ) as stream:
        for _ in stream.text_stream:
            pass
        msg = stream.get_final_message()
    dt = time.time() - t0

    text_blocks = [b.text for b in msg.content if b.type == "text"]
    if not text_blocks:
        block_types = [getattr(b, "type", "?") for b in msg.content]
        print(f"\nERROR: response had no text block. "
              f"stop_reason={msg.stop_reason}; block types={block_types}",
              file=sys.stderr)
        # Surface usage so the caller can still see how much we paid for the dud.
        u = msg.usage
        print(f"  input tokens: {u.input_tokens}", file=sys.stderr)
        print(f"  output tokens: {u.output_tokens}", file=sys.stderr)
        sys.exit(2)
    text = text_blocks[0]
    toc = TOC.model_validate_json(text)

    # Output
    out_path = Path(args.out) if args.out else (
        Path.home() / "tmp" / "segart" / "tmp" / "tocs"
        / f"{args.item}_toc_llm.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({
        "schema_version": "llm_toc_v2",
        "item": args.item,
        "model": args.model,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "entries": [e.model_dump() for e in toc.entries],
    }, indent=2))
    print(f"\nwrote {out_path}: {len(toc.entries)} entries", file=sys.stderr)

    # Stats
    u = msg.usage
    print(f"\nlatency: {dt:.1f}s", file=sys.stderr)
    print(f"input tokens (uncached): {u.input_tokens}", file=sys.stderr)
    print(f"cache read:              {getattr(u, 'cache_read_input_tokens', 0)}", file=sys.stderr)
    print(f"cache create:            {getattr(u, 'cache_creation_input_tokens', 0)}", file=sys.stderr)
    print(f"output tokens:           {u.output_tokens}", file=sys.stderr)
    print(f"stop reason:             {msg.stop_reason}", file=sys.stderr)


if __name__ == "__main__":
    main()
