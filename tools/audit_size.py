"""Just count: how many unique (issn, vol, issue, year) tuples we'd need
to query Crossref for if we audited all anchors."""
import json
from collections import defaultdict

CORPUS = "/Users/brewster/tmp/segart/tmp/qa_corpus.jsonl"

issues_by_issn = defaultdict(set)
n_anchors = 0
n_with_issn = 0
n_with_vol_iss = 0
journal_titles_no_issn = set()
with open(CORPUS) as f:
    for line in f:
        for a in json.loads(line).get("anchors", []):
            n_anchors += 1
            issn = (a.get("issn") or "").strip()
            vol = (a.get("volume") or "").strip()
            iss = (a.get("issue") or "").strip()
            yr = (a.get("year") or "").strip()[:4]
            if not issn:
                jt = (a.get("journal_title") or "").strip()
                if jt: journal_titles_no_issn.add(jt.lower())
                continue
            n_with_issn += 1
            if vol and iss:
                n_with_vol_iss += 1
                issues_by_issn[issn].add((vol, iss, yr))

n_journals = len(issues_by_issn)
n_unique_issues = sum(len(s) for s in issues_by_issn.values())
print(f"anchors total:                    {n_anchors:>9}")
print(f"anchors with ISSN:                {n_with_issn:>9}")
print(f"anchors with ISSN+vol+iss:        {n_with_vol_iss:>9}")
print(f"unique journals (ISSNs):          {n_journals:>9}")
print(f"unique (issn, vol, iss, year):    {n_unique_issues:>9}")
print(f"journals lacking ISSN (titles):   {len(journal_titles_no_issn):>9}")
print()
# Distribution: how many issues per journal?
sizes = sorted([len(s) for s in issues_by_issn.values()], reverse=True)
print(f"issues per journal: min={sizes[-1]}  median={sizes[len(sizes)//2]}  "
      f"mean={sum(sizes)/len(sizes):.1f}  max={sizes[0]}")
print(f"top 10 journals by # issues:")
top = sorted(issues_by_issn.items(), key=lambda kv: -len(kv[1]))[:10]
for issn, s in top:
    print(f"  {issn}: {len(s)} issues")
print()
# At 20 req/s polite pool
secs = n_unique_issues / 20
print(f"@20 req/s ≈ {secs:.0f}s ({secs/60:.0f} min, {secs/3600:.1f}h)")
