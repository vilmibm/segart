"""Parse the scanner-assertion and scanLog sections of a scandata.xml.

Returns:
  parse_assertions(path) → list[(leafNum: int, pageNum: str)]
  parse_scan_operators(path) → list[{email, save_count, first_date, last_date, active_seconds}]

These are the two scandata.xml sections required by `evaluate_scanner_assertions.py`
to score scanner quality. They are not consumed by any other part of the segart
pipeline (page_index.py reads scandata's <page> elements, but not <pageNumData> /
<scanLog>).
"""
import xml.etree.ElementTree as ET
from pathlib import Path


def parse_assertions(xml_path):
    """Return list of (leafNum, pageNum) tuples from <pageNumData><assertion>.

    leafNum is an int; pageNum is a string (preserves "iii", "S1", etc.).
    Returns [] when the file has no <pageNumData> or no <assertion> children.
    """
    try:
        root = ET.parse(xml_path).getroot()
    except (ET.ParseError, FileNotFoundError):
        return []
    out = []
    for asn in root.iter("assertion"):
        leaf_el = asn.find("leafNum")
        page_el = asn.find("pageNum")
        if leaf_el is None or leaf_el.text is None:
            continue
        try:
            leaf = int(leaf_el.text.strip())
        except ValueError:
            continue
        page = (page_el.text or "").strip() if page_el is not None else ""
        out.append((leaf, page))
    return out


def parse_scan_operators(xml_path):
    """Return list of operator dicts from <scanLog><userMetrics>.

    Each dict: {email, save_count, first_date, last_date, active_seconds}.
    Sorted by active_seconds descending so callers can use [0] as the
    primary operator.
    """
    try:
        root = ET.parse(xml_path).getroot()
    except (ET.ParseError, FileNotFoundError):
        return []
    out = []
    metrics = root.find("scanLog/userMetrics")
    if metrics is None:
        return []
    for child in list(metrics):
        # element tag is the email with @/. replaced by _ — use <user> child
        # for the canonical address.
        email_el = child.find("user")
        email = (email_el.text or "").strip() if email_el is not None else child.tag
        def _int(name):
            el = child.find(name)
            if el is None or el.text is None: return 0
            try: return int(el.text.strip())
            except ValueError: return 0
        def _text(name):
            el = child.find(name)
            return (el.text or "").strip() if el is not None and el.text else ""
        out.append({
            "email": email,
            "save_count": _int("saveCount"),
            "first_date": _text("firstDate"),
            "last_date": _text("lastDate"),
            "active_seconds": _int("activeTime"),
        })
    out.sort(key=lambda d: -d["active_seconds"])
    return out


if __name__ == "__main__":
    import sys
    p = Path(sys.argv[1])
    asserts = parse_assertions(p)
    ops = parse_scan_operators(p)
    print(f"file: {p}")
    print(f"assertions ({len(asserts)}):")
    for leaf, pn in asserts:
        print(f"  leaf {leaf} → pageNum={pn!r}")
    print(f"operators ({len(ops)}):")
    for o in ops:
        print(f"  {o['email']}  active={o['active_seconds']}s saves={o['save_count']} {o['first_date']}-{o['last_date']}")
