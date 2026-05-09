# segart per-item articles file: `<item>_articles.json.gz`

A bibliographic-enrichment companion to `<item>_toc.json`. One file per IA periodical issue, gzipped JSON, holding everything we know about each article in the issue from external metadata sources (Crossref, fatcat, OpenAlex, Unpaywall, PubMed).

`_toc.json` answers *"where in the scan is each article."*
`_articles.json.gz` answers *"what does the world know about each article."*

See [`toc_format.md`](./toc_format.md) for the TOC file. The two are linked by **`toc_entry_id`** (the `e1`, `e2`, … ordinals defined in `_toc.json`).

## Why distinct from `_toc.json`

| | `_toc.json` | `_articles.json.gz` |
|---|---|---|
| Source of truth for | Segmentation (page-index ranges per entry) | Bibliographic metadata per entry |
| Updated when | Re-segmentation pass runs | External dumps refresh |
| Size | ~5–20 KB / issue | ~80–200 KB / issue (gzipped: ~30–60 KB) |
| Required for an item | Yes | No (optional enrichment) |
| Primary consumers | BookReader, IA UI | Researchers, downstream pipelines |

Bundling them would couple two very different update cadences and force lightweight TOC consumers to download data they don't need.

## Naming and storage

- Filename: `<item>_articles.json.gz` — sibling of `<item>_toc.json` in the IA item file group.
- Compression: gzip on upload (Crossref `reference[]` and `abstract` compress ~6×).
- Encoding: UTF-8 JSON.
- Updates: re-derive when either (a) `_toc.json` is regenerated or (b) any source dump is refreshed.

## Full schema (v1)

```jsonc
{
  "schema_version": 1,
  "ia_item": "sim_new-england-journal-of-medicine_1961-12-28_265_26",

  // pin which _toc.json snapshot this articles file matches.
  // if these don't agree with the current _toc.json, this file is stale.
  "toc_schema_version": 1,
  "toc_generated_at": "2026-05-09T13:00:00Z",

  // ~18% of journals: bonus from Crossref type:journal-issue records
  "issue_doi": null,
  "special_issue_title": null,
  "issue_editors": [],

  "provenance": {
    "generated_at": "2026-05-09T14:00:00Z",
    "generator": { "name": "segart-annotate", "version": "0.1" },
    "sources": {
      "crossref":  { "via": "public_data_file",         "dump_date": "2026-03" },
      "fatcat":    { "via": "release_export_expanded",  "dump_date": "2024-02-18" },
      "openalex":  { "via": "snapshot",                 "dump_date": "2026-04" },
      "unpaywall": { "via": "snapshot",                 "dump_date": "2026-04" },
      "pubmed":    { "via": "baseline",                 "dump_date": "2026-01" }
    }
  },

  "license_notes": {
    "shell": "CC0 / public domain. Crossref bibliographic metadata is treated as facts under US law.",
    "abstracts": "Crossref-deposited abstracts retain the original publisher/author copyright. Stored here as an indexable convenience; downstream redistribution may inherit per-publisher terms.",
    "fatcat":  "CC0",
    "openalex": "CC0",
    "unpaywall": "CC0",
    "pubmed": "US government work, public domain"
  },

  // one entry per _toc.json entry, keyed by toc_entry_id.
  // every TOC entry is mirrored, even if no upstream record was found.
  "entries": {
    "e1": {
      "toc_entry_id": "e1",

      "ext_ids": {
        "doi":             "10.1056/NEJM196112282652601",
        "pmid":            "14462856",
        "pmcid":           null,
        "arxiv":           null,
        "fatcat_release":  "dtoonyptt5d2layb4nlokwk6he",
        "fatcat_work":     "esjkikobxva5hidsopmsygjaie",
        "openalex":        "W2104895729"
      },
      "match_method": "doi_lookup",
      "match_confidence": 1.0,

      "match_signature": {
        "first_page_index": "n26",
        "title_hash": "sha1:abcd1234...",
        "first_page": 1273
      },

      // ----- Crossref: full payload -----
      // Crossref /works/{doi} JSON, verbatim, with the following stripped because
      // they live in the pub_ collection (or the issue.json):
      //   container-title, short-container-title, ISSN, issn-type, publisher,
      //   member, prefix, source
      "crossref": {
        "DOI": "10.1056/NEJM196112282652601",
        "type": "journal-article",
        "title": ["Bacteriologic Flora of the Lower Respiratory Tract"],
        "subtitle": [],
        "original-title": [],
        "short-title": [],
        "author": [
          { "given": "Gustave A.", "family": "Laurenzi", "sequence": "first",
            "ORCID": null, "affiliation": [{ "name": "Seton Hall College of Medicine" }] }
        ],
        "editor": [],
        "translator": [],
        "abstract": "<jats:p>...</jats:p>",
        "subject": ["Medicine"],
        "volume": "265",
        "issue": "26",
        "page": "1273-1278",
        "article-number": null,
        "published-print":  { "date-parts": [[1961, 12, 28]] },
        "published-online": null,
        "issued":           { "date-parts": [[1961, 12, 28]] },
        "created":          { "date-parts": [[2007, 1, 4]], "date-time": "..." },
        "deposited":        { "date-parts": [[2020, 5, 1]],  "date-time": "..." },
        "indexed":          { "date-parts": [[2026, 3, 1]],  "date-time": "..." },
        "license": [
          { "URL": "...", "content-version": "vor",
            "delay-in-days": 0, "start": { "date-parts": [[1961,12,28]] } }
        ],
        "funder": [
          { "name": "NIH", "DOI": "10.13039/100000002", "award": ["AI-12345"] }
        ],
        "reference": [
          { "key": "B1", "DOI": "10.1056/...", "unstructured": "Smith J. ...",
            "author": "Smith", "year": "1958", "journal-title": "...", "volume": "...",
            "first-page": "..." }
        ],
        "reference-count": 42,
        "is-referenced-by-count": 287,
        "update-to":   [],          // retractions / corrections / errata
        "update-policy": null,
        "relation":    {},          // is-version-of, has-preprint, etc.
        "assertion":   [],          // peer-review status, copyright statements
        "link": [
          { "URL": "...", "content-type": "application/pdf",
            "content-version": "vor", "intended-application": "similarity-checking" }
        ],
        "alternative-id": [],
        "URL": "https://doi.org/10.1056/NEJM196112282652601",
        "language": "en",
        "clinical-trial-number": []
      },

      // ----- fatcat: slim, file linkage only -----
      "fatcat": {
        "release_ident": "dtoonyptt5d2layb4nlokwk6he",
        "work_ident":    "esjkikobxva5hidsopmsygjaie",
        "container_ident": "td5cjnem25b35nugn4qftmwcna",
        "release_stage": "published",
        "files": [
          {
            "ident":    "...",
            "sha1":     "...",
            "md5":      "...",
            "size":     412034,
            "mimetype": "application/pdf",
            "urls": [
              { "url": "https://archive.org/download/sim_.../article.pdf",
                "rel": "archive" }
            ]
          }
        ]
      },

      // ----- OpenAlex: slim -----
      "openalex": {
        "id": "https://openalex.org/W2104895729",
        "concepts": [
          { "id": "...", "display_name": "Pulmonary embolism", "level": 3, "score": 0.62 }
        ],
        "topics": [
          { "id": "...", "display_name": "Respiratory infections", "score": 0.71 }
        ],
        "cited_by_count": 287,
        "counts_by_year": [{ "year": 2024, "cited_by_count": 4 }],
        "open_access": {
          "is_oa": false,
          "oa_status": "closed",
          "oa_url": null,
          "any_repository_has_fulltext": false
        },
        "authorships": [
          { "author": { "id": "...", "display_name": "Gustave A. Laurenzi",
                        "orcid": null },
            "institutions": [
              { "id": "...", "display_name": "Seton Hall College of Medicine",
                "ror": "https://ror.org/...", "country_code": "US" }
            ],
            "is_corresponding": true }
        ]
      },

      // ----- Unpaywall: slim -----
      "unpaywall": {
        "is_oa":           false,
        "oa_status":       "closed",
        "best_oa_url":     null,
        "best_oa_license": null,
        "best_oa_version": null,
        "has_repository_copy": false
      },

      // ----- PubMed: slim, biomedical only (null for non-biomed) -----
      "pubmed": {
        "pmid":  "14462856",
        "pmcid": null,
        "mesh": [
          { "id": "D000208", "term": "Acute Disease",
            "major": false, "qualifiers": [] }
        ],
        "publication_types": ["Journal Article"],
        "structured_abstract": null,
        "grants": []
      }
    },

    "e2": {
      "toc_entry_id": "e2",
      "ext_ids": { "fatcat_release": "abc123..." },
      "match_method": "fuzzy_title_volume_issue",
      "match_confidence": 0.78,
      "match_signature": { "first_page_index": "n31", "title_hash": "sha1:...", "first_page": 1278 },
      "crossref":  null,
      "fatcat":    { "release_ident": "abc123...", "files": [] },
      "openalex":  null,
      "unpaywall": null,
      "pubmed":    null
    },

    "e3": {
      "toc_entry_id": "e3",
      "ext_ids": {},
      "match_method": "no_match",
      "match_confidence": 0.0,
      "_note": "Older microfilm; no Crossref/fatcat record found."
    },

    "e4": {
      "toc_entry_id": "e4",
      "ext_ids": {},
      "match_method": "skip",
      "_note": "Advertisement; not attempting bibliographic match."
    }
  }
}
```

## Top-level fields

| Field | Type | Notes |
|---|---|---|
| `schema_version` | int | Bumped on breaking changes. |
| `ia_item` | str | The IA item identifier. |
| `toc_schema_version` | int | Schema version of the `_toc.json` this was built against. |
| `toc_generated_at` | ISO 8601 | `_toc.json` `generated_at` value at build time. Mismatch with current TOC = stale. |
| `issue_doi` | str \| null | From Crossref `type:journal-issue` record (~18% of journals). |
| `special_issue_title` | str \| null | From the issue-DOI record. |
| `issue_editors` | array | Per-issue editors from the issue-DOI record. |
| `provenance` | object | Generator + per-source dump metadata. |
| `license_notes` | object | Free-text notes on license posture per source. See license section below. |
| `entries` | object | Map: `toc_entry_id` → entry record. One per `_toc.json` entry. |

## Per-entry fields

| Field | Type | Notes |
|---|---|---|
| `toc_entry_id` | str | Echoes the key. |
| `ext_ids` | object | All known IDs for this article: `doi`, `pmid`, `pmcid`, `arxiv`, `fatcat_release`, `fatcat_work`, `openalex`. Any subset, all optional. |
| `match_method` | enum | How this entry was tied to upstream data. See enum below. |
| `match_confidence` | float `[0,1]` | Confidence in the match (1.0 for `doi_lookup`, lower for fuzzy). |
| `match_signature` | object \| null | `{first_page_index, title_hash, first_page}` — used to re-tie if `_toc.json` IDs shift. |
| `crossref` | object \| null | Full Crossref `/works/{doi}` payload, periodical fields stripped. |
| `fatcat` | object \| null | Slim — release/work idents, container ident, files[]. |
| `openalex` | object \| null | Slim — concepts, topics, citation counts, OA, authorships. |
| `unpaywall` | object \| null | Slim — OA status, best URL, license. |
| `pubmed` | object \| null | Slim — PMID, MeSH, pub types. Null for non-biomed. |

`null` for a source means "no match found for this entry"; absence vs. null is not significant.

## `match_method` enum

| Value | Meaning |
|---|---|
| `doi_lookup` | TOC entry had a DOI → exact lookup in dumps. Confidence = 1.0. |
| `pmid_lookup` | TOC entry had a PMID → exact lookup. Confidence = 1.0. |
| `fuzzy_title_volume_issue` | No exact ID; matched fatcat by `(container, volume, issue)` + fuzzy title. Confidence per match score. |
| `pubmed_title_match` | Matched PubMed by title within journal+year. Confidence per score. |
| `no_match` | Search attempted, nothing found. |
| `skip` | Did not attempt match (ads, frontmatter, etc.). |

Extensible. Consumers MUST tolerate unknown values.

## Per-source subobject specs

### `crossref`

Stored as the verbatim Crossref `/works/{doi}` JSON, with these fields **stripped** because they belong to the `pub_*` collection or are otherwise redundant:

- `container-title`, `short-container-title`
- `ISSN`, `issn-type`
- `publisher`, `member`, `prefix`, `source`

Everything else from the Crossref record is preserved, including the full `reference[]` array and the `abstract` (subject to the license note below).

### `fatcat`

Slim projection. Only the fields needed for IA file linkage and entity round-trips.

| Field | Notes |
|---|---|
| `release_ident` | Stable fatcat release ID. |
| `work_ident` | Stable fatcat work ID. |
| `container_ident` | Stable fatcat container ID for the journal. |
| `release_stage` | `published`, `accepted`, `submitted`, etc. |
| `files[]` | `{ident, sha1, md5, size, mimetype, urls[]}` — the file artifacts; `urls[]` may include archive.org direct download links. |

Drop everything else fatcat carries (refs, abstracts, contribs) — those are richer in Crossref / OpenAlex.

### `openalex`

Slim projection. The fields where OpenAlex adds genuinely new info beyond Crossref.

| Field | Notes |
|---|---|
| `id` | OpenAlex Work ID (full URL). |
| `concepts[]` | OpenAlex's older concept taxonomy. |
| `topics[]` | OpenAlex's newer topic taxonomy (Wikidata-linked). |
| `cited_by_count` | Forward-citation count. |
| `counts_by_year[]` | Citation counts bucketed by year. |
| `open_access` | OA status, URL, repository availability. |
| `authorships[]` | Author + institution disambiguation, with ROR IDs. |

Drop everything OpenAlex re-shapes from Crossref (title, authors raw names, etc.) — keep canonical Crossref versions instead.

### `unpaywall`

Slim. Just the OA status fields.

| Field | Notes |
|---|---|
| `is_oa` | Boolean. |
| `oa_status` | `gold`, `green`, `bronze`, `hybrid`, `closed`. |
| `best_oa_url` | URL to the best OA copy, if any. |
| `best_oa_license` | License of the best OA copy. |
| `best_oa_version` | `publishedVersion`, `acceptedVersion`, `submittedVersion`. |
| `has_repository_copy` | Boolean. |

### `pubmed`

Biomedical only — null for non-biomed entries. Filled when a Crossref DOI maps to a PMID, or when a TOC entry was matched directly to PubMed.

| Field | Notes |
|---|---|
| `pmid` | PubMed ID. |
| `pmcid` | PubMed Central ID, when present. |
| `mesh[]` | Medical Subject Headings — `{id, term, major, qualifiers[]}`. The single highest-value enrichment for biomed. |
| `publication_types[]` | PubMed pub type tags. |
| `structured_abstract` | When PubMed has a structured (BACKGROUND/METHODS/…) form distinct from Crossref's flat abstract. |
| `grants[]` | NIH/funder grant info, when present. |

## License notes

The `license_notes` field is informational; it does not override per-record `license[]` fields where present (notably Crossref's per-article license[] array stays intact inside the `crossref` subobject).

- **Bibliographic shell** (everything except abstracts): CC0 / public domain. Crossref treats bibliographic metadata as facts; per their own posture and US law, this is freely redistributable.
- **Abstracts**: deposited by publishers, retain original copyright. We store them as an indexing convenience; downstream redistribution inherits per-publisher terms.
- **Fatcat, OpenAlex, Unpaywall**: CC0.
- **PubMed**: US government work, public domain.

## Update cadence and coupling

The articles file is a **function** of (current `_toc.json`) × (current source dumps). Re-derive when either changes.

- **Re-segmentation invalidates this file.** When `_toc.json` is regenerated, re-run the bibliographic join. The DOI lookups are O(1) hash lookups against locally-cached dumps; cost is negligible compared to OCR or LLM extraction.
- **Dump refresh.** Crossref publishes annually; OpenAlex / Unpaywall monthly. Refresh cadence is a deployment policy decision, not a schema concern.
- **Staleness detection.** A consumer comparing `toc_generated_at` here vs. the live `_toc.json` `generated_at` can tell at a glance whether the articles file is current.

## Backfill against `_toc.json` IDs that shift

If `_toc.json` is re-segmented and entry IDs reshuffle (`e1` becomes `e2`, etc.), the articles file becomes invalid by ID. Two recovery paths:

1. **Re-derive from scratch** (preferred). Cheap once dumps are local.
2. **Re-tie via `match_signature`.** Each entry caches `{first_page_index, title_hash, first_page}`. A best-effort matcher can recover tied identities even if `e1`/`e2` shifted, without re-running the bibliographic join.

## Extension policy

- Extra top-level fields and extra per-entry fields are allowed; consumers MUST ignore unknown fields.
- Adding a new value to `match_method` is non-breaking.
- Adding a new source (e.g. `semantic_scholar`) is a new top-level entry under `provenance.sources` and a new per-entry subobject; non-breaking.
- Removing or renaming an existing field bumps `schema_version`.

## Open questions

- **Reference list size**: Crossref `reference[]` for a heavily-cited article can be 30–80 KB on its own. At 6M issues × ~8 articles, this is the dominant cost. We embed it (per the design call) — but a future v2 might offer a `reference_count_only` mode for tight-storage deployments.
- **Continuations across issues**: an article split across two issues currently appears in both `_toc.json` files. Whether the articles file should mark `is_continuation_of: <other_item>/<other_entry_id>` is unresolved.
- **Multi-DOI entries**: a TOC entry that maps to multiple DOIs (rare; multi-part article registered separately) currently stores just one. Schema could accommodate a list, but the cost-benefit for the rare case is unclear.
