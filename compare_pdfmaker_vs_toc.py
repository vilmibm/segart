#!/usr/bin/env python3
"""Compare three leaf-resolution paths for post-2024-04 ILL requests:

  1. PDF-maker auto path:  patron's printed pages → normalized_orig_*
     (succeeds when the issue has a clean page-number map; fails with
      "Normalize page numbers failed" when it doesn't)
  2. Final delivered answer: start/stop (auto + any staffer correction)
  3. Our TOC path: fuzzy-match request title → TOC entry → entry's leaves

Counts how often each path produces the right answer (treating `start`/
`stop` as ground truth), and where they disagree.
"""
import csv
import glob
import json
import os
import re
import sys
from datetime import datetime, timezone
from difflib import SequenceMatcher
from collections import defaultdict, Counter

CUTOFF = int(datetime(2024, 4, 1, tzinfo=timezone.utc).timestamp())
SEGART = "/Users/brewster/tmp/segart"
LEAF_RE = re.compile(r"^n(\d+)$")
STOPWORDS = {"the", "a", "an", "of", "and", "in", "on", "for", "to",
             "la", "le", "les", "der", "die", "das", "el", "los", "il"}


def nint(s):
    if s is None: return None
    m = LEAF_RE.match(str(s).strip())
    return int(m.group(1)) if m else None


def all_leaves(arr):
    return arr and all(LEAF_RE.match(str(x).strip()) for x in arr)


def normalize_title(s):
    if not s: return ""
    s = re.sub(r"[^a-z0-9]+", " ", s.lower())
    return " ".join(w for w in s.split() if w not in STOPWORDS)


def title_similarity(a, b):
    na, nb = normalize_title(a), normalize_title(b)
    if not na or not nb: return 0.0
    if na == nb: return 1.0
    if na in nb or nb in na: return 0.95
    ta, tb = set(na.split()), set(nb.split())
    overlap = len(ta & tb) / max(1, min(len(ta), len(tb))) if ta and tb else 0
    ratio = SequenceMatcher(None, na, nb).ratio()
    return max(overlap, ratio)


def llm_to_legacy_lr(entry):
    sl = max(0, int(entry["start_leaf"]) - 1)
    el = max(sl, int(entry["end_leaf"]) - 1)
    return [(sl, el)]


def load_llm_toc(path):
    if not os.path.exists(path): return None
    d = json.load(open(path))
    out = []
    for e in d.get("entries") or []:
        sl = max(0, int(e["start_leaf"]) - 1)
        el = max(sl, int(e["end_leaf"]) - 1)
        out.append({"title": e.get("title") or "",
                    "start_leaf": sl, "end_leaf": el})
    return out


def load_heur_toc(path):
    """Heuristic TOC (segment_issue_docling.py output): leaf_ranges is
    [['nN', 'nM']] strings; convert first pair to ints."""
    if not os.path.exists(path): return None
    d = json.load(open(path))
    out = []
    for e in d.get("entries") or []:
        lr = e.get("leaf_ranges") or []
        if not lr: continue
        s, en = nint(lr[0][0]), nint(lr[0][1])
        if s is None or en is None: continue
        out.append({"title": e.get("title") or "",
                    "start_leaf": s, "end_leaf": en})
    return out


def predict_via_toc(toc, request_title):
    """Title-fuzzy-match the request against TOC; return [(s, e)] or None."""
    if not toc or not request_title: return None
    best = None; best_sim = 0
    for e in toc:
        sim = title_similarity(request_title, e["title"])
        if sim > best_sim:
            best, best_sim = e, sim
    if best is None or best_sim < 0.55:
        return None
    return [(best["start_leaf"], best["end_leaf"])]


def leaf_pair(arr_s, arr_e):
    """Return [(start_int, end_int), ...] for leaf-shaped arrays, else None."""
    if not all_leaves(arr_s) or not all_leaves(arr_e):
        return None
    if len(arr_s) != len(arr_e):
        return None
    return [(nint(s), nint(e)) for s, e in zip(arr_s, arr_e)]


def kind_match(predicted, truth, tol=1):
    """Compare first pair only (most articles are single-range)."""
    if predicted is None or truth is None: return "no_prediction"
    if not predicted or not truth: return "no_prediction"
    p = predicted[0]; t = truth[0]
    if p == t: return "exact"
    if abs(p[0] - t[0]) <= tol and abs(p[1] - t[1]) <= tol: return "soft"
    return "wrong"


def load_page_numbers(item):
    """Return a dict mapping printed page string → BookReader nN integer
    (0-indexed). Returns None if the file is missing.

    Uses scandata.xml to translate page_numbers.json's `leafNum`
    (which is the scandata Scribe-image counter, *including hidden
    leaves*) to BookReader's visible-only nN. The earlier "leafNum
    aligns directly with nN" assumption is incorrect for items with
    hidden leaves at scandata leafNum=0 (front Color Card — the common
    case) — see page_index.py.
    """
    pn_path = f"{SEGART}/tmp/items/{item}/{item}_page_numbers.json"
    if not os.path.exists(pn_path):
        return None
    from page_index import PageIndex
    pn_data = json.load(open(pn_path))
    try:
        pi = PageIndex.for_item(item, fetch=True)
    except Exception:
        return None
    return pi.printed_to_br(pn_data)


def pdfmaker_predict(item, orig_start, orig_stop):
    """Translate patron's printed pages → leaf via the issue's page-numbers
    map. Returns [(start_int, end_int)] or None if untranslatable."""
    pn = load_page_numbers(item)
    if pn is None: return None
    if not orig_start or not orig_stop: return None
    out = []
    for s_pp, e_pp in zip(orig_start, orig_stop):
        s_pp = (str(s_pp).strip()); e_pp = (str(e_pp).strip())
        if not s_pp or not e_pp: return None
        # Strip non-digits like trailing dashes
        s_pp = re.sub(r"[^0-9]", "", s_pp); e_pp = re.sub(r"[^0-9]", "", e_pp)
        if s_pp not in pn: return None
        sl = pn[s_pp]
        el = pn.get(e_pp, sl)
        out.append((sl, el))
    return out or None


def main():
    # Items we have processed (have docling caches)
    processed = set()
    for d in sorted(glob.glob(f"{SEGART}/tmp/items/*/")):
        item = os.path.basename(d.rstrip("/"))
        if os.path.exists(f"{d}{item}_docling.json.gz"):
            processed.add(item)

    # Load TOCs
    llm_tocs = {}
    heur_tocs = {}
    for it in processed:
        ll = load_llm_toc(f"{SEGART}/tmp/tocs/{it}_toc_llm.json")
        if ll is not None: llm_tocs[it] = ll
        hr = load_heur_toc(f"{SEGART}/tmp/tocs/{it}_toc.json")
        if hr is not None: heur_tocs[it] = hr

    # Walk raw post-2024-04 ILL rows for processed items.
    seen = set()
    rows = []
    for path in sorted(glob.glob(f"{SEGART}/tmp/ill_logs/*.csv")):
        with open(path, newline='') as fh:
            for row in csv.DictReader(fh):
                ident = row.get("source_identifier") or ""
                if ident not in processed: continue
                t = row.get("time")
                if not t or not t.isdigit() or int(t) < CUTOFF: continue
                try:
                    ff = json.loads(row.get("full_form") or "{}")
                except json.JSONDecodeError:
                    continue
                p = ff.get("original_request_params") or {}
                title = (p.get("article_title") or "").strip() or None
                # Determine the three leaf candidates.
                truth = leaf_pair(ff.get("start") or [], ff.get("stop") or [])
                # PDF-maker pre-correction suggestion: translate patron's
                # printed pages via the issue's page_numbers.json. This is
                # what the auto path would have produced before any operator
                # override.
                auto = pdfmaker_predict(
                    ident, ff.get("orig_start") or [], ff.get("orig_stop") or []
                )
                # Some final answers are still printed-page strings (no leaf
                # form). Skip those — we can't compare numerically.
                if truth is None: continue
                key = (ident, title, tuple(truth))
                if key in seen: continue
                seen.add(key)
                rows.append({
                    "ident": ident,
                    "title": title,
                    "truth": truth,
                    "auto": auto,
                    "auto_failed": auto is None,
                })

    print(f"unique post-2024-04 anchors with leaf-shaped truth: {len(rows)}",
          file=sys.stderr)

    # Compute predictions for each path on each row.
    n = len(rows)
    paths = {  # path-name → list of kinds, one per row
        "pdf_maker": [],
        "heur_toc": [],
        "llm_toc": [],
        "hybrid_pdf_then_heur": [],
        "hybrid_pdf_then_llm": [],
    }

    def kind_or(prediction, truth):
        """Returns kind given a prediction (or None) and the truth."""
        if prediction is None: return "no_prediction"
        return kind_match(prediction, truth)

    rows_with_heur = 0
    rows_with_llm = 0
    for r in rows:
        # PDF-maker
        pdf_pred = None if r["auto_failed"] else r["auto"]
        pdf_kind = kind_or(pdf_pred, r["truth"])
        paths["pdf_maker"].append(pdf_kind)

        # Heur TOC
        heur = heur_tocs.get(r["ident"])
        heur_pred = predict_via_toc(heur, r["title"]) if (heur and r["title"]) else None
        heur_kind = kind_or(heur_pred, r["truth"])
        paths["heur_toc"].append(heur_kind if heur is not None else "no_toc")
        if heur is not None: rows_with_heur += 1

        # LLM TOC
        llm = llm_tocs.get(r["ident"])
        llm_pred = predict_via_toc(llm, r["title"]) if (llm and r["title"]) else None
        llm_kind = kind_or(llm_pred, r["truth"])
        paths["llm_toc"].append(llm_kind if llm is not None else "no_toc")
        if llm is not None: rows_with_llm += 1

        # Hybrid: PDF-maker first, fall back to TOC if PDF-maker has no_prediction.
        # We do NOT fall back when PDF-maker is wrong — operators trust the auto
        # path when it returns; the "save" comes only when auto produces nothing.
        def hybrid(pdf_kind, pdf_pred, fb_pred):
            if pdf_kind != "no_prediction":
                return pdf_kind
            return kind_or(fb_pred, r["truth"])
        paths["hybrid_pdf_then_heur"].append(hybrid(pdf_kind, pdf_pred, heur_pred))
        paths["hybrid_pdf_then_llm"].append(hybrid(pdf_kind, pdf_pred, llm_pred))

    # ---------- Print scoreboards ----------
    def correct(kinds):
        return sum(1 for k in kinds if k in ("exact", "soft"))

    def fmt(kinds, denom):
        c = correct(kinds)
        return f"{c}/{denom} ({100*c//max(denom,1)}%)"

    print(f"\n{'='*78}")
    print(f"ALL {n} POST-2024-04 ANCHORS (across 55 processed items)")
    print(f"{'='*78}")
    print(f"{'path':<25} {'exact':>6} {'soft':>5} {'wrong':>6} {'noPred':>7} {'noTOC':>6}  correct")
    for p in ("pdf_maker", "heur_toc", "llm_toc",
              "hybrid_pdf_then_heur", "hybrid_pdf_then_llm"):
        c = Counter(paths[p])
        print(f"  {p:<23} {c['exact']:>6} {c['soft']:>5} {c['wrong']:>6} "
              f"{c['no_prediction']:>7} {c['no_toc']:>6}  {fmt(paths[p], n)}")

    # ---------- Subset where LLM TOC exists ----------
    sub_idx = [i for i, r in enumerate(rows) if r["ident"] in llm_tocs]
    sn = len(sub_idx)
    if sn:
        print(f"\n{'='*78}")
        print(f"SUBSET: {sn} anchors on the {len(set(rows[i]['ident'] for i in sub_idx))} "
              f"items where LLM TOC exists")
        print(f"{'='*78}")
        print(f"{'path':<25} {'exact':>6} {'soft':>5} {'wrong':>6} {'noPred':>7}  correct")
        for p in ("pdf_maker", "heur_toc", "llm_toc",
                  "hybrid_pdf_then_heur", "hybrid_pdf_then_llm"):
            sub_kinds = [paths[p][i] for i in sub_idx]
            c = Counter(sub_kinds)
            print(f"  {p:<23} {c['exact']:>6} {c['soft']:>5} {c['wrong']:>6} "
                  f"{c['no_prediction']:>7}  {fmt(sub_kinds, sn)}")


if __name__ == "__main__":
    main()
