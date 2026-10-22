#!/usr/bin/env python3
"""Draw the "How often does the lake come up at City Hall?" social card.

Reads data/lake-watch/attention.json (from attention.py) and writes a
self-contained HTML card, 1080x1350, the Facebook/Instagram portrait size.
Render it to PNG with any headless browser, e.g.

    python3 scripts/lakewatch/attention_card.py --out card.html
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless \
        --screenshot=card.png --window-size=1080,1350 --hide-scrollbars card.html

Two series, stacked per meeting: agenda items (teal, at the base) and topics
raised organically, by residents or officials outside any agenda item (gold,
on top). Meetings not yet reviewed are drawn as dashed outlines, never as
zero. The colour pair passes the dataviz palette checks against the cream
surface (CVD ΔE 19.8, normal-vision ΔE 25.5, both ≥ 3:1 contrast).
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "data" / "lake-watch" / "attention.json"

AGENDA, ORGANIC = "#1b7ea8", "#c27c12"
MON = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()


def short(d: str) -> str:
    y, m, day = (int(x) for x in d.split("-"))
    return f"{MON[m - 1]} {day}"


def card(doc: dict) -> str:
    rows = [{"date": m["date"], "agenda": m["agenda"],
             "organic": m["public_comment"] + m["council_or_staff"],
             "pc": m["public_comment"], "cs": m["council_or_staff"], "pending": False}
            for m in doc["meetings"]]
    rows += [{"date": d, "agenda": 0, "organic": 0, "pc": 0, "cs": 0, "pending": True}
             for d in doc.get("pending", [])]
    rows.sort(key=lambda r: r["date"])

    t = doc["totals"]
    reviewed, with_lake = t["meetings_reviewed"], t["meetings_with_lake"]
    agenda, organic = t["agenda"], t["public_comment"] + t["council_or_staff"]
    first, last = rows[0]["date"], rows[-1]["date"]

    # ── plot geometry ──
    W, H = 936, 500
    left, bottom, top = 34, 46, 14
    plot_w, plot_h = W - left - 6, H - bottom - top
    peak = max([r["agenda"] + r["organic"] for r in rows] + [4])
    peak = peak + (peak % 2)                       # even, so the gridlines land on integers
    step = plot_w / len(rows)
    bw = max(6.0, min(22.0, step * 0.62))
    y = lambda v: top + plot_h - v / peak * plot_h

    svg = [f'<svg viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" '
           f'aria-label="Lake topics per Council meeting, {html.escape(short(first))} to {html.escape(short(last))}">']
    for g in range(0, peak + 1, max(1, peak // 4)):
        svg.append(f'<line x1="{left}" x2="{W}" y1="{y(g):.1f}" y2="{y(g):.1f}" stroke="#e4ddd0" stroke-width="1"/>')
        svg.append(f'<text x="{left - 10}" y="{y(g) + 5:.1f}" text-anchor="end" class="ax">{g}</text>')

    # Every fourth meeting gets a date, plus the last; a label within two bars
    # of the last is dropped so they never collide. The year shows on the
    # first label and whenever it changes.
    n = len(rows)
    labelled = [i for i in range(0, n, 4) if n - 1 - i >= 3] + [n - 1]
    prev_year = None
    for i, r in enumerate(rows):
        cx = left + step * i + step / 2
        x = cx - bw / 2
        if r["pending"]:
            svg.append(f'<rect x="{x:.1f}" y="{y(1):.1f}" width="{bw:.1f}" height="{y(0) - y(1):.1f}" rx="3" '
                       f'fill="none" stroke="#b9b2a5" stroke-width="1.5" stroke-dasharray="3 3"/>')
        a, o = r["agenda"], r["organic"]
        if a:
            svg.append(f'<rect x="{x:.1f}" y="{y(a):.1f}" width="{bw:.1f}" height="{y(0) - y(a):.1f}" '
                       f'rx="4" fill="{AGENDA}"/>')
        if o:
            # 2px surface gap between the stacked fills
            y0 = y(a) - (2 if a else 0)
            svg.append(f'<rect x="{x:.1f}" y="{y(a + o):.1f}" width="{bw:.1f}" height="{y0 - y(a + o):.1f}" '
                       f'rx="4" fill="{ORGANIC}"/>')
        if not a and not o and not r["pending"]:
            svg.append(f'<rect x="{x:.1f}" y="{y(0) - 2:.1f}" width="{bw:.1f}" height="2" fill="#cfc7b8"/>')
        yr = r["date"][:4]
        if i in labelled:
            lab = short(r["date"]) + (f" ’{yr[2:]}" if yr != prev_year else "")
            prev_year = yr
            anchor, tx = ("end", cx + bw / 2) if i == n - 1 else ("start", cx - bw / 2) if i == 0 else ("middle", cx)
            svg.append(f'<text x="{tx:.1f}" y="{H - 18}" text-anchor="{anchor}" class="ax">{html.escape(lab)}</text>')
    svg.append(f'<line x1="{left}" x2="{W}" y1="{y(0):.1f}" y2="{y(0):.1f}" stroke="#8c8577" stroke-width="1"/>')
    svg.append("</svg>")

    pending_note = (f'<span class="key"><i class="sw pend"></i>Not yet reviewed</span>'
                    if doc.get("pending") else "")
    span = f"{short(first)},&nbsp;{first[:4]} to {short(last)},&nbsp;{last[:4]}".replace(" ", "&nbsp;", 1)

    return f"""<!DOCTYPE html>
<meta charset="utf-8">
<title>Lake Mattoon at City Hall</title>
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,400;0,700;1,400&family=Source+Serif+4:wght@300;400;600&display=swap" rel="stylesheet">
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
html,body{{width:1080px;height:1350px;background:#faf7f2;color:#1a2628;font-family:'Source Serif 4',Georgia,serif;-webkit-font-smoothing:antialiased}}
.card{{position:relative;width:1080px;height:1350px;padding:72px 72px 0;display:flex;flex-direction:column}}
.band{{position:absolute;left:0;right:0;top:0;height:14px;background:#0d2e3f}}
.eyebrow{{font-size:20px;letter-spacing:.2em;text-transform:uppercase;color:#8a6a12;font-weight:600}}
h1{{font-family:'Playfair Display',Georgia,serif;font-weight:400;font-size:66px;line-height:1.08;color:#0d2e3f;margin:18px 0 0}}
h1 em{{font-style:italic;color:#1b7ea8}}
.sub{{font-size:25px;color:#3d5a60;margin-top:20px;font-weight:300;line-height:1.45}}
.stats{{display:grid;grid-template-columns:repeat(3,1fr);gap:20px;margin:44px 0 40px}}
.stat{{border-top:3px solid #0d2e3f;padding-top:16px}}
.stat b{{display:block;font-family:'Playfair Display',Georgia,serif;font-weight:400;font-size:64px;line-height:1;color:#0d2e3f}}
.stat span{{display:block;font-size:20px;color:#3d5a60;margin-top:10px;line-height:1.35}}
.stat i{{display:inline-block;width:14px;height:14px;border-radius:3px;margin-right:8px;vertical-align:-1px}}
.keys{{display:flex;gap:30px;font-size:20px;color:#1a2628;margin-bottom:16px}}
.sw{{display:inline-block;width:16px;height:16px;border-radius:4px;margin-right:9px;vertical-align:-2px}}
.sw.pend{{border:1.5px dashed #b9b2a5}}
.ax{{font-family:'Source Serif 4',Georgia,serif;font-size:17px;fill:#52514e}}
.foot{{margin-top:auto;background:#0d2e3f;color:#b8dce8;margin-left:-72px;margin-right:-72px;padding:34px 72px 38px;display:flex;justify-content:space-between;align-items:flex-end;gap:30px}}
.foot .cta{{font-family:'Playfair Display',Georgia,serif;font-size:34px;color:#fff;line-height:1.2}}
.foot .cta em{{color:#e2b85a}}
.foot .cta small{{display:block;font-family:'Source Serif 4',Georgia,serif;font-size:22px;color:#c49a2a;letter-spacing:.04em;margin-top:8px}}
.foot .fine{{font-size:15px;line-height:1.45;max-width:380px;text-align:right;color:#9cc3d0}}
</style>
<div class="card">
  <div class="band"></div>
  <div class="eyebrow">Friends of Lake Mattoon · Lake Watch</div>
  <h1>How often does the lake come up <em>at City Hall?</em></h1>
  <p class="sub">Every lake-related moment in {reviewed} recorded Mattoon City Council meetings, {span}.</p>
  <div class="stats">
    <div class="stat"><b>{with_lake}<small style="font-size:34px;color:#3d5a60"> of {reviewed}</small></b><span>meetings where the lake came up at all</span></div>
    <div class="stat"><b>{agenda}</b><span><i style="background:{AGENDA}"></i>times it was on the agenda</span></div>
    <div class="stat"><b>{organic}</b><span><i style="background:{ORGANIC}"></i>times someone raised it without an agenda item</span></div>
  </div>
  <div class="keys">
    <span class="key"><i class="sw" style="background:{AGENDA}"></i>On the agenda</span>
    <span class="key"><i class="sw" style="background:{ORGANIC}"></i>Raised by residents or officials</span>
    {pending_note}
  </div>
  {''.join(svg)}
  <div class="foot">
    <div class="cta">Staying informed is good.<br><em>Getting involved is better.</em><small>friendsoflakemattoon.org/#lake-watch</small></div>
    <div class="fine">Lake topics counted from AI reviews of the City’s YouTube recordings; how each came up is an AI judgment from its description. Counts as of {dt.date.today():%b %-d, %Y}.</div>
  </div>
</div>
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=str(SRC))
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    doc = json.loads(pathlib.Path(args.src).read_text())
    pathlib.Path(args.out).write_text(card(doc))
    print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
