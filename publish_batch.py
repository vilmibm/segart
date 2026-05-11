"""End-to-end publish pipeline for a list of items.

For each item, runs:
  1. heuristic_toc_crossref.py        → raw heurxref output
  2. heur_xref_to_legacy.py           → v2 legacy TOC (with docling ToC entry)
  3. tools/build_articles_companion.py → articles companion
  4. tools/publish_toc.py             → upload all + post IA review

Skips each step if its output already exists. Logs to
tmp/audit/publish_batch.log.

Usage:
  python3 publish_batch.py /tmp/items32.txt
"""
import argparse
import gzip
import json
import subprocess
import sys
import time
from pathlib import Path

SEGART = Path("/Users/brewster/tmp/segart")
TOCS = SEGART / "tmp" / "tocs"
PILOTS = SEGART / "tmp" / "audit"
LOG = SEGART / "tmp" / "audit" / "publish_batch.log"
SKIPLIST = SEGART / "tmp" / "audit" / "publish_batch_skipped.jsonl"

sys.path.insert(0, str(SEGART / "tools"))
from pn_health import assess_pn_health, load_pn_for_item  # noqa: E402


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG, "a") as fh:
        fh.write(line + "\n")


def run(cmd, **kwargs):
    """Run, capture, return (rc, stdout, stderr)."""
    r = subprocess.run(cmd, capture_output=True, text=True, **kwargs)
    return r.returncode, r.stdout, r.stderr


def step_heurxref(item):
    """Generate raw heurxref output. Always re-runs — the heurxref tool
    has been improving and any cached output may be from an older
    version. Cheap (~5-30s) so just regenerate."""
    out = TOCS / f"{item}_toc_heur_xref.json"
    rc, _, err = run([sys.executable, str(SEGART / "heuristic_toc_crossref.py"),
                      item, "--out", str(out)])
    if rc != 0:
        return False, f"heurxref exit={rc}: {err.strip()[-300:]}"
    return out.exists(), "ok"


def step_legacy(item):
    """Convert raw heurxref → v2 legacy TOC."""
    src = TOCS / f"{item}_toc_heur_xref.json"
    pilot_dir = PILOTS / f"pilot_{item}"
    pilot_dir.mkdir(parents=True, exist_ok=True)
    out = pilot_dir / f"{item}_toc.json"
    rc, _, err = run([sys.executable, str(SEGART / "heur_xref_to_legacy.py"),
                      str(src), "--out", str(out)])
    if rc != 0:
        return False, f"legacy exit={rc}: {err.strip()[-300:]}"
    return out.exists(), "ok"


def step_articles(item):
    """Build articles companion."""
    pilot_dir = PILOTS / f"pilot_{item}"
    toc = pilot_dir / f"{item}_toc.json"
    out = pilot_dir / f"{item}_articles.json.gz"
    rc, _, err = run([sys.executable, str(SEGART / "tools" / "build_articles_companion.py"),
                      str(toc), str(out)])
    if rc != 0:
        return False, f"articles exit={rc}: {err.strip()[-300:]}"
    return out.exists(), "ok"


def step_publish(item):
    """Upload TOC + articles + docling cache, post review."""
    pilot_dir = PILOTS / f"pilot_{item}"
    toc = pilot_dir / f"{item}_toc.json"
    art = pilot_dir / f"{item}_articles.json.gz"
    rc, _, err = run([sys.executable, str(SEGART / "tools" / "publish_toc.py"),
                      item, "--toc", str(toc), "--articles", str(art),
                      "--method", "heurxref+docling"])
    if rc != 0:
        return False, f"publish exit={rc}: {err.strip()[-300:]}"
    return True, "ok"


def step_pn_health(item):
    """Inform-only: log pn.json health for the run record. heurxref now
    handles bad pn.json internally (bypasses pn.json when health != ok
    and uses docling title-match instead), so we don't skip here."""
    pn = load_pn_for_item(item)
    if not pn:
        return "no_pn_json"
    a = assess_pn_health(pn, item=item)
    return a["status"]


def process(item):
    health = step_pn_health(item)
    log(f"  step 0 pn_health: {health}")
    log(f"  step 1/4 heurxref")
    ok, msg = step_heurxref(item)
    if not ok: return False, f"heurxref FAIL: {msg}"
    log(f"  step 2/4 legacy ({msg})")
    ok, msg = step_legacy(item)
    if not ok: return False, f"legacy FAIL: {msg}"
    log(f"  step 3/4 articles ({msg})")
    ok, msg = step_articles(item)
    if not ok: return False, f"articles FAIL: {msg}"
    log(f"  step 4/4 publish ({msg})")
    ok, msg = step_publish(item)
    if not ok: return False, f"publish FAIL: {msg}"
    return True, "published"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("items_file")
    ap.add_argument("--dry-run", action="store_true",
                    help="Run steps 1-3 only, skip publish")
    args = ap.parse_args()

    items = [l.strip() for l in Path(args.items_file).read_text().splitlines()
             if l.strip()]
    LOG.parent.mkdir(parents=True, exist_ok=True)
    log(f"publish_batch starts; {len(items)} items "
        f"({'DRY-RUN' if args.dry_run else 'live'})")

    n_ok = n_fail = 0
    t_start = time.time()
    for i, item in enumerate(items, 1):
        elapsed = (time.time() - t_start) / 60
        log(f"[{i}/{len(items)}] {item} (elapsed {elapsed:.1f}m)")
        t0 = time.time()
        try:
            if args.dry_run:
                ok, msg = True, "dry-run"
                for fn in (step_heurxref, step_legacy, step_articles):
                    ok, msg = fn(item)
                    if not ok: break
            else:
                ok, msg = process(item)
        except Exception as ex:
            ok, msg = False, f"EXC: {ex}"
        dt = time.time() - t0
        if ok:
            n_ok += 1
            log(f"  ✓ {msg} in {dt:.0f}s")
        else:
            n_fail += 1
            log(f"  ✗ {msg} in {dt:.0f}s")
    log(f"batch done: ok={n_ok} fail={n_fail} "
        f"in {(time.time()-t_start)/60:.1f}m")

    if not args.dry_run:
        regenerate_qa_report()


def regenerate_qa_report():
    """Rebuild QA_REPORT.md from every published item's per-issue
    `qa.entries_needing_qa` block. Runs at end of every live batch so
    the repo's QA report stays in sync."""
    import glob
    from datetime import datetime

    rows = []
    all_items = 0
    for fn in sorted(glob.glob(str(PILOTS / "pilot_sim_*" / "sim_*_toc.json"))):
        item = Path(fn).parent.name.replace("pilot_", "")
        try: d = json.load(open(fn))
        except Exception: continue
        all_items += 1
        qa_ids = (d.get("qa") or {}).get("entries_needing_qa") or []
        if not qa_ids: continue
        for e in d["entries"]:
            if e["id"] not in qa_ids: continue
            pi = e["page_index_ranges"][0]
            ev = e.get("evidence") or []
            if "span_co_located_with_siblings" in ev:
                reason = "co-located"
                details = ("Multiple Crossref entries share this start page-"
                           "index. Each kept at 1 page; verify whether "
                           "boundaries should differ.")
            elif "span_extended_to_end" in ev:
                reason = "extended-to-end"
                details = ("Last entry whose Crossref deposit was a single "
                           "start page; span extended to end of visible "
                           "pages (may over-claim trailing backmatter).")
            else:
                reason = "other"; details = "; ".join(ev)
            rows.append({"item": item, "id": e["id"],
                          "title": e.get("title") or "",
                          "pi_start": pi[0], "pi_end": pi[1],
                          "printed": e.get("printed_pages"),
                          "reason": reason, "details": details,
                          "confidence": e.get("confidence")})

    n_items = len({r["item"] for r in rows})
    n_co = sum(1 for r in rows if r["reason"] == "co-located")
    n_ee = sum(1 for r in rows if r["reason"] == "extended-to-end")

    out = [
        "# QA Review: heurxref Pilot Batch",
        "",
        f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M')} — "
        f"**{all_items}** items published, **{len(rows)}** entries flagged "
        f"across **{n_items}** items.",
        "",
        "## Flag breakdown",
        "",
        f"- **co-located**: {n_co} — multiple Crossref entries share a "
        f"start page-index AND title (typically end-of-issue announcements, "
        f"repeated-title book reviews, or front-matter pairs).",
        f"- **extended-to-end**: {n_ee} — last entry whose Crossref deposit "
        f"was a single start page; span extended to end of visible pages.",
        "",
        "## How to QA each entry",
        "",
        "1. Click the BookReader link to view the flagged page.",
        "2. Confirm the article's actual extent on the page.",
        "3. If a flag is a false positive (entry is correctly placed), no action needed.",
        "4. If our range is wrong, edit `tmp/audit/pilot_<item>/<item>_toc.json` and re-publish.",
        "",
        "## Entries",
        "",
    ]
    by_item = {}
    for r in rows: by_item.setdefault(r["item"], []).append(r)
    for item in sorted(by_item):
        out.append(f"### `{item}`")
        out.append(f"Item: https://archive.org/details/{item}?admin=1")
        out.append("")
        for r in by_item[item]:
            out.append(f"- **{r['id']}** — _{r['title']}_  ")
            out.append(f"  - position: `{r['pi_start']}` to `{r['pi_end']}` "
                       f"(printed pp. {r['printed']})  ")
            out.append(f"  - flag: **{r['reason']}** "
                       f"(confidence {r['confidence']})  ")
            out.append(f"  - {r['details']}  ")
            out.append(f"  - view: https://archive.org/details/{item}"
                       f"/page/{r['pi_start']}/mode/1up?admin=1")
        out.append("")

    # All published items, including clean ones with no QA flags.
    all_published = sorted(
        Path(p).parent.name.replace("pilot_", "")
        for p in glob.glob(str(PILOTS / "pilot_sim_*" / "sim_*_toc.json"))
    )
    out.append("## All published items")
    out.append("")
    out.append(f"{len(all_published)} items have segart `_toc.json` + "
               f"`_articles.json.gz` + `_docling.json.gz` + review on IA.")
    out.append("")
    for item in all_published:
        out.append(f"- [`{item}`](https://archive.org/details/{item}?admin=1)")
    out.append("")

    report_path = SEGART / "QA_REPORT.md"
    report_path.write_text("\n".join(out))
    log(f"  QA_REPORT.md regenerated: {all_items} items, "
        f"{len(rows)} flagged across {n_items} items")


if __name__ == "__main__":
    main()
