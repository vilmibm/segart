# Page numbers: docling vs IA's `_page_numbers.json`

## Question

For Internet Archive periodical items, the canonical printed-page label for each leaf comes from `<item>_page_numbers.json` — produced by an IA-side OCR pass over running headers/footers. Docling's PDF layout pass also typed-labels running headers/footers (as `page_footer` / `page_header` items in `content_layer: "furniture"`), and that text frequently contains the printed page number. Can docling improve on the IA-side pipeline?

## Method

Across 55 cached docling documents (`tmp/items/*/<item>_docling.json.gz`):

1. Extracted every `page_footer` / `page_header` text in the `furniture` content layer, attributed to a leaf via `prov[0].page_no`.
2. Parsed each text for short numeric tokens (`\b\d{1,4}\b`) as page-number candidates.
3. Compared per-leaf candidates against IA's `_page_numbers.json`, treating only `confidence >= 50` IA entries as labeled.
4. Categorized each leaf: agree, disagree, IA-only (docling missed), docling-only (potential fill-in), neither.

## Findings

### Pass 1 — naive comparison

```
=== AGGREGATE across 55 items, 11,762 leaves ===
  IA has high-conf pageNumber:        82.4%  (9,689)
  docling has page_footer/header num: 79.1%  (9,305)
  both have a number:                 71.6%  (8,419)
     agree:                            0.9%  (100)
     disagree:                         70.7%  (8,319)
  IA-only (docling missing):          10.8%  (1,270)
  docling-only (potential FILL-IN):    7.5%  (886)
  neither:                            10.1%  (1,187)
```

The 0.9% agreement is alarming on its face but the disagreement pattern is suspiciously systematic. Sampling `sim_academic-medicine_1989-02_64_2`:

```
leaf=15  IA='58'  docling=['1989', '57']
leaf=17  IA='60'  docling=['1989', '59']
leaf=18  IA='61'  docling=['60']
leaf=20  IA='63'  docling=['62']
```

Docling consistently reads `N-1` where IA reads `N`. Off-by-one.

### Pass 2 — cause of the off-by-one

Comparing leaf counts per item:

```
docling_pages - ia_leafs across 55 items:
  delta=0:  6 items
  delta=+1: 49 items
```

**The downloaded PDFs include one generated leading page** (presumably a cover / title insert) that IA's `page_numbers.json` doesn't index. `page_numbers.json` is keyed off the JP2 leaves; the PDF derivative has the cover prepended. So for 49 of 55 items, `docling.page_no = ia.leafNum + 1`.

The 6 zero-delta items span multiple publishers and decades — `disability-and-rehabilitation_2014/2015`, `initiatives_summer-1988`, `journal-of-forensic-sciences_1998-01`, `pediatrics_1977-09`, `psychiatric-clinics-of-north-america_1989-06` — no obvious pattern, just artifact of how each PDF was generated. The robust fix is per-item delta detection, not a global constant: `delta = doc_pages - ia_leafs`, applied once per item.

### Pass 3 — agreement after delta correction

```
both IA+docling have a number:  8,438
agree:                          7,230  (85.7%)
docling fill-in opportunities:    808  (IA blank, docling has candidate)
```

Agreement jumps from 0.9% to **85.7%**. The remaining 14.3% disagreement is mostly:

- Year tokens leaking into candidates (e.g., footer text `"FEBRUARY 1989"` produces `[1989, 57]`; raw `in` test counts both as candidates).
- Per-page OCR drift on either side.
- A few pages where docling labels journal-name-only running heads as `page_footer` and they have no number.

Tightening the candidate filter (1–3 digit non-year tokens, or pick the candidate that fits a monotonic neighbor sequence) would close most of that gap without changing the underlying signal.

### Pass 4 — top fill-in opportunities

Items where docling can label leaves IA left blank, after delta correction:

| leaves | IA labeled | docling labeled | fill-in | item |
|---:|---:|---:|---:|---|
| 269 | 97 | 244 | 158 | `journal-of-forensic-sciences_1998-01_43_1` |
| 201 | 52 | 159 | 116 | `disability-and-rehabilitation_2015_37_14-15` |
| 177 | 61 | 147 |  95 | `disability-and-rehabilitation_2014_36_16-17` |
| 434 | 324 | 382 |  64 | `sim_chest_2006-12_130_6` |
| 176 |  0 |  63 |  63 | `journal-of-cancer-education_spring-2005_20_1` |
| 104 | 35 |  87 |  55 | `experimental-clinical-gastroenterology_january-march-1991_5_1` |
| 305 | 210 | 240 |  44 | `psychiatric-clinics-of-north-america_1989-06_12_2` |

`journal-of-cancer-education_spring-2005_20_1` is the standout: IA had **zero** confident page numbers; docling has 63. The IA pipeline appears to have failed entirely on that issue.

## Recommendations

1. **Use docling page-footer text as a fallback** when an IA entry has `confidence: 0`. ~808 leaves across this 55-item slice — extrapolates substantially at IA scale, and gets us coverage on issues where the IA pipeline failed altogether.

2. **Use docling-vs-IA agreement as a confidence boost** on existing IA labels. Disagreements (after delta correction and candidate filtering) are a small enough set to surface for human review or re-OCR.

3. **Per-item delta detection is non-optional.** A global "+1" rule would be wrong for 6 of 55 items observed here. The detection itself is one-line: `delta = len(docling.pages) - len(ia.pages)`.

4. **Candidate filter** before scoring: drop tokens that match `^(19|20)\d{2}$` (year), prefer tokens that fit a monotonic ±1 sequence with the previous and next leaf's candidates. Likely lifts agreement from 85.7% well into the 90s.

## Reproducing

The investigation script lives inline in chat history (May 2026); the operative numbers come from running the per-leaf comparison over `tmp/items/*/<item>_docling.json.gz` and `tmp/items/*/<item>_page_numbers.json`. To re-run on the current cache, the core loop is:

```python
delta = doc_pages_count - ia_leafs_count
for t in doc.texts:
    if t.content_layer == 'furniture' and 'footer' in t.label:
        leaf = t.prov[0].page_no - delta   # align to ia leafNum
        cands = re.findall(r'\b\d{1,4}\b', t.text)
        # compare to ia_map[leaf]
```
