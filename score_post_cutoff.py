#!/usr/bin/env python3
"""Score TOC quality against post-2024-04 ILL anchors for items we've
actually processed (have docling caches for).

Builds anchor list directly from raw ILL CSVs (filtered to post-2024-04,
deduped by (identifier, title, leaf_ranges)), then scores each anchor
against either the heuristic TOC or the LLM TOC (or both) using the
same matching rules score_toc.py uses.

Usage:
    ./score_post_cutoff.py [--variant heur|llm|both]
"""
import argparse
import csv
import glob
import json
import os
import re
import sys
from datetime import datetime, timezone
from difflib import SequenceMatcher

CUTOFF = int(datetime(2024, 4, 1, tzinfo=timezone.utc).timestamp())
SEGART = "/Users/brewster/tmp/segart"

STOPWORDS = {
    "the", "a", "an", "of", "and", "in", "on", "for", "to",
    "la", "le", "les", "der", "die", "das", "el", "los", "il",
}

WORD = re.compile(r"[a-z0-9]+")


def normalize_title(s):
    if not s: return ""
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(w for w in s.split() if w not in STOPWORDS)


def title_match(a, b):
    """Fuzzy title comparison: containment, token-overlap, or ratio match."""
    na, nb = normalize_title(a), normalize_title(b)
    if not na or not nb: return False
    if na in nb or nb in na: return True
    ta, tb = set(na.split()), set(nb.split())
    if not ta or not tb: return False
    overlap = len(ta & tb) / max(1, min(len(ta), len(tb)))
    if overlap >= 0.6: return True
    return SequenceMatcher(None, na, nb).ratio() >= 0.7


def surnames(s):
    """Extract surname tokens from an author string."""
    if not s: return set()
    out = set()
    # "Smith, J" → "Smith"
    for chunk in re.split(r"[;,&]| and ", s):
        chunk = chunk.strip().strip(".")
        if not chunk: continue
        # Heuristic: pick the longest alphabetic token ≥3 chars
        tokens = [t.strip(".") for t in chunk.split()]
        long_tokens = [t for t in tokens if t.isalpha() and len(t) >= 3]
        if long_tokens:
            out.add(max(long_tokens, key=len).lower())
    return out


def author_match(anchor_author, entry_authors):
    sa = surnames(anchor_author)
    if not sa: return False
    se = set()
    for a in entry_authors or []:
        se |= surnames(a if isinstance(a, str) else a.get("name", ""))
    return bool(sa & se)


LEAF_RE = re.compile(r"^n(\d+)$")


def nint(s):
    if s is None: return None
    m = LEAF_RE.match(str(s).strip())
    return int(m.group(1)) if m else None


def leaves_match(a_lr, e_lr, tol=1):
    """a/e_lr are leaf_ranges lists like [["n34","n72"]]. Compare first pair
    on numeric leaf indices, allowing ±tol on start and ±tol on end."""
    if not a_lr or not e_lr: return False, False
    a0, a1 = nint(a_lr[0][0]), nint(a_lr[0][1])
    e0, e1 = nint(e_lr[0][0]), nint(e_lr[0][1])
    if a0 is None or e0 is None: return False, False
    strict = (a0 == e0 and a1 == e1)
    soft = (abs(a0 - e0) <= tol and abs(a1 - e1) <= tol)
    return strict, soft


def llm_to_legacy_lr(entry):
    """Convert an llm_toc entry's start_leaf/end_leaf (1-indexed ints) to
    legacy leaf_ranges using the -1 offset convention."""
    sl = max(0, int(entry["start_leaf"]) - 1)
    el = max(sl, int(entry["end_leaf"]) - 1)
    return [[f"n{sl}", f"n{el}"]]


def load_toc_entries(path, kind):
    """Return a list of normalized entries with leaf_ranges + title +
    authors regardless of source schema."""
    if not os.path.exists(path): return None
    d = json.load(open(path))
    out = []
    if kind == "llm":
        for e in d.get("entries") or []:
            out.append({
                "title": e.get("title"),
                "authors": [a.get("name") for a in (e.get("authors") or [])],
                "leaf_ranges": llm_to_legacy_lr(e),
                "start_leaf_int": e.get("start_leaf"),
                "end_leaf_int": e.get("end_leaf"),
                "start_page": e.get("start_page"),
                "end_page": e.get("end_page"),
            })
    else:
        for e in d.get("entries") or []:
            out.append({
                "title": e.get("title"),
                "authors": [a.get("name") for a in (e.get("authors") or [])
                            if isinstance(a, dict)] or e.get("authors"),
                "leaf_ranges": e.get("leaf_ranges") or [],
                "printed_pages": e.get("printed_pages"),
            })
    return out


def find_match(anchor, entries):
    """Best-effort match: try strict leaves, then soft leaves, then content."""
    a_lr = anchor["leaf_ranges"]
    a_title = anchor.get("article_title")
    a_author = anchor.get("article_author")
    # 1. Exact leaves
    for e in entries:
        s, _ = leaves_match(a_lr, e["leaf_ranges"], tol=0)
        if s:
            return e, "exact"
    # 2. Soft leaves
    for e in entries:
        _, sf = leaves_match(a_lr, e["leaf_ranges"], tol=1)
        if sf:
            return e, "soft"
    # 3. Title-only (rare but happens)
    if a_title:
        for e in entries:
            if title_match(a_title, e["title"]):
                return e, "title_only"
    return None, "miss"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--variant", choices=("heur", "llm", "both"), default="both")
    args = ap.parse_args()

    # Items with docling caches
    processed = set()
    for dpath in sorted(glob.glob(f"{SEGART}/tmp/items/*/")):
        item = os.path.basename(dpath.rstrip("/"))
        if os.path.exists(f"{dpath}{item}_docling.json.gz"):
            processed.add(item)

    # Build deduped post-2024-04 anchors from raw CSVs
    anchors = {}  # key → anchor record
    for path in sorted(glob.glob(f"{SEGART}/tmp/ill_logs/*.csv")):
        with open(path, newline='') as fh:
            for row in csv.DictReader(fh):
                ident = row.get("source_identifier") or ""
                if ident not in processed: continue
                t = row.get("time")
                if not t or not t.isdigit() or int(t) < CUTOFF: continue
                try:
                    ff = json.loads(row.get("full_form") or "{}")
                except json.JSONDecodeError:
                    continue
                if not (ff.get("start") and ff.get("stop")): continue
                # Pick leaf-shaped pair (raw or normalized_orig_*).
                raw_s, raw_e = ff.get("start") or [], ff.get("stop") or []
                norm_s, norm_e = ff.get("normalized_orig_start") or [], ff.get("normalized_orig_stop") or []
                def all_leaves(arr): return arr and all(LEAF_RE.match(str(x).strip()) for x in arr)
                if all_leaves(raw_s) and all_leaves(raw_e) and len(raw_s) == len(raw_e):
                    starts, stops = raw_s, raw_e
                elif all_leaves(norm_s) and all_leaves(norm_e) and len(norm_s) == len(norm_e):
                    starts, stops = norm_s, norm_e
                else:
                    continue  # skip rows without a clean leaf pair
                params = ff.get("original_request_params") or {}
                title = (params.get("article_title") or "").strip() or None
                author = (params.get("article_author") or "").strip() or None
                lr = [[s, e] for s, e in zip(starts, stops)]
                key = (ident, title, tuple(tuple(p) for p in lr))
                if key in anchors: continue
                anchors[key] = {
                    "identifier": ident,
                    "article_title": title,
                    "article_author": author,
                    "journal_pages": (params.get("journal_pages") or "").strip() or None,
                    "leaf_ranges": lr,
                }

    print(f"unique post-2024-04 anchors for processed items: {len(anchors)}",
          file=sys.stderr)

    # Score
    variants = ["heur", "llm"] if args.variant == "both" else [args.variant]
    results = {v: {"hit": 0, "exact": 0, "soft": 0, "title_only": 0,
                   "miss": 0, "title_field": 0, "author_field": 0,
                   "items_with_toc": 0, "items_total": 0} for v in variants}
    by_item = {v: {} for v in variants}

    items_seen = set()
    for anchor in anchors.values():
        items_seen.add(anchor["identifier"])

    for variant in variants:
        suffix = "_toc.json" if variant == "heur" else "_toc_llm.json"
        for it in items_seen:
            results[variant]["items_total"] += 1
            path = f"{SEGART}/tmp/tocs/{it}{suffix}"
            entries = load_toc_entries(path, variant)
            by_item[variant][it] = entries
            if entries is not None:
                results[variant]["items_with_toc"] += 1

    for anchor in anchors.values():
        for variant in variants:
            entries = by_item[variant].get(anchor["identifier"])
            if entries is None:
                results[variant]["miss"] += 1
                continue
            match, kind = find_match(anchor, entries)
            results[variant][kind] += 1
            if kind in ("exact", "soft"):
                results[variant]["hit"] += 1
            if match and anchor.get("article_title") and match.get("title") \
                    and title_match(anchor["article_title"], match["title"]):
                results[variant]["title_field"] += 1
            if match and anchor.get("article_author") \
                    and author_match(anchor["article_author"], match.get("authors")):
                results[variant]["author_field"] += 1

    # Print scoreboard
    print(f"\n{'='*72}")
    print(f"{'variant':<8} {'items':>6} {'TOC':>5} {'hits':>9} {'exact':>6} {'soft':>5} {'title':>5} {'auth':>5} {'miss':>5}")
    print(f"{'-'*72}")
    n_anch = len(anchors)
    for v in variants:
        r = results[v]
        coverage = f"{r['items_with_toc']}/{r['items_total']}"
        hit_pct = 100*r['hit']//max(n_anch,1)
        print(f"{v:<8} {r['items_total']:>6} {coverage:>5}  "
              f"{r['hit']:>3}/{n_anch:<3} ({hit_pct:>2}%)  "
              f"{r['exact']:>5} {r['soft']:>5} "
              f"{r['title_field']:>5} {r['author_field']:>5} "
              f"{r['miss']:>5}")
    print()
    if "llm" in variants:
        # Per-item LLM coverage
        llm_anchors_only = [a for a in anchors.values() if by_item["llm"].get(a["identifier"])]
        print(f"LLM TOC available for {len({a['identifier'] for a in llm_anchors_only})} items "
              f"({len(llm_anchors_only)} of {n_anch} anchors)")
        # Recompute LLM stats restricted to its 7 items
        llm_only = {"hit": 0, "exact": 0, "soft": 0, "title_only": 0, "miss": 0,
                    "title_field": 0, "author_field": 0}
        heur_on_same = {"hit": 0, "exact": 0, "soft": 0, "title_only": 0, "miss": 0,
                        "title_field": 0, "author_field": 0}
        for anchor in llm_anchors_only:
            for variant, dest in (("llm", llm_only), ("heur", heur_on_same)):
                entries = by_item[variant].get(anchor["identifier"]) or []
                match, kind = find_match(anchor, entries)
                dest[kind] += 1
                if kind in ("exact", "soft"): dest["hit"] += 1
                if match and anchor.get("article_title") and match.get("title") \
                        and title_match(anchor["article_title"], match["title"]):
                    dest["title_field"] += 1
                if match and anchor.get("article_author") \
                        and author_match(anchor["article_author"], match.get("authors")):
                    dest["author_field"] += 1
        n = len(llm_anchors_only)
        print(f"\n=== restricted to the {len({a['identifier'] for a in llm_anchors_only})} items where LLM TOC exists ===")
        print(f"{'variant':<8} {'hits':>10} {'exact':>6} {'soft':>5} {'title':>5} {'auth':>5}")
        for v, dest in (("heur", heur_on_same), ("llm", llm_only)):
            print(f"{v:<8} {dest['hit']:>3}/{n:<3} ({100*dest['hit']//max(n,1):>3}%) "
                  f"{dest['exact']:>5} {dest['soft']:>5} "
                  f"{dest['title_field']:>5} {dest['author_field']:>5}")


if __name__ == "__main__":
    main()
