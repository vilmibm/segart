"""Per-journal Crossref reliability audit.

For each (journal ISSN, year-bucket), measure: of the articles ILL
patrons asked for, what % did Crossref have a matching record for in
the queried (issn, vol, issue) response?

- Title fuzzy match: normalize, drop stopwords, take first 4 content
  words on each side, SequenceMatcher >= 0.8 -> hit.
- Year bucketing: 5-year windows.
- Per-(journal, bucket) early-stop: after K consecutive zero-hit
  issues within a bucket, skip remaining issues in that bucket but
  keep checking other buckets.
- Streaming output: append per-journal result to JSONL as we finish each.
- Resumable: skip ISSNs already in the output file.

Usage:
  python3 journal_xref_audit.py --out /tmp/journal_xref_audit.jsonl \
      [--max-journals N] [--polite-mailto you@example.com]
"""
import argparse
import concurrent.futures
import json
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path

SEGART = Path("/Users/brewster/tmp/segart")
CORPUS = SEGART / "tmp" / "qa_corpus.jsonl"
CACHE_DIR = SEGART / "tmp" / "crossref_journal_year_cache"
DEFAULT_OUT = SEGART / "tmp" / "audit" / "journal_xref_audit.jsonl"

STOPWORDS = {
    "the","a","an","of","and","in","on","for","to","with","by","from",
    "la","le","les","der","die","das","el","los","il","de","du",
    "is","at","as","that","this","be","or","not","but",
}


def normalize(s):
    if not s: return []
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return [w for w in s.split() if w and w not in STOPWORDS and len(w) > 1]


def fuzzy_first_n_match(a, b, n=4, ratio=0.8):
    wa = normalize(a)
    wb = normalize(b)
    if not wa or not wb: return False
    # Loosened: accept identical 1-word titles ("Murder" == "Murder").
    # Single-word ILL anchors are common for short article titles.
    if len(wa) >= 1 and len(wb) >= 1 and wa[0] == wb[0] and (len(wa) == 1 or len(wb) == 1):
        return True
    k = min(n, len(wa), len(wb))
    if k < 2: return False
    ha = " ".join(wa[:k])
    hb = " ".join(wb[:k])
    if ha == hb: return True
    return SequenceMatcher(None, ha, hb).ratio() >= ratio


def is_junk_anchor(article_title, journal_title):
    """Anchor's article_title is essentially the journal name (a common ILL
    metadata error where the title field got the journal name)."""
    if not article_title or not journal_title: return False
    a = normalize(article_title); j = normalize(journal_title)
    if not a or not j: return False
    sa = " ".join(a); sj = " ".join(j)
    if not sa or not sj: return False
    if sa in sj or sj in sa: return True
    return SequenceMatcher(None, sa, sj).ratio() >= 0.85


def extract_surnames(s):
    """Pull surnames from a free-form ILL author string."""
    if not s: return set()
    surnames = set()
    for piece in re.split(r"\s*;\s*|\s+and\s+|\s*&\s*|\s*\u2219\s*", s):
        piece = piece.strip()
        if not piece: continue
        if "," in piece:
            surname_text = piece.split(",", 1)[0]
            words = re.findall(r"[A-Z][a-zA-Z'\u00c0-\u017f-]+", surname_text)
            if words:
                surnames.add(words[0].lower())
        else:
            words = re.findall(r"[A-Z][a-zA-Z'\u00c0-\u017f-]+", piece)
            long_words = [w for w in words if len(w) >= 3]
            chosen = long_words or words
            if chosen:
                surnames.add(chosen[-1].lower())
    return surnames


def author_surname_match(anchor_author, xref_records):
    """Within an issue, author surname collisions are very rare. If any
    Crossref author surname matches an ILL author surname, treat as a hit."""
    anchor_set = extract_surnames(anchor_author or "")
    if not anchor_set: return False
    for r in xref_records:
        for a in r.get("author") or []:
            fam = (a.get("family") or "").strip().lower()
            if fam and fam in anchor_set:
                return True
    return False


def year_bucket(year_str, span=5):
    try:
        y = int(str(year_str)[:4])
    except (ValueError, TypeError):
        return "unknown"
    if y < 1900 or y > 2030:
        return "unknown"
    lo = (y // span) * span
    return f"{lo}-{lo + span - 1}"


_cache_lock = threading.Lock()


def _cache_path(issn, year):
    safe = re.sub(r"[^A-Za-z0-9-]", "_", issn)
    return CACHE_DIR / f"{safe}_{year}.json"


def fetch_crossref_year(issn, year, mailto):
    """Fetch all journal-articles for (issn, year) from Crossref. File-cached
    by (issn, year). Uses cursor pagination so prolific journal-years
    (>200 articles/year) aren't silently truncated at the 200-row cap.

    Cache invariants:
      - paginated=True + items: trust cache (already complete)
      - paginated=True + 0 items + 429 error: previously wiped; refetch
      - len==200 + not paginated: pre-cursor truncated cache; refetch
      - else (len < 200): complete; trust

    On 429, retries with exponential backoff (up to 5 attempts).
    On final failure, DOES NOT overwrite a cache that had data — preserves
    the old data so a transient rate-limit doesn't destroy good cache.
    """
    p = _cache_path(issn, year)
    old_items, old_error = None, None
    if p.exists():
        try:
            d = json.loads(p.read_text())
            old_items = d.get("items", [])
            old_error = d.get("error")
            paginated = d.get("paginated")
            # Refetch when:
            #   - cached error exists (transient failure, try again)
            #   - exactly 200 records with no paginated marker (legacy
            #     pre-cursor truncation)
            # Otherwise trust the cache.
            should_refetch = bool(old_error) or (len(old_items) == 200 and not paginated)
            if not should_refetch:
                return old_items, old_error
        except Exception:
            old_items, old_error = None, None  # corrupted; refetch

    items, error = [], None
    cursor = "*"
    pages_fetched = 0
    while True:
        qs_dict = {
            "rows": 200,
            "filter": (f"type:journal-article,from-pub-date:{year}-01,"
                       f"until-pub-date:{year}-12"),
            "select": "DOI,title,page,volume,issue,author",
            "cursor": cursor,
        }
        if mailto:
            qs_dict["mailto"] = mailto
        qs = urllib.parse.urlencode(qs_dict)
        url = f"https://api.crossref.org/journals/{issn}/works?{qs}"
        req = urllib.request.Request(url, headers={
            "User-Agent": f"segart-journal-audit/0.1 (mailto:{mailto or 'unknown'})",
        })
        # Retry with exponential backoff on 429
        attempt = 0
        last_err = None
        page_items = None
        next_cursor = None
        while attempt < 5:
            try:
                with urllib.request.urlopen(req, timeout=60) as fh:
                    data = json.load(fh)
                msg = data.get("message", {})
                page_items = msg.get("items", [])
                next_cursor = msg.get("next-cursor")
                last_err = None
                break
            except urllib.error.HTTPError as e:
                last_err = f"HTTP Error {e.code}: {e.reason}"
                if e.code == 429:
                    delay = 5 * (2 ** attempt)  # 5, 10, 20, 40, 80s
                    time.sleep(delay)
                    attempt += 1
                    continue
                break
            except Exception as e:
                last_err = str(e)
                break

        if last_err:
            error = last_err
            break
        items.extend(page_items or [])
        pages_fetched += 1
        if not page_items or not next_cursor or next_cursor == cursor:
            break
        cursor = next_cursor
        if pages_fetched >= 50:  # safety cap
            break

    # Cache-preservation: if we failed and old cache had real data, KEEP it.
    if error and old_items:
        return old_items, old_error

    # write cache atomically
    with _cache_lock:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({
        "items": items, "error": error, "paginated": True,
        "pages": pages_fetched,
    }))
    tmp.replace(p)
    return items, error


def filter_to_issue(records, vol, iss):
    return [r for r in records
            if str(r.get("volume","")).strip() == str(vol).strip()
            and str(r.get("issue","")).strip() == str(iss).strip()]


def already_done(out_path):
    if not out_path.exists(): return set()
    done = set()
    with open(out_path) as fh:
        for line in fh:
            try:
                d = json.loads(line)
                done.add(d["issn"])
            except Exception:
                pass
    return done


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(DEFAULT_OUT),
                    help=f"Output JSONL (default: {DEFAULT_OUT})")
    ap.add_argument("--max-journals", type=int, default=None)
    ap.add_argument("--polite-mailto", default="brewster@archive.org")
    ap.add_argument("--bucket-span", type=int, default=5)
    ap.add_argument("--early-stop-zeroes", type=int, default=3,
                    help="After K consecutive zero-hit issues within a "
                         "year bucket, skip remaining issues in that bucket")
    ap.add_argument("--per-request-sleep", type=float, default=0.0)
    ap.add_argument("--workers", type=int, default=8,
                    help="Concurrent Crossref API connections")
    ap.add_argument("--min-anchors", type=int, default=3,
                    help="Skip journals with fewer than this many ILL anchors")
    args = ap.parse_args()

    out_path = Path(args.out)
    done = already_done(out_path)
    print(f"already done: {len(done)} journals", file=sys.stderr)

    # --- Load anchors and group by (issn, vol, issue, year)
    # Each entry: (title, author). Junk anchors (article_title ≈ journal_title)
    # are dropped because they're useless as ground truth.
    anchors_per_issue = defaultdict(list)  # (issn, vol, iss, year) -> [(title, author)]
    n_anchors = 0
    n_junk = 0
    with open(CORPUS) as f:
        for line in f:
            line = line.strip()
            if not line: continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            for a in rec.get("anchors", []):
                issn = (a.get("issn") or "").strip()
                vol  = (a.get("volume") or "").strip()
                iss  = (a.get("issue") or "").strip()
                yr   = (a.get("year") or "").strip()[:4]
                title = (a.get("article_title") or "").strip()
                author = (a.get("article_author") or "").strip()
                jt = (a.get("journal_title") or "").strip()
                if not (issn and vol and iss and yr.isdigit() and title):
                    continue
                if is_junk_anchor(title, jt):
                    n_junk += 1
                    continue
                anchors_per_issue[(issn, vol, iss, yr)].append((title, author))
                n_anchors += 1
    print(f"loaded {n_anchors} anchors over {len(anchors_per_issue)} unique issues "
          f"(discarded {n_junk} junk anchors)", file=sys.stderr)

    # group issues by journal
    issues_by_journal = defaultdict(list)
    for (issn, vol, iss, yr), titles in anchors_per_issue.items():
        issues_by_journal[issn].append((vol, iss, yr, titles))

    journals = sorted(issues_by_journal.keys(),
                      key=lambda j: -len(issues_by_journal[j]))
    journals = [j for j in journals
                if sum(len(t) for _,_,_,t in issues_by_journal[j])
                   >= args.min_anchors]
    if args.max_journals:
        journals = journals[:args.max_journals]
    todo = [j for j in journals if j not in done]
    print(f"journals to audit: {len(todo)} (skipping {len(done)} already done; "
          f"filtered by min_anchors={args.min_anchors})", file=sys.stderr)

    fout_lock = threading.Lock()
    fout = open(out_path, "a")
    t_start = time.time()
    n_done = [0]

    def audit_one(issn):
        issues = issues_by_journal[issn]
        by_bucket = defaultdict(list)
        for vol, iss, yr, titles in issues:
            by_bucket[year_bucket(yr, args.bucket_span)].append((vol, iss, yr, titles))

        bucket_stats = {}
        per_issue = []  # per-issue raw stats so post-hoc analysis can re-bucket
        n_queried_total = 0
        n_anchors_total = 0
        n_hits_total = 0
        n_api_errors = 0

        # year-cache: avoid repeat (issn, year) fetches within this journal
        year_cache = {}
        def get_year(year):
            if year in year_cache:
                return year_cache[year]
            recs, err = fetch_crossref_year(issn, year, args.polite_mailto)
            year_cache[year] = (recs, err)
            return recs, err

        for bkt, bkt_issues in sorted(by_bucket.items()):
            zero_streak = 0
            queried = 0
            anchors_seen = 0
            anchors_matched = 0
            for (vol, iss, yr, titles) in sorted(bkt_issues, key=lambda x: x[2]):
                if zero_streak >= args.early_stop_zeroes:
                    break
                year_recs, err = get_year(yr)
                queried += 1
                n_queried_total += 1
                if err is not None:
                    n_api_errors += 1
                    per_issue.append({
                        "year": yr, "volume": vol, "issue": iss,
                        "anchors": len(titles), "title_hits": 0, "author_hits": 0,
                        "either_hits": 0, "error": True,
                    })
                    continue
                xref = filter_to_issue(year_recs, vol, iss)
                title_hits_n = 0
                author_hits_n = 0
                either_hits_n = 0
                for (t, au) in titles:
                    title_hit = any(
                        fuzzy_first_n_match(t, (r.get("title") or [""])[0])
                        for r in xref
                    )
                    author_hit = author_surname_match(au, xref) if au else False
                    if title_hit: title_hits_n += 1
                    if author_hit: author_hits_n += 1
                    if title_hit or author_hit: either_hits_n += 1
                anchors_seen += len(titles)
                anchors_matched += either_hits_n
                n_anchors_total += len(titles)
                n_hits_total += either_hits_n
                per_issue.append({
                    "year": yr, "volume": vol, "issue": iss,
                    "anchors": len(titles),
                    "title_hits": title_hits_n,
                    "author_hits": author_hits_n,
                    "either_hits": either_hits_n,
                })
                if either_hits_n == 0:
                    zero_streak += 1
                else:
                    zero_streak = 0
            bucket_stats[bkt] = {
                "queried": queried,
                "issues_in_bucket": len(bkt_issues),
                "anchors_seen": anchors_seen,
                "anchors_matched": anchors_matched,
                "match_pct": (round(100 * anchors_matched / anchors_seen, 1)
                              if anchors_seen else None),
                "early_stopped": zero_streak >= args.early_stop_zeroes,
            }
        return {
            "issn": issn,
            "issues_in_ill": len(issues),
            "issues_queried": n_queried_total,
            "anchors_seen": n_anchors_total,
            "anchors_matched": n_hits_total,
            "overall_match_pct": (round(100 * n_hits_total / n_anchors_total, 1)
                                  if n_anchors_total else None),
            "api_errors": n_api_errors,
            "year_buckets": bucket_stats,
            "per_issue": per_issue,
        }

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(audit_one, issn): issn for issn in todo}
        for fut in concurrent.futures.as_completed(futs):
            result = fut.result()
            with fout_lock:
                fout.write(json.dumps(result) + "\n")
                fout.flush()
            n_done[0] += 1
            elapsed = time.time() - t_start
            print(f"[{n_done[0]:>5}/{len(todo)}] {result['issn']}  "
                  f"q={result['issues_queried']:>3} "
                  f"hit={result['anchors_matched']}/{result['anchors_seen']} "
                  f"({result['overall_match_pct']}%)  "
                  f"buckets={len(result['year_buckets'])} "
                  f"elapsed={elapsed/60:.1f}m",
                  file=sys.stderr)


if __name__ == "__main__":
    main()
