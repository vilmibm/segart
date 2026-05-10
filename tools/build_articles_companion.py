"""Build a v1 _articles.json.gz companion for a pilot item, given the
v2 _toc.json plus the cached Crossref response for the issue's year.

For a pilot: embeds the full Crossref payload per article. fatcat /
OpenAlex / Unpaywall / PubMed slots are left as `null` placeholders —
those enrichments are TODO for production.
"""
import gzip
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from segart_version import software_versions

if len(sys.argv) != 3:
    print("usage: build_articles_companion.py <toc.json> <out_articles.json.gz>")
    sys.exit(1)

toc_path = Path(sys.argv[1])
out_path = Path(sys.argv[2])

toc = json.loads(toc_path.read_text())
item = toc["item"]; issn = toc["issn"]
year = toc["year"]; vol = toc["volume"]; iss = toc["issue"]

# Load cached crossref year data
CACHE = Path("/Users/brewster/tmp/segart/tmp/crossref_journal_year_cache")
safe = re.sub(r"[^A-Za-z0-9-]", "_", issn)
cache_file = CACHE / f"{safe}_{year}.json"
if not cache_file.exists():
    print(f"ERROR: cache missing for ({issn}, {year}) at {cache_file}",
          file=sys.stderr)
    sys.exit(2)

crossref_records = json.loads(cache_file.read_text()).get("items") or []
# Filter to this (vol, iss); use the relaxed match same as heurxref
def parts(s):
    s = str(s or "").strip()
    if not s: return {""}
    p = {s}
    for sep in "-/,":
        for q in s.split(sep):
            q = q.strip()
            if q: p.add(q)
    return p

def match(crossref_label, query_label):
    c = str(crossref_label or "").strip()
    q = str(query_label or "").strip()
    if c == q: return True
    if not c or not q: return False
    return bool(parts(c) & parts(q))

xref_issue = [r for r in crossref_records
              if match(r.get("volume"), vol) and match(r.get("issue"), iss)]

# Index Crossref records by DOI (lowercase) for fast lookup
by_doi = {(r.get("DOI") or "").lower(): r for r in xref_issue if r.get("DOI")}

# Strip periodical-level fields per articles_format.md
PERIODICAL_FIELDS = {"container-title", "short-container-title", "ISSN",
                     "issn-type", "publisher", "member", "prefix", "source"}

def strip_periodical(rec):
    return {k: v for k, v in rec.items() if k not in PERIODICAL_FIELDS}


# Build the companion file
entries = {}
for e in toc.get("entries") or []:
    doi = (e.get("ext_ids") or {}).get("doi", "")
    xref = by_doi.get((doi or "").lower())
    record = {
        "toc_entry_id": e["id"],
        "ext_ids": e.get("ext_ids") or {},
        "match_method": "doi_lookup" if xref else "no_match",
        "match_confidence": 1.0 if xref else 0.0,
        "crossref": strip_periodical(xref) if xref else None,
        "fatcat": None,        # TODO: fatcat release/file linkage
        "openalex": None,      # TODO: OpenAlex concepts + OA
        "unpaywall": None,     # TODO: Unpaywall OA status
        "pubmed": None,        # TODO: PubMed/MeSH (biomedical only)
    }
    entries[e["id"]] = record

companion = {
    "schema_version": 1,
    "ia_item": item,
    "toc_schema_version": toc.get("schema_version"),
    "toc_generated_at": toc.get("generated_at"),
    "issue_doi": None,
    "special_issue_title": None,
    "issue_editors": [],
    "provenance": {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "software_versions": software_versions(),
        "sources": {
            "crossref": {"via": "live_api_cached", "fetched_at": "2026-05-11"},
            # Other sources not yet wired:
            "fatcat":   None,
            "openalex": None,
            "unpaywall": None,
            "pubmed":   None,
        },
    },
    "license_notes": {
        "shell": "CC0 / public domain. Crossref bibliographic metadata.",
        "abstracts": "Crossref-deposited abstracts retain publisher copyright.",
    },
    "entries": entries,
}

# Write gzipped
with gzip.open(out_path, "wt", encoding="utf-8") as fh:
    json.dump(companion, fh, indent=2)

print(f"wrote {out_path} ({out_path.stat().st_size} bytes)")
print(f"  {len(entries)} entries")
print(f"  matched_to_crossref: "
      f"{sum(1 for e in entries.values() if e['crossref'])}")
