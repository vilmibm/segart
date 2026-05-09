#!/usr/bin/env python3
"""Score a segart-produced <item>_toc.json against ILL ground-truth anchors.

For each ILL anchor in the corpus matching the TOC's item, decide whether
the TOC contains an entry that matches on (leaf range, title, author).

Match criteria for v0:
  - leaves: TOC entry's first leaf_ranges pair == anchor's first pair (exact)
  - title: normalized exact match, with Levenshtein-ratio fallback (>=0.85)
  - author: at least one surname overlap between anchor and entry

Usage:
  ./score_toc.py --corpus /tmp/qa_corpus.jsonl --toc <item>_toc.json
  ./score_toc.py --corpus /tmp/qa_corpus.jsonl --toc-dir tocs/
"""
import argparse
import json
import os
import re
import sys
from difflib import SequenceMatcher

STOPWORDS = {
    "the", "a", "an", "of", "and", "in", "on", "for", "to",
    "la", "le", "les", "der", "die", "das", "el", "los", "il",
}


def normalize_title(s):
    if not s:
        return ""
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(w for w in s.split() if w not in STOPWORDS)


def title_match(a, b,
                token_overlap=0.6, ratio_threshold=0.7,
                short_min_words=2):
    """Search-style title match: would a user looking for `a` be happy
    with `b` in the result list?

    Strategy:
      - Normalize both, drop stopwords.
      - PASS if one is contained in the other (any direction).
      - PASS if shared content words / shorter content words ≥
        token_overlap (e.g. anchor and entry share ≥60% of the smaller
        side's meaningful words). Catches "Pedagogic Hegemonicide"
        matching the full "Pedagogic Hegemonicide and the Asian
        American Student" plus messy OCR truncations.
      - PASS if SequenceMatcher ratio ≥ ratio_threshold (loosened from
        0.85 since we're optimizing for "find the article" not byte
        equality).

    Tight enough that an unrelated article won't match (default
    short_min_words=2 prevents 1-word coincidences).
    """
    na, nb = normalize_title(a), normalize_title(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    wa, wb = na.split(), nb.split()
    if len(wa) < short_min_words or len(wb) < short_min_words:
        return False
    short, long_ = (na, nb) if len(na) <= len(nb) else (nb, na)
    if f" {short} " in f" {long_} " or long_.startswith(short + " ") or long_.endswith(" " + short):
        return True
    sa, sb = set(wa), set(wb)
    shared = sa & sb
    smaller = min(len(sa), len(sb))
    if smaller > 0 and len(shared) / smaller >= token_overlap:
        return True
    return SequenceMatcher(None, na, nb).ratio() >= ratio_threshold


def extract_surnames(s):
    """Pull plausible surnames from a free-form author string.

    ILL authors come in two Western conventions:
      surname-first comma-separated: 'Miller, G E', 'Wallin, J',
        'Pounds, L A', 'Patten, Dennis M.,'
      given-name-first:               'George E. Miller', 'Lois A. Pounds',
        'Henk J. Haarmann, Marcel Adam Just, Patricia A. Ca'

    Multiple authors can be separated by `;` or ` and ` or `&`.
    """
    if not s:
        return set()
    surnames = set()
    # Author boundary: ;, ` and `, &  (NOT comma — comma is used inside
    # surname-first names)
    for piece in re.split(r"\s*;\s*|\s+and\s+|\s*&\s*", s):
        piece = piece.strip()
        if not piece:
            continue
        if "," in piece:
            # Surname-first: take everything before the first comma.
            surname_text = piece.split(",", 1)[0]
            words = re.findall(r"[A-Z][a-zA-Z'\u00c0-\u017f-]+", surname_text)
            if words:
                surnames.add(words[0].lower())
        else:
            # Given-first: surname is the last capitalized multi-letter word.
            words = re.findall(r"[A-Z][a-zA-Z'\u00c0-\u017f-]+", piece)
            long_words = [w for w in words if len(w) >= 3]
            chosen = (long_words or words)
            if chosen:
                surnames.add(chosen[-1].lower())
    return surnames


def author_match(anchor_author, toc_authors):
    anchor_set = extract_surnames(anchor_author or "")
    if not anchor_set:
        return False
    for a in toc_authors or []:
        toc_set = extract_surnames(a.get("name") or "")
        if anchor_set & toc_set:
            return True
    return False


import re as _re


def _leaf_int(s):
    if not isinstance(s, str):
        return None
    m = _re.match(r"n(\d+)$", s)
    return int(m.group(1)) if m else None


def _ranges(d):
    """Read either the v2 `page_index_ranges` or legacy `leaf_ranges` from
    a TOC entry or qa_corpus anchor. Returns the list (possibly empty).
    The two names are aliases for the same coordinate (IA's accessible
    page-index `nN` strings)."""
    if d is None: return []
    return d.get("page_index_ranges") or d.get("leaf_ranges") or []


def leaves_match(anchor_ranges, toc_ranges):
    """Strict: exact equality on the first range pair."""
    if not anchor_ranges or not toc_ranges:
        return False
    return list(anchor_ranges[0]) == list(toc_ranges[0])


def leaves_match_soft(anchor_ranges, toc_ranges, start_tol=1):
    """Soft: start leaves within ±start_tol. End leaf is intentionally
    NOT checked — the segmenter computes end as 'next-article-start − 1',
    which routinely overshoots the true end by many leaves when an
    intervening article wasn't detected. The article identity is
    determined by start position; end is bookkeeping.
    """
    if not anchor_ranges or not toc_ranges:
        return False
    a0 = anchor_ranges[0]
    t0 = toc_ranges[0]
    a_s = _leaf_int(a0[0])
    t_s = _leaf_int(t0[0])
    if a_s is None or t_s is None:
        return False
    return abs(a_s - t_s) <= start_tol


def end_leaf_offset(anchor_ranges, toc_ranges):
    """Signed end-leaf offset (toc_end − anchor_end). Positive means
    segmenter overshoots past the article. None if either is missing."""
    if not anchor_ranges or not toc_ranges:
        return None
    a_e = _leaf_int(anchor_ranges[0][-1])
    t_e = _leaf_int(toc_ranges[0][-1])
    if a_e is None or t_e is None:
        return None
    return t_e - a_e


def findable(anchor, entries, start_tol=1, end_overshoot=2):
    """Practical "is this article findable in the TOC + would the segmenter
    cut a usable PDF excerpt?" check.

    Pass criteria:
      - some TOC entry's (title OR author) matches the anchor
      - that entry's start leaf is within ±start_tol of the anchor's start
      - that entry's end leaf is between anchor_end + 0 and anchor_end +
        end_overshoot inclusive (overshoot OK up to a couple leaves;
        undershoot fails because the article is cut short)

    Returns dict: {"hit": bool, "n_content_candidates": int,
                   "n_passing": int, "ambiguous": bool}
    """
    a_leaves = _ranges(anchor)
    a_title = anchor.get("article_title")
    a_author = anchor.get("article_author")
    if not a_leaves:
        return {"hit": False, "n_content_candidates": 0, "n_passing": 0, "ambiguous": False}
    a_s = _leaf_int(a_leaves[0][0])
    a_e = _leaf_int(a_leaves[0][-1])
    if a_s is None:
        return {"hit": False, "n_content_candidates": 0, "n_passing": 0, "ambiguous": False}

    candidates = [
        e for e in entries
        if title_match(a_title, e.get("title"))
        or author_match(a_author, e.get("authors"))
    ]
    n_pass = 0
    for e in candidates:
        e_leaves = _ranges(e)
        if not e_leaves: continue
        e_s = _leaf_int(e_leaves[0][0])
        if e_s is None or abs(e_s - a_s) > start_tol: continue
        if a_e is not None:
            e_e = _leaf_int(e_leaves[0][-1])
            if e_e is None: continue
            end_off = e_e - a_e
            if end_off < 0 or end_off > end_overshoot: continue
        n_pass += 1
    return {
        "hit": n_pass >= 1,
        "n_content_candidates": len(candidates),
        "n_passing": n_pass,
        "ambiguous": n_pass >= 2,
    }


def find_hit(anchor, entries, start_tol=1):
    """Pick the best entry for `anchor`.

    Preference order:
      1. exact leaves + title + author  (`exact` hit)
      2. soft leaves + title + author   (`soft` hit — start within ±1)
      3. exact-leaves entry, even if title/author miss  (segmenter put a
         wrong title at the right place — useful diagnostic)
      4. content-only match: title + author match an entry whose leaves
         are off (segmenter found the article but on the wrong page)
      5. nothing                                     (`miss`)
    """
    a_leaves = _ranges(anchor)
    a_title = anchor.get("article_title")
    a_author = anchor.get("article_author")

    exact = [e for e in entries if leaves_match(a_leaves, _ranges(e))]
    soft = [
        e for e in entries
        if leaves_match_soft(a_leaves, _ranges(e), start_tol)
    ]

    for e in exact:
        if title_match(a_title, e.get("title")) and author_match(a_author, e.get("authors")):
            return e, {
                "match": "exact",
                "leaves_strict": True,
                "leaves_soft": True,
                "title": True,
                "author": True,
            }
    for e in soft:
        if title_match(a_title, e.get("title")) and author_match(a_author, e.get("authors")):
            return e, {
                "match": "soft",
                "leaves_strict": leaves_match(a_leaves, _ranges(e)),
                "leaves_soft": True,
                "title": True,
                "author": True,
            }
    if exact:
        e = exact[0]
        return e, {
            "match": "leaves_only",
            "leaves_strict": True,
            "leaves_soft": True,
            "title": title_match(a_title, e.get("title")),
            "author": author_match(a_author, e.get("authors")),
        }
    # Content-only fallback: scan all entries
    for e in entries:
        if title_match(a_title, e.get("title")) and author_match(a_author, e.get("authors")):
            return e, {
                "match": "content_only",
                "leaves_strict": False,
                "leaves_soft": False,
                "title": True,
                "author": True,
            }
    return None, {
        "match": "miss",
        "leaves_strict": False,
        "leaves_soft": False,
        "title": False,
        "author": False,
    }


def score_toc(corpus_anchors, toc):
    item = toc.get("item")
    entries = toc.get("entries") or []
    out = []
    for anchor in corpus_anchors:
        hit, reasons = find_hit(anchor, entries)
        end_off = end_leaf_offset(
            _ranges(anchor),
            _ranges(hit) if hit else None,
        )
        out.append(
            {
                "item": item,
                "anchor_title": anchor.get("article_title"),
                "anchor_author": anchor.get("article_author"),
                "anchor_page_index_ranges": _ranges(anchor),
                "matched_entry_title": hit.get("title") if hit else None,
                "matched_entry_authors": hit.get("authors") if hit else None,
                "matched_entry_page_index_ranges": _ranges(hit) if hit else None,
                "matched_entry_id": hit.get("id") if hit else None,
                "reasons": reasons,
                "match": reasons["match"],
                "full_hit": reasons["match"] in ("exact", "soft"),
                # End-leaf offset (segmenter_end − anchor_end). Matters
                # for PDF excerpt quality: positive means we overshoot
                # (PDF includes next article); negative means we
                # undershoot (PDF cuts the article short).
                "end_offset": end_off,
            }
        )
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--corpus", required=True, help="QA corpus JSONL")
    p.add_argument("--toc", help="Single TOC file to score")
    p.add_argument("--toc-dir", help="Directory of <item>_toc.json files")
    p.add_argument(
        "-o",
        "--output",
        help="Per-anchor results JSONL (default stdout)",
    )
    args = p.parse_args()

    if not (args.toc or args.toc_dir):
        p.error("--toc or --toc-dir required")

    corpus = {}
    with open(args.corpus) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            corpus[r["identifier"]] = r["anchors"]

    toc_paths = []
    if args.toc:
        toc_paths.append(args.toc)
    if args.toc_dir:
        for fn in os.listdir(args.toc_dir):
            if fn.endswith("_toc.json"):
                toc_paths.append(os.path.join(args.toc_dir, fn))

    fout = open(args.output, "w") if args.output else sys.stdout
    n_anchors = 0
    n_entries_total = 0
    n_entries_matched = 0
    match_counts = {"exact": 0, "soft": 0, "leaves_only": 0, "content_only": 0, "miss": 0}
    field_counts = {"leaves_strict": 0, "leaves_soft": 0, "title": 0, "author": 0}
    findable_counts = {"hit": 0, "ambiguous": 0}
    end_offsets = []
    for path in toc_paths:
        with open(path) as f:
            toc = json.load(f)
        item = toc.get("item")
        n_entries_total += len(toc.get("entries") or [])
        anchors = corpus.get(item, [])
        if not anchors:
            print(
                f"  no corpus anchors for {item} — skipping",
                file=sys.stderr,
            )
            continue
        matched_ids = set()
        entries = toc.get("entries") or []
        for anchor, r in zip(anchors, score_toc(anchors, toc)):
            n_anchors += 1
            match_counts[r["match"]] += 1
            for k in field_counts:
                if r["reasons"].get(k):
                    field_counts[k] += 1
            if r["matched_entry_id"] and r["match"] in ("exact", "soft", "content_only"):
                matched_ids.add(r["matched_entry_id"])
            if r.get("end_offset") is not None and r["match"] in ("exact", "soft", "content_only"):
                end_offsets.append(r["end_offset"])
            f_res = findable(anchor, entries)
            r["findable"] = f_res
            if f_res["hit"]: findable_counts["hit"] += 1
            if f_res["ambiguous"]: findable_counts["ambiguous"] += 1
            fout.write(json.dumps(r, ensure_ascii=False) + "\n")
        n_entries_matched += len(matched_ids)

    if args.output:
        fout.close()
    if n_anchors:
        pct = lambda n: f"{n}/{n_anchors} ({n * 100 // n_anchors}%)"
        full_n = match_counts["exact"] + match_counts["soft"]
        print(f"\nscored {n_anchors} anchors over {n_entries_total} TOC entries", file=sys.stderr)
        print(f"  hit (exact|soft):       {pct(full_n)}", file=sys.stderr)
        print(f"  findable:               {pct(findable_counts['hit'])}  "
              f"(ambiguous: {findable_counts['ambiguous']})", file=sys.stderr)
        print(f"    exact leaves:         {match_counts['exact']}", file=sys.stderr)
        print(f"    soft leaves (±1):     {match_counts['soft']}", file=sys.stderr)
        print(f"  leaves-only (no content): {match_counts['leaves_only']}", file=sys.stderr)
        print(f"  content-only (wrong leaf): {match_counts['content_only']}", file=sys.stderr)
        print(f"  miss:                   {match_counts['miss']}", file=sys.stderr)
        print(f"  field hits:", file=sys.stderr)
        print(f"    leaves_strict: {pct(field_counts['leaves_strict'])}", file=sys.stderr)
        print(f"    leaves_soft:   {pct(field_counts['leaves_soft'])}", file=sys.stderr)
        print(f"    title:         {pct(field_counts['title'])}", file=sys.stderr)
        print(f"    author:        {pct(field_counts['author'])}", file=sys.stderr)
        if n_entries_total:
            print(
                f"  TOC entries matched ≥1 anchor: {n_entries_matched}/{n_entries_total} "
                f"({n_entries_matched * 100 // n_entries_total}%)",
                file=sys.stderr,
            )
        if end_offsets:
            ends_within = sum(1 for o in end_offsets if abs(o) <= 1)
            ends_within3 = sum(1 for o in end_offsets if abs(o) <= 3)
            over = sum(1 for o in end_offsets if o > 3)
            under = sum(1 for o in end_offsets if o < -3)
            n = len(end_offsets)
            print(f"  end-leaf accuracy (matched only):", file=sys.stderr)
            print(f"    within ±1 leaf: {ends_within}/{n} ({ends_within*100//n}%)", file=sys.stderr)
            print(f"    within ±3 leaf: {ends_within3}/{n} ({ends_within3*100//n}%)", file=sys.stderr)
            print(f"    overshoot >3:   {over}/{n} ({over*100//n}%)  (PDF too long)", file=sys.stderr)
            print(f"    undershoot <-3: {under}/{n} ({under*100//n}%)  (PDF too short)", file=sys.stderr)
    else:
        print("no anchors scored", file=sys.stderr)


if __name__ == "__main__":
    main()
