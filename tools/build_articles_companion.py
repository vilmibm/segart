"""Build a v1 _articles.json.gz companion for an IA item.

Per articles_format.md: per-entry payload joins five bibliographic
sources, each addressed by DOI:

  - crossref   — verbatim /works/{doi}, periodical fields stripped
  - fatcat     — slim projection: idents + file linkage
  - openalex   — slim: concepts, topics, citations, OA, authorships
  - unpaywall  — slim: OA status
  - pubmed     — slim: PMID, MeSH, pub types (biomedical only; via Europe PMC)

Each source is file-cached by DOI (or by (issn, year) for the Crossref
bulk fetch) so re-runs are free.
"""
import gzip
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from segart_version import software_versions


CACHE_ROOT = Path("/Users/brewster/tmp/segart/tmp")
FULL_CACHE = CACHE_ROOT / "crossref_full_cache"
FATCAT_CACHE = CACHE_ROOT / "fatcat_doi_cache"
FATCAT_FILES_CACHE = CACHE_ROOT / "fatcat_files_cache"
OPENALEX_CACHE = CACHE_ROOT / "openalex_doi_cache"
UNPAYWALL_CACHE = CACHE_ROOT / "unpaywall_doi_cache"
PUBMED_CACHE = CACHE_ROOT / "pubmed_doi_cache"

EMAIL = "brewster@archive.org"
HEADERS = {"User-Agent": f"segart-articles/1.0 (mailto:{EMAIL})"}

# Periodical-level Crossref fields stripped before embedding per article.
# Per articles_format.md: those belong in the pub_* collection, not on
# every article record.
PERIODICAL_FIELDS = {"container-title", "short-container-title", "ISSN",
                     "issn-type", "publisher", "member", "prefix", "source"}


def fetch_crossref_full_for_year(issn: str, year: str) -> tuple[list, str | None]:
    """Fetch ALL fields for every article in (issn, year) from Crossref,
    with cursor pagination so we never silently truncate. File-cached by
    (issn, year) under tmp/crossref_full_cache/.

    Distinct from the audit cache (tmp/crossref_journal_year_cache/) —
    the audit used select=DOI,title,page,volume,issue,author to keep the
    cache small for tier scoring. The articles file needs the rest of
    each record (abstract, references, funder, license, dates, etc.).
    """
    safe = re.sub(r"[^A-Za-z0-9-]", "_", issn)
    p = FULL_CACHE / f"{safe}_{year}.json"
    if p.exists():
        try:
            d = json.loads(p.read_text())
            return d.get("items", []), d.get("error")
        except Exception:
            pass  # corrupted; refetch

    items, error = [], None
    cursor = "*"
    pages_fetched = 0
    while True:
        qs = urllib.parse.urlencode({
            "rows": 200,
            "filter": (f"type:journal-article,from-pub-date:{year}-01,"
                       f"until-pub-date:{year}-12"),
            "cursor": cursor,
            "mailto": "brewster@archive.org",
            # NB: no `select=` — we want everything Crossref has
        })
        url = f"https://api.crossref.org/journals/{issn}/works?{qs}"
        req = urllib.request.Request(url, headers=HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=60) as fh:
                data = json.load(fh)
        except Exception as e:
            error = str(e); break
        msg = data.get("message", {})
        page_items = msg.get("items", [])
        items.extend(page_items)
        pages_fetched += 1
        next_cursor = msg.get("next-cursor")
        if not page_items or not next_cursor or next_cursor == cursor:
            break
        cursor = next_cursor
        if pages_fetched >= 50:  # safety cap
            break

    FULL_CACHE.mkdir(parents=True, exist_ok=True)
    p_tmp = p.with_suffix(".json.tmp")
    p_tmp.write_text(json.dumps({"items": items, "error": error,
                                  "paginated": True, "pages": pages_fetched}))
    p_tmp.replace(p)
    return items, error


def strip_periodical(rec: dict) -> dict:
    return {k: v for k, v in rec.items() if k not in PERIODICAL_FIELDS}


def _doi_cache_get(cache_dir: Path, doi: str, url: str,
                   extra_headers: dict | None = None) -> dict | None:
    """File-cached single GET by DOI. Returns parsed JSON, None for a
    cached 404 (no record exists). Transient errors are NOT cached —
    re-runs will retry."""
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", doi)
    p = cache_dir / f"{safe}.json"
    if p.exists():
        try:
            d = json.loads(p.read_text())
            # Only trust the cache when the previous fetch reached the
            # server (status 200 or 404). Transient errors get a retry.
            if not d.get("error"):
                return d.get("data")
        except Exception:
            pass
    req = urllib.request.Request(url, headers={**HEADERS, **(extra_headers or {})})
    data = None
    error = None
    try:
        with urllib.request.urlopen(req, timeout=30) as fh:
            data = json.load(fh)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            data = None  # legitimately no record — cache this
        else:
            error = f"HTTP {e.code}"
    except Exception as e:
        error = str(e)
    cache_dir.mkdir(parents=True, exist_ok=True)
    p_tmp = p.with_suffix(".json.tmp")
    p_tmp.write_text(json.dumps({"data": data, "error": error, "url": url}))
    p_tmp.replace(p)
    return data


def fetch_fatcat_by_doi(doi: str) -> dict | None:
    """Look up a fatcat release by DOI (via scholar.archive.org's fatcat
    v2 API — the v0 host api.fatcat.wiki was retired). Files are not
    embedded; follow up with /release/{id}/files."""
    url = ("https://scholar.archive.org/api/fatcat/v2/release/lookup"
           f"?id_type=doi&id_value={urllib.parse.quote(doi)}")
    rec = _doi_cache_get(FATCAT_CACHE, doi, url)
    if not rec or not rec.get("id"):
        return rec
    rid = rec["id"]
    files_url = (f"https://scholar.archive.org/api/fatcat/v2/release/{rid}/files")
    files_resp = _doi_cache_get(FATCAT_FILES_CACHE, rid, files_url)
    rec["_files"] = (files_resp or {}).get("items") or []
    return rec


def fetch_openalex_by_doi(doi: str) -> dict | None:
    url = (f"https://api.openalex.org/works/doi:{urllib.parse.quote(doi, safe='')}"
           f"?mailto={EMAIL}")
    return _doi_cache_get(OPENALEX_CACHE, doi, url)


def fetch_unpaywall_by_doi(doi: str) -> dict | None:
    url = (f"https://api.unpaywall.org/v2/{urllib.parse.quote(doi, safe='')}"
           f"?email={EMAIL}")
    return _doi_cache_get(UNPAYWALL_CACHE, doi, url)


def fetch_pubmed_by_doi(doi: str) -> dict | None:
    """Via Europe PMC's REST API — returns JSON with MeSH, pub types,
    structured abstract, grants. (NCBI EFetch is XML-only for these fields.)"""
    url = ("https://www.ebi.ac.uk/europepmc/webservices/rest/search"
           f"?query=DOI:{urllib.parse.quote(doi)}&resultType=core&format=json")
    data = _doi_cache_get(PUBMED_CACHE, doi, url)
    if not data:
        return None
    results = (data.get("resultList") or {}).get("result") or []
    return results[0] if results else None


def project_fatcat(rec: dict | None) -> dict | None:
    """Slim projection per articles_format.md. Accepts the fatcat v2
    release record with `_files` attached by fetch_fatcat_by_doi."""
    if not rec or not rec.get("id"): return None
    files_out = []
    for f in rec.get("_files") or []:
        urls = [{"url": u.get("url"), "rel": u.get("rel")}
                for u in (f.get("urls") or [])]
        files_out.append({
            "ident":    f.get("id"),
            "sha1":     f.get("sha1"),
            "md5":      f.get("md5"),
            "size":     f.get("size_bytes") or f.get("size"),
            "mimetype": f.get("mimetype"),
            "urls":     urls,
        })
    return {
        "release_ident":   rec.get("id"),
        "work_ident":      rec.get("work_id"),
        "container_ident": rec.get("container_id"),
        "release_stage":   rec.get("release_stage"),
        "files":           files_out,
    }


def project_openalex(rec: dict | None) -> dict | None:
    if not rec: return None
    return {
        "id":              rec.get("id"),
        "concepts":        rec.get("concepts") or [],
        "topics":          rec.get("topics") or [],
        "cited_by_count":  rec.get("cited_by_count"),
        "counts_by_year":  rec.get("counts_by_year") or [],
        "open_access":     rec.get("open_access") or {},
        "authorships":     rec.get("authorships") or [],
    }


def project_unpaywall(rec: dict | None) -> dict | None:
    if not rec: return None
    best = rec.get("best_oa_location") or {}
    return {
        "is_oa":           rec.get("is_oa"),
        "oa_status":       rec.get("oa_status"),
        "best_oa_url":     best.get("url"),
        "best_oa_license": best.get("license"),
        "best_oa_version": best.get("version"),
        "has_repository_copy": any(
            (loc.get("host_type") == "repository")
            for loc in (rec.get("oa_locations") or [])
        ),
    }


def project_pubmed(rec: dict | None) -> dict | None:
    """Europe PMC `result` object → schema's pubmed slim form."""
    if not rec: return None
    if not rec.get("pmid"):
        return None  # not in PubMed
    # Europe PMC sometimes returns descriptorName/qualifierName as a plain
    # string, sometimes as a dict {value, ui, majorTopic_YN}. Handle both.
    def _v(x):
        if isinstance(x, dict): return x.get("value")
        return x
    def _ui(x):
        return x.get("ui") if isinstance(x, dict) else None
    mesh = []
    for h in (rec.get("meshHeadingList") or {}).get("meshHeading") or []:
        d = h.get("descriptorName")
        quals = [_v(q.get("qualifierName"))
                 for q in (h.get("meshQualifierList") or {}).get("meshQualifier") or []]
        mesh.append({
            "id":    _ui(d) or h.get("descriptorName_UI"),
            "term":  _v(d),
            "major": h.get("majorTopic_YN") == "Y",
            "qualifiers": [q for q in quals if q],
        })
    pub_types = []
    for pt in (rec.get("pubTypeList") or {}).get("pubType") or []:
        pub_types.append(pt if isinstance(pt, str) else pt.get("value"))
    grants = []
    for g in (rec.get("grantsList") or {}).get("grant") or []:
        grants.append({
            "agency":   g.get("agency"),
            "grant_id": g.get("grantId"),
            "country":  g.get("country"),
        })
    return {
        "pmid":  rec.get("pmid"),
        "pmcid": rec.get("pmcid"),
        "mesh":  mesh,
        "publication_types":  pub_types,
        "structured_abstract": rec.get("abstractText"),
        "grants": grants,
    }


def label_parts(s):
    s = str(s or "").strip()
    if not s: return {""}
    parts = {s}
    for sep in "-/,":
        for p in s.split(sep):
            p = p.strip()
            if p: parts.add(p)
    return parts


def label_matches(crossref_label, query_label):
    c = str(crossref_label or "").strip()
    q = str(query_label or "").strip()
    if c == q: return True
    if not c or not q: return False
    return bool(label_parts(c) & label_parts(q))


if len(sys.argv) != 3:
    print("usage: build_articles_companion.py <toc.json> <out_articles.json.gz>")
    sys.exit(1)

toc_path = Path(sys.argv[1])
out_path = Path(sys.argv[2])

toc = json.loads(toc_path.read_text())
item = toc["item"]; issn = toc["issn"]
year = toc["year"]; vol = toc["volume"]; iss = toc["issue"]

# Fetch full Crossref records for (issn, year) — all fields, not the slim
# audit-cache fields. articles_format.md requires the full /works/{doi}
# payload (abstract, references, funder, license, dates, ...) minus
# periodical-level fields.
crossref_records, fetch_error = fetch_crossref_full_for_year(issn, year)
if fetch_error and not crossref_records:
    print(f"ERROR: Crossref fetch failed for ({issn}, {year}): {fetch_error}",
          file=sys.stderr)
    sys.exit(2)

xref_issue = [r for r in crossref_records
              if label_matches(r.get("volume"), vol)
              and label_matches(r.get("issue"), iss)]

# Index Crossref records by DOI (lowercase) for fast lookup
by_doi = {(r.get("DOI") or "").lower(): r for r in xref_issue if r.get("DOI")}


# Build the companion file. For every TOC entry with a DOI, hit all
# five sources. Each call is file-cached, so re-runs are free.
entries = {}
src_hits = {"crossref": 0, "fatcat": 0, "openalex": 0,
            "unpaywall": 0, "pubmed": 0}
for e in toc.get("entries") or []:
    doi = (e.get("ext_ids") or {}).get("doi", "") or ""
    doi_l = doi.lower()
    xref = by_doi.get(doi_l)

    fatcat_raw = openalex_raw = unpaywall_raw = pubmed_raw = None
    if doi:
        try:
            fatcat_raw   = fetch_fatcat_by_doi(doi)
        except Exception as ex:
            print(f"  fatcat fetch failed for {doi}: {ex}", file=sys.stderr)
        try:
            openalex_raw = fetch_openalex_by_doi(doi)
        except Exception as ex:
            print(f"  openalex fetch failed for {doi}: {ex}", file=sys.stderr)
        try:
            unpaywall_raw = fetch_unpaywall_by_doi(doi)
        except Exception as ex:
            print(f"  unpaywall fetch failed for {doi}: {ex}", file=sys.stderr)
        try:
            pubmed_raw   = fetch_pubmed_by_doi(doi)
        except Exception as ex:
            print(f"  pubmed fetch failed for {doi}: {ex}", file=sys.stderr)
        time.sleep(0.1)  # be polite across providers

    fatcat    = project_fatcat(fatcat_raw)
    openalex  = project_openalex(openalex_raw)
    unpaywall = project_unpaywall(unpaywall_raw)
    pubmed    = project_pubmed(pubmed_raw)

    # Bubble up any new ext_ids we discovered
    ext_ids = dict(e.get("ext_ids") or {})
    if pubmed and pubmed.get("pmid"):  ext_ids.setdefault("pmid",  pubmed["pmid"])
    if pubmed and pubmed.get("pmcid"): ext_ids.setdefault("pmcid", pubmed["pmcid"])
    if fatcat and fatcat.get("release_ident"):
        ext_ids.setdefault("fatcat_release", fatcat["release_ident"])
    if fatcat and fatcat.get("work_ident"):
        ext_ids.setdefault("fatcat_work", fatcat["work_ident"])
    if openalex and openalex.get("id"):
        ext_ids.setdefault("openalex", openalex["id"].rsplit("/", 1)[-1])

    if xref:      src_hits["crossref"]  += 1
    if fatcat:    src_hits["fatcat"]    += 1
    if openalex:  src_hits["openalex"]  += 1
    if unpaywall: src_hits["unpaywall"] += 1
    if pubmed:    src_hits["pubmed"]    += 1

    record = {
        "toc_entry_id": e["id"],
        "ext_ids": ext_ids,
        "match_method": "doi_lookup" if doi else "no_match",
        "match_confidence": 1.0 if doi else 0.0,
    }
    # Per articles_format.md "absence vs null is not significant": omit
    # source keys for which we found nothing, rather than emit stubs.
    if xref:      record["crossref"]  = strip_periodical(xref)
    if fatcat:    record["fatcat"]    = fatcat
    if openalex:  record["openalex"]  = openalex
    if unpaywall: record["unpaywall"] = unpaywall
    if pubmed:    record["pubmed"]    = pubmed
    entries[e["id"]] = record

today = time.strftime("%Y-%m-%d")
SOURCE_VIA = {
    "crossref":  "live_api_cached",
    "fatcat":    "fatcat_release_lookup",
    "openalex":  "openalex_works_doi_lookup",
    "unpaywall": "unpaywall_v2_doi_lookup",
    "pubmed":    "europe_pmc_search_by_doi",
}
SOURCE_LICENSE = {
    "crossref":  "CC0 (bibliographic shell); abstracts retain publisher copyright",
    "fatcat":    "CC0",
    "openalex":  "CC0",
    "unpaywall": "CC0",
    "pubmed":    "US government work, public domain",
}
sources_used = {s: {"via": SOURCE_VIA[s], "fetched_at": today}
                for s, n in src_hits.items() if n > 0}
licenses_used = {s: SOURCE_LICENSE[s] for s in sources_used}

companion = {
    "schema_version": 1,
    "ia_item": item,
    "toc_schema_version": toc.get("schema_version"),
    "provenance": {
        "software_versions": software_versions(),
        "sources": sources_used,
    },
    "license_notes": licenses_used,
    "entries": entries,
}

# Write gzipped
with gzip.open(out_path, "wt", encoding="utf-8") as fh:
    json.dump(companion, fh, indent=2)

print(f"wrote {out_path} ({out_path.stat().st_size} bytes)")
print(f"  {len(entries)} entries")
for k, v in src_hits.items():
    print(f"  matched_{k:9s} {v}/{len(entries)}")
