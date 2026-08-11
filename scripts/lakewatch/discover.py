#!/usr/bin/env python3
"""Weekly source discovery for the FOLM watershed monitor.

Asks Claude to search the web for government and institutional sources that
publish information about Lake Mattoon, Lake Paradise, or the surrounding
watershed, then reports anything not already in data/sources.json.

Candidates are written to stdout as Markdown for a GitHub issue. Nothing is
added to the registry automatically -- a source that turns out to be a content
farm, a stale mirror, or a hallucinated URL would undermine the credibility
this whole project exists to build. A human promotes candidates by editing
data/sources.json.

Usage:
    ANTHROPIC_API_KEY=... python3 scripts/lakewatch/discover.py
"""

from __future__ import annotations

import json
import os
import pathlib
import ssl
import sys
import urllib.error
import urllib.request
from typing import Any

import anthropic

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SOURCES_PATH = REPO_ROOT / "data" / "sources.json"

MODEL = "claude-opus-5"

SYSTEM = """You research public information sources for Friends of Lake Mattoon, \
a volunteer group in central Illinois.

Lake Mattoon spans Coles, Shelby, and Cumberland counties. It is owned by the \
City of Mattoon and serves as a CONTINGENCY drinking water source; Lake Paradise \
is the city's PRIMARY source. Most people who live on Lake Mattoon are outside \
Mattoon city limits.

Your job is to find OFFICIAL and INSTITUTIONAL sources that publish information \
about these lakes, their watershed, water quality, or the governing bodies that \
make decisions about them. Good sources are government agencies, county boards, \
soil and water conservation districts, state environmental agencies, universities, \
and named advisory committees. Local news outlets are acceptable but lower value.

Do NOT report: aggregator sites, fishing/real-estate/tourism listing sites, \
AI-generated content, sites that merely mention the lake in passing, or any URL \
you have not actually seen in a search result.

For every candidate, you must be able to point to a real URL you encountered. \
If you are unsure a URL exists, omit it. It is far better to return two solid \
candidates than ten speculative ones."""

PROMPT_TEMPLATE = """Search for public information sources about Lake Mattoon and \
Lake Paradise in central Illinois, and about the governing bodies with authority \
over them.

Run several distinct searches. Cover at minimum:
- Lake Mattoon / Lake Paradise water quality, algal blooms, microcystin
- The Lake Mattoon and Lake Paradise Watershed Committee
- The Lake Mattoon wake boat ordinance
- Shelby County and Cumberland County boards and their meeting agendas (Lake \
Mattoon lies mostly in these two counties, and we currently have NO source for \
either -- this is our biggest known gap)
- County health departments with a water-testing role
- Illinois EPA, Illinois DNR, and University of Illinois Extension material \
specific to these lakes or the Little Wabash River watershed

We already track these sources, so do not report them again:
{known}

We have already determined these are inaccessible, so do not report them:
{blocked}

Return your findings as a JSON array and nothing else. Each element:
{{
  "name": "<official name of the source>",
  "url": "<exact URL you saw>",
  "why": "<one sentence: what it publishes and why FOLM should care>",
  "gap": "<which of the gaps above it fills, or 'new'>",
  "confidence": "high" | "medium" | "low"
}}

If you find nothing new worth reporting, return []."""


def load_registry() -> dict[str, Any]:
    with SOURCES_PATH.open() as fh:
        return json.load(fh)


def known_urls(registry: dict[str, Any]) -> set[str]:
    urls = {s["url"] for s in registry["sources"]}
    urls |= {s.get("api", "") for s in registry["sources"]}
    urls.discard("")
    return urls


def normalize(url: str) -> str:
    """Strip scheme, www, and trailing slash so we compare hosts+paths fairly."""
    u = url.strip().lower()
    for prefix in ("https://", "http://"):
        if u.startswith(prefix):
            u = u[len(prefix) :]
    if u.startswith("www."):
        u = u[4:]
    return u.rstrip("/")


def ask_claude(registry: dict[str, Any]) -> list[dict[str, Any]]:
    client = anthropic.Anthropic()

    known = "\n".join(f"- {s['name']} — {s['url']}" for s in registry["sources"])
    blocked = "\n".join(
        f"- {s['name']} — {s['reason']}" for s in registry["blocked"]
    )

    prompt = PROMPT_TEMPLATE.format(known=known, blocked=blocked)

    # Streaming: web search plus adaptive thinking can push a single request
    # well past the non-streaming timeout.
    with client.messages.stream(
        model=MODEL,
        max_tokens=8000,
        system=SYSTEM,
        thinking={"type": "adaptive"},
        output_config={"effort": "high"},
        tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": 12}],
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        response = stream.get_final_message()

    if response.stop_reason == "refusal":
        print("Claude declined this request.", file=sys.stderr)
        return []

    text = "".join(b.text for b in response.content if b.type == "text").strip()

    # The model may wrap the array in prose or a fence; pull out the array.
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1:
        print(f"No JSON array in response:\n{text}", file=sys.stderr)
        return []

    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        print(f"Could not parse candidates: {exc}\n{text}", file=sys.stderr)
        return []


def ssl_context() -> ssl.SSLContext:
    """Prefer certifi's bundle so a valid site isn't misreported as unreachable.

    Without this, hosts whose chain the local store can't complete look
    identical to hallucinated URLs -- which would make the check worse than
    useless. Verification stays on; this only widens the trusted roots.
    """
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def url_is_live(url: str) -> tuple[str, str]:
    """Check whether a candidate URL actually resolves.

    Returns (verdict, detail) where verdict is "live", "tls", or "dead".

    "tls" is called out separately because it is not a hallucination signal:
    a certificate error means the host exists and completed a TCP connection,
    it just serves an incomplete chain. Several county sites in this watershed
    do exactly that (colesco.illinois.gov among them), and collapsing them into
    "dead" would train the reviewer to ignore the warning.
    """
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "FriendsOfLakeMattoon-SourceCheck/1.0"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=25, context=ssl_context()) as resp:
            return ("live" if 200 <= resp.status < 400 else "dead", str(resp.status))
    except urllib.error.HTTPError as exc:
        return "dead", f"HTTP {exc.code}"
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, ssl.SSLCertVerificationError):
            return "tls", "incomplete certificate chain"
        return "dead", str(exc.reason)
    except Exception as exc:  # noqa: BLE001 - anything else means "can't verify"
        return "dead", type(exc).__name__


def main() -> int:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set.", file=sys.stderr)
        return 1

    registry = load_registry()
    seen = {normalize(u) for u in known_urls(registry)}
    seen |= {normalize(s.get("detail", "")) for s in registry["blocked"]}

    candidates = ask_claude(registry)
    fresh = [c for c in candidates if normalize(c.get("url", "")) not in seen]

    if not fresh:
        print("NO_CANDIDATES")
        return 0

    lines = [
        "Claude's weekly sweep turned up sources not in "
        "[`data/sources.json`](../blob/main/data/sources.json).",
        "",
        "Every URL below was fetched to confirm it resolves — but **reachable is not "
        "the same as trustworthy**. Open each one before promoting it into the "
        "registry, and check that it is an official or institutional publisher "
        "rather than an aggregator.",
        "",
    ]

    verdicts = {
        "live": "✅ resolves",
        "tls": "🔒 host is up, but serves an incomplete certificate chain",
        "dead": "⚠️ did not resolve — verify this URL exists before trusting it",
    }

    for c in fresh:
        url = c.get("url", "")
        verdict, detail = url_is_live(url)
        status = f"{verdicts[verdict]} ({detail})"
        lines += [
            f"### {c.get('name', 'Untitled')}",
            "",
            f"- **URL:** {url}",
            f"- **Reachability:** {status}",
            f"- **Model confidence:** {c.get('confidence', 'unknown')}",
            f"- **Fills gap:** {c.get('gap', 'new')}",
            f"- **Why it matters:** {c.get('why', '—')}",
            "",
        ]

    lines += [
        "---",
        "",
        "To accept a source, add it to `data/sources.json` with an `access` field "
        "describing how it can actually be retrieved. If it turns out to be behind "
        "a CAPTCHA or disallowed by robots.txt, record it under `blocked` instead "
        "so we don't re-test it every week.",
    ]

    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
