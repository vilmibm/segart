#!/usr/bin/env python3
"""Parse the `cover_text` blob inside an ILL fulfillment log row.

The IA ILL system stores patron-supplied article metadata in two places
on each fulfillment row:
  - `original_request_params`: structured fields (title, author, journal,
    ISSN, vol, issue, year, pages). May be empty/missing.
  - `cover_text`: human-readable summary printed on the delivered PDF.
    Generated from the same submission. Populated for ~99.999% of all
    rows (164,544 / 164,546 in our 2022-2024 sample) including ones
    where `original_request_params` is empty.

`parse_ill_logs.py` only reads the structured params, so it discards
title/author for the ~43% of rows where params are empty. This module
parses cover_text to recover that data.

Format (strict — verified against >100k samples):

    Request on <day-of-week>, <month> <date>, <year>
    <journal title> <year-range>
    <ISSN>
    <article title — 1+ lines>
    <author(s) — single line, varied separators>
    volume <V> issue <I> year <Y>[<-month-or-date>] [pages <P>]
    pages <P>

    Response from Internet Archive from: <url>
    We hope this is helpful. [--<staffer-initials>]

The bibliographic line beginning `volume ` (or `year ` if no volume) and
the `pages ` line anchor the bottom of the structured data; the `Request
on` line anchors the top. The author is exactly the line above the
volume/year line, and everything between ISSN and author is the title.
"""
import re

# ISSN: 8 digits with optional hyphen and X check-digit.
# ISBN-13: 13 digits starting with 978 or 979.
# ISBN-10: 10 chars, last may be X.
ISSN_RE = re.compile(r"^\s*(\d{4}-?\d{3}[\dXx])\s*$")
ISBN_RE = re.compile(r"^\s*((?:97[89])\d{10}|\d{9}[\dXx])\s*$")
VOL_RE = re.compile(r"^\s*volume\s+", re.I)
PAGES_RE = re.compile(r"^\s*pages?\s+", re.I)
RESPONSE_RE = re.compile(r"^\s*Response from Internet Archive\b", re.I)
REQUEST_RE = re.compile(r"^\s*Request on\b", re.I)
YEAR_LINE_RE = re.compile(r"^\s*(?:volume\s+\S+\s+)?issue\s+\S+\s+year\s+",
                           re.I)
JUST_YEAR_RE = re.compile(r"^\s*year\s+\d", re.I)


def parse(cover_text):
    """Return dict with keys: journal_title, issn, article_title,
    article_author, volume, issue, year, pages. Missing fields are None.
    Returns None when the blob doesn't look like a cover_text at all."""
    if not cover_text or not cover_text.strip():
        return None
    text = cover_text.replace("\r\n", "\n").replace("\r", "\n")
    # Trim leading/trailing blank lines and split.
    raw_lines = [ln.rstrip() for ln in text.split("\n")]
    while raw_lines and not raw_lines[0].strip():
        raw_lines.pop(0)
    # Truncate at the response footer.
    end = len(raw_lines)
    for i, ln in enumerate(raw_lines):
        if RESPONSE_RE.match(ln):
            end = i
            break
    # Strip trailing blanks.
    body = raw_lines[:end]
    while body and not body[-1].strip():
        body.pop()
    if not body:
        return None
    if not REQUEST_RE.match(body[0]):
        return None  # not a recognizable cover blob

    # body[0] = "Request on ..."
    # body[1] = journal title <year-range>
    # body[2] = ISSN
    # body[3..k-1] = title (≥1 line)
    # body[k] = author
    # body[k+1] = "volume X issue Y year Z" (sometimes with pages on same line)
    # body[k+2] (optional) = "pages X-Y"
    if len(body) < 5:
        return None
    journal_line = body[1].strip()
    issn = None
    isbn = None
    issn_idx = None
    # Sometimes ISSN is missing; tolerate that. Also accept ISBN (book requests).
    if len(body) > 2:
        if ISSN_RE.match(body[2]):
            issn = ISSN_RE.match(body[2]).group(1)
            issn_idx = 2
        elif ISBN_RE.match(body[2]):
            isbn = ISBN_RE.match(body[2]).group(1)
            issn_idx = 2  # treat as identifier line

    # Find the bibliographic suffix anchor. Journals have a `volume ...` /
    # `year ...` line; books have only a `pages ...` line. Search from the
    # bottom up.
    biblio_idx = None
    pages_only = False
    for i in range(len(body) - 1, 1, -1):
        if VOL_RE.match(body[i]) or YEAR_LINE_RE.match(body[i]) \
                or JUST_YEAR_RE.match(body[i]):
            biblio_idx = i
            break
    if biblio_idx is None:
        # Fall back: a bare `pages ...` line (book chapter).
        for i in range(len(body) - 1, 1, -1):
            if PAGES_RE.match(body[i]):
                biblio_idx = i
                pages_only = True
                break
    if biblio_idx is None:
        return None

    # Walk down past biblio_idx for an optional `pages` line.
    pages_idx = None
    if (not pages_only) and biblio_idx + 1 < len(body) \
            and PAGES_RE.match(body[biblio_idx + 1]):
        pages_idx = biblio_idx + 1

    # Title spans from line after ISSN (or after journal) up through the line
    # immediately above the biblio line. If only ONE line sits between ISSN
    # and biblio, that's the title and there is no author. If two+ lines,
    # the last is the author and the earlier ones are the (multi-line) title.
    title_start = (issn_idx + 1) if issn_idx is not None else 2
    span = [l.strip() for l in body[title_start: biblio_idx] if l.strip()]
    if not span:
        return None
    if len(span) == 1:
        article_title = span[0]
        author = None
    else:
        article_title = " ".join(span[:-1])
        author = span[-1]

    # Journal title: strip a trailing "<year>-<year>" or "<year>-<short>" range.
    journal_title = re.sub(
        r"\s+\d{4}\s*[-–]\s*(?:\d{4}|\d{2})\s*$", "", journal_line
    ).strip()

    # Parse the biblio line.
    biblio = body[biblio_idx]
    vol = iss = year = pages = None
    m = re.search(r"volume\s+(\S+)", biblio, re.I)
    if m: vol = m.group(1).strip().rstrip(",")
    m = re.search(r"issue\s+(\S+)", biblio, re.I)
    if m: iss = m.group(1).strip().rstrip(",")
    m = re.search(r"year\s+(\S+)", biblio, re.I)
    if m: year = m.group(1).strip().rstrip(",")
    m = re.search(r"pages?\s+([\w\-– ]+?)\s*$", biblio, re.I)
    if m: pages = m.group(1).strip()
    if pages_idx is not None:
        m = re.search(r"pages?\s+([\w\-– ]+?)\s*$", body[pages_idx], re.I)
        if m: pages = m.group(1).strip()
    # "pages na" or "pages NA" → no real page range
    if pages and pages.strip().lower() in ("na", "n/a"):
        pages = None

    return {
        "journal_title": journal_title or None,
        "issn": issn,
        "isbn": isbn,
        "article_title": article_title or None,
        "article_author": author or None,
        "volume": vol,
        "issue": iss,
        "year": year,
        "pages": pages,
    }


if __name__ == "__main__":
    import json, sys
    samples = [
        # sample 1: simple author with &
        """Request on Friday, July 1st, 2022
Journal of Addictive Diseases 1991-2010
1055-0887
What heroin users tell us about overdose
Baca CT & Grant KJ
volume 26 issue 4 year 2007
pages 63-68

Response from Internet Archive from: https://archive.org/details/sim_journal-of-addictive-diseases_2007_26_4
We hope this is helpful.""",
        # sample 2: semicolon authors
        """Request on Friday, July 1st, 2022
Journal of Applied Polymer Science 1959-2014
0021-8995
Fabrication methods for latex-based elastomer composites reinforced with long discontinuous fibers
Epstein, Mikael;Shishoo, R L;
volume 44  issue 2  year 1992
pages 263-277

Response from Internet Archive from: ...""",
        # sample 3: book review with year-month
        """Request on Friday, July 1st, 2022
Gifted Child Quarterly 1957-2015
0016-9862
Book Reviews: CSIKSZENTMIHALYI, MIHALY (1996). Creativity: Flow and the psychology of discovery and invention. New York: Harper Collins
Burrus, Jill
volume 41  issue 3  year 1997-07
pages 114 - 116

Response from Internet Archive from: ...""",
        # sample 4: amerasia 1989 - the case that triggered all this
        """Request on Tuesday, April 4th, 2023
Amerasia Journal 1973-2012
0044-7471
"On Strike!" San Francisco State College Strike, 1968–69: The Role of Asian American Students
Karen Umemoto
year 1989

Response from Internet Archive from: ...""",
    ]
    for i, s in enumerate(samples, 1):
        print(f"--- sample {i} ---")
        print(json.dumps(parse(s), indent=2, ensure_ascii=False))
