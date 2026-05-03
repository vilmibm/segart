# Internet Archive ILL fulfillment logs — schema notes

This document describes the schema of the Internet Archive Interlibrary Loan (ILL) fulfillment log CSVs and how `segart` uses them as ground-truth for periodical-issue segmentation.

## Why these logs matter for segart

When a librarian fulfills an ILL request from an IA scanned periodical, they identify the exact item and leaf range where the requested article lives. Each row therefore asserts:

> "Article X (with title, author, journal/volume/issue/year/pages) is located in IA item Y at leaves [start..stop]."

This is human-vetted ground truth at scale. Segmenter output for a given item can be evaluated by checking, for each ILL ground-truth article, whether a TOC entry exists with overlapping leaf range and a matching title.

## CSV columns

```
time, username, source_identifier, action, full_form
```

| Column | Type | Notes |
|---|---|---|
| `time` | unix int | When this fulfillment record was logged. |
| `username` | str | Staff fulfiller's archive.org email. |
| `source_identifier` | str | The IA item identifier the article was found in (e.g. `sim_new-england-journal-of-medicine_1961-12-28_265_26`). |
| `action` | str | Mostly `create_pdf`. |
| `full_form` | JSON string | The structured request and answer; see below. |

## `full_form` JSON

Two halves: the **query** the patron submitted and the **answer** the staffer recorded.

### Query — `original_request_params`

| Field | Description |
|---|---|
| `provider` | Source of the request: `rapid` (RapidILL), `ids` (IDS), `oclc` (Worldshare). |
| `type` | Always `Article` in samples seen. |
| `journal_title` | Periodical title as recorded by patron. |
| `article_author` | Free-form author string (single field, may be multiple authors comma-separated). |
| `article_title` | The article title. |
| `journal_pages` | Printed page range, e.g. `205-223`, `833`, `99-100`, sometimes with trailing dash. |
| `journal_volume` | May have trailing whitespace. |
| `journal_issue` | May be empty or have trailing whitespace. |
| `journal_year` | 4-digit year. |
| `journal_month` | Often null; sometimes name (`Aug`), sometimes number (`04`). |
| `journal_date` | Usually null. |
| `standard_number` | ISSN. |
| `patron_notes` | Usually empty. |

### Answer — top-level fields in `full_form`

| Field | Description |
|---|---|
| `identifier` | The IA item containing the article. Same as `source_identifier` in outer CSV. |
| `start` | **Array** of leaf identifiers (e.g. `["n26"]`) where the article begins. |
| `stop` | **Array** of leaf identifiers where the article ends. Same length as `start`. |
| `orig_start`, `orig_stop` | Patron-supplied printed page numbers (strings). |
| `normalized_orig_start`, `normalized_orig_stop` | Mirror of `start`/`stop`. |
| `start_time` | Unix int, when the patron submitted. |
| `filename` | Provider-side request ID. |
| `provider`, `fill_type` | Delivery mechanism. |
| `cover_text` | Human-readable summary printed on the delivered PDF. Redundant with structured fields. |
| `unfill_reason` | Present (e.g. `"Not available."`) when the request could not be delivered. |

### Leaf identifiers

Leaves are indexed in the IA Bookreader URL space — `n26` is the 27th leaf image in the scan stack and surfaces as `/page/n26/` in archive.org URLs. `segart` uses these exact strings; do not strip the `n` prefix.

## Important quirks

1. **Discontinuous articles.** `start`/`stop` are arrays because some articles span multiple leaf ranges (e.g. front-of-book article continued in back-of-book). Example from sample data: *Glamour* 1993-04, "his and her brains" → `start: ["n241","n281"]`, `stop: ["n244","n283"]`. The TOC schema must support multi-range articles.

2. **Unfilled rows.** Rows with `unfill_reason` (e.g. `"Not available."`) still carry `start`/`stop` populated by the staffer's investigation. Treat as lower-confidence ground truth — the leaf range was identified but the PDF was not delivered through the normal channel.

3. **Duplicates.** The same request can appear twice within seconds (operator retries). Dedupe by `(identifier, article_title, leaf_ranges)` or by `filename`.

4. **Whitespace.** `journal_volume`, `journal_issue`, etc. frequently have trailing whitespace. Always `.strip()` before comparing.

5. **Identifier ≠ journal.** Occasionally an `identifier` does not correspond to the `journal_title` (e.g. a biorxiv preprint identifier returned in lieu of the published *Nature Methods* version). When using these rows as segmentation ground truth for *this journal*, sanity-check that the identifier prefix is consistent with the journal.

6. **Scale.** The provided sample is one day, ~400 records. Multi-year accumulation will be on the order of 10⁵–10⁶ articles — large enough that grouping by `identifier` should be done streaming.

## Output format produced by `parse_ill_logs.py`

The parser emits JSON Lines. One line per article appearance:

```json
{
  "identifier": "sim_new-england-journal-of-medicine_1961-12-28_265_26",
  "article_title": "Bacteriologic Flora of the Lower Respiratory Tract",
  "article_author": "Laurenzi, G A",
  "journal_title": "The New England Journal of Medicine",
  "issn": "0028-4793",
  "volume": "265",
  "issue": "26",
  "year": "1961",
  "month": null,
  "printed_pages": "1273-1278",
  "leaf_ranges": [["n26", "n31"]],
  "unfill_reason": null,
  "provider": "rapid",
  "ill_request_id": "26587872",
  "request_time": 1776124973
}
```

To group by item:

```bash
python parse_ill_logs.py ill_logs_2026-04-14.csv | jq -s 'group_by(.identifier)'
```
