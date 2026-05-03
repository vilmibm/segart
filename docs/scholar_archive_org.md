# scholar.archive.org / fatcat — what segart can use

`scholar.archive.org` is the Internet Archive's full-text search index over scholarly content. Underneath it sits **fatcat**, the bibliographic database. For segart, fatcat is the canonical answer to *"what articles exist in this journal issue?"* — a second ground-truth source orthogonal to the ILL fulfillment logs.

## Data model (lightweight FRBR)

| Entity | What it is | Fields most relevant to segart |
|---|---|---|
| **Release** | A specific published article version. | `ident`, `work_id`, `container_id`, `title`, `release_date`, `release_year`, `release_stage`, `volume`, `issue`, `pages`, `contribs[]`, `ext_ids` (`doi`, `pmid`, `pmcid`, `arxiv`, `jstor`, `mag`, `wikidata_qid`, etc.) |
| **Work** | Abstract grouping; preprint, published, etc. share one `work_id`. | `ident` |
| **Container** | The journal/series. | `ident`, `name`, `issnl`, `issne`, `issnp`, `publisher`, `wikidata_qid`, `extra.ia.sim.sim_pubid`, `extra.ia.sim.year_spans` |
| **File** | A digital artifact (PDF, etc.). | `ident`, `md5`, `sha1`, `sha256`, `size`, `mimetype`, `urls[]` (often empty for "dark" preservation-only), `release_ids[]` |
| **Creator** | Author personas. | `ident`, names, external IDs |
| **Fileset**, **Webcapture** | Datasets, web snapshots. | Less relevant here. |

Entities cross-reference by `ident`, not revision, so metadata can evolve while persistent links remain stable.

## What segart pulls from fatcat

1. **Per-issue article roster.** Given an IA periodical issue, the canonical article set is releases with matching `container_id` + `volume` + `issue`. This is the densest article-level ground truth available — most issues have a fatcat record per article from Crossref/PubMed harvests.
2. **DOI / PMID round-trip.** Lookup canonical release by DOI/PMID, recover work_id and container_id.
3. **IA item ↔ container link.** IA SIM item IDs follow the pattern
   ```
   sim_<container-slug>_<YYYY-MM-DD>_<volume>_<issue>
   ```
   e.g. `sim_new-england-journal-of-medicine_1961-12-28_265_26`. Given a fatcat container plus a release's `release_date`/`volume`/`issue`, the expected IA item ID is constructible.
4. **IA's internal pub ID.** `container.extra.ia.sim.sim_pubid` (e.g. `"693"` for NEJM) — IA's own periodical pub identifier, useful when joining against IA's own catalog.
5. **Coverage signal for prioritization.** `container.extra.ia.sim.year_spans` indicates which years IA holds SIM scans for. Combined with `kbart.{hathitrust,lockss,portico}` it gives a rough preservation map — segart can prioritize titles/years where IA has scans but downstream coverage is sparse.

## What fatcat does *not* give us

- **No leaf or page numbers.** `release.pages` is printed-page numbers (e.g. `"1273-1278"`), not leaf indices. Linking each release to its `start_leaf`/`stop_leaf` inside the IA scan is exactly what segart must produce. Fatcat tells us *which* articles exist; segart determines *where* in the leaf stack each lives.
- **`file.urls` is often empty.** Many IA-held files are "dark" preservation-only and carry no public URLs in the fatcat record. The archive.org link rendered on scholar work pages is constructed at render time from container + release metadata, not stored on the file.
- **Patchy ext_id coverage.** Pre-1970 and many non-English titles lack DOIs. ILL log → fatcat matching has to fall back to fuzzy title + container + volume/issue/year matching when no DOI is present.

## Endpoints

OpenAPI spec at `https://scholar.archive.org/openapi.json`. All endpoints are GET, JSON, no auth.

| Endpoint | Use |
|---|---|
| `/api/fatcat/v1/lookup_release?extid_type=doi&extid_value={doi}` | Resolve a release by DOI/PMID/PMCID/etc. |
| `/api/fatcat/v1/lookup_container?extid_type=issnl&extid_value={issn}` | Resolve a journal container by ISSN-L. |
| `/api/fatcat/v1/get_release/{ident}?expand=files,container` | Full release with embedded container + files. |
| `/api/fatcat/v1/get_release_files/{ident}` | Files attached to a release. |
| `/api/fatcat/v1/get_container/{ident}` | Container by fatcat ident. |
| `/api/fatcat/v1/get_work/{ident}` / `/get_work_releases/{ident}` | Work entity and all its release versions. |
| `/api/fatcat/v1/get_creator/{ident}` / `/get_creator_releases/{ident}` | Author entity and their releases. |

Fatcat-side full-text/structured search (releases by `container_issnl` + `volume` + `issue`, etc.) is not in this OpenAPI surface; it lives in the scholar search index at `https://scholar.archive.org/search?q=...`. As of 2026-05, that endpoint is returning HTTP 405 — there is a banner in the rendered HTML noting an archive.org-wide degradation. Don't depend on `/search` for the evaluation loop until it recovers; use bulk dumps as a substitute.

## Bulk dumps (preferred at scale)

Full fatcat metadata is exported as JSONL plus a periodic ~100 GB compressed PostgreSQL dump:

> https://archive.org/details/fatcat_snapshots_and_exports

For segart's evaluation loop, the relevant dumps are `release_export*.json.gz` and `container_export*.json.gz`. A streaming filter on (release_type=`article-journal`, container_id ∈ {SIM-covered containers}) yields a per-issue article ground-truth list far cheaper than per-record API calls.

## Worked example

NEJM 1961-12-28, vol 265, issue 26, page 1273 — the example from the project doc.

| Source | Value |
|---|---|
| ILL log answer | `sim_new-england-journal-of-medicine_1961-12-28_265_26`, leaves `n26`–`n31` |
| Fatcat release | `dtoonyptt5d2layb4nlokwk6he` |
| Fatcat work | `esjkikobxva5hidsopmsygjaie` |
| Fatcat container | `td5cjnem25b35nugn4qftmwcna` |
| ISSN-L | `0028-4793` |
| `sim_pubid` | `693` |
| Release `pages` | `"1273-1278"` (printed pages, not leaves) |
| DOI | `10.1056/nejm196112282652601` |
| PMID | `14462856` |

Segart's job: produce a TOC entry for this IA item that pairs `release dtoonyptt5d2layb4nlokwk6he` (or the bibliographic tuple) with leaves `n26`–`n31`. The ILL log gives one such ground-truth pair; fatcat gives the full expected article list for the issue.
