#!/usr/bin/env python3
"""Derive a corrected printed-page → leaf map from docling's page_header items.

Many issues' IA-supplied `page_numbers.json` is sparse or noisy because the
OCR pass that built it failed to detect the small page-number text in the
gutter. But docling captures running headers that often contain authoritative
page-range strings like "Journal Research Reading (1987), 10(1), 29-42" — and
those let us anchor leaf-to-printed-page calibration deterministically.

Strategy:
  1. Walk docling's `page_header` and `page_footer` items.
  2. Extract page-range tokens (`<start>-<end>` near the start or end of the
     header text), one anchor per leaf.
  3. Compute leaf↔printed-page offsets at each anchor; cluster.
  4. For the dominant offset zone, output a leaf→printed-page map.

Usage:
  ./repair_page_numbers.py <item> [--out <path>]
"""
import argparse
import gzip
import json
import re
import sys
from collections import Counter
from pathlib import Path

SEGART = Path("/Users/brewster/tmp/segart")
ITEMS = SEGART / "tmp" / "items"

# A page-range token: 1-4 digits, dash (any kind), 1-4 digits. Allow the dash
# to be hyphen, en-dash, em-dash. Require word boundary or end-of-line on
# both sides so we don't grab volume-issue tokens like "10(1)" or "1987".
PAGE_RANGE_RE = re.compile(r"(?<!\d)(\d{1,4})\s*[-–—]\s*(\d{1,4})(?!\d)")


def load_docling(item):
    p = ITEMS / item / f"{item}_docling.json.gz"
    if not p.exists(): return None
    with gzip.open(p, "rt") as fh:
        return json.load(fh)


def extract_anchors(doc):
    """Return list of (leaf, printed_page_start, printed_page_end, source_text)
    where each entry comes from a page_header / page_footer with a credible
    page-range token."""
    out = []
    for t in doc.get("texts") or []:
        label = t.get("label")
        if label not in ("page_header", "page_footer"): continue
        text = (t.get("text") or "").strip()
        if not text: continue
        prov = (t.get("prov") or [{}])[0]
        page_no = prov.get("page_no")
        if page_no is None: continue
        # docling page_no is 1-indexed; the legacy leaf coordinate is
        # 0-indexed (see segment_issue_docling.py:739 / llm_toc_to_legacy.py).
        leaf = page_no - 1
        # Find candidate page ranges. Reject tokens whose first num looks
        # like a year (4 digits starting with 19/20). Prefer ranges where
        # both numbers are <= 4 digits and end > start.
        for m in PAGE_RANGE_RE.finditer(text):
            s, e = int(m.group(1)), int(m.group(2))
            # Reject year-like tokens.
            if 1900 <= s <= 2100 or 1900 <= e <= 2100: continue
            if e < s or e - s > 200: continue  # implausible spans
            out.append({"leaf": leaf, "start_page": s, "end_page": e,
                        "text": text})
            break  # one per page_header item is plenty
    return out


def consistency_filter(anchors):
    """Drop anchors whose offset (leaf - start_page) is a singleton when
    other offsets have ≥2 support. If every offset is singleton, reject
    all anchors (the running headers are noise). With ≤1 anchor, accept
    as-is."""
    if len(anchors) <= 1:
        return anchors
    offsets = Counter(a["leaf"] - a["start_page"] for a in anchors)
    if max(offsets.values()) < 2:
        return []
    well = {o for o, c in offsets.items() if c >= 2}
    return [a for a in anchors if (a["leaf"] - a["start_page"]) in well]


def build_repaired_map(anchors, n_leaves):
    """Each anchor `(leaf_start, page_start, page_end)` covers a paginated
    zone whose offset is constant within the article. Different articles
    have different offsets because of unpaginated breaks between them.

    Strategy:
      1. Within each anchor's region, emit page→leaf with that anchor's offset.
      2. BETWEEN anchors, take the offset from whichever anchor is nearer
         (page-wise). This extrapolates printed pages on filler leaves.
      3. BEYOND the last anchor, keep extrapolating the last offset until
         the end of the issue (n_leaves).
      4. BEFORE the first anchor, keep extrapolating the first offset down
         to printed page 1 (or until leaf 0, whichever comes first).
    """
    if not anchors:
        return {}, {}, 0
    sorted_anchors = sorted(anchors, key=lambda a: a["start_page"])
    page_to_leaf, leaf_to_page = {}, {}

    # 1) Emit anchored zones.
    for a in sorted_anchors:
        for p in range(a["start_page"], a["end_page"] + 1):
            leaf = a["leaf"] + (p - a["start_page"])
            page_to_leaf.setdefault(str(p), leaf)
            leaf_to_page.setdefault(leaf, p)

    offsets_seen = {a["leaf"] - a["start_page"] for a in sorted_anchors}

    # 2) Between consecutive anchors, fill the gap. Use the LATER anchor's
    #    offset (filler leaves usually belong to the next article's run).
    for prev, nxt in zip(sorted_anchors, sorted_anchors[1:]):
        gap_start_page = prev["end_page"] + 1
        gap_end_page = nxt["start_page"] - 1
        if gap_end_page < gap_start_page: continue
        offset = nxt["leaf"] - nxt["start_page"]
        for p in range(gap_start_page, gap_end_page + 1):
            leaf = p + offset
            page_to_leaf.setdefault(str(p), leaf)
            leaf_to_page.setdefault(leaf, p)

    # 3) Beyond last anchor: extrapolate to end of issue.
    last = sorted_anchors[-1]
    last_offset = last["leaf"] - last["start_page"]
    leaf = last["leaf"] + (last["end_page"] - last["start_page"]) + 1
    p = last["end_page"] + 1
    while leaf < n_leaves and p < 10000:
        page_to_leaf.setdefault(str(p), leaf)
        leaf_to_page.setdefault(leaf, p)
        leaf += 1
        p += 1

    # 4) Before first anchor: extrapolate down to page 1 or leaf 0.
    first = sorted_anchors[0]
    first_offset = first["leaf"] - first["start_page"]
    p = first["start_page"] - 1
    leaf = first["leaf"] - 1
    while p >= 1 and leaf >= 0:
        page_to_leaf.setdefault(str(p), leaf)
        leaf_to_page.setdefault(leaf, p)
        p -= 1
        leaf -= 1

    return page_to_leaf, leaf_to_page, len(offsets_seen)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("item")
    ap.add_argument("--out")
    ap.add_argument("--show-anchors", action="store_true")
    args = ap.parse_args()

    doc = load_docling(args.item)
    if doc is None:
        sys.exit(f"no docling cache for {args.item}")

    raw_anchors = extract_anchors(doc)
    anchors = consistency_filter(raw_anchors)
    n_leaves = len(doc.get("pages") or {})
    print(f"item: {args.item}", file=sys.stderr)
    print(f"  total leaves: {n_leaves}", file=sys.stderr)
    print(f"  anchors raw: {len(raw_anchors)}  after consistency filter: {len(anchors)}",
          file=sys.stderr)

    if args.show_anchors or not anchors:
        for a in anchors:
            print(f"    leaf {a['leaf']:>3} → pp {a['start_page']}-{a['end_page']}"
                  f"   src: {a['text'][:70]!r}", file=sys.stderr)

    if not anchors:
        sys.exit("no anchors")
    page_to_leaf, leaf_to_page, n_zones = build_repaired_map(anchors, n_leaves)
    print(f"  derived {len(page_to_leaf)} printed-page entries across "
          f"{n_zones} pagination zone(s)", file=sys.stderr)
    print(f"  leaf range covered: {min(leaf_to_page)}–{max(leaf_to_page)}",
          file=sys.stderr)

    out_path = Path(args.out) if args.out else (
        ITEMS / args.item / f"{args.item}_page_numbers_repaired.json"
    )
    out_path.write_text(json.dumps({
        "item": args.item,
        "method": "docling_running_headers",
        "n_anchors": len(anchors),
        "n_pagination_zones": n_zones,
        "anchors": [
            {"leaf": a["leaf"], "start_page": a["start_page"],
             "end_page": a["end_page"], "src": a["text"][:100]}
            for a in anchors
        ],
        "page_to_leaf": page_to_leaf,
    }, indent=2))
    print(f"\nwrote {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
