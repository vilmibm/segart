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


def title_match(a, b, threshold=0.85):
    na, nb = normalize_title(a), normalize_title(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    return SequenceMatcher(None, na, nb).ratio() >= threshold


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


def leaves_match(anchor_ranges, toc_ranges):
    if not anchor_ranges or not toc_ranges:
        return False
    return list(anchor_ranges[0]) == list(toc_ranges[0])


def find_hit(anchor, entries):
    a_leaves = anchor["leaf_ranges"]
    a_title = anchor.get("article_title")
    a_author = anchor.get("article_author")
    candidates = [
        e for e in entries if leaves_match(a_leaves, e.get("leaf_ranges"))
    ]
    for e in candidates:
        t = title_match(a_title, e.get("title"))
        au = author_match(a_author, e.get("authors"))
        if t and au:
            return e, {"leaves": True, "title": True, "author": True}
    if candidates:
        e = candidates[0]
        return e, {
            "leaves": True,
            "title": title_match(a_title, e.get("title")),
            "author": author_match(a_author, e.get("authors")),
        }
    return None, {"leaves": False, "title": False, "author": False}


def score_toc(corpus_anchors, toc):
    item = toc.get("item")
    entries = toc.get("entries") or []
    out = []
    for anchor in corpus_anchors:
        hit, reasons = find_hit(anchor, entries)
        out.append(
            {
                "item": item,
                "anchor_title": anchor.get("article_title"),
                "anchor_author": anchor.get("article_author"),
                "anchor_leaves": anchor.get("leaf_ranges"),
                "matched_entry_title": hit.get("title") if hit else None,
                "matched_entry_id": hit.get("id") if hit else None,
                "reasons": reasons,
                "full_hit": all(reasons.values()),
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
    counts = {"full": 0, "leaves": 0, "title": 0, "author": 0}
    for path in toc_paths:
        with open(path) as f:
            toc = json.load(f)
        item = toc.get("item")
        anchors = corpus.get(item, [])
        if not anchors:
            print(
                f"  no corpus anchors for {item} — skipping",
                file=sys.stderr,
            )
            continue
        for r in score_toc(anchors, toc):
            n_anchors += 1
            if r["full_hit"]:
                counts["full"] += 1
            for k in ("leaves", "title", "author"):
                if r["reasons"][k]:
                    counts[k] += 1
            fout.write(json.dumps(r, ensure_ascii=False) + "\n")

    if args.output:
        fout.close()
    if n_anchors:
        pct = lambda n: f"{n}/{n_anchors} ({n * 100 // n_anchors}%)"
        print(f"\nscored {n_anchors} anchors", file=sys.stderr)
        print(f"  full hit:  {pct(counts['full'])}", file=sys.stderr)
        print(f"  leaves:    {pct(counts['leaves'])}", file=sys.stderr)
        print(f"  title:     {pct(counts['title'])}", file=sys.stderr)
        print(f"  author:    {pct(counts['author'])}", file=sys.stderr)
    else:
        print("no anchors scored", file=sys.stderr)


if __name__ == "__main__":
    main()
