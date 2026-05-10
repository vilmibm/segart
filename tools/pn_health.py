"""Assess the health of a `<item>_page_numbers.json` for use by the
heurxref production pipeline.

Returns a structured assessment + a categorical recommendation:

  ok                    — use pn.json directly
  repair_via_docling    — pn.json sparse/low-conf; run repair_page_numbers
                          on docling output and retry. Only meaningful if
                          a docling cache exists or can be produced.
  restart_pagination    — duplicate non-empty printed-page values across
                          leaves indicate restart pagination at issue
                          boundaries (combined issue, etc.); printed-page →
                          leaf translation is ambiguous. Route to LLM.
  unusable              — pn.json is essentially absent or completely
                          unreliable. Route to LLM.

Usage (CLI):
  python3 tools/pn_health.py <item>
  python3 tools/pn_health.py <item> --pn-path <custom_pn.json>
  python3 tools/pn_health.py <item> --json     # machine-readable

Usage (library):
  from pn_health import assess_pn_health
  result = assess_pn_health(pn_data, item="...")
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Optional


SEGART = Path("/Users/brewster/tmp/segart")
DEFAULT_ITEMS = SEGART / "tmp" / "items"

# Tuning knobs (production driver can override)
DEFAULT_MIN_COVERAGE = 0.60          # ≥60% of leaves should have a pageNumber
DEFAULT_MIN_CONF_FRAC = 0.20         # ≥20% of populated entries with confidence > 0
DEFAULT_MIN_MONOTONIC = 0.85         # ≥85% of consecutive-page deltas should be +1
DEFAULT_MAX_DUP_FRAC = 0.05          # ≤5% of non-empty values can be duplicates
                                     # (small slack for OCR noise; high signals restart)


def _is_restart_pagination(pn_pages):
    """True iff non-empty pageNumber values repeat across leaves at a
    rate above DEFAULT_MAX_DUP_FRAC, signalling restart pagination."""
    vals = [str(p.get("pageNumber") or "").strip() for p in pn_pages]
    non_empty = [v for v in vals if v]
    if not non_empty: return False
    counts = Counter(non_empty)
    dups = sum(n - 1 for n in counts.values() if n > 1)
    return dups / len(non_empty) > DEFAULT_MAX_DUP_FRAC


def _coverage(pn_pages):
    if not pn_pages: return 0.0
    n_with_pp = sum(1 for p in pn_pages if (p.get("pageNumber") or "").strip())
    return n_with_pp / len(pn_pages)


def _confidence_fraction(pn_pages):
    """Fraction of populated entries that carry a confidence > 0 (anything
    other than the default 'unscored' value)."""
    populated = [p for p in pn_pages if (p.get("pageNumber") or "").strip()]
    if not populated: return 0.0
    confident = sum(1 for p in populated if (p.get("confidence") or 0) > 0)
    return confident / len(populated)


def _monotonicity(pn_pages):
    """Fraction of consecutive (printed-page → printed-page) transitions
    that increase by exactly 1. Catches OCR-noise and out-of-order pages
    while allowing single jumps (combined issues, supplements)."""
    vals = []
    for p in pn_pages:
        s = (p.get("pageNumber") or "").strip()
        if s.isdigit():
            vals.append(int(s))
        else:
            vals.append(None)
    pairs = [(a, b) for a, b in zip(vals, vals[1:])
             if a is not None and b is not None]
    if not pairs: return 0.0
    good = sum(1 for a, b in pairs if b - a == 1)
    return good / len(pairs)


def assess_pn_health(
    pn_data: dict,
    *,
    item: Optional[str] = None,
    min_coverage: float = DEFAULT_MIN_COVERAGE,
    min_conf_frac: float = DEFAULT_MIN_CONF_FRAC,
    min_monotonic: float = DEFAULT_MIN_MONOTONIC,
) -> dict:
    pages = pn_data.get("pages") or []
    n_leaves = len(pages)
    coverage = _coverage(pages)
    conf_frac = _confidence_fraction(pages)
    monotonic = _monotonicity(pages)
    restart = _is_restart_pagination(pages)
    issue_conf = pn_data.get("confidence")  # IA pipeline confidence

    # Categorize
    if restart:
        status = "restart_pagination"
    elif n_leaves == 0:
        status = "unusable"
    elif coverage < 0.10 and conf_frac < 0.05:
        # Essentially empty
        status = "unusable"
    elif (coverage >= min_coverage and conf_frac >= min_conf_frac
          and monotonic >= min_monotonic):
        status = "ok"
    else:
        # Sparse / low confidence / chaotic — docling could rescue
        status = "repair_via_docling"

    return {
        "item": item,
        "n_leaves": n_leaves,
        "coverage": round(coverage, 3),
        "confidence_fraction": round(conf_frac, 3),
        "monotonicity": round(monotonic, 3),
        "restart_pagination": restart,
        "issue_confidence": issue_conf,
        "status": status,
    }


def load_pn_for_item(item: str, items_dir: Path = DEFAULT_ITEMS) -> Optional[dict]:
    p = items_dir / item / f"{item}_page_numbers.json"
    if not p.exists():
        return None
    with open(p) as fh:
        return json.load(fh)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("item", help="IA item identifier")
    ap.add_argument("--pn-path", help="Direct path to a pn.json (overrides item lookup)")
    ap.add_argument("--items-dir", default=str(DEFAULT_ITEMS),
                    help="Items directory (default: %(default)s)")
    ap.add_argument("--json", action="store_true",
                    help="Emit machine-readable JSON (default: human-readable)")
    args = ap.parse_args()

    if args.pn_path:
        with open(args.pn_path) as fh:
            pn = json.load(fh)
    else:
        pn = load_pn_for_item(args.item, Path(args.items_dir))
        if pn is None:
            print(f"ERROR: no pn.json found for {args.item} under "
                  f"{args.items_dir}", file=sys.stderr)
            return 3

    result = assess_pn_health(pn, item=args.item)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        s = result
        print(f"item: {s['item']}")
        print(f"  n_leaves:           {s['n_leaves']}")
        print(f"  coverage:           {s['coverage']*100:.1f}% have printed-page")
        print(f"  confidence_frac:    {s['confidence_fraction']*100:.1f}% of "
              f"populated entries have confidence > 0")
        print(f"  monotonicity:       {s['monotonicity']*100:.1f}% of consecutive "
              f"transitions are +1")
        print(f"  restart_pagination: {s['restart_pagination']}")
        print(f"  issue_confidence:   {s['issue_confidence']}")
        print(f"  status:             {s['status']}")
    # Exit codes for shell pipelines
    return {
        "ok": 0,
        "repair_via_docling": 1,
        "restart_pagination": 2,
        "unusable": 3,
    }.get(result["status"], 4)


if __name__ == "__main__":
    sys.exit(main())
