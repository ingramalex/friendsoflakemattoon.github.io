#!/usr/bin/env python3
"""Extract Lake Mattoon and Neoga items from a recorded meeting, then write it up.

The City streams Council meetings to YouTube. Their captions sit behind
endpoints YouTube's robots.txt disallows, and the official Data API only lets
the video's owner download them -- so neither is used here. Instead the video
URL is handed to Google's own Gemini API, which accepts public YouTube links as
input. That is the platform owner's sanctioned route, and it touches none of
the disallowed paths.

Division of labour, on purpose:

  Gemini  watches the meeting and returns structured findings with timestamps.
          It is the only model here that can see the video.
  Claude  turns those findings into the article, so meeting write-ups read in
          the same voice as the weekly brief.

Findings are saved permanently under data/transcripts/ so action items and
resident concerns can be tracked across meetings rather than re-derived.

Usage:
    GEMINI_API_KEY=... ANTHROPIC_API_KEY=... \
        python3 scripts/lakewatch/transcribe.py --latest 2
    python3 scripts/lakewatch/transcribe.py --video PV7CejNeCpo --date 2026-08-04
    python3 scripts/lakewatch/transcribe.py --latest 10 --extract-only
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import re
import sys
import urllib.error
import urllib.request

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
DATA = REPO_ROOT / "data"
TRANSCRIPTS = DATA / "transcripts"
NEWS = REPO_ROOT / "content" / "news"

GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/interactions"
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")
CLAUDE_MODEL = "claude-opus-5"

EXTRACTION_PROMPT = """You are reviewing a recorded public meeting of the Mattoon \
City Council in central Illinois, on behalf of Friends of Lake Mattoon, a \
volunteer group of residents around the lake.

Find everything in this meeting that concerns:
- Lake Mattoon or Lake Paradise
- water quality, algal blooms, microcystin, the water treatment plant
- the watershed, shoreline erosion, dredging, nutrient runoff, septic systems
- wake boats or boating rules on the lake
- the City of Neoga, or anything affecting Neoga residents
- any comment from a member of the public about the lake or water

Return ONLY a JSON object, no prose around it, in exactly this shape:

{
  "meeting_summary": "<two sentences on what this meeting covered overall>",
  "lake_discussed": true or false,
  "topics": [
    {
      "timestamp": "MM:SS",
      "topic": "<short label>",
      "what_happened": "<2-3 sentences, specific>",
      "speakers": "<roles if identifiable, e.g. 'city manager', 'resident'>",
      "importance": "high" or "normal"
    }
  ],
  "citizen_concerns": [
    {
      "timestamp": "MM:SS",
      "concern": "<what the resident actually raised>",
      "response": "<how officials responded, or 'no response recorded'>"
    }
  ],
  "action_items": [
    {
      "timestamp": "MM:SS",
      "action": "<what was committed to>",
      "owner": "<who committed, if stated>",
      "status": "committed" | "proposed" | "voted"
    }
  ],
  "votes": [
    {"timestamp": "MM:SS", "measure": "<what was voted on>", "outcome": "<result>"}
  ]
}

Rules that matter more than completeness:
- Every timestamp must be one you actually observed. Do not estimate.
- If the lake never comes up, set lake_discussed false and return empty arrays.
  A meeting with nothing about the lake is a normal and useful result.
- Do not infer commitments. An official saying "we'll look into it" is an
  action item; an official saying "that's a good question" is not.
- Prefer omitting an item to guessing at one."""


def gemini_extract(video_url: str, api_key: str, timeout: int = 900) -> dict:
    """Ask Gemini to watch the meeting and return structured findings."""
    body = json.dumps({
        "model": GEMINI_MODEL,
        "input": [
            {"type": "text", "text": EXTRACTION_PROMPT},
            {"type": "video", "uri": video_url},
        ],
    }).encode()

    req = urllib.request.Request(
        GEMINI_ENDPOINT,
        data=body,
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = json.loads(r.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:600]
        raise RuntimeError(f"Gemini returned HTTP {exc.code}: {detail}") from exc

    text = find_text(payload)
    if not text:
        raise RuntimeError(f"No text in Gemini response: {json.dumps(payload)[:600]}")

    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise RuntimeError(f"Gemini did not return JSON: {text[:400]}")
    return json.loads(text[start:end + 1])


def find_text(payload) -> str:
    """Pull the model's text out of the response without assuming its shape.

    The response envelope has moved between API versions; the findings matter
    more than the wrapper, so walk for the longest string rather than hard-code
    a path that a version bump would silently break.
    """
    best = ""

    def walk(o):
        nonlocal best
        if isinstance(o, str):
            if len(o) > len(best):
                best = o
        elif isinstance(o, dict):
            for k, v in o.items():
                if k in {"text", "content", "output", "parts", "candidates",
                         "message", "outputs", "input"}:
                    walk(v)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(payload)
    return best


ARTICLE_SYSTEM = """You write short meeting write-ups for Friends of Lake Mattoon, \
a volunteer group of residents around Lake Mattoon in central Illinois.

Ground rules:
1. Use ONLY the findings provided. Add no history, context, or numbers of your
   own. If a detail is not in the findings, it does not go in the article.
2. These findings come from an AI review of an auto-captioned recording. Names,
   figures, and technical terms are frequently misheard. Never present anything
   as a direct quotation. Link timestamps so readers can hear it themselves.
3. Lake Paradise is the City's PRIMARY drinking water source; Lake Mattoon is a
   CONTINGENCY source. Never imply Lake Mattoon caused the 2025 bloom.
4. Most people who live on Lake Mattoon are OUTSIDE Mattoon city limits and do
   not vote for this council. Write for them.
5. Lead with what residents raised and what officials committed to. Those are
   the two things people can act on.

250-450 words. Calm and specific. No filler."""


def write_article(findings: dict, meeting_date: str, video_url: str) -> str:
    import anthropic

    client = anthropic.Anthropic()
    prompt = (
        f"Write the write-up for the Mattoon City Council meeting of {meeting_date}.\n\n"
        f"Recording: {video_url}\n"
        "Link timestamps as markdown links using the form "
        f"{video_url}&t=SECONDSs\n\n"
        "Formatting: H1 title naming the date and the main lake item; short H2s; "
        "a closing line noting the findings come from an AI review of automatic "
        "captions and should be verified against the recording.\n\n"
        f"FINDINGS\n========\n{json.dumps(findings, indent=1)}"
    )

    with client.messages.stream(
        model=CLAUDE_MODEL,
        max_tokens=3000,
        system=ARTICLE_SYSTEM,
        thinking={"type": "adaptive"},
        output_config={"effort": "high"},
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        msg = stream.get_final_message()

    if msg.stop_reason == "refusal":
        raise RuntimeError("Claude declined to write the meeting article.")
    return "".join(b.text for b in msg.content if b.type == "text").strip()


def load_meeting_index() -> list[dict]:
    """Reuse the channel index the brief already builds."""
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    import brief  # noqa: E402

    ev = brief.Evidence()
    brief.gather_meeting_videos(ev)
    for item in ev.items:
        if item.get("kind") == "meeting_videos":
            return item["recent"]
    return []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--latest", type=int, metavar="N",
                    help="process the N most recent meetings from the channel")
    ap.add_argument("--video", help="a single YouTube video id")
    ap.add_argument("--date", help="meeting date (YYYY-MM-DD) when using --video")
    ap.add_argument("--extract-only", action="store_true",
                    help="save findings without writing articles (no Anthropic key needed)")
    ap.add_argument("--force", action="store_true",
                    help="re-process meetings already saved")
    args = ap.parse_args()

    gemini_key = os.environ.get("GEMINI_API_KEY")
    if not gemini_key:
        print("GEMINI_API_KEY is not set. Get one at https://aistudio.google.com/apikey",
              file=sys.stderr)
        return 1
    if not args.extract_only and not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set (or pass --extract-only).", file=sys.stderr)
        return 1

    if args.video:
        meetings = [{
            "video_id": args.video,
            "date": args.date or dt.date.today().isoformat(),
            "title": f"Meeting {args.date or ''}".strip(),
            "url": f"https://www.youtube.com/watch?v={args.video}",
        }]
    elif args.latest:
        meetings = load_meeting_index()[:args.latest]
        if not meetings:
            print("Could not read the channel index.", file=sys.stderr)
            return 1
    else:
        ap.error("pass --latest N or --video ID")

    TRANSCRIPTS.mkdir(parents=True, exist_ok=True)
    NEWS.mkdir(parents=True, exist_ok=True)
    processed = failed = 0

    for m in meetings:
        date, vid = m.get("date") or "undated", m["video_id"]
        out = TRANSCRIPTS / f"{date}_{vid}.json"

        if out.exists() and not args.force:
            print(f"· {date} already saved, skipping ({out.name})")
            continue

        print(f"→ {date} {m['title'][:52]} … reviewing recording", flush=True)
        try:
            findings = gemini_extract(m["url"], gemini_key)
        except Exception as exc:
            print(f"  FAILED: {exc}", file=sys.stderr)
            failed += 1
            continue

        record = {
            "meeting_date": date,
            "title": m["title"],
            "video_id": vid,
            "url": m["url"],
            "extracted": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "extracted_by": GEMINI_MODEL,
            "caveat": ("Findings from an automated review of an auto-captioned "
                       "recording. Verify specifics against the video before "
                       "repeating them."),
            "findings": findings,
        }
        out.write_text(json.dumps(record, indent=2) + "\n")

        topics = findings.get("topics", [])
        concerns = findings.get("citizen_concerns", [])
        actions = findings.get("action_items", [])
        print(f"  saved {out.name} — lake discussed: {findings.get('lake_discussed')}, "
              f"{len(topics)} topic(s), {len(concerns)} concern(s), {len(actions)} action(s)")

        if args.extract_only:
            processed += 1
            continue

        if not findings.get("lake_discussed"):
            print("  no lake content — no article written")
            processed += 1
            continue

        try:
            article = write_article(findings, date, m["url"])
        except Exception as exc:
            print(f"  article FAILED: {exc}", file=sys.stderr)
            failed += 1
            continue

        title = next((l.lstrip("# ").strip() for l in article.splitlines()
                      if l.startswith("# ")), f"Council meeting, {date}")
        path = NEWS / f"{date}-council-meeting.md"
        path.write_text(
            f"---\ntitle: {json.dumps(title)}\ndate: {date}\n"
            f"category: Council meetings\nsource_video: {m['url']}\n"
            f"status: draft\n---\n\n{article}\n"
        )
        print(f"  wrote {path.relative_to(REPO_ROOT)}")
        processed += 1

    print(f"\n{processed} processed, {failed} failed.")
    return 1 if failed and not processed else 0


if __name__ == "__main__":
    sys.exit(main())
