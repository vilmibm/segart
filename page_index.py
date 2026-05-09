"""Canonical mapping between the three indices IA uses for a scanned item.

Three distinct indices, often confused in this codebase:

  - **scandata leafNum**: the Scribe scan-image counter from
    `<item>_scandata.xml`. 0-indexed, contiguous over every captured image
    including hidden ones (color cards, foldouts, removed pages).
    Despite the name, this is not a leaf in the bookbinding sense (one
    physical sheet = recto + verso = two pages); the Scribe assigns one
    integer per scan image, i.e. one per page side.

  - **page index** (BookReader `nN`): the visible-only access counter
    that surfaces in BookReader URLs (`/page/nN/`) and in our `_toc.json`
    `page_index_ranges`. 0-indexed, contiguous over `addToAccessFormats=true`
    pages only — hidden leaves are skipped.

  - **page_numbers.json `leafNum`**: keys into IA's per-image OCR output.
    Uses the SAME integer values as scandata leafNum (i.e. the Scribe
    numbering, with potential gaps where hidden leaves are skipped),
    but additionally OMITS some `pageType`s (Title, Cover, etc.) per
    item — so even for an item with no hidden leaves, page_numbers.json
    may not contain every scandata leafNum.

`page_index` (BookReader nN) is segart's source-of-truth ordinal.
Use this module's mapper whenever crossing formats. For pure ordering
or comparison among page_index values from one TOC, the integer is
sufficient — no scandata required.
"""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

_BR_RE = re.compile(r"^n(\d+)$")


def parse_br(s: str | int | None) -> Optional[int]:
    """Parse a BookReader page-index string ('n26') to its integer (26).

    Returns None on empty/invalid input. Tolerates already-integer input.
    """
    if s is None:
        return None
    if isinstance(s, int):
        return s if s >= 0 else None
    s = str(s).strip()
    if not s:
        return None
    m = _BR_RE.match(s)
    if m:
        return int(m.group(1))
    if s.isdigit():
        return int(s)
    return None


def format_br(n: int) -> str:
    """Format an integer page index as 'nN' (e.g. 26 -> 'n26')."""
    if n is None or n < 0:
        raise ValueError(f"invalid page index: {n!r}")
    return f"n{n}"


@dataclass
class PageIndex:
    """Mapping derived from one item's scandata.xml.

    Attributes:
      br_to_leafnum: page-index N -> scandata leafNum
      leafnum_to_br: scandata leafNum -> page-index N (None if hidden)
      total_scan_count: number of scan images in scandata
      visible_count: number of accessible (addToAccessFormats=true) pages
    """
    br_to_leafnum: list[int] = field(default_factory=list)
    leafnum_to_br: dict[int, Optional[int]] = field(default_factory=dict)
    total_scan_count: int = 0
    visible_count: int = 0
    item: Optional[str] = None

    @classmethod
    def from_scandata_xml(cls, xml: str | bytes, *, item: Optional[str] = None) -> "PageIndex":
        root = ET.fromstring(xml)
        rows: list[tuple[int, bool]] = []  # (leafNum, accessible)
        for page in root.iter("page"):
            ln_attr = page.attrib.get("leafNum")
            if ln_attr is None:
                continue
            ln = int(ln_attr)
            access_text = (page.findtext("addToAccessFormats") or "").strip()
            accessible = access_text == "true"
            rows.append((ln, accessible))
        rows.sort(key=lambda r: r[0])
        br_to_leafnum: list[int] = []
        leafnum_to_br: dict[int, Optional[int]] = {}
        for ln, accessible in rows:
            if accessible:
                leafnum_to_br[ln] = len(br_to_leafnum)
                br_to_leafnum.append(ln)
            else:
                leafnum_to_br[ln] = None
        return cls(
            br_to_leafnum=br_to_leafnum,
            leafnum_to_br=leafnum_to_br,
            total_scan_count=len(rows),
            visible_count=len(br_to_leafnum),
            item=item,
        )

    @classmethod
    def from_scandata_path(cls, path: str | Path) -> "PageIndex":
        path = Path(path)
        return cls.from_scandata_xml(path.read_bytes(), item=path.stem.removesuffix("_scandata"))

    @classmethod
    def for_item(cls, item: str, *, base: str | Path = None,
                 fetch: bool = False) -> "PageIndex":
        """Load PageIndex for an IA item id.

        Tries, in order:
          1. `{base or items_dir}/{item}/{item}_scandata.xml`
          2. `{scandata_dir}/{item}_scandata.xml` (a flat fixture/cache dir)
          3. archive.org, if `fetch=True`
        """
        items_dir = Path(base) if base is not None else Path("/Users/brewster/tmp/segart/tmp/items")
        scandata_dir = Path("/Users/brewster/tmp/segart/tmp/scandata")
        candidates = [
            items_dir / item / f"{item}_scandata.xml",
            scandata_dir / f"{item}_scandata.xml",
        ]
        for path in candidates:
            if path.exists():
                return cls.from_scandata_path(path)
        if fetch:
            import urllib.request
            url = f"https://archive.org/download/{item}/{item}_scandata.xml"
            req = urllib.request.Request(
                url, headers={"User-Agent": "segart/0.1 (mailto:brewster@archive.org)"}
            )
            with urllib.request.urlopen(req, timeout=30) as fh:
                xml = fh.read()
            return cls.from_scandata_xml(xml, item=item)
        raise FileNotFoundError(
            f"no scandata for {item}: tried {[str(p) for p in candidates]}"
        )

    # ----- mapping queries -----

    def br_to_scandata(self, br_n: int) -> Optional[int]:
        """page index N -> scandata leafNum, or None if N is out of range."""
        if br_n is None or br_n < 0 or br_n >= len(self.br_to_leafnum):
            return None
        return self.br_to_leafnum[br_n]

    def scandata_to_br(self, leafnum: int) -> Optional[int]:
        """scandata leafNum -> page index N, or None if leaf is hidden / unknown."""
        return self.leafnum_to_br.get(leafnum)

    def is_hidden(self, leafnum: int) -> bool:
        """True if this scandata leafNum exists but is not in BookReader."""
        return leafnum in self.leafnum_to_br and self.leafnum_to_br[leafnum] is None

    # ----- page_numbers.json integration -----

    def lookup_pn(self, pn: dict, br_n: int) -> Optional[dict]:
        """Find the page_numbers.json entry corresponding to a BookReader nN.

        page_numbers.json is keyed by scandata leafNum (with some pageTypes
        omitted). This walks scandata to find the right leafNum for br_n,
        then looks up by leafNum in the pn data. Returns None if br_n is
        out of range or if pn has no entry for that leafNum (some pageTypes
        are filtered out by the OCR pass).
        """
        leafnum = self.br_to_scandata(br_n)
        if leafnum is None:
            return None
        for entry in pn.get("pages", []):
            if entry.get("leafNum") == leafnum:
                return entry
        return None

    @staticmethod
    def load_pn(item: str, *, base: str | Path = None) -> Optional[dict]:
        if base is None:
            base = Path("/Users/brewster/tmp/segart/tmp/items")
        path = Path(base) / item / f"{item}_page_numbers.json"
        if not path.exists():
            return None
        with open(path) as fh:
            return json.load(fh)
