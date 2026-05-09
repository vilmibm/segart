#!/usr/bin/env python3
"""Validate `repair_page_numbers.py` output on the 7 benchmark items.

Two checks:
  (2)  IA agreement.   For printed pages that appear in BOTH IA's
       `_page_numbers.json` and the repaired map, do they map to the
       same leaf?
  (2b) Crossref→docling round-trip.   For each Crossref article in the
       issue, the repaired map predicts a start_leaf from the article's
       printed start_page. We then look at docling's text on that leaf
       and check whether its title shows up there (token overlap ≥ 0.5).
       If repair is right and Crossref is right, this should land on
       the article-opening page.

ILL/patron page numbers are NOT used here (per user note: patron-supplied
pages are noisy, so they don't make a clean ground truth).

Usage:
  ./validate_page_numbers.py
"""
import json
import re
import sys
from pathlib import Path

# Reuse logic from the existing scripts so we don't drift.
sys.path.insert(0, str(Path(__file__).parent))
from repair_page_numbers import load_docling, extract_anchors, build_repaired_map
from heuristic_toc_crossref import (
    derive_metadata, fetch_crossref_full, load_page_numbers,
    load_docling_blocks, parse_page_range, find_title_in_docling,
    title_tokens, CACHE_DIR,
)

SEGART = Path("/Users/brewster/tmp/segart")
ITEMS_DIR = SEGART / "tmp" / "items"

ITEMS = [
    "sim_amerasia-journal_1989_15_1",
    "sim_american-journal-of-clinical-nutrition_1991-07_54_1",
    "sim_journal-of-college-student-development_november-december-1995_36_6",
    "sim_academy-of-management-review_2000-10_25_4",
    "sim_journal-of-clinical-psychiatry_1983-05_44_5_0",
    "sim_behavioral-and-brain-sciences_1980-09_3_3",
    "sim_ans_1978-10_1_1",
]


def get_repaired_map(item):
    """Run repair pipeline; return (page_to_leaf, n_anchors, n_zones, n_leaves)."""
    doc = load_docling(item)
    if not doc:
        return {}, 0, 0, 0
    anchors = extract_anchors(doc)
    n_leaves = len(doc.get("pages") or {})
    if not anchors:
        return {}, 0, 0, n_leaves
    page_to_leaf, _leaf_to_page, n_zones = build_repaired_map(anchors, n_leaves)
    return page_to_leaf, len(anchors), n_zones, n_leaves


def get_crossref(item):
    """Use cached crossref full file if present, else fetch.
    Returns (md, articles_or_None)."""
    md = derive_metadata(item)
    if not md: return None, None
    issn, yr, vol, iss = md
    full = CACHE_DIR / f"{issn}_{yr}_{vol}_{iss}_full.json"
    if full.exists():
        return md, json.loads(full.read_text())
    try:
        arts = fetch_crossref_full(issn, yr, vol, iss) or []
    except Exception as e:
        print(f"  crossref fetch failed for {item}: {e}", file=sys.stderr)
        return md, None
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(json.dumps(arts, indent=2))
    return md, arts


def test_ia_agreement(repaired, ia_map):
    """ia_map is {printed_page_str: leaf}; repaired is {printed_page_str: leaf}.
    Restrict to printed pages present in BOTH maps."""
    common = sorted(set(repaired) & set(ia_map),
                    key=lambda p: int(p) if p.isdigit() else 99999)
    deltas = []
    agree = 0
    for p in common:
        d = repaired[p] - ia_map[p]
        deltas.append(d)
        if d == 0: agree += 1
    return {
        "common_pages": len(common),
        "agree": agree,
        "mismatch": len(common) - agree,
        "deltas": deltas,
    }


def test_roundtrip(repaired, articles, blocks):
    """For each article: predict start_leaf from repaired map, check
    whether title text is found on/near that leaf in docling."""
    n_total = n_predicted = n_title_hit = n_title_hit_local = 0
    misses = []
    for a in articles:
        n_total += 1
        sp, _ep = parse_page_range(a.get("page"))
        if not sp or sp not in repaired: continue
        n_predicted += 1
        predicted_leaf = repaired[sp]
        # Free-text scan (anywhere in issue) — does the title appear at all?
        sl_anywhere, score_anywhere = find_title_in_docling(
            a.get("title"), blocks
        )
        if sl_anywhere is None:
            misses.append({"page": sp, "predicted_leaf": predicted_leaf,
                           "title": (a.get("title") or "")[:60],
                           "reason": "title-not-found-in-docling"})
            continue
        n_title_hit += 1
        # Did the title actually land on (or within ±2 of) the predicted leaf?
        if abs(sl_anywhere - predicted_leaf) <= 2:
            n_title_hit_local += 1
        else:
            misses.append({"page": sp, "predicted_leaf": predicted_leaf,
                           "found_leaf": sl_anywhere,
                           "delta": sl_anywhere - predicted_leaf,
                           "title": (a.get("title") or "")[:60]})
    return {
        "articles": n_total,
        "predicted": n_predicted,
        "title_found_anywhere": n_title_hit,
        "title_within_2_of_predicted": n_title_hit_local,
        "misses": misses,
    }


def fmt_pct(n, d):
    return f"{n}/{d} ({100*n//max(d,1)}%)"


def main():
    print(f"\n{'item':<60} {'anchors':>7} {'zones':>5} {'IA-agree':>10} {'rndtrip':>10}")
    print("-" * 100)
    agg = {"ia_common": 0, "ia_agree": 0,
           "rt_pred": 0, "rt_local": 0, "rt_total": 0}
    detail = []

    for item in ITEMS:
        repaired, n_anchors, n_zones, n_leaves = get_repaired_map(item)
        ia_full = load_page_numbers(item) or {}
        # Only keep IA entries whose printed-page is a positive integer string.
        ia_map = {p: l for p, l in ia_full.items() if p.isdigit()}

        md_arts = get_crossref(item)
        if md_arts is None or md_arts[1] is None:
            articles = []
        else:
            _md, articles = md_arts

        blocks = load_docling_blocks(item)

        ia = test_ia_agreement(repaired, ia_map)
        rt = test_roundtrip(repaired, articles, blocks)

        ia_str = fmt_pct(ia["agree"], ia["common_pages"]) if ia["common_pages"] else "—"
        rt_str = fmt_pct(rt["title_within_2_of_predicted"], rt["predicted"]) if rt["predicted"] else "—"

        short = item[:58]
        print(f"{short:<60} {n_anchors:>7} {n_zones:>5} {ia_str:>10} {rt_str:>10}")

        agg["ia_common"] += ia["common_pages"]
        agg["ia_agree"] += ia["agree"]
        agg["rt_pred"] += rt["predicted"]
        agg["rt_local"] += rt["title_within_2_of_predicted"]
        agg["rt_total"] += rt["articles"]
        detail.append((item, ia, rt))

    print("-" * 100)
    print(f"{'AGGREGATE':<60} {'':>7} {'':>5} "
          f"{fmt_pct(agg['ia_agree'], agg['ia_common']):>10} "
          f"{fmt_pct(agg['rt_local'], agg['rt_pred']):>10}")

    # Detail: misses per item
    print("\n=== detail: round-trip misses (title found, but >2 leaves from predicted) ===")
    for item, _ia, rt in detail:
        far = [m for m in rt["misses"] if "found_leaf" in m]
        if not far: continue
        print(f"\n  {item} ({len(far)} far misses)")
        for m in far[:5]:
            print(f"    pp{m['page']} → predicted leaf {m['predicted_leaf']}, "
                  f"title at leaf {m['found_leaf']} (delta {m['delta']:+d}): "
                  f"{m['title']!r}")

    print("\n=== detail: IA mismatches (delta histogram) ===")
    for item, ia, _rt in detail:
        if not ia["common_pages"]: continue
        if ia["mismatch"] == 0:
            print(f"  {item}: all {ia['common_pages']} agree")
            continue
        from collections import Counter
        c = Counter(ia["deltas"])
        top = ", ".join(f"{d:+d}:{n}" for d, n in sorted(c.items())[:8])
        print(f"  {item}: {ia['agree']}/{ia['common_pages']} agree, deltas: {top}")


if __name__ == "__main__":
    main()
