#!/usr/bin/env python3
"""segart segmenter v0.2 — heuristic article-start detection from hOCR font sizes.

Approach (no LLM call required):
  1. Pull <item>_hocr.html and <item>_page_numbers.json via the `ia` CLI
     (raw HTTP gets 403s on most periodical items).
  2. For each leaf, parse hOCR words with their bounding boxes.
  3. Detect "article title pages" by font size: a leaf is an article start
     iff it has a cluster of unusually-large words in its upper half.
  4. For each title-page leaf, take the largest font cluster + any
     contiguous medium-large continuation block as the article title.
     Then look for a byline ("by ...") immediately below for authors.
  5. The article runs from its start leaf to (next title-page leaf − 1).
  6. Emit <item>_toc.json per docs/toc_format.md.

This is the pure-heuristic v0; an LLM pass to clean up titles and parse
authors is the natural v1, once an API key is wired up. On the segart
TOC schema: each entry is marked `evidence: ["ocr"]` and given a
moderate confidence (the heuristic is good but noisy).

Caveats of this v0:
  - Doesn't separate articles that share a title page (e.g. paired essays).
  - Author parsing is regex-based; multi-author bylines often get all
    surnames but lose the first/middle names.
  - End-of-article boundary is "next title page minus one"; pages of
    references/figures at the very end of an article go to that article
    correctly, but advertisement spreads break the boundary.

Usage:
  ./segment_issue.py sim_academic-medicine_1989-02_64_2
  ./segment_issue.py <item> -o /tmp/foo.json
"""
import argparse
import json
import os
import re
import statistics
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

CACHE = Path(os.environ.get("SEGART_CACHE", "/tmp/segart_items"))
SCHEMA_VERSION = 1
SEGMENTER_VERSION = "0.2"

PAGE_RE = re.compile(
    r"<div class=['\"]ocr_page['\"]\s+id=['\"]page_(\d+)['\"]"
)
WORD_RE = re.compile(
    r"<span class=['\"]ocrx_word['\"]\s+[^>]*"
    r"title=['\"]bbox\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)[^'\"]*['\"]\s*>"
    r"([^<]*)</span>"
)


def fetch_derive_files(item, cache_dir):
    """Pull hOCR and page_numbers.json into the cache via `ia`."""
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
            ["ia", "download", item, "--glob", glob, "--destdir", str(cache_dir)],
            check=True, capture_output=True,
        )
    return item_dir


def parse_hocr(hocr_path):
    """Return [(leaf_num, words[])] where each word is (x1,y1,x2,y2,text)."""
    text = open(hocr_path, encoding="utf-8").read()
    starts = list(PAGE_RE.finditer(text))
    pages = []
    for i, m in enumerate(starts):
        # IA hOCR `id="page_NNNNNN"` is 0-indexed (matches IA URL `nN`).
        leaf = int(m.group(1))
        end = starts[i + 1].start() if i + 1 < len(starts) else len(text)
        chunk = text[m.start():end]
        words = [
            (int(a), int(b), int(c), int(d), t)
            for a, b, c, d, t in WORD_RE.findall(chunk)
        ]
        pages.append((leaf, words))
    return pages


def cluster_lines(words, y_tol=25):
    """Group words into lines by y-coordinate. Return [(avg_y, avg_h, words)]."""
    if not words:
        return []
    sw = sorted(words, key=lambda w: w[1])
    lines = []
    current = [sw[0]]
    for w in sw[1:]:
        if abs(w[1] - current[-1][1]) <= y_tol:
            current.append(w)
        else:
            lines.append(current)
            current = [w]
    lines.append(current)
    out = []
    for line in lines:
        line.sort(key=lambda w: w[0])
        avg_y = sum(w[1] for w in line) / len(line)
        avg_h = sum(w[3] - w[1] for w in line) / len(line)
        out.append((avg_y, avg_h, line))
    return out


def detect_title_blocks(words):
    """Return ordered list of "big-font blocks" in the upper half of the page.

    Each block is a contiguous run of lines where every line's avg height
    exceeds 1.6 * the page's median word height. The biggest such block is
    typically the article title (with optional subtitle continuation).
    """
    if not words:
        return []
    heights = [w[3] - w[1] for w in words if w[3] > w[1]]
    if not heights:
        return []
    med = statistics.median(heights)
    page_h = max(w[3] for w in words)
    upper = [w for w in words if w[1] <= page_h // 2]
    lines = cluster_lines(upper)
    threshold = 1.6 * med
    blocks = []
    current = []
    last_y = None
    for avg_y, avg_h, line_words in lines:
        if avg_h >= threshold:
            # Start a new block if the gap from the previous big line is too
            # large to be normal line spacing — otherwise section headers
            # higher on the page get merged into the article title block.
            if current and (avg_y - last_y) > 150:
                blocks.append(current)
                current = []
            current.append((avg_y, avg_h, line_words))
            last_y = avg_y
        elif current:
            blocks.append(current)
            current = []
            last_y = None
    if current:
        blocks.append(current)
    return [
        {
            "y_start": min(ln[0] for ln in blk),
            "y_end": max(ln[0] for ln in blk),
            "avg_height": sum(ln[1] for ln in blk) / len(blk),
            "max_height": max(ln[1] for ln in blk),
            "lines": blk,
            "text": " ".join(w[4] for ln in blk for w in ln[2]),
        }
        for blk in blocks
    ]


def pick_title_block(blocks):
    """Of all title blocks, the one with the highest avg height is the title."""
    if not blocks:
        return None
    return max(blocks, key=lambda b: b["avg_height"])


def words_below(words, y_floor, y_ceiling):
    """Return words whose y is in (y_floor, y_ceiling]."""
    return [w for w in words if y_floor < w[1] <= y_ceiling]


CRED_RE = re.compile(
    r"\b(?:M\.?\s*D\.?|Ph\.?\s*D\.?|MA|MS|MPH|MSc|R\.?\s*N\.?|D\.?\s*O\.?|RD|"
    r"PhD|Sc\.?\s*D\.?|Dr\.?|Jr\.?|Sr\.?|II|III|FRCP|FRCS|FACP|MHA|EdD|DSc)"
    r"\.?\b",
    re.IGNORECASE,
)


def parse_author_string(author_str):
    """Split a byline string into a list of {name, affiliation}."""
    parts = re.split(r"\s*,\s*|\s+and\s+|\s*&\s*", author_str)
    out = []
    for part in parts:
        name = CRED_RE.sub("", part).strip(" ,.;-")
        if name and len(name) > 1 and any(c.isalpha() for c in name):
            # Title-case if the byline was all caps (common journal layout)
            if name.isupper():
                name = name.title()
            out.append({"name": name, "affiliation": None})
    return out


def extract_byline(words, after_y, before_y):
    """Find the byline in the lines immediately below the title block.

    Bylines vary by layout — some say `by Lois A. Pounds, M.D.` and some
    just print `LOIS A. POUNDS, M.D.` in slightly-larger-than-body font.
    Strategy: read the next few lines below the title, take the first one
    that looks byline-shaped (capitalized words and/or credentials).
    """
    region = sorted(
        [w for w in words if after_y < w[1] <= before_y],
        key=lambda w: (w[1], w[0]),
    )
    if not region:
        return []
    # Cluster into lines (small y-tolerance, ~30px)
    lines = []
    current = [region[0]]
    for w in region[1:]:
        if abs(w[1] - current[-1][1]) < 30:
            current.append(w)
        else:
            lines.append(current)
            current = [w]
    lines.append(current)

    for line in lines[:3]:
        text = " ".join(w[4] for w in line).strip()
        if not text:
            continue
        # Pattern A: "by NAME, NAME, NAME"
        m = re.search(r"\bby\s+(.+?)$", text, re.IGNORECASE)
        if m:
            authors = parse_author_string(m.group(1))
            if authors:
                return authors
        # Pattern B: line of mostly-capitalized words including a credential
        caps = [w for w in line if w[4][:1].isupper() or w[4].isupper()]
        has_cred = any(CRED_RE.search(w[4]) for w in line)
        if has_cred and len(caps) >= 2 and len(caps) / max(len(line), 1) >= 0.6:
            authors = parse_author_string(text)
            if authors:
                return authors
    return []


def page_to_leaf_map(pn_path):
    data = json.load(open(pn_path))
    m = {}
    for entry in data.get("pages", []):
        ppage = (entry.get("pageNumber") or "").strip()
        leaf = entry.get("leafNum")
        if ppage and leaf is not None:
            m.setdefault(ppage, leaf)
    return m, data


def leaf_to_page_map(pn_path):
    """Inverse of page_to_leaf — given a leaf, what printed page does it have?"""
    data = json.load(open(pn_path))
    out = {}
    for entry in data.get("pages", []):
        ppage = (entry.get("pageNumber") or "").strip() or None
        leaf = entry.get("leafNum")
        if leaf is not None:
            out[leaf] = ppage
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("item", help="IA item identifier")
    p.add_argument("-o", "--output", help="Output path (default <item>_toc.json)")
    p.add_argument("--cache-dir", default=str(CACHE))
    p.add_argument(
        "--min-leaf", type=int, default=2,
        help="Skip leaves before this index (cover/ad/TOC; default 2)",
    )
    p.add_argument(
        "--verbose", "-v", action="store_true",
        help="Print per-leaf detection details",
    )
    args = p.parse_args()

    cache_dir = Path(args.cache_dir)
    item_dir = fetch_derive_files(args.item, cache_dir)
    hocr_path = item_dir / f"{args.item}_hocr.html"
    pn_path = item_dir / f"{args.item}_page_numbers.json"

    pages = parse_hocr(hocr_path)
    leaf_count = len(pages)
    leaf_to_page = leaf_to_page_map(pn_path)

    # Per-leaf candidate detection
    candidates = []
    for leaf, words in pages:
        if leaf < args.min_leaf:
            continue
        blocks = detect_title_blocks(words)
        title_block = pick_title_block(blocks)
        if title_block is None:
            if args.verbose:
                print(f"  n{leaf}: no title block", file=sys.stderr)
            continue
        # Reject pages where the biggest block is short text (likely a header
        # like "ACADEMIC MEDICINE")
        words_in_block = sum(len(ln[2]) for ln in title_block["lines"])
        if words_in_block < 2:
            continue
        title_text = title_block["text"]
        # Skip pages whose biggest block is too small (just a folio number)
        if len(title_text) < 8:
            continue
        # Pull byline from below the title (search ~600px below the block end)
        authors = extract_byline(
            words, after_y=title_block["y_end"], before_y=title_block["y_end"] + 600
        )
        candidates.append({
            "leaf": leaf,
            "title": title_text,
            "title_height": title_block["max_height"],
            "authors": authors,
            "printed_page": leaf_to_page.get(leaf),
        })
        if args.verbose:
            print(
                f"  n{leaf} (page {leaf_to_page.get(leaf)}): "
                f"h={title_block['max_height']:.0f} {title_text[:80]!r}"
                + (f" authors={[a['name'] for a in authors]}" if authors else ""),
                file=sys.stderr,
            )

    # Filter: the median title_height across all candidates is a sanity bar.
    # Drop candidates whose height is well below the median (probably folios
    # or section headers, not real title pages).
    if candidates:
        med_h = statistics.median(c["title_height"] for c in candidates)
        candidates = [c for c in candidates if c["title_height"] >= 0.85 * med_h]

    candidates.sort(key=lambda c: c["leaf"])

    # Build TOC entries: each candidate is an article from its leaf to
    # the next candidate's leaf - 1
    entries = []
    for i, c in enumerate(candidates):
        start = c["leaf"]
        end = (
            candidates[i + 1]["leaf"] - 1
            if i + 1 < len(candidates)
            else leaf_count - 1
        )
        if end < start:
            end = start
        entries.append({
            "id": f"e{i+1}",
            "type": "article",
            "title": c["title"],
            "authors": c["authors"] if c["authors"] else None,
            "leaf_ranges": [[f"n{start}", f"n{end}"]],
            "printed_pages": c["printed_page"],
            "ext_ids": {},
            "confidence": 0.55,
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
            "method": "hocr-fontsize-titlepage",
        },
        "entries": entries,
    }
    out_path = args.output or f"{args.item}_toc.json"
    with open(out_path, "w") as f:
        json.dump(toc, f, indent=2)
    print(f"  wrote {out_path}: {len(entries)} entries", file=sys.stderr)


if __name__ == "__main__":
    main()
