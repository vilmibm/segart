#!/usr/bin/env python3
"""Cross-check TOCs against post-2024-04 ILL anchors.

Two views from one matching engine:

  1. Fulfillment lookup (predictive). For each ILL anchor (article_title +
     journal_pages + author from a real patron request), what does our TOC
     return as the leaf range? Compare to the human staffer's answer.
     Tells us: "if this were in production, what fraction of ILL requests
     would be auto-fulfilled at correct leaves?"

  2. ILL-confirmed entries (validation). For each TOC entry, attach
     evidence from any anchor that matches it. Entries with `ill_evidence`
     are gold — title/author/leaves all externally confirmed. The rest are
     LLM-only.

Outputs:
  - tmp/tocs/<item>_toc_<variant>_xref.json: original TOC with each entry
    augmented by an `ill_evidence` list (empty if no anchor confirms it).
  - stdout: summary tables (fulfillment-recall + entry-confirmation rates).
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
from collections import defaultdict

CUTOFF = int(datetime(2024, 4, 1, tzinfo=timezone.utc).timestamp())
SEGART = "/Users/brewster/tmp/segart"

LEAF_RE = re.compile(r"^n(\d+)$")
STOPWORDS = {"the", "a", "an", "of", "and", "in", "on", "for", "to",
             "la", "le", "les", "der", "die", "das", "el", "los", "il"}


def nint(s):
    if s is None: return None
    m = LEAF_RE.match(str(s).strip())
    return int(m.group(1)) if m else None


def normalize_title(s):
    if not s: return ""
    s = re.sub(r"[^a-z0-9]+", " ", s.lower())
    return " ".join(w for w in s.split() if w not in STOPWORDS)


def title_similarity(a, b):
    """Returns a similarity score in [0, 1]."""
    na, nb = normalize_title(a), normalize_title(b)
    if not na or not nb: return 0.0
    if na == nb: return 1.0
    if na in nb or nb in na: return 0.95
    ta, tb = set(na.split()), set(nb.split())
    overlap = len(ta & tb) / max(1, min(len(ta), len(tb)))
    ratio = SequenceMatcher(None, na, nb).ratio()
    return max(overlap, ratio)


def title_match(a, b, threshold=0.6):
    return title_similarity(a, b) >= threshold


def surnames(s):
    if not s: return set()
    out = set()
    for chunk in re.split(r"[;,&]| and ", s):
        tokens = [t.strip(" .'\"-") for t in chunk.split()]
        long_tokens = [t for t in tokens if t.isalpha() and len(t) >= 3]
        if long_tokens:
            out.add(max(long_tokens, key=len).lower())
    return out


def author_match(anchor_author, entry_authors):
    sa = surnames(anchor_author)
    if not sa: return False
    se = set()
    for a in entry_authors or []:
        se |= surnames(a if isinstance(a, str) else (a.get("name") or ""))
    return bool(sa & se)


def leaf_overlap(a_lr, e_lr, tol=1):
    """Returns ('exact', 'soft', or 'miss') for first-pair leaf comparison."""
    if not a_lr or not e_lr: return "miss"
    a0, a1 = nint(a_lr[0][0]), nint(a_lr[0][1])
    e0, e1 = nint(e_lr[0][0]), nint(e_lr[0][1])
    if a0 is None or e0 is None: return "miss"
    if a0 == e0 and a1 == e1: return "exact"
    if abs(a0 - e0) <= tol and abs(a1 - e1) <= tol: return "soft"
    return "miss"


# ----------------------------------------------------------- toc loading --


def llm_to_legacy_lr(entry):
    sl = max(0, int(entry["start_leaf"]) - 1)
    el = max(sl, int(entry["end_leaf"]) - 1)
    return [[f"n{sl}", f"n{el}"]]


def load_toc(path, kind):
    """Return (raw_dict, normalized_entries). Returns (None, None) if missing."""
    if not os.path.exists(path): return None, None
    raw = json.load(open(path))
    out = []
    for i, e in enumerate(raw.get("entries") or []):
        if kind == "llm":
            lr = llm_to_legacy_lr(e)
            authors = [a.get("name") for a in (e.get("authors") or [])]
        else:
            lr = e.get("leaf_ranges") or []
            authors_raw = e.get("authors") or []
            authors = [a.get("name") if isinstance(a, dict) else a
                       for a in authors_raw]
        out.append({
            "idx": i,
            "title": e.get("title") or "",
            "authors": [a for a in authors if a],
            "leaf_ranges": lr,
            "start_page": e.get("start_page"),
            "end_page": e.get("end_page"),
        })
    return raw, out


# ------------------------------------------------------------- matching --


def best_match(anchor, entries):
    """Score every entry, return (best_entry, score, breakdown).

    Score blends title similarity, leaf overlap, and author overlap.
    Returned breakdown notes which signals contributed.
    """
    best = (None, -1.0, {})
    for e in entries:
        tsim = title_similarity(anchor.get("article_title"), e["title"])
        leaf_kind = leaf_overlap(anchor["leaf_ranges"], e["leaf_ranges"])
        leaf_score = {"exact": 1.0, "soft": 0.6, "miss": 0.0}[leaf_kind]
        author_hit = author_match(anchor.get("article_author"), e["authors"])
        # Weighted blend. Title is the primary signal post-2024-04 (99%
        # of anchors have it). Leaf is the strongest cross-check. Author
        # is a tie-breaker.
        score = 0.55 * tsim + 0.40 * leaf_score + 0.05 * (1 if author_hit else 0)
        if score > best[1]:
            best = (e, score, {"title_sim": tsim, "leaf": leaf_kind,
                               "author_hit": author_hit})
    return best


# ----------------------------------------------------------- main --


def collect_anchors(processed):
    """Build deduped post-2024-04 anchors from raw CSVs (only items in
    `processed`). Returns list of anchor dicts."""
    seen = set()
    anchors = []
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
                # leaf-shape filter
                def all_leaves(arr):
                    return arr and all(LEAF_RE.match(str(x).strip()) for x in arr)
                raw_s, raw_e = ff.get("start") or [], ff.get("stop") or []
                norm_s, norm_e = ff.get("normalized_orig_start") or [], ff.get("normalized_orig_stop") or []
                if all_leaves(raw_s) and all_leaves(raw_e) and len(raw_s) == len(raw_e):
                    starts, stops = raw_s, raw_e
                elif all_leaves(norm_s) and all_leaves(norm_e) and len(norm_s) == len(norm_e):
                    starts, stops = norm_s, norm_e
                else:
                    continue
                p = ff.get("original_request_params") or {}
                title = (p.get("article_title") or "").strip() or None
                author = (p.get("article_author") or "").strip() or None
                lr = [[s, e] for s, e in zip(starts, stops)]
                key = (ident, title, tuple(tuple(x) for x in lr))
                if key in seen: continue
                seen.add(key)
                anchors.append({
                    "identifier": ident,
                    "article_title": title,
                    "article_author": author,
                    "journal_pages": (p.get("journal_pages") or "").strip() or None,
                    "leaf_ranges": lr,
                })
    return anchors


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--variant", choices=("heur", "llm"), default="llm",
                    help="Which TOC schema to load")
    ap.add_argument("--match-threshold", type=float, default=0.55,
                    help="Combined score below which we declare a miss")
    ap.add_argument("--write-xref", action="store_true",
                    help="Write annotated TOCs to *_xref.json files")
    args = ap.parse_args()

    # 1. Identify processed items
    processed = set()
    for d in sorted(glob.glob(f"{SEGART}/tmp/items/*/")):
        item = os.path.basename(d.rstrip("/"))
        if os.path.exists(f"{d}{item}_docling.json.gz"):
            processed.add(item)

    # 2. Build anchors
    anchors = collect_anchors(processed)
    print(f"unique post-2024-04 anchors for {len(processed)} processed items: "
          f"{len(anchors)}", file=sys.stderr)

    # 3. Group anchors by item, then run matching against the chosen TOC variant
    by_item = defaultdict(list)
    for a in anchors:
        by_item[a["identifier"]].append(a)

    suffix = "_toc.json" if args.variant == "heur" else "_toc_llm.json"
    confirmed_entries = defaultdict(list)  # (item, entry_idx) → list of anchors
    fulfillment_results = []  # one per anchor

    n_with_toc = n_no_toc = 0
    for item, item_anchors in by_item.items():
        toc_path = f"{SEGART}/tmp/tocs/{item}{suffix}"
        raw, entries = load_toc(toc_path, args.variant)
        if entries is None:
            n_no_toc += 1
            for a in item_anchors:
                fulfillment_results.append({"anchor": a, "match": None,
                                             "kind": "no_toc"})
            continue
        n_with_toc += 1
        for a in item_anchors:
            best, score, breakdown = best_match(a, entries)
            kind = "miss"
            if best is not None and score >= args.match_threshold:
                kind = breakdown["leaf"] if breakdown["leaf"] != "miss" \
                                         else "title_only"
                confirmed_entries[(item, best["idx"])].append({
                    "anchor_title": a.get("article_title"),
                    "anchor_author": a.get("article_author"),
                    "anchor_leaves": a["leaf_ranges"],
                    "match_kind": kind,
                    "score": round(score, 3),
                })
            fulfillment_results.append({
                "anchor": a, "match": best, "score": score,
                "breakdown": breakdown, "kind": kind,
            })

        # 4. Write annotated xref
        if args.write_xref and raw is not None:
            for i, e in enumerate(raw.get("entries") or []):
                e["ill_evidence"] = confirmed_entries.get((item, i), [])
            xref_path = toc_path.replace("_toc.json", "_toc_heur_xref.json") \
                if args.variant == "heur" else \
                toc_path.replace("_toc_llm.json", "_toc_llm_xref.json")
            with open(xref_path, "w") as fh:
                json.dump(raw, fh, indent=2)

    # ------- View 1: Fulfillment lookup ----------------------------------
    print(f"\n{'='*72}")
    print(f"VIEW 1 — FULFILLMENT LOOKUP (variant={args.variant})")
    print(f"  given an ILL request and the issue's TOC, can we return the leaves?")
    print(f"{'='*72}")
    n = len(fulfillment_results)
    counts = defaultdict(int)
    for r in fulfillment_results:
        counts[r["kind"]] += 1
    print(f"{'kind':<14} {'n':>4} {'pct':>5}")
    for k in ("exact", "soft", "title_only", "miss", "no_toc"):
        v = counts[k]
        print(f"  {k:<14} {v:>4} {100*v//max(n,1):>4}%")
    correct_leaf = counts["exact"] + counts["soft"]
    print(f"  ── correct leaf ({correct_leaf}/{n} = {100*correct_leaf//max(n,1)}%)")

    # ------- View 2: ILL confirmation per TOC entry ----------------------
    print(f"\n{'='*72}")
    print(f"VIEW 2 — ILL-CONFIRMED TOC ENTRIES (variant={args.variant})")
    print(f"  what fraction of TOC entries get external ILL confirmation?")
    print(f"{'='*72}")
    print(f"{'item':<60} {'entries':>8} {'confirmed':>10}")
    total_entries = total_confirmed = 0
    for item in sorted(by_item):
        toc_path = f"{SEGART}/tmp/tocs/{item}{suffix}"
        _, entries = load_toc(toc_path, args.variant)
        if entries is None: continue
        total = len(entries)
        confirmed = sum(1 for i in range(total)
                        if confirmed_entries.get((item, i)))
        total_entries += total
        total_confirmed += confirmed
        print(f"  {item[:58]:<58} {total:>8} {confirmed:>10}")
    pct = 100*total_confirmed//max(total_entries,1)
    print(f"\n  TOTAL: {total_confirmed} of {total_entries} entries ILL-confirmed ({pct}%)")
    print(f"  (the rest are LLM-only — useful but unconfirmed)")


if __name__ == "__main__":
    main()
