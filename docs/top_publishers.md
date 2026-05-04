# IA periodical publishers by issue volume

## Methodology

Run on 2026-05-04 against the fatcat 2024-02-18 dump. Each pub_* collection identified by `build_pub_collections_index.py` was queried via IA's advancedsearch endpoint with `q=collection:"pub_<slug>" AND mediatype:texts, rows=0` to get the issue count. Counts were then aggregated by publisher.

A second view restricts to pub_* collections that `match_pub_to_fatcat.py` linked to a fatcat container — that excludes most book-of-the-week historical text dumps (ECCO, EEBO, etc.) and gives a closer-to-scholarly view.

## Headline numbers

- **28,002** pub_* collections queried
- **2,606,193** issues counted in total
- **1,433** pubs (5.1%) returned an error or no count — likely transient API timeouts; worth a re-run before relying on individual values
- **16,502** pubs (58.9%) have a fatcat container match

## Important caveats

1. **"Issue" is loose here.** `pub_eighteenth-century` (141,138 items, the #1 individual pub) and `pub_early-english-books-1641-1700` (69,671) are book collections (ECCO, EEBO) grouped under the `pub_*` namespace — each "issue" is actually a book. Most of Open Court Publishing's 729 pubs and 136,235 issues fall in this category too, which is why Open Court tops the raw publisher ranking.
2. **Newspapers dominate the high-volume end.** NYT, Washington Post, LA Times, Christian Science Monitor, Atlanta Constitution, Chicago Tribune, etc. each have tens of thousands of issues at daily cadence. They're real periodical issues but pose a very different segmentation problem than scholarly journals.
3. **Single-pub publisher counts can be misleading.** `Tribune International Corp` ranks high in the raw view because of one ~11k-issue collection (`pub_abendpost-sonntagpost`). Look at the `pubs` column to spot single-collection publishers.
4. **Some publisher names are duplicated.** `Wiley Subscription Services, Inc.` and `John Wiley & Sons, Inc.` are both Wiley; `Reed Business Information US` and `Reed Business Information UK` likewise. A normalization pass would consolidate these.

## Top 30 publishers — raw view

| rank | pubs | issues | publisher |
|---:|---:|---:|---|
| 1 | 729 | 136,235 | Open Court Publishing Co (mostly historical text dumps, see caveats) |
| 2 | 1,185 | 92,767 | ELSEVIER LTD. |
| 3 | 202 | 63,324 | Superintendent of Government Documents |
| 4 | 1 | 32,566 | Constitution Pub. Co. (Atlanta Constitution) |
| 5 | 137 | 31,145 | Out-of-copyright |
| 6 | 1 | 30,672 | Federal Information & News Dispatch, Inc. (Federal Register) |
| 7 | 689 | 28,432 | Springer Science & Business Media |
| 8 | 62 | 21,774 | Penton Media, Inc. |
| 9 | 7 | 18,389 | McGraw Hill Publications Company |
| 10 | 1 | 17,298 | Los Angeles Times Communications LLC |
| 11 | 211 | 13,551 | Blackwell Publishing Ltd. |
| 12 | 15 | 13,510 | American Medical Association |
| 13 | 35 | 13,106 | Reed Business Information US |
| 14 | 11 | 12,319 | H.M. The Stationery Office |
| 15 | 315 | 11,923 | John Wiley & Sons, Inc. |
| 16 | 201 | 11,244 | SAGE PUBLICATIONS, INC. |
| 17 | 1 | 11,155 | Christian Science Publishing Society |
| 18 | 79 | 11,051 | Oxford Publishing Limited (England) |
| 19 | 1 | 10,925 | Tribune International Corp (Abendpost-Sonntagpost) |
| 20 | 167 | 10,547 | Wiley Subscription Services, Inc. |
| 21 | 184 | 9,950 | Cambridge University Press |
| 22 | 3 | 9,946 | International Government Document |
| 23 | 1 | 9,906 | Daily Worker Pub. Co. |
| 24 | 255 | 9,344 | Taylor & Francis Ltd. |
| 25 | 1 | 9,237 | Massachusetts Medical Society (NEJM) |
| 26 | 1 | 9,129 | The Irish Times Trust CLG |
| 27 | 1 | 9,129 | Nash Holdings (Washington Post) |
| 28 | 1 | 8,795 | Harper's Magazine Co (Harper's Weekly) |
| 29 | 30 | 8,097 | University of Chicago Press |
| 30 | 11 | 7,914 | Reed Business Information UK |

## Top 30 individual pub_* collections — raw view

| rank | issues | issn | pub_id | publisher |
|---:|---:|---|---|---|
| 1 | 141,138 | — | pub_eighteenth-century | (book collection: ECCO) |
| 2 | 69,671 | — | pub_early-english-books-1641-1700 | (book collection: EEBO) |
| 3 | 34,902 | — | pub_early-english-books-1475-1640 | (book collection: EEBO) |
| 4 | 32,997 | 0362-4331 | pub_new-york-times | — |
| 5 | 32,566 | 2473-1609 | pub_atlanta-constitution | Constitution Pub. Co. |
| 6 | 32,308 | — | pub_chicago-daily-tribune | — |
| 7 | 30,672 | 0097-6326 | pub_federal-register-find | Federal Information & News Dispatch |
| 8 | 29,580 | 1930-9600 | pub_st-louis-post-dispatch | — |
| 9 | 17,298 | 0458-3035 | pub_los-angeles-times | LA Times |
| 10 | 12,367 | 0891-9526 | pub_enr | McGraw Hill |
| 11 | 12,093 | 2057-4436 | pub_london-gazette | H.M. The Stationery Office |
| 12 | 11,155 | 0882-7729 | pub_christian-science-monitor | CSPS |
| 13 | 10,925 | 0896-3762 | pub_abendpost-sonntagpost | Tribune International |
| 14 | 10,635 | — | pub_united-states-congress-hearings-prints-and-reports | Sup. of Gov. Docs |
| 15 | 9,906 | 2769-4763 | pub_daily-worker | Daily Worker Pub. |
| 16 | 9,820 | — | pub_journal-officiel-de-la-republique-francaise | French gov |
| 17 | 9,237 | 0028-4793 | pub_new-england-journal-of-medicine | MMS |
| 18 | 9,129 | 0791-5144 | pub_irish_times | Irish Times |
| 19 | 9,129 | 0190-8286 | pub_washington-post | Nash Holdings |
| 20 | 8,795 | 0360-2397 | pub_harpers-weekly | Harper's |
| 21 | 8,323 | 0098-7484 | pub_jama | AMA |
| 22 | 7,597 | — | pub_alexandria-gazette-packet | Connection Newspapers |
| 23 | 7,464 | 0013-0613 | pub_economist | The Economist |
| 24 | 7,277 | 0038-6952 | pub_spectator-uk | The Spectator |
| 25 | 7,057 | 1937-4100 | pub_star-news | Halifax Media |
| 26 | 6,996 | 0140-6736 | pub_the-lancet | Elsevier |
| 27 | 6,779 | 0163-2876 | pub_commercial-and-financial-chronicle | — |
| 28 | 6,681 | 0019-2422 | pub_illustrated-london-news | ILN |
| 29 | 6,313 | 0021-633X | pub_jewish-chronicle | — |
| 30 | 6,047 | 0038-1047 | pub_solicitors-journal | — |

## Top 25 publishers — fatcat-matched (scholarly-leaning) view

Restricting to pub_* collections that have a fatcat container match. This filters out most ECCO/EEBO and government text dumps but keeps newspapers since many do have ISSNs and fatcat containers.

| rank | pubs | issues | publisher |
|---:|---:|---:|---|
| 1 | 1,178 | 92,416 | ELSEVIER LTD. |
| 2 | 155 | 44,378 | Superintendent of Government Documents |
| 3 | 1 | 30,672 | Federal Information & News Dispatch, Inc. |
| 4 | 688 | 28,431 | Springer Science & Business Media |
| 5 | 62 | 21,774 | Penton Media, Inc. |
| 6 | 7 | 18,389 | McGraw Hill Publications Company |
| 7 | 1 | 17,298 | Los Angeles Times Communications LLC |
| 8 | 211 | 13,551 | Blackwell Publishing Ltd. |
| 9 | 13 | 13,307 | American Medical Association |
| 10 | 310 | 11,895 | John Wiley & Sons, Inc. |
| 11 | 201 | 11,244 | SAGE PUBLICATIONS, INC. |
| 12 | 1 | 11,155 | Christian Science Publishing Society |
| 13 | 79 | 11,051 | Oxford Publishing Limited |
| 14 | 1 | 10,925 | Tribune International Corp |
| 15 | 167 | 10,547 | Wiley Subscription Services, Inc. |
| 16 | 175 | 9,886 | Cambridge University Press |
| 17 | 255 | 9,344 | Taylor & Francis Ltd. |
| 18 | 1 | 9,237 | Massachusetts Medical Society |
| 19 | 1 | 9,129 | Nash Holdings |
| 20 | 41 | 9,066 | Open Court Publishing Co |
| 21 | 1 | 8,795 | Harper's Magazine Co |
| 22 | 29 | 8,361 | Reed Business Information US |
| 23 | 30 | 8,097 | University of Chicago Press |
| 24 | 11 | 7,914 | Reed Business Information UK |
| 25 | 109 | 7,817 | Lippincott Williams & Wilkins |

If Wiley's two entries are merged that's ~22.4k issues across 477 titles, putting Wiley combined #5.

## Recommended starting targets for segart

Cross-referencing the project doc's "scholarly journals first" guidance with the rankings above, plus a bias toward consistent layouts and high QA leverage:

**Tier 1 — high-leverage scholarly publisher umbrellas.** A single segmenter trained on one publisher's house style should generalize across hundreds of titles:

1. **Elsevier** (1,178 titles, 92,416 issues) — biggest scholarly footprint by far. House style is reasonably consistent.
2. **Springer** (688, 28,431) — second-largest scholarly footprint. STM-heavy.
3. **Wiley** (combined ~477, ~22,442) — broad, multi-discipline.
4. **SAGE** (201, 11,244) — social sciences focused.
5. **Cambridge University Press** (175, 9,886) — humanities and STM.
6. **Taylor & Francis Ltd.** (255, 9,344) — broad.
7. **Blackwell** (211, 13,551) — now part of Wiley but listed separately in IA's metadata; would normalize together.

**Tier 2 — single-title flagships.** Big issue counts in one publication; useful for ILL-matching density and have lots of fatcat releases per issue:

8. **NEJM** (`pub_new-england-journal-of-medicine`, 9,237 issues) — already our worked example; ILL traffic is high.
9. **JAMA** (`pub_jama`, 8,323) — same shape as NEJM.
10. **The Lancet** (`pub_the-lancet`, 6,996) — Elsevier flagship.
11. **Nature** (`pub_nature-uk`, 5,168) — short-form articles, dense layouts.

**Skip in early passes:**
- **ECCO / EEBO / Open Court historical books** — not periodicals; different segmentation problem.
- **Government documents** (`pub_federal-register-find`, `pub_united-states-congress-hearings-...`, `pub_london-gazette`) — different document structure.
- **Newspapers** (`pub_new-york-times`, `pub_washington-post`, `pub_los-angeles-times`, `pub_christian-science-monitor`, `pub_irish_times`, etc.) — different segmentation problem (front-page-jump articles, multi-section issues, no consistent printed page numbering). Worth tackling in a later pass once scholarly is solid.

## Open follow-ons

- **Re-run** to recover the 1,433 pubs (5.1%) that errored on the count query — likely transient.
- **Publisher-name normalization** to merge `Wiley Subscription Services` ↔ `John Wiley & Sons`, `Reed Business Information US` ↔ `Reed Business Information UK`, etc.
- **Filter on `pub_type`/`scholarly`** fields from the pub_collections index to get a tighter "scholarly journals only" cut. The current matched-to-fatcat view is the proxy; the IA-side `scholarly:true` flag would be more direct.
- **Per-title issue-count distribution** within each top publisher, to identify the few flagship titles vs. the long tail.
