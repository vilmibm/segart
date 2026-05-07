#!/usr/bin/env python3
"""Render a side-by-side HTML eyeball report from a scores JSONL.

For each anchor result, the page shows:
  - The anchor's first/last leaf as IA page thumbnails
  - The matched TOC entry's first/last leaf as IA page thumbnails
  - Title and author for both
  - Match category badge + end-leaf delta
Each thumbnail links to the IA BookReader for that page.

Image URL pattern: https://archive.org/download/<item>/page/<leaf>_w400.jpg
BookReader URL:    https://archive.org/details/<item>/page/<leaf>

Usage:
  ./eyeball_html.py --scores /tmp/v07_filtered_scores.jsonl -o tmp/eyeball.html
  ./eyeball_html.py --scores ... --only soft,exact --max 100
"""
import argparse
import html
import json
import re


def fmt_leaf(s):
    if isinstance(s, str) and s.startswith("n"):
        return s
    return f"n{s}" if s else None


def br_url(item, leaf):
    if not leaf:
        return f"https://archive.org/details/{item}"
    return f"https://archive.org/details/{item}/page/{leaf}"


def embed_url(item, leaf):
    """IA's BookReader embed URL pinned to a specific leaf.

    URL fragments (`#page/n36/mode/1up`) get dropped when the
    BookReader app inside the iframe re-initializes; the embed-path
    form (`/embed/{item}/page/n36`) is preserved through the
    redirect to `/details/?view=theater&ui=embed`.
    """
    if not leaf:
        return f"https://archive.org/embed/{item}"
    return f"https://archive.org/embed/{item}/page/{leaf}"


def author_text(authors):
    if isinstance(authors, list):
        names = [(a or {}).get("name") or "" for a in authors[:6]]
        return ", ".join(n for n in names if n)
    return str(authors) if authors else ""


def render_thumb(item, leaf, label):
    if not leaf:
        return (
            f'<div class="thumb missing">'
            f'<div class="lbl">{label}</div>'
            f'<div class="empty">—</div></div>'
        )
    burl = br_url(item, leaf)
    eurl = embed_url(item, leaf)
    return (
        f'<div class="thumb">'
        f'<div class="lbl">{label} '
        f'<a href="{burl}" target="_blank">{leaf}</a></div>'
        f'<iframe src="{eurl}" loading="lazy" referrerpolicy="no-referrer"></iframe>'
        f'</div>'
    )


CSS = """
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
       margin: 1em; background: #f7f7f7; color: #222; }
h1 { font-size: 1.4em; }
.row { background: white; border: 1px solid #ddd; border-radius: 6px;
       margin: 1em 0; padding: .8em; }
.head { display: flex; gap: 1em; align-items: baseline; flex-wrap: wrap;
        font-size: .9em; }
.item { font-family: ui-monospace, SFMono-Regular, monospace;
        color: #666; font-size: .85em; }
.match { padding: 2px 8px; border-radius: 10px; font-weight: 600;
         font-size: .8em; }
.match.exact { background: #c6f6c6; color: #1a4a1a; }
.match.soft  { background: #d6efff; color: #0a4070; }
.match.leaves_only  { background: #ffeeb0; color: #6a4500; }
.match.content_only { background: #ffd7c0; color: #6a2200; }
.match.miss { background: #f0c0c0; color: #6a0000; }
.delta { font-family: ui-monospace, monospace; color: #555; font-size: .85em; }
.cols  { display: grid; grid-template-columns: 1fr 1fr; gap: 1em;
         margin-top: .6em; }
.col   { border: 1px solid #eee; padding: .6em; border-radius: 4px;
         background: #fafafa; }
.col h3 { margin: 0 0 .4em 0; font-size: .9em; color: #666;
          text-transform: uppercase; letter-spacing: .04em; }
.col .ttl { font-weight: 600; font-size: .95em; margin-bottom: .25em; }
.col .au  { color: #555; font-size: .85em; }
.thumbs   { display: flex; gap: .8em; margin-top: .6em; }
.thumb    { display: flex; flex-direction: column; align-items: center;
            font-size: .75em; }
.thumb .lbl { color: #666; margin-bottom: 3px; }
.thumb iframe { width: 240px; height: 320px; border: 1px solid #ccc;
                box-shadow: 0 1px 3px rgba(0,0,0,.1); background: white; }
.thumb.missing .empty { width: 240px; height: 320px; background: #eee;
                        display: flex; align-items: center;
                        justify-content: center; color: #999; }
.thumb a { color: #06c; text-decoration: none; }
.thumb a:hover { text-decoration: underline; }
"""

HEAD_HTML = """<!doctype html>
<html><head><meta charset="utf-8">
<title>segart eyeball report</title>
<style>{css}</style>
</head><body>
<h1>segart eyeball report</h1>
<p style="color:#666;font-size:.9em">
Each row pairs one ILL-corpus anchor with the segmenter's nearest match.
Thumbnails are linked to the IA BookReader. Match badge legend:
<span class="match exact">exact</span>=leaves and content match;
<span class="match soft">soft</span>=start within ±1 leaf, content matches;
<span class="match leaves_only">leaves_only</span>=segmenter put a different
title at the right place;
<span class="match content_only">content_only</span>=right article, wrong page;
<span class="match miss">miss</span>=nothing matched.
</p>
"""


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--scores", required=True)
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--only",
                   default="exact,soft,leaves_only,content_only",
                   help="Categories to include. Default excludes 'miss' "
                        "since those rows have no segart-side content to "
                        "show; pass --only ...,miss to include them.")
    p.add_argument("--max", type=int, default=200)
    p.add_argument("--per-item-max", type=int, default=4)
    args = p.parse_args()

    cats = set(args.only.split(","))
    rows = []
    seen_per_item = {}
    for line in open(args.scores):
        r = json.loads(line)
        if r["match"] not in cats:
            continue
        item = r["item"]
        if seen_per_item.get(item, 0) >= args.per_item_max:
            continue
        seen_per_item[item] = seen_per_item.get(item, 0) + 1
        rows.append(r)
        if len(rows) >= args.max:
            break

    with open(args.output, "w") as f:
        f.write(HEAD_HTML.format(css=CSS))
        for r in rows:
            item = r["item"]
            a_l = r.get("anchor_leaves") or [["", ""]]
            a_start = fmt_leaf(a_l[0][0]) if a_l and a_l[0] else None
            a_end = fmt_leaf(a_l[0][-1]) if a_l and a_l[0] else None
            t_l = r.get("matched_entry_leaves")
            t_start = fmt_leaf(t_l[0][0]) if t_l and t_l[0] else None
            t_end = fmt_leaf(t_l[0][-1]) if t_l and t_l[0] else None
            end_off = r.get("end_offset")
            end_str = f"end Δ {end_off:+d}" if end_off is not None else ""

            f.write('<div class="row">\n')
            f.write(
                f'<div class="head">'
                f'<span class="match {r["match"]}">{r["match"]}</span>'
                f'<span class="item">{html.escape(item)}</span>'
                f'<span class="delta">{end_str}</span>'
                f'</div>\n'
            )
            f.write('<div class="cols">\n')
            # Anchor column — always show first AND last (collapse to one
            # iframe only if start == end, since the same render twice is
            # redundant)
            f.write('<div class="col">\n')
            f.write('<h3>ILL anchor</h3>\n')
            f.write(f'<div class="ttl">{html.escape(r.get("anchor_title") or "(no title)")}</div>\n')
            f.write(f'<div class="au">{html.escape(r.get("anchor_author") or "(no author)")}</div>\n')
            f.write('<div class="thumbs">\n')
            f.write(render_thumb(item, a_start, "first"))
            f.write(render_thumb(item, a_end, "last"))
            f.write('</div></div>\n')
            # TOC column
            f.write('<div class="col">\n')
            f.write('<h3>segart TOC</h3>\n')
            f.write(f'<div class="ttl">{html.escape(r.get("matched_entry_title") or "(no match)")}</div>\n')
            f.write(f'<div class="au">{html.escape(author_text(r.get("matched_entry_authors")))}</div>\n')
            f.write('<div class="thumbs">\n')
            f.write(render_thumb(item, t_start, "first"))
            f.write(render_thumb(item, t_end, "last"))
            f.write('</div></div>\n')
            f.write('</div></div>\n')

        f.write(f'<p style="color:#999;font-size:.8em">{len(rows)} rows</p>\n')
        f.write('</body></html>\n')

    print(f"wrote {args.output} ({len(rows)} rows)")


if __name__ == "__main__":
    main()
