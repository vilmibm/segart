# QA Review: heurxref Pilot Batch

Generated 2026-05-11 06:27 — **49** items published, **15** entries flagged across **12** items.

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
  - position: `n183` to `n204` (printed pp. [['298', '298']])  
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
  - position: `n113` to `n116` (printed pp. [['776', '776']])  
  - flag: **extended-to-end** (confidence 0.4)  
  - Last entry whose Crossref deposit was a single start page; span extended to end of visible pages (may over-claim trailing backmatter).  
  - view: https://archive.org/details/sim_canadian-entomologist_1981-08_113_8/page/n113/mode/1up?admin=1

### `sim_clinical-journal-of-sport-medicine_1999-07_9_3`
Item: https://archive.org/details/sim_clinical-journal-of-sport-medicine_1999-07_9_3?admin=1

- **e20** — _Adverse Effects of Oral Creatine Supplementation_  
  - position: `n83` to `n86` (printed pp. [['190', '190']])  
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
  - position: `n192` to `n200` (printed pp. [['191', '191']])  
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
