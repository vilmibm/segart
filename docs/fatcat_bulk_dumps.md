# Fatcat bulk metadata dumps

The fatcat catalog is exported periodically as JSONL and TSV to the IA collection [`fatcat_snapshots_and_exports`](https://archive.org/details/fatcat_snapshots_and_exports). For segart, these dumps are how we get a per-issue **candidate article list** at scale without hammering the API.

## Catalog (latest dump: 2024-02-18)

> ⚠️ **Staleness caveat.** As of 2026-05-03 the most recent bulk export is dated 2024-02-18 — about 15 months old. Articles deposited or harvested since then are not represented. Acceptable for back-catalog work; fall back to the live `scholar.archive.org/api/fatcat/v1/*` API for recency-sensitive checks.

There are two parallel series in the collection:

- `fatcat_bulk_exports_<date>` — JSONL/TSV exports (what segart uses).
- `fatcat_sqldump_public_<date>` — full PostgreSQL dump (~150–230 GB compressed). Useful for ad-hoc SQL but heavy for everyday work.

The 2024-02-18 bulk export contains:

| File | Size | Schema |
|---|---:|---|
| `container_export.json.gz` | **25 MB** | One JSONL record per journal/series. Top-level: `ident`, `name`, `issnl`, `issne`, `issnp`, `publisher`, `container_type`, `state`, `revision`. The IA-relevant bits live in `extra.ia.sim.{sim_pubid, year_spans, peer_reviewed}` and `extra.kbart.*` (preservation coverage). |
| `release_export_expanded.json.gz` | **232 GB** | One JSONL record per article. Top-level: `ident`, `work_id`, `title`, `release_date`, `release_year`, `release_stage`, `release_type`, `volume`, `issue`, `pages`, `ext_ids`, `contribs`, `refs`, `abstracts`, `extra`. Embeds `container`, `files`, `filesets`, `webcaptures` as nested objects. |
| `file_export.json.gz` | 25 GB | One JSONL per file artifact: `ident`, `md5`, `sha1`, `sha256`, `size`, `mimetype`, `urls[]` (each `{url, rel}`), `release_ids[]`. Note: `urls[]` is populated here, unlike many live API responses. |
| `release_extid.tsv.gz` | 11 GB | Headerless TSV; columns appear to be `revision_uuid, release_uuid, doi, pmid, pmcid, wikidata_qid, …`. Cheap DOI ↔ release-ident lookups. |
| `file_hashes.tsv.gz` | 15 GB | Compact file-hash index. |
| `abstracts.json.gz` | 18 GB | Article abstracts; useful for fuzzy matching against scan OCR. |
| `creator_export.json.gz` | 914 MB | Author records. |
| `webcapture_export.json.gz` | 2.3 GB | Web archives. |
| `fileset_export.json.gz` | 19 KB | Datasets; effectively empty. |

## Coverage check (verified from container_export.json.gz)

- 199,060 total fatcat containers
- 193,416 (97%) carry an ISSN-L
- **11,424 carry `extra.ia.sim.sim_pubid`** — the universe segart can leverage fatcat for. The project doc cites ~27,000 IA periodical titles, so fatcat covers roughly **40%** of IA's periodical catalog with article-level metadata. The remaining ~60% segart will need to handle from OCR alone, without a fatcat candidate list.

## Recommended workflow for segart

1. **Always pull `container_export.json.gz` first** — 25 MB, trivial to refresh. Use `build_sim_container_index.py` to filter to the ~11,424 SIM-bearing containers and emit a compact JSONL keyed by ISSN-L. That's segart's fatcat-addressable scope.
2. **Stream-process `release_export_expanded.json.gz`** through `zcat | jq` or Python. Filter on `container.ident ∈ SIM-bearing set`, project to a small per-record schema, and emit a per-issue index keyed by `(container_id, volume, issue, year)`. This collapses 232 GB into something segart code can mmap.
3. **Use `release_extid.tsv.gz`** for fast DOI ↔ `release_ident` joins during evaluation.
4. **Optionally pull `file_export.json.gz`** if we want to know which releases already have IA-archived PDFs (vs. which only exist as bibliographic stubs).
5. For anything where recency matters (e.g. checking 2025 deposits), bypass the dump and hit `https://scholar.archive.org/api/fatcat/v1/lookup_release?...`.

## Reminder

Fatcat releases per `(container, volume, issue)` are an **indicator/candidate signal**, not ground truth. Upstream Crossref/PubMed records can disagree with what's actually printed in the IA scan — wrong issue assignments, missing articles, articles in fatcat that were never scanned, page-range mismatches. ILL fulfillment logs remain the closer-to-ground-truth source. See `scholar_archive_org.md`.
