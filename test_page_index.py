"""Tests for page_index.py.

Validates against:
- A no-hidden-leaves item (icp would have hidden, britishaffairs etc.)
- Items with edge-only hidden leaves (color cards at front + back)
- The pure parsers in isolation

Run: python3 -m pytest test_page_index.py -v
   or directly: python3 test_page_index.py
"""
import json
from pathlib import Path

import page_index as pi


# Items present in tmp/items/ with hidden leaves (verified 2026-05-09):
ICP = "imagination-cognition-and-personality_1989-1990_9_3"           # 102 total, 2 hidden at edges
NO_HIDDEN = "sim_american-journal-of-clinical-nutrition_1991-07_54_1"  # 195 total, 0 hidden

ITEMS = Path("/Users/brewster/tmp/segart/tmp/items")


# ---------- pure parsers ----------

def test_parse_br_basic():
    assert pi.parse_br("n0") == 0
    assert pi.parse_br("n26") == 26
    assert pi.parse_br("n999") == 999

def test_parse_br_tolerates_int():
    assert pi.parse_br(0) == 0
    assert pi.parse_br(26) == 26

def test_parse_br_tolerates_digits_only():
    assert pi.parse_br("26") == 26

def test_parse_br_invalid():
    assert pi.parse_br("") is None
    assert pi.parse_br(None) is None
    assert pi.parse_br("foo") is None
    assert pi.parse_br("n") is None
    assert pi.parse_br(-1) is None

def test_format_br_basic():
    assert pi.format_br(0) == "n0"
    assert pi.format_br(26) == "n26"


# ---------- icp: edge-hidden case ----------
# scandata: leafNum 0..101 (102 total)
#   leafNum=0   Color Card (hidden)
#   leafNum=1..100  visible (BookReader n0..n99)
#   leafNum=101 Color Card (hidden)

SCANDATA_DIR = Path("/Users/brewster/tmp/segart/tmp/scandata")


def test_icp_loads():
    p = pi.PageIndex.from_scandata_path(SCANDATA_DIR / f"{ICP}_scandata.xml")
    assert p.total_scan_count == 102
    assert p.visible_count == 100

def test_icp_br_to_scandata():
    p = pi.PageIndex.for_item(ICP)
    assert p.br_to_scandata(0) == 1   # BookReader n0 -> scandata leafNum 1 (because 0 is hidden)
    assert p.br_to_scandata(1) == 2
    assert p.br_to_scandata(99) == 100
    assert p.br_to_scandata(100) is None  # out of range

def test_icp_scandata_to_br():
    p = pi.PageIndex.for_item(ICP)
    assert p.scandata_to_br(0) is None  # hidden Color Card
    assert p.scandata_to_br(1) == 0     # first visible page
    assert p.scandata_to_br(100) == 99  # last visible page
    assert p.scandata_to_br(101) is None  # hidden Color Card
    assert p.scandata_to_br(999) is None  # not in scandata at all

def test_icp_is_hidden():
    p = pi.PageIndex.for_item(ICP)
    assert p.is_hidden(0) is True
    assert p.is_hidden(1) is False
    assert p.is_hidden(101) is True
    # Non-existent leafnums report False (not "hidden" — just unknown)
    assert p.is_hidden(999) is False

def test_icp_pn_lookup_matches_known_values():
    """Empirically: pn.leafNum=1 has pageNumber='4' for icp.
    BookReader n0 should land on the same record."""
    p = pi.PageIndex.for_item(ICP)
    pn = pi.PageIndex.load_pn(ICP)
    rec = p.lookup_pn(pn, br_n=0)
    assert rec is not None
    assert rec["leafNum"] == 1
    assert rec["pageNumber"] == "4"

def test_icp_pn_lookup_last_page():
    """Empirically: pn for icp ends at leafNum=100. BookReader n99 should land there."""
    p = pi.PageIndex.for_item(ICP)
    pn = pi.PageIndex.load_pn(ICP)
    rec = p.lookup_pn(pn, br_n=99)
    assert rec is not None
    assert rec["leafNum"] == 100

def test_icp_pn_lookup_out_of_range():
    p = pi.PageIndex.for_item(ICP)
    pn = pi.PageIndex.load_pn(ICP)
    assert p.lookup_pn(pn, br_n=100) is None
    assert p.lookup_pn(pn, br_n=-1) is None


# ---------- no-hidden control ----------

def test_no_hidden_identity():
    """For an item with no hidden leaves, br_n == scandata leafNum (modulo any
    pageType filtering on pn — that's a separate concern)."""
    p = pi.PageIndex.for_item(NO_HIDDEN)
    assert p.total_scan_count == p.visible_count
    for br_n in (0, 1, 50, p.visible_count - 1):
        assert p.br_to_scandata(br_n) == br_n
        assert p.scandata_to_br(br_n) == br_n


# ---------- naive lstrip("n") demonstrates the bug ----------

def test_naive_lstrip_diverges_for_hidden_edge_items():
    """Documents the bug the new module fixes: naive int(s.lstrip("n"))
    gives the wrong scandata leafNum for items with hidden leaves at
    leafNum=0 (the very common Color Card case)."""
    p = pi.PageIndex.for_item(ICP)
    # Naive: "n0" -> 0
    naive = int("n0".lstrip("n"))
    correct = p.br_to_scandata(0)
    assert naive == 0
    assert correct == 1
    assert naive != correct  # off by one


# ---------- main ----------

def _run_all():
    """Lightweight runner so we don't need pytest installed."""
    import inspect, traceback
    fns = [(n, f) for n, f in globals().items()
           if n.startswith("test_") and callable(f)]
    failed = 0
    for n, f in fns:
        try:
            f()
            print(f"  PASS  {n}")
        except Exception as e:
            failed += 1
            print(f"  FAIL  {n}: {type(e).__name__}: {e}")
            traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    return failed


if __name__ == "__main__":
    import sys
    sys.exit(_run_all())
