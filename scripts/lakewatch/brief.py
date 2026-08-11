#!/usr/bin/env python3
"""Write the weekly Lake Watch brief.

Gathers what actually changed around Lake Mattoon this week, then has Claude
write it up as a short news article for residents. Three standing beats:

  1. Water quality  — the microcystin series and any new advisory
  2. Watershed planning — the Lake Mattoon/Paradise Watershed Committee, IEPA
     TMDL and Section 319 activity
  3. What is coming up — meeting dates and agenda items across the four
     governments with authority over the lake, so people know when to show up

Evidence gathering is deterministic; Claude only writes. It is told to use
nothing beyond the evidence it is handed, and every claim it makes is expected
to trace to an item below. Sources that could not be checked are passed through
explicitly, because "we could not reach the county site" and "the county had no
lake items" must never render as the same sentence.

Usage:
    ANTHROPIC_API_KEY=... python3 scripts/lakewatch/brief.py [--dry-run]
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import io
import json
import os
import pathlib
import re
import ssl
import sys
import urllib.error
import urllib.request

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
DATA = REPO_ROOT / "data"
NEWS = REPO_ROOT / "content" / "news"
STATE_PATH = DATA / "brief-state.json"
BRIEF_PATH = DATA / "latest-brief.json"

MODEL = "claude-opus-5"
UA = "FriendsOfLakeMattoon-LakeWatch/1.0 (+https://friendsoflakemattoon.org)"

# Two tiers, because one loose list produces false positives that read as
# fabrication. A county's generic "Farm Complaint" form matches "erosion" and
# has nothing to do with the lake; surfacing it as an agenda item would be
# worse than surfacing nothing.
#
# STRONG decides whether a document is about the lake at all.
LAKE_STRONG = re.compile(
    r"lake mattoon|lake paradise|watershed|microcystin|cyanobacter|algal bloom|"
    r"blue-?green algae|wake ?boat|wake ?surf|tmdl|water treatment plant|"
    r"water quality",
    re.IGNORECASE,
)
# WEAK only pulls supporting sentences out of a document STRONG already cleared.
LAKE_WEAK = re.compile(
    r"shoreline|erosion|spillway|dredg|nutrient|septic|well water|algae|"
    r"water plant|intake|advisory",
    re.IGNORECASE,
)


# ── plumbing ──────────────────────────────────────────────────────────────────

# Intermediate CA certificates some county sites forget to send. Verification
# stays fully on -- these complete a chain whose root is already trusted, which
# is exactly what a browser does by following the certificate's AIA extension.
# Disabling verification instead would be a real downgrade; this is not.
EXTRA_CA_URLS = [
    # colesco.illinois.gov sends only its leaf. Its issuer, GoDaddy Secure
    # Certificate Authority G2, chains to a root already in certifi.
    "http://certificates.godaddy.com/repository/gdig2.crt",
]
_context: ssl.SSLContext | None = None


def ssl_context() -> ssl.SSLContext:
    global _context
    if _context is not None:
        return _context

    try:
        import certifi

        ctx = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        ctx = ssl.create_default_context()

    for url in EXTRA_CA_URLS:
        try:
            der = urllib.request.urlopen(url, timeout=20).read()
            ctx.load_verify_locations(cadata=ssl.DER_cert_to_PEM_cert(der))
        except Exception:
            pass  # that host simply stays unreachable, and we report it as such

    _context = ctx
    return ctx


def fetch(url: str, timeout: int = 40) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout, context=ssl_context()) as r:
        return r.read()


def fetch_text(url: str, timeout: int = 40) -> str:
    return fetch(url, timeout).decode("utf-8", errors="replace")


def visible(raw: str) -> str:
    t = re.sub(r"<(script|style|nav|footer).*?</\1>", " ", raw, flags=re.S | re.I)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", t)))


def pdf_text(data: bytes, max_pages: int = 12) -> str:
    try:
        import pypdf

        reader = pypdf.PdfReader(io.BytesIO(data))
        pages = reader.pages[:max_pages]
        return re.sub(r"\s+", " ", " ".join((p.extract_text() or "") for p in pages))
    except Exception:
        return ""


def docx_text(data: bytes) -> str:
    """Read a .docx without a dependency — it is a zip of XML."""
    import zipfile

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            xml = z.read("word/document.xml").decode("utf-8", errors="replace")
        xml = re.sub(r"</w:p>", "\n", xml)
        return html.unescape(re.sub(r"<[^>]+>", "", xml)).strip()
    except Exception:
        return ""


def is_about_lake(text: str) -> bool:
    return bool(LAKE_STRONG.search(text))


def excerpts(text: str, limit: int = 4) -> list[str]:
    """Pull the sentences that carry the signal, strongest first.

    Only called on documents that already cleared is_about_lake(), so a WEAK
    match here is supporting detail rather than the sole reason we surfaced it.
    """
    strong, weak = [], []
    for chunk in re.split(r"(?<=[.;])\s+|\s{3,}", text):
        chunk = chunk.strip()
        if not (15 < len(chunk) < 320):
            continue
        if LAKE_STRONG.search(chunk) and chunk not in strong:
            strong.append(chunk)
        elif LAKE_WEAK.search(chunk) and chunk not in weak:
            weak.append(chunk)
    return (strong + weak)[:limit]


# ── evidence gathering ────────────────────────────────────────────────────────

class Evidence:
    def __init__(self) -> None:
        self.items: list[dict] = []
        self.unavailable: list[dict] = []

    def add(self, beat: str, **kw) -> None:
        self.items.append({"beat": beat, **kw})

    def miss(self, source: str, why: str) -> None:
        self.unavailable.append({"source": source, "why": why})

    def by_beat(self, beat: str) -> list[dict]:
        return [i for i in self.items if i["beat"] == beat]


def gather_water_quality(ev: Evidence, state: dict) -> None:
    path = DATA / "water-quality.json"
    if not path.exists():
        ev.miss("Water quality series", "data/water-quality.json has not been generated")
        return

    d = json.loads(path.read_text())
    readings = d.get("readings", [])
    if not readings:
        ev.miss("Water quality series", "no readings parsed")
        return

    # On the very first brief there is no previous run to diff against, so
    # nothing is "new" -- without this guard the first article would announce
    # all 75 readings as this week's news.
    seen = state.get("last_reading_date")
    new = [r for r in readings if r["date"] > seen] if seen else []

    ev.add(
        "water",
        kind="series",
        first_brief=not seen,
        latest=d["latest"],
        new_since_last_brief=new,
        recent=readings[-6:],
        count=d["count"],
        max_ppb=d["max_ppb"],
        exceedances=d["exceedances"],
        advisory_sensitive=d["advisory_sensitive_ppb"],
        advisory_general=d["advisory_general_ppb"],
        source=d["source_url"],
        note=(
            "Readings are finished treated tap water. Lake Paradise is the City's "
            "primary source; Lake Mattoon is a contingency source."
        ),
    )


def gather_city(ev: Evidence, state: dict) -> None:
    """New public notices and city news, via the robots-sanctioned sitemap."""
    try:
        xml = fetch_text("https://mattoon.illinois.gov/post-sitemap.xml")
    except Exception as exc:
        ev.miss("City of Mattoon notices", f"sitemap unreachable ({type(exc).__name__})")
        return

    entries = re.findall(r"<loc>([^<]+)</loc>\s*(?:<lastmod>([^<]*)</lastmod>)?", xml)
    known = set(state.get("seen_city_urls", []))
    fresh = [(u, m) for u, m in entries if u not in known][:8]

    for url, lastmod in fresh:
        try:
            text = visible(fetch_text(url))
        except Exception:
            continue
        if not is_about_lake(text):
            continue
        title = re.search(r"<title>([^<]*)</title>", fetch_text(url))
        ev.add(
            "water" if re.search(r"water quality|algal|microcystin", text, re.I) else "planning",
            kind="notice",
            title=(title.group(1).split(" - ")[0].strip() if title else url),
            url=url,
            lastmod=lastmod or None,
            excerpts=excerpts(text),
        )

    state["seen_city_urls"] = sorted({u for u, _ in entries})


def gather_council_meetings(ev: Evidence) -> None:
    """Meeting dates and document titles.

    The agenda PDFs themselves are CAPTCHA-gated (see data/sources.json), so we
    surface when the meeting is and what documents exist, and link out. That is
    still the thing a resident needs in order to show up.
    """
    idx = "https://mattoon.illinois.gov/government/citycouncil/upcomingmeetings/"
    try:
        raw = fetch_text(idx)
    except Exception as exc:
        ev.miss("Mattoon City Council meetings", f"unreachable ({type(exc).__name__})")
        return

    slugs = re.findall(r"/download/(city-council-meeting-(\d{4}-\d{2}-\d{2})[^/\"]*)/", raw)
    today = dt.date.today()
    upcoming, recent = [], []
    for slug, date in sorted(set(slugs), key=lambda s: s[1], reverse=True)[:8]:
        url = f"https://mattoon.illinois.gov/download/{slug}/"
        try:
            docs = re.findall(r'wpdm-filelist-item__title">([^<]+)<', fetch_text(url))
        except Exception:
            docs = []
        rec = {"date": date, "url": url, "documents": docs}
        (upcoming if date >= today.isoformat() else recent).append(rec)

    if upcoming or recent:
        ev.add(
            "upcoming",
            kind="council",
            body="Mattoon City Council",
            cadence="1st and 3rd Tuesdays",
            upcoming=upcoming,
            most_recent=recent[:2],
            index=idx,
            caveat=(
                "Agenda and packet PDFs are behind a CAPTCHA, so only meeting dates "
                "and document titles could be read — not the agenda contents."
            ),
        )


def gather_watershed_committee(ev: Evidence) -> None:
    page = ("http://www.colescountyswcd.org/resources/watersheds/"
            "lake-mattoon-lake-paradise-watershed-committee/")
    try:
        raw = fetch_text(page)
    except Exception as exc:
        ev.miss("Lake Mattoon/Paradise Watershed Committee", f"unreachable ({type(exc).__name__})")
        return

    text = visible(raw)
    body = re.search(r"Lake Mattoon/Paradise Watershed Committee(.{0,1200})", text)
    agenda_url = re.search(r'href="([^"]+\.(?:docx?|pdf))"', raw)

    agenda = ""
    if agenda_url:
        try:
            data = fetch(agenda_url.group(1))
            agenda = (docx_text(data) if agenda_url.group(1).endswith(("doc", "docx"))
                      else pdf_text(data))
        except Exception:
            agenda = ""

    ev.add(
        "planning",
        kind="watershed_committee",
        body="Lake Mattoon and Lake Paradise Watershed Committee",
        summary=(body.group(1).strip()[:900] if body else ""),
        agenda_url=agenda_url.group(1) if agenda_url else None,
        agenda_text=agenda[:1800],
        page=page,
        note="Open to anyone; contact mattoonandparadisewatershed@gmail.com.",
    )


COUNTIES = [
    ("Coles County Board", "https://www.colesco.illinois.gov/board/agendas/"),
    ("Shelby County Board", "https://shelbycounty-il.gov/cominutes.aspx"),
    ("Cumberland County Board", "https://cumberlandcoil.gov/county-board-agendas-minutes/"),
]


def recent_pdf_links(index_url: str, page_html: str, limit: int = 6) -> list[str]:
    """Absolute PDF URLs from a county index, newest-looking first.

    Shelby publishes ~1000 links, relative and with an uppercase .PDF suffix,
    ordered oldest-first -- taking the first few would scan 2023 minutes. Rank
    by any year in the URL so the current season floats up.
    """
    from urllib.parse import urljoin

    links = re.findall(r'href="([^"]+\.pdf)"', page_html, re.IGNORECASE)
    seen, ranked = set(), []
    for href in links:
        url = urljoin(index_url, html.unescape(href))
        if url in seen:
            continue
        seen.add(url)
        years = [int(y) for y in re.findall(r"20\d{2}", url)]
        ranked.append((max(years) if years else 0, url))

    ranked.sort(key=lambda p: p[0], reverse=True)
    return [u for _, u in ranked[:limit]]


def gather_counties(ev: Evidence) -> None:
    """Scan county agenda PDFs for lake and watershed items.

    Lake Mattoon lies mostly in Shelby and Cumberland counties even though the
    City of Mattoon (Coles County) owns it, so all three boards can take up
    items that affect the lake.
    """
    for name, index in COUNTIES:
        try:
            raw = fetch_text(index)
        except Exception as exc:
            ev.miss(name, f"unreachable ({type(exc).__name__})")
            continue

        hits, checked = [], 0
        for link in recent_pdf_links(index, raw):
            try:
                text = pdf_text(fetch(link, timeout=50))
            except Exception:
                continue
            checked += 1
            if not is_about_lake(text):
                continue
            found = excerpts(text, limit=3)
            if found:
                from urllib.parse import unquote

                hits.append({
                    "document": unquote(link.rsplit("/", 1)[-1]),
                    "url": link,
                    "excerpts": found,
                })

        ev.add(
            "upcoming",
            kind="county",
            body=name,
            index=index,
            documents_checked=checked,
            lake_items=hits,
        )


# ── the article ───────────────────────────────────────────────────────────────

SYSTEM = """You write a weekly community brief for Friends of Lake Mattoon, a \
volunteer group in central Illinois. Your readers live on or near the lake.

Ground rules, in order of importance:

1. Use ONLY the evidence provided. Never add background, history, or numbers \
from your own knowledge. If the evidence does not support a sentence, cut it.
2. Distinguish "nothing happened" from "we could not check." An unreachable \
source is reported as unchecked, never as quiet.
3. Lake Paradise is the City of Mattoon's PRIMARY drinking water source; Lake \
Mattoon is a CONTINGENCY source. The July 2025 Do Not Drink order followed a \
bloom in Lake Paradise. Never imply Lake Mattoon caused it.
4. Microcystin is a liver toxin, not a neurological one, whatever the City's \
notices call it. If you quote their wording, note the discrepancy once.
5. Most people who live on Lake Mattoon are OUTSIDE Mattoon city limits and do \
not vote for the council that governs the lake. Write for them.
6. Be plain and calm. No alarm when readings are low; no reassurance when they \
are not. Say what the numbers are and let them speak.

Length: 350-600 words. Lead with whatever actually matters most this week — \
often that is an upcoming meeting, not a water reading that has not moved."""

PROMPT = """Write this week's brief from the evidence below.

Cover these beats, in whatever order the news justifies:
- WATER QUALITY: what the latest readings show and whether anything changed.
- WATERSHED PLANNING: the Watershed Committee, TMDL work, funding, erosion.
- COMING UP: meetings residents could attend, with dates, and any agenda item \
touching the lake. This is the most actionable section — do not bury it.

Formatting:
- Start with an H1 title. Make it specific to this week, not evergreen.
- Use short H2 sections.
- Link to sources inline with markdown links.
- End with a one-line note on anything that could not be checked, if any.
- If a beat genuinely has no news, say so in one sentence rather than padding.

Today is {today}.

EVIDENCE
========
{evidence}

SOURCES THAT COULD NOT BE CHECKED
=================================
{unavailable}"""


def write_article(ev: Evidence) -> str:
    import anthropic

    client = anthropic.Anthropic()
    prompt = PROMPT.format(
        today=dt.date.today().isoformat(),
        evidence=json.dumps(ev.items, indent=1, default=str)[:60000],
        unavailable=json.dumps(ev.unavailable, indent=1) or "(none)",
    )

    with client.messages.stream(
        model=MODEL,
        max_tokens=4000,
        system=SYSTEM,
        thinking={"type": "adaptive"},
        output_config={"effort": "high"},
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        msg = stream.get_final_message()

    if msg.stop_reason == "refusal":
        raise RuntimeError("Claude declined to write the brief.")
    return "".join(b.text for b in msg.content if b.type == "text").strip()


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="gather and print evidence without calling the API")
    args = ap.parse_args()

    state = json.loads(STATE_PATH.read_text()) if STATE_PATH.exists() else {}
    ev = Evidence()

    gather_water_quality(ev, state)
    gather_city(ev, state)
    gather_council_meetings(ev)
    gather_watershed_committee(ev)
    gather_counties(ev)

    if args.dry_run:
        print(json.dumps({"items": ev.items, "unavailable": ev.unavailable},
                         indent=2, default=str))
        print(f"\n{len(ev.items)} evidence items, "
              f"{len(ev.unavailable)} unreachable source(s).", file=sys.stderr)
        return 0

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set.", file=sys.stderr)
        return 1

    article = write_article(ev)
    today = dt.date.today().isoformat()
    title = next((l.lstrip("# ").strip() for l in article.splitlines()
                  if l.startswith("# ")), "Lake Watch brief")

    NEWS.mkdir(parents=True, exist_ok=True)
    out = NEWS / f"{today}-lake-watch-brief.md"
    out.write_text(
        f"---\ntitle: {json.dumps(title)}\ndate: {today}\n"
        f"category: Lake Watch\nstatus: draft\n---\n\n{article}\n"
    )

    BRIEF_PATH.write_text(json.dumps({
        "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "date": today,
        "title": title,
        "markdown": article,
        "evidence_count": len(ev.items),
        "unavailable": ev.unavailable,
    }, indent=2) + "\n")

    wq = ev.by_beat("water")
    if wq and wq[0].get("latest"):
        state["last_reading_date"] = wq[0]["latest"]["date"]
    state["last_brief"] = today
    STATE_PATH.write_text(json.dumps(state, indent=2) + "\n")

    print(f"Wrote {out.relative_to(REPO_ROOT)} ({len(article.split())} words) "
          f"from {len(ev.items)} evidence items.")
    if ev.unavailable:
        print(f"Unreachable: {[u['source'] for u in ev.unavailable]}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
