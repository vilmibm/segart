#!/usr/bin/env python3
"""Post-filter a segart `<item>_toc.json` to drop obvious non-articles.

`segment_issue_docling.py` extracts every (section_header, byline) pair on
each page. That picks up real articles plus mastheads, ads, indexes, and
section labels — anything where docling labels a chunk as section_header
and a name-shaped string follows below.

This filter applies cheap rules to drop false positives without re-running
docling. Iteration is fast (pure-Python, milliseconds per item), so we can
tune rules empirically against the QA corpus.

Filters applied (in order, each independent):
  1. drop_label: title is a short all-caps phrase (typical section label
     like "FEATURES", "AAMC FOCUS", "COMPUTERS").
  2. drop_front_matter: first --front-matter-frac of leaves are skipped
     (covers, masthead, contributor lists, ads up front).
  3. drop_back_matter_index: trailing entries that all have short
     all-caps titles (back-of-book index categories).
  4. drop_editorial_author: byline matches editor-ish patterns.
  5. drop_advert: title looks like a slogan (contains '!' or words like
     ASKING, BUY, FREE, ORDER, CALL).

Usage:
  ./filter_toc.py tmp/tocs/<item>_toc.json -o tmp/tocs/<item>_toc.filtered.json
  ./filter_toc.py --in-place tmp/tocs/*_toc.json
"""
import argparse
import json
import re
import sys
from pathlib import Path

STOPWORDS = {"the", "a", "an", "of", "and", "in", "on", "for", "to", "or", "by", "from", "with", "at", "as", "is"}

ADVERT_HINTS = re.compile(
    r"\b(ASKING|BUY|ORDER|CALL|FREE|MAIL|SAVE|WRITE TO|PLEASE SEND|SUBSCRIBE|"
    r"\$\d|YOUR PATIENT|LOOK FOR|WE ACCEPT)\b"
)
# Pharma / drug-ad telltales appearing in the "byline" position (which is
# really the lede of an ad, not an author line). Hits common section
# headers in advertorial inserts: ADVERSE REACTIONS, INDICATIONS,
# DOSAGE, PRECAUTIONS, CONTRAINDICATIONS, HOW SUPPLIED, COMPOSITION,
# WARNINGS, ®/™ trademark glyphs.
AD_BYLINE_HINTS = re.compile(
    r"\b(ADVERSE\s+REACTIONS?|INDICATIONS?|DOSAGE\s+AND\s+ADMIN|PRECAUTIONS?|"
    r"CONTRAINDICATIONS?|HOW\s+SUPPLIED|PRESCRIBING\s+INFORMATION|COMPOSITION|"
    r"WARNINGS?|N\.?F\.?\s+Units|MG\.?\s*PER|TABLETS|CAPSULES)\b"
    r"|[®™©]"
)
EDITOR_HINTS = re.compile(
    r"\b(editor|editorial|editor-in-chief|managing|publisher|"
    r"chairman|board|administration)\b",
    re.IGNORECASE,
)
PRINTED_PAGE_RE = re.compile(r"^[ivxlc]+$|^\d+$", re.IGNORECASE)

# Plausible name shapes — at least one must match for a string to count as
# a real author. Mirrors NAME_SHAPE_RES in segment_issue_docling.py but
# kept local so this filter is independent.
AUTHOR_NAME_SHAPES = [
    re.compile(r"\b[A-Z][a-z]+\s+[A-Z]\.\s*[A-Z][a-z]+\b"),
    re.compile(r"\b[A-Z]{2,}\s+[A-Z]\.?\s*[A-Z]{2,}\b"),
    re.compile(r"\b[A-Z][a-z]+,\s+[A-Z](?:\s|\.|$)"),
    re.compile(r"\b[A-Z]{2,},\s+[A-Z](?:\s|\.|$)"),
    # Two adjacent capitalized words at the START of the string. Anchored
    # so coincidental "Duke University" inside body text doesn't count.
    re.compile(r"^[A-Z][a-z]+\s+[A-Z][a-z]+\b"),
    # OCR-tolerant ("Anthony 5. Joyce") at the start.
    re.compile(r"^[A-Z][a-z]+\s+\S{1,4}\s+[A-Z][a-z]+\b"),
]


def normalize_ws(s):
    return re.sub(r"\s+", " ", s).strip()


def content_words(title):
    """Words excluding stopwords/punctuation. Used to count title 'meat'."""
    words = re.findall(r"[A-Za-z][A-Za-z'\-]*", title)
    return [w for w in words if w.lower() not in STOPWORDS]


def is_short_label(title):
    """True if title looks like a section/category label, not an article.

    Criteria:
      - All letters are uppercase (or contain no lowercase), AND
      - ≤ 4 content words (excluding stopwords), AND
      - Has no terminal punctuation (?, !, .)
      - Length under 50 chars.

    Examples that match: 'COMPUTERS', 'AAMC FOCUS', 'INVITED ARTICLES',
      'MEDICAL EDUCATION IN OTHER COUNTRIES' (4 content words: MEDICAL,
      EDUCATION, OTHER, COUNTRIES).
    Examples that don't: 'Beyond Florence Nightingale: ...' (mixed case),
      'AAMC DIRECTORY OF AMERICAN MEDICAL EDUCATION 1988-89' (has digits
      bumping length over 50), 'Rural Health Care: ...' (mixed case).
    """
    t = normalize_ws(title)
    if not t or len(t) >= 50:
        return False
    if any(c.islower() for c in t):
        return False
    cw = content_words(t)
    if len(cw) > 4:
        return False
    if t.endswith(("?", "!", ".")):
        return False
    return True


def is_advert_slogan(title):
    if "!" in title or "?" in title:
        # Promotional copy; real article titles rarely have terminal !
        if ADVERT_HINTS.search(title.upper()):
            return True
    return ADVERT_HINTS.search(title.upper()) is not None


def author_looks_editorial(authors):
    if not authors:
        return False
    for a in authors:
        name = a.get("name") or ""
        if EDITOR_HINTS.search(name):
            return True
    return False


def authors_look_pharma_ad(authors):
    """True if the byline text reads like the lede of a pharma ad
    (PRECAUTIONS:, ADVERSE REACTIONS:, ®, etc.). The segmenter byline
    heuristic occasionally captures these as 'authors'."""
    if not authors:
        return False
    for a in authors:
        name = (a.get("name") or "").strip()
        if not name:
            continue
        if AD_BYLINE_HINTS.search(name):
            return True
    return False


def authors_look_fake(authors):
    """True if every parsed author looks like leaked body text, not a name.

    The byline detector occasionally captures a paragraph of body text
    when docling labels a section_header as the start of an article-like
    block. We can spot those after-the-fact: real bylines have at least
    one author whose name is short (≤60 chars) and matches a
    name-shaped regex; body text breaks both rules.
    """
    if not authors:
        return False
    for a in authors:
        name = (a.get("name") or "").strip()
        if not name:
            continue
        if len(name) > 60:
            continue
        for r in AUTHOR_NAME_SHAPES:
            if r.search(name):
                return False
    return True


def leaf_int(leaf_str):
    """`'n23'` → 23. Returns None if unparseable."""
    if not leaf_str:
        return None
    m = re.match(r"n(\d+)", leaf_str)
    return int(m.group(1)) if m else None


def has_real_author(authors):
    """True if at least one author has a clean name-shaped string and is
    not editor-shaped. Used as a "trust override": a header that looks
    label-shaped but is followed by a real byline is more likely a real
    article in an all-caps journal layout than a section label."""
    if not authors:
        return False
    for a in authors:
        name = (a.get("name") or "").strip()
        if not name or len(name) > 60:
            continue
        if EDITOR_HINTS.search(name):
            continue
        for r in AUTHOR_NAME_SHAPES:
            if r.search(name):
                return True
    return False


def filter_entries(entries, leaf_count, args):
    front_cutoff = int(leaf_count * args.front_matter_frac)
    back_cutoff = int(leaf_count * (1 - args.back_matter_index_frac))

    kept, dropped = [], []
    for e in entries:
        reason = None
        title = e.get("title") or ""
        start_leaf = leaf_int(e.get("leaf_ranges", [[None]])[0][0])
        real_author = has_real_author(e.get("authors"))

        # is_short_label gets overridden when a real-author byline sits
        # under it — many journals print article titles in all-caps and
        # those would otherwise be misclassified as section labels.
        if is_short_label(title) and not real_author:
            reason = "label"
        elif is_advert_slogan(title):
            reason = "advert"
        elif author_looks_editorial(e.get("authors")):
            reason = "editorial"
        elif authors_look_pharma_ad(e.get("authors")):
            reason = "pharma_ad"
        elif authors_look_fake(e.get("authors")):
            reason = "fake_byline"
        elif start_leaf is not None and start_leaf < front_cutoff:
            reason = "front_matter"
        elif (
            start_leaf is not None
            and start_leaf >= back_cutoff
            and is_short_label(title)
            and not real_author
        ):
            # back-matter rejection only fires if title is label-shaped
            # AND no real byline; legitimate back-of-book articles stay.
            reason = "back_matter_index"

        if reason:
            dropped.append((e, reason))
        else:
            kept.append(e)

    # After filtering, extend each kept entry's first leaf range to end at
    # the leaf before the next kept entry's start (or leaf_count-1 for the
    # last). This corrects ranges that were truncated by intervening
    # entries that turned out to be false positives.
    kept.sort(key=lambda e: leaf_int(e["leaf_ranges"][0][0]) or 0)
    for i, e in enumerate(kept):
        if not e.get("leaf_ranges"):
            continue
        first = list(e["leaf_ranges"][0])
        start = leaf_int(first[0])
        if start is None:
            continue
        if i + 1 < len(kept):
            next_start = leaf_int(kept[i + 1]["leaf_ranges"][0][0])
            new_end = (next_start - 1) if next_start is not None else None
        else:
            new_end = max(leaf_count - 1, start)
        if new_end is not None and new_end >= start:
            first[1] = f"n{new_end}"
            e["leaf_ranges"] = [first] + list(e["leaf_ranges"][1:])

    return kept, dropped


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("inputs", nargs="+", help="One or more <item>_toc.json files")
    p.add_argument("-o", "--output", help="Output path (single input only)")
    p.add_argument("--in-place", action="store_true",
                   help="Overwrite each input with its filtered version")
    p.add_argument("--suffix", default=".filtered",
                   help="Suffix added to output basename when not in-place "
                        "and no -o (default '.filtered')")
    p.add_argument("--front-matter-frac", type=float, default=0.05,
                   help="Drop entries whose start leaf is in the first N "
                        "fraction of the issue (default 0.05)")
    p.add_argument("--back-matter-index-frac", type=float, default=0.10,
                   help="Drop label-like entries whose start leaf is in the "
                        "last N fraction (default 0.10)")
    p.add_argument("--verbose", "-v", action="store_true")
    args = p.parse_args()

    if args.output and len(args.inputs) > 1:
        p.error("--output only valid with a single input")

    total_kept = total_dropped = total_in = 0
    by_reason = {}
    for path in args.inputs:
        toc = json.load(open(path))
        leaf_count = toc.get("leaf_count") or 0
        entries = toc.get("entries") or []
        kept, dropped = filter_entries(entries, leaf_count, args)
        total_in += len(entries)
        total_kept += len(kept)
        total_dropped += len(dropped)
        for _, reason in dropped:
            by_reason[reason] = by_reason.get(reason, 0) + 1

        toc["entries"] = kept
        gen = toc.setdefault("generator", {})
        gen["filtered"] = True
        gen["filter_version"] = "0.1"

        if args.in_place:
            out_path = path
        elif args.output:
            out_path = args.output
        else:
            stem, ext = path.rsplit(".", 1) if "." in path else (path, "json")
            out_path = f"{stem}{args.suffix}.{ext}"

        with open(out_path, "w") as f:
            json.dump(toc, f, indent=2)

        if args.verbose:
            print(
                f"  {Path(path).name}: kept {len(kept)}/{len(entries)} "
                f"(dropped {len(dropped)}) → {out_path}",
                file=sys.stderr,
            )
            for e, reason in dropped:
                print(f"    [{reason}] {e['leaf_ranges'][0][0]}-{e['leaf_ranges'][0][1]}: "
                      f"{e['title'][:70]!r}", file=sys.stderr)

    print(
        f"\n  total entries in: {total_in}, kept: {total_kept}, "
        f"dropped: {total_dropped}",
        file=sys.stderr,
    )
    if by_reason:
        print("  drop reasons:", file=sys.stderr)
        for r, n in sorted(by_reason.items(), key=lambda x: -x[1]):
            print(f"    {r}: {n}", file=sys.stderr)


if __name__ == "__main__":
    main()
