# segart per-item TOC file: `<item>_toc.json`

## Background — what's already on the rails

For an IA item, BookReader's chapters plugin (`src/plugins/plugin.chapters.js`) has three TOC data sources, in priority order:

1. `this.br.options.table_of_contents` — passed in by the embedding page.
2. `${olHost}/books/${openLibraryId}.json` — if an OL edition is linked.
3. `${olHost}/query.json?type=/type/edition&ocaid=...` (and a `source_records=ia:...` fallback) — OL lookup by IA item id.

It does **not** read: any IA-side derive file (`_toc.xml`, `_chapters.json`, `_scandata.xml`), PDF outlines, hOCR chapter markers (`ocr_chapter`), or any IA metadata field. Empirically: rendering `archive.org/details/sim_new-england-journal-of-medicine_1961-12-28_265_26` and grepping the served HTML for `table_of_contents` and `chapter(s)` returns zero matches — no TOC reaches BookReader for this issue.

So the de facto path today is **IA item → OL edition → BookReader**. The canonical OL `TocEntry` (from `openlibrary/plugins/upstream/table_of_contents.py`):

```python
@dataclass
class TocEntry:
    level: int
    label: str | None
    title: str | None
    pagenum: str | None
    authors: list[AuthorRecord] | None
    subtitle: str | None
    description: str | None
```

That covers some of what segart needs but is missing: `leaf` (the BookReader plugin uses it if present but it's not in the OL schema), DOI / fatcat release_id / scholar.archive.org work_id, multi-range support (front-of-book / back-of-book continuations), confidence, evidence trail, structured affiliations, and an entry-type field. OL is also book-centric — minting an OL edition per periodical issue at scale would be heavy and culturally awkward.

## Decision

segart publishes a rich, segart-native TOC alongside each IA item, named `<item>_toc.json`. It is the source of truth. A narrower BookReader-compatible projection can be derived from it later, either by an IA-side bootstrap change or a BookReader plugin extension — both are feature requests segart files but does not block on.

## File schema (v2)

One JSON object per IA item, written to `<item>_toc.json` in the item's file group.

> **v1 → v2 changes (2026-05-09):**
> - Renamed `leaf_count` → `page_index_count` and `leaf_ranges` → `page_index_ranges`. The `nN` values are IA's 0-indexed page-access counter ("page index"), not leaves (a leaf is a physical sheet = recto + verso = two pages). The previous name was a misnomer.
> - `printed_pages` is now an array of `[start, end]` string pairs (mirroring `page_index_ranges`), so split / continued articles can carry non-contiguous printed-page ranges. Was a single string in v1.

```json
{
  "schema_version": 2,
  "item": "sim_new-england-journal-of-medicine_1961-12-28_265_26",
  "pub_collection": "pub_new-england-journal-of-medicine",
  "container_id": "td5cjnem25b35nugn4qftmwcna",
  "issn": "0028-4793",
  "journal_title": "The New England Journal of Medicine",
  "volume": "265",
  "issue": "26",
  "issue_date": "1961-12-28",
  "year": 1961,
  "page_index_count": 105,
  "generated_at": "2026-05-04T12:00:00Z",
  "generator": {
    "name": "segart",
    "version": "0.1",
    "method": "llm-extract+ill-anchor"
  },
  "entries": [
    {
      "id": "e1",
      "type": "article",
      "title": "Bacteriologic Flora of the Lower Respiratory Tract",
      "subtitle": null,
      "authors": [
        {
          "name": "Gustave A. Laurenzi",
          "affiliation": "Assistant professor of medicine, Seton Hall College of Medicine, Jersey City, New Jersey"
        },
        {
          "name": "Robert T. Porter",
          "affiliation": "Instructor in surgery, Columbia University College of Physicians and Surgeons"
        },
        {
          "name": "Edward H. Kass",
          "affiliation": "Associate professor of bacteriology and immunology, Harvard Medical School"
        }
      ],
      "page_index_ranges": [["n26", "n31"]],
      "printed_pages": [["1273", "1278"]],
      "ext_ids": {
        "doi": "10.1056/NEJM196112282652601",
        "pmid": "14462856",
        "fatcat_release": "dtoonyptt5d2layb4nlokwk6he",
        "fatcat_work": "esjkikobxva5hidsopmsygjaie"
      },
      "confidence": 0.94,
      "evidence": ["ill", "scholar"],
      "level": 1,
      "label": null
    }
  ]
}
```

### Top-level fields

| Field | Type | Notes |
|---|---|---|
| `schema_version` | int | Bumped when breaking changes happen. v2 here. |
| `item` | str | The IA item identifier. |
| `pub_collection` | str \| null | The IA `pub_*` slug, when known (see `match_pub_to_fatcat.py`). |
| `container_id` | str \| null | Fatcat container ident, when matched. |
| `issn` | str \| null | ISSN-L preferred. |
| `journal_title`, `volume`, `issue`, `issue_date`, `year` | various | Standard issue-level metadata. |
| `page_index_count` | int | Total page indices (IA `nN` access-counter values) in the scan; useful for sanity-checking ranges. |
| `generated_at` | ISO 8601 | When this TOC was produced. |
| `generator` | object | `{name, version, method}` — provenance for the run that produced this TOC. |
| `entries` | array | Articles and other items, in physical-page order. |

### Per-entry fields

| Field | Type | Notes |
|---|---|---|
| `id` | str | Stable within this TOC. Convention: `e<n>` ordinal, but opaque. |
| `type` | str | `article` \| `editorial` \| `letter` \| `review` \| `book_review` \| `news` \| `advertisement` \| `toc` \| `frontmatter` \| `backmatter` \| `other`. Extensible. |
| `title` | str \| null | The article title. |
| `subtitle` | str \| null | If printed separately. |
| `authors` | array \| null | Each: `{name, affiliation?, fatcat_creator?}`. Empty array allowed for known-anonymous; null for unknown. |
| `page_index_ranges` | array of `[start, end]` pairs | Page-index identifiers (IA's 0-indexed `n<int>` access-counter strings). Multi-range supported for split / continued articles. **Required.** |
| `printed_pages` | array of `[start, end]` string pairs \| null | As-printed page ranges, e.g. `[["1273", "1278"]]` or `[["S1", "S4"], ["A12", "A12"]]`. Strings (not ints) since printed pages can be Roman (`i`–`iv`), prefixed (`S1`, `e2`), or letter-suffixed. Mirrors the multi-range shape of `page_index_ranges`. Null when the printed pagination is unknown. |
| `ext_ids` | object | `{doi, pmid, pmcid, arxiv, fatcat_release, fatcat_work, scholar_work, ...}` — any combination, all optional. |
| `confidence` | float in `[0,1]` | Overall confidence segart has in this entry. |
| `evidence` | array of str | Which signals support this entry: `ill` (matched an ILL log), `scholar` (matches a fatcat release for this `(container, volume, issue)`), `ocr` (LLM-extracted from scan only), `manual` (human curator). Empty array means unsupported. |
| `level` | int | Hierarchy depth. Default `1` for flat article lists; `2`+ for sub-entries (e.g. parts of a symposium). Mirrors OL `level` for projection. |
| `label` | str \| null | Section/part label if any (e.g. `"Original Article"`). Mirrors OL `label`. |

### Extension policy

- Extra top-level fields and extra per-entry fields are allowed; consumers MUST ignore unknown fields.
- Adding a new value to `type`, `evidence`, or `ext_ids` is non-breaking.
- Removing or renaming an existing field bumps `schema_version`.

## BookReader projection

A consumer wanting to feed BookReader's chapters plugin can derive an OL-compatible TOC from this file:

| BookReader / OL field | Source in `<item>_toc.json` |
|---|---|
| `level` | `entry.level` |
| `label` | `entry.label` |
| `title` | `entry.title` |
| `pagenum` | `entry.printed_pages[0][0]` (or null) |
| `leaf` | `int(entry.page_index_ranges[0][0].lstrip("n"))` — note BookReader's `leaf` field is also a misnomer for page-index, but we project to it as named for compatibility |
| `authors` | `entry.authors` (already shaped compatibly; OL uses `{name, author?}`) |
| `subtitle` | `entry.subtitle` |
| `description` | optionally a one-line synthesized abstract |

Multi-range (`page_index_ranges` with more than one pair) is collapsed to the first range's start in the projection, since OL's TocEntry has no equivalent. The full ranges remain in the segart file. Same applies to `printed_pages`.

## Where the file lives

In an item's file group at archive.org, alongside `<item>_djvu.xml`, `<item>_hocr.html`, etc. The naming follows IA's convention of `<item>_<role>.<ext>`. To upload, segart uses the IA `ia` CLI / S3-compatible API; the file becomes part of the standard derive set. Updating the file (re-segmentation passes) is a normal IA item update.

## Companion: `<item>_articles.json.gz`

`_toc.json` answers *where* in the scan each article lives. A separate companion file, `<item>_articles.json.gz`, answers *what the world knows* about each article — full Crossref payload (with abstracts and references), plus slim projections from fatcat (file linkage), OpenAlex (topics, OA, citation counts), Unpaywall (OA status), and PubMed (MeSH, biomed only).

The two files are linked by **`toc_entry_id`** (the `e<n>` ordinal defined here). Every TOC entry is mirrored in the articles file, including those without DOIs; the articles file uses `match_method` and `match_confidence` to record how (or whether) each entry was tied to upstream metadata.

Full schema: [`articles_format.md`](./articles_format.md).

## Forward paths (feature requests)

Once segart is producing useful `<item>_toc.json` files at scale, two follow-on requests make BookReader UI display straightforward:

1. **Extend BookReader's chapters plugin** to add a fourth source: fetch `<item>_toc.json` from the IA item directly when `options.table_of_contents` and OL lookups are both empty. The plugin would consume the BookReader projection above; the projection-derivation code is small and could ship in the plugin or be done client-side.
2. **Or extend IA's details-page bootstrap** to read `<item>_toc.json` server-side and inject the projection into `options.table_of_contents`. Cheaper for the BookReader maintainers; requires IA-side cooperation.

Both are non-blocking — segart's value (richer per-article metadata, ILL/scholar cross-references, evaluation-ready TOCs) accrues regardless of whether BookReader displays them.

## Open questions

- **Article-spanning issues.** Some periodicals number articles across an issue boundary (a series, or a combined issue). The current schema treats one IA item = one TOC; cross-issue continuations would need a separate work-level join table.
- **Front/back matter granularity.** Should every advertisement be a TOC entry, or grouped? Default: skip ads unless they're indexed in the printed TOC.
- **Confidence calibration.** What 0/1 means in practice — likely emerges from the evaluation loop against ILL logs and won't be settled in v1.
