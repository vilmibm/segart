# QA Review: heurxref Pilot Batch

Generated 2026-05-11 17:36 — **49** items published, **15** entries flagged across **12** items.

## Flag breakdown

- **co-located**: 7 — multiple Crossref entries share a start page-index AND title (typically end-of-issue announcements, repeated-title book reviews, or front-matter pairs).
- **extended-to-end**: 8 — last entry whose Crossref deposit was a single start page; span extended to end of visible pages.

## How to QA each entry

1. Click the BookReader link to view the flagged page.
2. Confirm the article's actual extent on the page.
3. If a flag is a false positive (entry is correctly placed), no action needed.
4. If our range is wrong, edit `tmp/audit/pilot_<item>/<item>_toc.json` and re-publish.

## Entries

### `sim_american-journal-of-sports-medicine_march-april-1989_17_2`
Item: https://archive.org/details/sim_american-journal-of-sports-medicine_march-april-1989_17_2?admin=1

- **e25** — _Society News_  
  - position: `n183` to `n204` (printed pp. [['298', '319']])  
  - flag: **extended-to-end** (confidence 0.4)  
  - Last entry whose Crossref deposit was a single start page; span extended to end of visible pages (may over-claim trailing backmatter).  
  - view: https://archive.org/details/sim_american-journal-of-sports-medicine_march-april-1989_17_2/page/n183/mode/1up?admin=1

### `sim_ans_1978-10_1_1`
Item: https://archive.org/details/sim_ans_1978-10_1_1?admin=1

- **e13** — _ANS Open Forum_  
  - position: `n98` to `n98` (printed pp. [['93', '93']])  
  - flag: **co-located** (confidence 0.4)  
  - Multiple Crossref entries share this start page-index. Each kept at 1 page; verify whether boundaries should differ.  
  - view: https://archive.org/details/sim_ans_1978-10_1_1/page/n98/mode/1up?admin=1

### `sim_ans_1990-09_13_1`
Item: https://archive.org/details/sim_ans_1990-09_13_1?admin=1

- **e10** — _RESEARCH FELLOWSHIP_  
  - position: `n92` to `n92` (printed pp. [['85', '85']])  
  - flag: **co-located** (confidence 0.4)  
  - Multiple Crossref entries share this start page-index. Each kept at 1 page; verify whether boundaries should differ.  
  - view: https://archive.org/details/sim_ans_1990-09_13_1/page/n92/mode/1up?admin=1
- **e11** — _SEMINARS, CONFERENCES, AND EXAMS_  
  - position: `n92` to `n92` (printed pp. [['85', '85']])  
  - flag: **co-located** (confidence 0.4)  
  - Multiple Crossref entries share this start page-index. Each kept at 1 page; verify whether boundaries should differ.  
  - view: https://archive.org/details/sim_ans_1990-09_13_1/page/n92/mode/1up?admin=1
- **e12** — _CALL FOR PAPERS_  
  - position: `n92` to `n92` (printed pp. [['85', '85']])  
  - flag: **co-located** (confidence 0.4)  
  - Multiple Crossref entries share this start page-index. Each kept at 1 page; verify whether boundaries should differ.  
  - view: https://archive.org/details/sim_ans_1990-09_13_1/page/n92/mode/1up?admin=1

### `sim_canadian-entomologist_1981-08_113_8`
Item: https://archive.org/details/sim_canadian-entomologist_1981-08_113_8?admin=1

- **e17** — _ERRATA_  
  - position: `n113` to `n116` (printed pp. [['776', '779']])  
  - flag: **extended-to-end** (confidence 0.4)  
  - Last entry whose Crossref deposit was a single start page; span extended to end of visible pages (may over-claim trailing backmatter).  
  - view: https://archive.org/details/sim_canadian-entomologist_1981-08_113_8/page/n113/mode/1up?admin=1

### `sim_clinical-journal-of-sport-medicine_1999-07_9_3`
Item: https://archive.org/details/sim_clinical-journal-of-sport-medicine_1999-07_9_3?admin=1

- **e20** — _Adverse Effects of Oral Creatine Supplementation_  
  - position: `n83` to `n86` (printed pp. [['190', '193']])  
  - flag: **extended-to-end** (confidence 0.4)  
  - Last entry whose Crossref deposit was a single start page; span extended to end of visible pages (may over-claim trailing backmatter).  
  - view: https://archive.org/details/sim_clinical-journal-of-sport-medicine_1999-07_9_3/page/n83/mode/1up?admin=1

### `sim_journal-american-academy-child-adolescent-psychiatry_2013-06_52_6`
Item: https://archive.org/details/sim_journal-american-academy-child-adolescent-psychiatry_2013-06_52_6?admin=1

- **e13** — _Sexual Healing_  
  - position: `n110` to `n110` (printed pp. [['655', '655']])  
  - flag: **co-located** (confidence 0.4)  
  - Multiple Crossref entries share this start page-index. Each kept at 1 page; verify whether boundaries should differ.  
  - view: https://archive.org/details/sim_journal-american-academy-child-adolescent-psychiatry_2013-06_52_6/page/n110/mode/1up?admin=1

### `sim_journal-of-chemical-ecology_1977-03_3_2`
Item: https://archive.org/details/sim_journal-of-chemical-ecology_1977-03_3_2?admin=1

- **e12** — _Announcement_  
  - position: `n126` to `n132` (printed pp. [['239', '239']])  
  - flag: **extended-to-end** (confidence 0.4)  
  - Last entry whose Crossref deposit was a single start page; span extended to end of visible pages (may over-claim trailing backmatter).  
  - view: https://archive.org/details/sim_journal-of-chemical-ecology_1977-03_3_2/page/n126/mode/1up?admin=1

### `sim_journal-of-clinical-pharmacology_2002-11_42_11`
Item: https://archive.org/details/sim_journal-of-clinical-pharmacology_2002-11_42_11?admin=1

- **e14** — _Marijuana Smoking and Head and Neck Cancer_  
  - position: `n210` to `n220` (printed pp. None)  
  - flag: **extended-to-end** (confidence 0.4)  
  - Last entry whose Crossref deposit was a single start page; span extended to end of visible pages (may over-claim trailing backmatter).  
  - view: https://archive.org/details/sim_journal-of-clinical-pharmacology_2002-11_42_11/page/n210/mode/1up?admin=1

### `sim_journal-of-communication_autumn-1993_43_4`
Item: https://archive.org/details/sim_journal-of-communication_autumn-1993_43_4?admin=1

- **e24** — _Acknowledgment_  
  - position: `n192` to `n200` (printed pp. [['191', '199']])  
  - flag: **extended-to-end** (confidence 0.4)  
  - Last entry whose Crossref deposit was a single start page; span extended to end of visible pages (may over-claim trailing backmatter).  
  - view: https://archive.org/details/sim_journal-of-communication_autumn-1993_43_4/page/n192/mode/1up?admin=1

### `sim_journal-of-obstetric-gynecologic-neonatal-nursing-jognn_1995-09_24_7`
Item: https://archive.org/details/sim_journal-of-obstetric-gynecologic-neonatal-nursing-jognn_1995-09_24_7?admin=1

- **e1** — _Advertising of Bottle-feeding Products_  
  - position: `n20` to `n20` (printed pp. [['593', '593']])  
  - flag: **co-located** (confidence 0.4)  
  - Multiple Crossref entries share this start page-index. Each kept at 1 page; verify whether boundaries should differ.  
  - view: https://archive.org/details/sim_journal-of-obstetric-gynecologic-neonatal-nursing-jognn_1995-09_24_7/page/n20/mode/1up?admin=1
- **e2** — _Nursing Education_  
  - position: `n20` to `n20` (printed pp. [['593', '593']])  
  - flag: **co-located** (confidence 0.4)  
  - Multiple Crossref entries share this start page-index. Each kept at 1 page; verify whether boundaries should differ.  
  - view: https://archive.org/details/sim_journal-of-obstetric-gynecologic-neonatal-nursing-jognn_1995-09_24_7/page/n20/mode/1up?admin=1

### `sim_physician-and-sportsmedicine_1983-03_11_3`
Item: https://archive.org/details/sim_physician-and-sportsmedicine_1983-03_11_3?admin=1

- **e20** — _Is Excessive Sweating Healthy?_  
  - position: `n196` to `n198` (printed pp. [['195', '195']])  
  - flag: **extended-to-end** (confidence 0.4)  
  - Last entry whose Crossref deposit was a single start page; span extended to end of visible pages (may over-claim trailing backmatter).  
  - view: https://archive.org/details/sim_physician-and-sportsmedicine_1983-03_11_3/page/n196/mode/1up?admin=1

### `sim_psychology-in-the-schools_1986-04_23_2`
Item: https://archive.org/details/sim_psychology-in-the-schools_1986-04_23_2?admin=1

- **e19** — _Books received recently_  
  - position: `n110` to `n116` (printed pp. [['223', '223']])  
  - flag: **extended-to-end** (confidence 0.4)  
  - Last entry whose Crossref deposit was a single start page; span extended to end of visible pages (may over-claim trailing backmatter).  
  - view: https://archive.org/details/sim_psychology-in-the-schools_1986-04_23_2/page/n110/mode/1up?admin=1

## All published items

49 items have segart `_toc.json` + `_articles.json.gz` + `_docling.json.gz` + review on IA.

- https://archive.org/details/sim_academy-of-management-review_2000-10_25_4?admin=1
- https://archive.org/details/sim_acta-radiologica_2005-10_46_6?admin=1
- https://archive.org/details/sim_advanced-drug-delivery-reviews_1997-01-15_23_1-3?admin=1
- https://archive.org/details/sim_american-journal-of-sports-medicine_march-april-1989_17_2?admin=1
- https://archive.org/details/sim_annual-review-of-psychology_2015_66?admin=1
- https://archive.org/details/sim_ans_1978-10_1_1?admin=1
- https://archive.org/details/sim_ans_1990-09_13_1?admin=1
- https://archive.org/details/sim_aphasiology_july-october-1991_5_4-5?admin=1
- https://archive.org/details/sim_canadian-entomologist_1981-08_113_8?admin=1
- https://archive.org/details/sim_child-abuse-neglect_1995-05_19_5_0?admin=1
- https://archive.org/details/sim_clinical-journal-of-sport-medicine_1999-07_9_3?admin=1
- https://archive.org/details/sim_clinics-in-sports-medicine_1994-04_13_2?admin=1
- https://archive.org/details/sim_clinics-in-sports-medicine_2000-04_19_2?admin=1
- https://archive.org/details/sim_european-journal-of-personality_1987-09_1_3?admin=1
- https://archive.org/details/sim_exceptionality_2007_15_3?admin=1
- https://archive.org/details/sim_futures-uk_2009-05_41_4?admin=1
- https://archive.org/details/sim_hand-clinics_1992-02_8_1?admin=1
- https://archive.org/details/sim_hand-clinics_2004-02_20_1?admin=1
- https://archive.org/details/sim_human-communication-research_2002-04_28_2?admin=1
- https://archive.org/details/sim_infectious-disease-clinics-of-north-america_2003-06_17_2?admin=1
- https://archive.org/details/sim_infectious-disease-clinics-of-north-america_2004-03_18_1?admin=1
- https://archive.org/details/sim_infectious-disease-clinics-of-north-america_2004-06_18_2?admin=1
- https://archive.org/details/sim_infectious-disease-clinics-of-north-america_2014-03_28_1?admin=1
- https://archive.org/details/sim_international-journal-of-group-psychotherapy_1996-01_46_1?admin=1
- https://archive.org/details/sim_international-journal-of-intercultural-relations-ijir_1985_9?admin=1
- https://archive.org/details/sim_issues-in-mental-health-nursing_april-may-2003_24_3?admin=1
- https://archive.org/details/sim_journal-american-academy-child-adolescent-psychiatry_2013-06_52_6?admin=1
- https://archive.org/details/sim_journal-of-adolescent-research_1992-04_7_2?admin=1
- https://archive.org/details/sim_journal-of-chemical-ecology_1977-03_3_2?admin=1
- https://archive.org/details/sim_journal-of-clinical-pharmacology_2002-11_42_11?admin=1
- https://archive.org/details/sim_journal-of-communication_autumn-1993_43_4?admin=1
- https://archive.org/details/sim_journal-of-gerontological-nursing_2012-12_38_12?admin=1
- https://archive.org/details/sim_journal-of-interpersonal-violence_2001-07_16_7?admin=1
- https://archive.org/details/sim_journal-of-obstetric-gynecologic-neonatal-nursing-jognn_1995-09_24_7?admin=1
- https://archive.org/details/sim_journal-of-social-and-clinical-psychology_1986_4_3?admin=1
- https://archive.org/details/sim_journal-of-social-issues_summer-1975_31_3?admin=1
- https://archive.org/details/sim_journal-of-zoology_1991-12_225_4?admin=1
- https://archive.org/details/sim_marine-biology_1991-02_108_1?admin=1
- https://archive.org/details/sim_nursing-clinics-of-north-america_1992-03_27_1?admin=1
- https://archive.org/details/sim_nursing-research_may-june-1991_40_3?admin=1
- https://archive.org/details/sim_personality-and-individual-differences_2002-04-05_32_5_0?admin=1
- https://archive.org/details/sim_physician-and-sportsmedicine_1983-03_11_3?admin=1
- https://archive.org/details/sim_psychiatric-clinics-of-north-america_1989-06_12_2?admin=1
- https://archive.org/details/sim_psychiatric-clinics-of-north-america_1989-09_12_3?admin=1
- https://archive.org/details/sim_psychiatric-clinics-of-north-america_2006-03_29_1?admin=1
- https://archive.org/details/sim_psychiatric-clinics-of-north-america_2013-03_36_1?admin=1
- https://archive.org/details/sim_psychiatry_1956-08_19_3_0?admin=1
- https://archive.org/details/sim_psychology-in-the-schools_1986-04_23_2?admin=1
- https://archive.org/details/sim_rural-special-education-quarterly_fall-2012_31_3?admin=1
