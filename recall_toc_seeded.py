#!/usr/bin/env python3
"""
TOC-seeded recall pass: for each entry in docling's document_index that
no existing segart body entry matches, search the document body for the
title text and emit a new entry with `evidence: ["toc_seeded", ...]`.

Why: the body-walk segmenter (segment_issue_docling.py) emits a
candidate only when a header-shaped item is followed by a byline-shaped
item. Articles whose byline got mis-OCR'd, hyphenated, or split into a
different docling element are dropped. But the issue's own printed
TOC names them — so we can take each TOC entry and go find it in the
body, applying a more permissive search than the body-walk uses.

Pipeline order:
    segment_issue_docling.py   (body-walk produces base entries)
    augment_evidence.py        (adds in_issue_toc / crossref_match flags)
    recall_toc_seeded.py       (this script)

Usage:
    ./recall_toc_seeded.py --toc-dir tmp/tocs --items-dir tmp/items
    ./recall_toc_seeded.py --toc tmp/tocs/sim_xyz_toc.json
"""
import argparse
import gzip
import json
import os
import re
import sys
from pathlib import Path

# ---------------------------------------------------------- title matching --

STOP = set("a an the of for in on to and or but with by at from as is are was "
           "were be that this if".split())
WORD = re.compile(r"[a-z0-9]+")


def norm(s):
    return [w for w in WORD.findall((s or "").lower())
            if w not in STOP and len(w) > 2]


def title_match(a, b, threshold=0.5):
    wa, wb = set(norm(a)), set(norm(b))
    if not wa or not wb:
        return False
    return len(wa & wb) / max(1, min(len(wa), len(wb))) >= threshold


def title_match_strict(a, b, threshold=0.7):
    """Stricter version used for body-text recall — TOC titles are noisy
    enough that we want a high-overlap match before believing a body
    text item is the article's title."""
    wa, wb = set(norm(a)), set(norm(b))
    if not wa or not wb:
        return False
    if len(wa) < 3 or len(wb) < 3:
        return False
    return len(wa & wb) / max(1, min(len(wa), len(wb))) >= threshold


# -------------------------------------------------- docling TOC extraction --

PG_RE = re.compile(r"(?<!\d)(\d{1,4})(?!\d)")


def y_top(t):
    bb = (t.get("prov") or [{}])[0].get("bbox") or {}
    return bb.get("t") or 0


def in_bbox(t_bb, T_bb):
    if not t_bb or not T_bb:
        return False
    t_top = max(t_bb.get("t") or 0, t_bb.get("b") or 0)
    t_bot = min(t_bb.get("t") or 0, t_bb.get("b") or 0)
    T_top = max(T_bb.get("t") or 0, T_bb.get("b") or 0)
    T_bot = min(T_bb.get("t") or 0, T_bb.get("b") or 0)
    return t_bot >= T_bot - 5 and t_top <= T_top + 5


def is_pn_token(s):
    if not s:
        return False
    m = PG_RE.fullmatch(s.strip())
    if not m:
        return False
    return 1 <= int(m.group(1)) <= 2000


# Lettered page-number tokens (e.g., "234A", "Cover 3") are the
# tell-tale signature of advertiser indices — paid listings of which
# advertiser appears on which "ad page" — that medical journals print
# on or near their TOC pages.
ADVERT_INDEX_RE = re.compile(r"\b\d+\s*[A-Z]\b")
# Person-name byline fragments alone aren't articles.
NAME_RE = re.compile(r"^[A-Z][a-z]+\.?\s+[A-Z]\.?\s+[A-Z][a-z]+", re.UNICODE)


def looks_like_article_toc_entry(s):
    """Filter for TOC strings worth seeding a recall search from. Drops:
      - too-short fragments (< 4 content words)
      - advertiser-index patterns ("234A, Cover 3, 12B")
      - bare author-name lines ("Maeona K. Jacobs, R.N., Ph.D.")
      - all-caps blobs (mostly company-name lists in ad indices)
    """
    if not s:
        return False
    cw = norm(s)
    if len(cw) < 4:
        return False
    if len(ADVERT_INDEX_RE.findall(s)) >= 2:
        return False
    # Author-name-only line: starts with a name and is short.
    if NAME_RE.match(s.strip()) and len(s.split()) <= 8:
        return False
    # All-caps blob (>=70% of letters are upper-case): typical of
    # advertiser-index company-name listings.
    letters = [c for c in s if c.isalpha()]
    if letters and sum(1 for c in letters if c.isupper()) / len(letters) > 0.85:
        return False
    return True


def _cluster_pages(pages, gap=1):
    """Group sorted page numbers into contiguous runs. `gap=1` means a
    one-page gap between detected TOC tables still counts as the same
    cluster (allowing for an interleaved blank or non-TOC page)."""
    if not pages:
        return []
    pages = sorted(set(pages))
    runs, cur = [], [pages[0], pages[0]]
    for p in pages[1:]:
        if p - cur[1] <= gap:
            cur[1] = p
        else:
            runs.append(cur); cur = [p, p]
    runs.append(cur)
    return runs


def _bbox_x(t):
    bb = (t.get("prov") or [{}])[0].get("bbox") or {}
    return bb.get("l") or 0, bb.get("r") or 0


def _anchored_rows(tbl, by_page):
    """Yield (title_string, declared_page_str, declared_page_int) for each
    TOC row in `tbl` that's anchored by a right-edge page-number token.

    Anchoring rule: a TOC entry exists only if there's a small-integer
    item at the right edge of the table region (the page-number column),
    AND there is title-shaped text at lower x in the same y-row OR in
    rows that follow until the next anchor. Rows without a right-edge
    page-number are NOT entries — they're ad copy, masthead, or
    spillover from a different visual section co-residing in the
    same docling document_index region.

    This is the key change from the previous flat extraction that pulled
    every text item in the table region: items without a right-column
    page-number anchor are no longer treated as TOC entries, eliminating
    the bulk of the ads-on-TOC-page noise seen in earlier runs."""
    prov = (tbl.get("prov") or [{}])[0]
    page = prov.get("page_no")
    T_bb = prov.get("bbox")
    if page is None or not T_bb:
        return
    cands = [
        t for t in by_page.get(page, [])
        if in_bbox((t.get("prov") or [{}])[0].get("bbox"), T_bb)
        and t.get("content_layer") != "furniture"
    ]
    if not cands:
        return

    T_r = T_bb.get("r") or 0
    pg_col_left = T_r - 80  # right ~80 units = page-number column

    # Group into y-rows; cluster by y_top within ~12 units (typical line height).
    cands.sort(key=lambda t: -y_top(t))
    rows = []
    cur_row = []
    last_y = None
    for t in cands:
        y = y_top(t)
        if last_y is None or abs(last_y - y) <= 12:
            cur_row.append(t)
        else:
            if cur_row:
                rows.append(cur_row)
            cur_row = [t]
        last_y = y
    if cur_row:
        rows.append(cur_row)

    # Find anchor rows (rows containing a right-column page-number token)
    # and collect the title-text items between consecutive anchors.
    anchored = []
    pending_titles = []
    for row in rows:
        row_sorted = sorted(row, key=lambda t: _bbox_x(t)[0])
        anchor_pn = None
        title_parts = []
        for t in row_sorted:
            tx = (t.get("text") or "").strip()
            if not tx:
                continue
            x_l, _ = _bbox_x(t)
            if x_l >= pg_col_left and is_pn_token(tx):
                anchor_pn = tx
                continue
            if not re.search(r"[A-Za-z]{3,}", tx):
                continue
            title_parts.append(tx)
        if anchor_pn is not None:
            # Real TOC row. Combine anchor's title (this row + any
            # continuation lines that came BEFORE the next anchor).
            # Wait — the spec is simpler: the anchor's title is the
            # left-column items in THE SAME y-row AS the anchor,
            # plus any subsequent rows without their own anchor.
            combined = title_parts + pending_titles
            if combined:
                s = re.sub(r"\s+", " ",
                           " ".join(p.strip() for p in combined)).strip()
                if len(s) > 5 and re.search(r"[A-Za-z]{3,}", s):
                    try:
                        anchored.append((s, anchor_pn, int(anchor_pn)))
                    except ValueError:
                        pass
            pending_titles = []
        else:
            # No anchor: this row is title continuation for the NEXT
            # entry below (if any). In a top-down walk, we accumulate
            # these and attach to the next anchored row.
            pending_titles.extend(title_parts)

    for entry in anchored:
        yield entry


def _entries_in_table(tbl, by_page):
    """Backwards-compatible wrapper: yield (title, src_page) pairs for
    cluster-scoring code that doesn't care about the page-number anchor."""
    page = ((tbl.get("prov") or [{}])[0]).get("page_no")
    for title, _pn_str, _pn_int in _anchored_rows(tbl, by_page):
        yield title, page


CONTENTS_HEADING_RE = re.compile(
    r"\b(?:contents|table\s+of\s+contents)\b", re.IGNORECASE,
)


def _has_contents_heading(by_page, pages):
    """True if any text item on `pages` (looking near the top of each
    page, where TOC headings sit) reads 'Contents' or 'Table of
    Contents'. English-language journals overwhelmingly mark their TOC
    page this way; non-English would need translated equivalents."""
    for p in pages:
        for t in by_page.get(p, []):
            if t.get("content_layer") == "furniture":
                continue
            tx = (t.get("text") or "").strip()
            if CONTENTS_HEADING_RE.search(tx) and len(tx) <= 60:
                return True
    return False


def _monotonic_score(page_ints):
    """Fraction of consecutive pairs in page_ints that are non-decreasing.
    A clean TOC has near-1.0 (page numbers list articles in order); a
    cluster of unrelated numbers from ad copy is closer to 0.5."""
    if len(page_ints) < 2:
        return 0.0
    ok = sum(1 for a, b in zip(page_ints, page_ints[1:]) if a <= b)
    return ok / (len(page_ints) - 1)


def extract_toc_entries(d, min_entries=3):
    """Return (entries, toc_pages) for the canonical TOC cluster.

    Strategy: docling tags multiple regions as `document_index` per
    item — sometimes a real multi-page TOC, often interleaved with
    advertiser indices and per-chapter mini-TOCs. We cluster the
    `document_index` page numbers by adjacency, then pick the cluster
    with the strongest TOC signal: most page-number-anchored entries,
    bonus for monotonically-increasing anchor pages (ad copy and
    advertiser indices have unordered numbers), bonus for an explicit
    'Contents' heading on the page (English-language journals print
    one). Publisher-position-agnostic — works for front-of-book TOCs,
    back-of-book TOCs, and multi-page TOCs alike. Skips the item if no
    cluster has at least `min_entries` anchored entries."""
    by_page = {}
    for t in d.get("texts", []):
        prov = (t.get("prov") or [{}])[0]
        pn = prov.get("page_no")
        if pn is None:
            continue
        by_page.setdefault(pn, []).append(t)

    tables_by_page = {}
    for tbl in d.get("tables", []):
        if tbl.get("label") != "document_index":
            continue
        pn = (tbl.get("prov") or [{}])[0].get("page_no")
        if pn is not None:
            tables_by_page.setdefault(pn, []).append(tbl)
    runs = _cluster_pages(list(tables_by_page.keys()))
    if not runs:
        return [], set()

    best_run = None
    best_score = 0.0
    best_entries = []
    for first, last in runs:
        run_pages = [p for p in tables_by_page if first <= p <= last]
        anchored = []  # list of (title, pn_str, pn_int, src_page)
        for p in run_pages:
            for tbl in tables_by_page[p]:
                for s, pn_str, pn_int in _anchored_rows(tbl, by_page):
                    if looks_like_article_toc_entry(s):
                        anchored.append((s, pn_str, pn_int, p))
        if not anchored:
            continue
        n_anchored = len(anchored)
        mono = _monotonic_score([a[2] for a in anchored])
        # Score: each anchored entry counts 1; monotonicity adds up to
        # the same total again (so a fully-ordered sequence ≈ doubles
        # the cluster's weight); explicit "Contents" heading adds a
        # flat 5 (enough to break ties in favor of the labeled page).
        heading_bonus = 5.0 if _has_contents_heading(by_page, run_pages) else 0.0
        score = n_anchored * (1.0 + mono) + heading_bonus
        if score > best_score:
            best_score = score
            best_run = (first, last)
            best_entries = [(s, p) for s, _, _, p in anchored]

    if not best_entries or len(best_entries) < min_entries:
        return [], set()

    toc_pages = set(p for p in tables_by_page if best_run[0] <= p <= best_run[1])
    return best_entries, toc_pages


# ------------------------------------------------ body search for a title --

HEADER_LABELS = {"section_header", "title", "paragraph_header"}


def find_title_in_body(d, title, toc_pages, exclude_pages):
    """Search docling text items for one whose text fuzzy-matches `title`.

    Skips items inside `toc_pages` (so TOC-region text doesn't self-match)
    and items already used by another recall match (`exclude_pages` is a
    set of (page_no, idx) tuples).

    Returns (page_no, text_idx, matched_text) or None.
    """
    best = None  # (overlap_ratio, page_no, text_idx, matched_text)
    title_words = set(norm(title))
    if not title_words:
        return None
    for idx, t in enumerate(d.get("texts", [])):
        if t.get("content_layer") == "furniture":
            continue
        prov = (t.get("prov") or [{}])[0]
        page = prov.get("page_no")
        if page is None:
            continue
        if page in toc_pages:
            continue
        if (page, idx) in exclude_pages:
            continue
        # Prefer header-labeled items but allow text-labeled fallbacks
        # for cases where docling missed the header tag.
        label = str(t.get("label") or "")
        is_header = label in HEADER_LABELS
        text = (t.get("text") or "").strip()
        if not text:
            continue
        text_words = set(norm(text))
        if not text_words:
            continue
        inter = len(title_words & text_words)
        denom = min(len(title_words), len(text_words))
        if denom == 0:
            continue
        overlap = inter / denom
        # Stricter threshold for non-header items to avoid matching
        # random body paragraphs that happen to share words.
        threshold = 0.7 if is_header else 0.85
        if overlap < threshold:
            continue
        # Bias toward earliest page on ties.
        score = (overlap, -page if is_header else -(page + 1000))
        if best is None or score > best[0]:
            best = (score, page, idx, text)
    if best is None:
        return None
    return best[1], best[2], best[3]


# ------------------------------------------------------ augment a TOC file --

def recall_one(toc_path, items_dir):
    toc = json.load(open(toc_path))
    item = toc.get("item")
    if not item:
        return None
    docling_path = os.path.join(items_dir, item, f"{item}_docling.json.gz")
    if not os.path.exists(docling_path):
        return None
    with gzip.open(docling_path, "rt", encoding="utf-8") as fh:
        d = json.load(fh)

    toc_entries, toc_pages = extract_toc_entries(d)
    if not toc_entries:
        return toc, 0  # nothing to seed from

    existing = toc.get("entries", []) or []
    existing_titles = [e.get("title") or "" for e in existing]

    # Identify TOC entries already covered by a body entry.
    used = set()  # text indices we've already matched, to avoid double-emission
    new_entries = []
    seen_norm = {tuple(sorted(norm(e.get("title") or ""))) for e in existing}

    leaf_count = toc.get("leaf_count") or 0

    for tt, toc_page in toc_entries:
        # Skip if ANY existing entry already matches this TOC title.
        if any(title_match(tt, et) for et in existing_titles):
            continue
        # Skip degenerate / too-short titles (e.g., 'AAMC FOCUS')
        if len(norm(tt)) < 2:
            continue
        # Look in the body
        loc = find_title_in_body(d, tt, toc_pages, used)
        if not loc:
            continue
        page_no, idx, matched_text = loc
        used.add((page_no, idx))

        # Match segart's existing leaf convention: leaf = docling page_no - 1.
        start_leaf = max(0, page_no - 1)
        end_leaf = max(start_leaf, leaf_count - 1) if leaf_count else start_leaf
        # Use the cleaner of (TOC string, body matched text). Body text
        # tends to have less concatenated noise.
        title = matched_text if len(matched_text) >= 8 else tt
        title = re.sub(r"\s+", " ", title).strip()

        # Avoid duplicate emission if normalized title already seen
        sig = tuple(sorted(norm(title)))
        if sig in seen_norm:
            continue
        seen_norm.add(sig)

        new_entries.append({
            "id": f"r{len(new_entries) + 1}",  # 'r' prefix for recall-seeded
            "type": "article",
            "title": title,
            "authors": None,
            "leaf_ranges": [[f"n{start_leaf}", f"n{end_leaf}"]],
            "printed_pages": None,
            "ext_ids": {},
            "confidence": 0.5,
            "evidence": ["toc_seeded", "in_issue_toc"],
            "level": 1,
        })

    if not new_entries:
        return toc, 0

    # Insert recall-seeded entries in leaf order alongside existing entries.
    all_entries = list(existing) + new_entries
    # Sort by start-leaf for stable ordering downstream
    def _start_leaf(e):
        lr = e.get("leaf_ranges") or [[None, None]]
        s = lr[0][0] if lr and lr[0] else None
        try:
            return int((s or "n0").lstrip("n"))
        except ValueError:
            return 0
    all_entries.sort(key=_start_leaf)
    # Re-tighten end leaves so each entry's end is the next entry's start - 1
    for i, e in enumerate(all_entries):
        s = _start_leaf(e)
        if i + 1 < len(all_entries):
            ns = _start_leaf(all_entries[i + 1])
            new_end = max(s, ns - 1)
        else:
            new_end = max(s, leaf_count - 1) if leaf_count else s
        lr = e.get("leaf_ranges") or [[None, None]]
        if lr and lr[0]:
            lr[0][1] = f"n{new_end}"
    toc["entries"] = all_entries
    return toc, len(new_entries)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--toc-dir", help="Directory of <item>_toc.json files")
    p.add_argument("--toc", help="Single TOC file")
    p.add_argument("--items-dir",
                   default=str(Path.home() / "tmp" / "segart" / "tmp" / "items"))
    args = p.parse_args()
    if not args.toc and not args.toc_dir:
        p.error("must supply --toc or --toc-dir")

    paths = [args.toc] if args.toc else sorted(
        Path(args.toc_dir).glob("*_toc.json"))
    n_files = 0
    n_added = 0
    for path in paths:
        result = recall_one(str(path), args.items_dir)
        if result is None:
            continue
        toc, n_new = result
        json.dump(toc, open(path, "w"), indent=2)
        n_files += 1
        n_added += n_new

    print(f"recall pass: {n_files} TOC files, +{n_added} TOC-seeded entries",
          file=sys.stderr)


if __name__ == "__main__":
    main()
