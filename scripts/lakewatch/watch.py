#!/usr/bin/env python3
"""Parse the City of Mattoon water quality log into data/lake-watch/water-quality.json.

The most recent "Water Quality Update" public notice is a cumulative archive of
every microcystin reading the city has published, so one fetch rebuilds the
whole series. This script re-derives the file from scratch each run rather than
appending, which means a correction on the city's side propagates to us.

No credentials required -- this source is plain HTML.

Usage:
    python3 scripts/lakewatch/watch.py
"""

from __future__ import annotations

import datetime as dt
import html
import json
import pathlib
import re
import ssl
import sys
import urllib.request

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT_PATH = REPO_ROOT / "data" / "lake-watch" / "water-quality.json"

# The public-notices index renders notices inline without linking each post, so
# the sitemap is the discovery path. robots.txt permits it (only /wp-json/ and
# search are disallowed), and it carries a lastmod we can surface.
SITEMAP_URL = "https://mattoon.illinois.gov/post-sitemap.xml"
UA = "FriendsOfLakeMattoon-WaterQuality/1.0 (+https://friendsoflakemattoon.org)"

# EPA 10-day drinking water health advisory for microcystin.
ADVISORY_SENSITIVE = 0.30  # children under 6, pregnant/nursing, liver conditions,
#                            dialysis patients, elderly, immunocompromised
ADVISORY_GENERAL = 1.60  # healthy adults and children over 6

READING_RE = re.compile(
    r"Sample collected on "
    r"([A-Z][a-z]+ \d{1,2}(?:st|nd|rd|th)?(?:,? \d{4})?)"
    r"[^.]*?at\s+([0-9.oO]+)\s*ppb",
    re.IGNORECASE,
)

MONTHS = {
    m: i
    for i, m in enumerate(
        [
            "january", "february", "march", "april", "may", "june",
            "july", "august", "september", "october", "november", "december",
        ],
        start=1,
    )
}


def ssl_context() -> ssl.SSLContext:
    """Prefer certifi's bundle.

    Homebrew Python ships without a populated CA store, and some county sites in
    this project serve an incomplete certificate chain. Verification stays on
    either way -- this only widens which roots we trust.
    """
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=45, context=ssl_context()) as resp:
        return resp.read().decode("utf-8", errors="replace")


def strip_markup(raw: str) -> str:
    text = re.sub(r"<(script|style).*?</\1>", " ", raw, flags=re.S | re.I)
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    return re.sub(r"\s+", " ", text)


def find_latest_update(sitemap_xml: str) -> tuple[str, str | None] | None:
    """Locate the current 'Water Quality Update' post and its lastmod.

    Date slugs are inconsistent (july21, august-25-2025, july-30-2026), so the
    URL cannot be constructed -- it has to be discovered. The city keeps a
    single cumulative post rather than one per sampling round, so we take the
    newest by lastmod if more than one ever appears.
    """
    entries = re.findall(
        r"<url>\s*<loc>([^<]*water-quality-update[^<]*)</loc>"
        r"(?:\s*<lastmod>([^<]*)</lastmod>)?",
        sitemap_xml,
        re.IGNORECASE,
    )
    if not entries:
        return None
    url, lastmod = max(entries, key=lambda e: e[1] or "")
    return url, (lastmod or None)


def parse_value(raw: str) -> float | None:
    """Handle the city's occasional typos, e.g. '0.02o' with a letter o."""
    cleaned = raw.replace("o", "0").replace("O", "0")
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_date(raw: str, fallback_year: int) -> str | None:
    """Older entries omit the year ('August 7th'); infer it from context."""
    cleaned = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", raw).replace(",", "")
    parts = cleaned.split()
    if len(parts) < 2:
        return None

    month = MONTHS.get(parts[0].lower())
    if month is None:
        return None

    try:
        day = int(parts[1])
        year = int(parts[2]) if len(parts) > 2 else fallback_year
        return dt.date(year, month, day).isoformat()
    except (ValueError, IndexError):
        return None


def main() -> int:
    found = find_latest_update(fetch(SITEMAP_URL))
    if not found:
        print("No Water Quality Update post in the sitemap.", file=sys.stderr)
        return 1
    update_url, lastmod = found

    text = strip_markup(fetch(update_url))
    raw_readings = READING_RE.findall(text)
    if not raw_readings:
        print(f"No readings parsed from {update_url}", file=sys.stderr)
        return 1

    # Readings run newest-first. Undated entries inherit the year of the last
    # dated reading above them, stepping back a year when the month increases
    # (which means we crossed a January boundary going backwards).
    readings: list[dict] = []
    year = dt.date.today().year
    prev_month: int | None = None
    skipped: list[str] = []

    for raw_date, raw_value in raw_readings:
        value = parse_value(raw_value)
        month = MONTHS.get(raw_date.split()[0].lower())

        if month is not None and prev_month is not None and month > prev_month:
            year -= 1
        prev_month = month

        iso = parse_date(raw_date, year)
        if iso is None or value is None:
            skipped.append(f"{raw_date} @ {raw_value} ppb")
            continue

        year = int(iso[:4])
        readings.append(
            {
                "date": iso,
                "ppb": value,
                "over_sensitive_advisory": value > ADVISORY_SENSITIVE,
            }
        )

    readings.sort(key=lambda r: r["date"])
    values = [r["ppb"] for r in readings]

    payload = {
        "schema": {"name": "water-quality", "version": 1},
        "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "source_url": update_url,
        "source_lastmod": lastmod,
        "toxin": "microcystin",
        "toxin_note": (
            "Microcystin is a cyanotoxin produced by blue-green algae. It is "
            "primarily a liver toxin. The City of Mattoon's notices describe it "
            "as a 'neurological toxin', which is imprecise."
        ),
        "sampling_note": (
            "Readings are of finished (treated) drinking water. Illinois EPA "
            "recommends sampling every two weeks. Lake Paradise is Mattoon's "
            "primary water source; Lake Mattoon is a contingency source."
        ),
        "advisory_sensitive_ppb": ADVISORY_SENSITIVE,
        "advisory_general_ppb": ADVISORY_GENERAL,
        "count": len(readings),
        "latest": readings[-1] if readings else None,
        "max_ppb": max(values) if values else None,
        "exceedances": sum(1 for r in readings if r["over_sensitive_advisory"]),
        "unparsed": skipped,
        "readings": readings,
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(payload, indent=2) + "\n")

    latest = payload["latest"]
    print(
        f"{payload['count']} readings through {latest['date']} "
        f"({latest['ppb']} ppb); max {payload['max_ppb']} ppb; "
        f"{payload['exceedances']} above the {ADVISORY_SENSITIVE} ppb advisory."
    )
    if skipped:
        print(f"Skipped {len(skipped)} unparseable: {skipped}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
