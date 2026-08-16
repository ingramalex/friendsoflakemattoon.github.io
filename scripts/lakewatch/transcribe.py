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

Findings are saved permanently under data/lake-watch/meetings/ so action items
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
import time
import urllib.error
import urllib.request

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
DATA = REPO_ROOT / "data"
MEETINGS = DATA / "lake-watch" / "meetings"
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


DONE_STATES = {"completed", "succeeded", "done", "finished"}
FAIL_STATES = {"failed", "errored", "cancelled", "canceled"}


def gemini_call(parts: list[dict], api_key: str, poll_for: int = 1500) -> dict:
    """Submit an interaction and wait for it to finish.

    Reviewing a 46-minute meeting takes minutes, and the endpoint returns an
    interaction resource rather than a finished answer -- a synchronous read
    either comes back still-running or dies at the gateway with a 504. So
    submit, then poll the interaction by id until it reports a terminal state.
    """
    body = json.dumps({"model": GEMINI_MODEL, "input": parts}).encode()

    # Submitting is the flaky step: a ten-meeting backfill hit one "high demand"
    # 500 and two read timeouts. Both are transient and cost nothing to retry,
    # whereas losing a meeting means noticing and re-running it by hand.
    payload = None
    last = ""
    for attempt in range(4):
        if attempt:
            wait = 20 * (2 ** (attempt - 1))
            print(f"    retry {attempt}/3 in {wait}s after {last}", flush=True)
            time.sleep(wait)
        req = urllib.request.Request(
            GEMINI_ENDPOINT, data=body,
            headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                payload = json.loads(r.read())
            break
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:400]
            last = f"HTTP {exc.code}"
            # 4xx other than rate limiting is a bad request; retrying won't help.
            if exc.code < 500 and exc.code != 429:
                raise RuntimeError(f"Gemini returned HTTP {exc.code}: {detail}") from exc
            if exc.code == 429:
                # A per-minute spike is worth one short wait; a daily quota is
                # not, and on the free tier a backfill of hour-long videos hits
                # the daily cap long before it hits a per-minute one. Say so
                # rather than burning minutes and reporting a vague failure.
                if attempt >= 1:
                    raise RuntimeError(
                        "Gemini rate limit (HTTP 429) after a retry. On the free "
                        "tier this is usually the 8 hours/day of YouTube video "
                        "cap rather than a momentary spike — roughly ten "
                        "hour-long meetings. Re-run after the daily quota "
                        f"resets, or move to a paid tier. Detail: {detail}"
                    ) from exc
        except Exception as exc:
            last = f"{type(exc).__name__}"

    if payload is None:
        raise RuntimeError(f"Gemini submit failed after 4 attempts ({last})")

    interaction_id = payload.get("id")
    status = str(payload.get("status", "")).lower()
    waited = 0

    while interaction_id and status and status not in DONE_STATES | FAIL_STATES:
        if waited >= poll_for:
            raise RuntimeError(
                f"Interaction {interaction_id} still '{status}' after {waited}s")
        time.sleep(15)
        waited += 15
        poll = urllib.request.Request(
            f"{GEMINI_ENDPOINT}/{interaction_id}",
            headers={"x-goog-api-key": api_key}, method="GET")
        try:
            with urllib.request.urlopen(poll, timeout=120) as r:
                payload = json.loads(r.read())
        except Exception as exc:
            raise RuntimeError(f"Polling failed after {waited}s: "
                               f"{type(exc).__name__}: {exc}") from exc
        status = str(payload.get("status", "")).lower()
        print(f"    …{waited}s, status={status}", flush=True)

    if status in FAIL_STATES:
        raise RuntimeError(f"Interaction ended '{status}': {json.dumps(payload)[:500]}")
    return payload


def gemini_extract(video_url: str, api_key: str, timeout: int = 900) -> dict:
    """Ask Gemini to watch the meeting and return structured findings."""
    payload = gemini_call([
        {"type": "text", "text": EXTRACTION_PROMPT},
        {"type": "video", "uri": video_url},
    ], api_key)

    text = find_text(payload)
    if not text:
        raise RuntimeError(f"No text in Gemini response: {json.dumps(payload)[:600]}")

    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise RuntimeError(f"Gemini did not return JSON: {text[:400]}")
    return json.loads(text[start:end + 1])


def find_text(payload) -> str:
    """Pull the model's text out of the response.

    An earlier version took the longest string anywhere in the payload. That
    silently returned an encrypted reasoning signature -- a long opaque blob --
    and reported success, which is worse than failing. Only fields actually
    named as text now count, and blobs that do not look like prose are rejected.
    """
    found: list[str] = []

    def looks_like_prose(s: str) -> bool:
        if len(s) < 2 or " " not in s:
            return False
        # Base64-ish signatures are long, spaceless-ish, and punctuation-heavy.
        letters = sum(c.isalpha() or c.isspace() for c in s)
        return letters / len(s) > 0.75

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k == "text" and isinstance(v, str) and looks_like_prose(v):
                    found.append(v)
                else:
                    walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(payload)
    return "\n".join(found).strip()


def outline(o, depth: int = 0, path: str = "") -> list[str]:
    """A compact map of a response so its shape can be read at a glance."""
    out: list[str] = []
    pad = "  " * depth
    if isinstance(o, dict):
        for k, v in list(o.items())[:14]:
            if isinstance(v, (dict, list)):
                out.append(f"{pad}{k}: {type(v).__name__}")
                if depth < 4:
                    out += outline(v, depth + 1, f"{path}/{k}")
            else:
                s = str(v)
                out.append(f"{pad}{k}: {type(v).__name__} = "
                           f"{s[:70]}{'…' if len(s) > 70 else ''}")
    elif isinstance(o, list):
        out.append(f"{pad}[{len(o)} items]")
        if o and depth < 4:
            out += outline(o[0], depth + 1, f"{path}[0]")
    return out


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
    ap.add_argument("--probe", action="store_true",
                    help="one cheap call to check auth, model name, and that "
                         "YouTube URLs are accepted, before spending on a backfill")
    args = ap.parse_args()

    if args.probe:
        key = os.environ.get("GEMINI_API_KEY")
        if not key:
            print("GEMINI_API_KEY is not set.", file=sys.stderr)
            return 1
        url = "https://www.youtube.com/watch?v=" + (args.video or "PV7CejNeCpo")
        print(f"Probing {GEMINI_MODEL} with {url}", flush=True)
        try:
            payload = gemini_call([
                {"type": "text",
                 "text": "In one sentence: what kind of meeting is this, and "
                         "roughly how long does it run?"},
                {"type": "video", "uri": url},
            ], key)
        except Exception as exc:
            print(f"{exc}", file=sys.stderr)
            return 1
        print(f"final status: {payload.get('status')}")

        print("\n--- response outline ---")
        print("\n".join(outline(payload)[:60]))

        text = find_text(payload)
        print(f"\n--- extracted text ---\n{text[:600] or '(none)'}\n")

        if not text:
            print("PROBE FAILED: the call succeeded but no prose was extracted. "
                  "Use the outline above to correct find_text().", file=sys.stderr)
            return 1
        print("PROBE OK — auth, model name, YouTube input, and text extraction "
              "all work.")
        return 0

    gemini_key = os.environ.get("GEMINI_API_KEY")
    if not gemini_key:
        print("GEMINI_API_KEY is not set. Get one at https://aistudio.google.com/apikey",
              file=sys.stderr)
        return 1
    if not args.extract_only and not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set (or pass --extract-only).", file=sys.stderr)
        return 1

    if args.video:
        # Look the meeting up in the channel index rather than stamping today's
        # date on it. Findings are filed by meeting date and tracked across
        # meetings, so a wrong date quietly misfiles the record -- an August
        # meeting's findings would sit under the day it happened to be reviewed.
        known = {m["video_id"]: m for m in load_meeting_index()}
        found = known.get(args.video)
        if found:
            meetings = [found]
            print(f"Matched {args.video} to {found['date']} — {found['title']}")
        elif args.date:
            meetings = [{
                "video_id": args.video,
                "date": args.date,
                "title": f"Meeting {args.date}",
                "url": f"https://www.youtube.com/watch?v={args.video}",
            }]
        else:
            print(f"{args.video} is not in the channel index; pass --date "
                  "explicitly so the findings are filed correctly.", file=sys.stderr)
            return 1
    elif args.latest:
        meetings = load_meeting_index()[:args.latest]
        if not meetings:
            print("Could not read the channel index.", file=sys.stderr)
            return 1
    else:
        ap.error("pass --latest N or --video ID")

    MEETINGS.mkdir(parents=True, exist_ok=True)
    NEWS.mkdir(parents=True, exist_ok=True)
    processed = failed = 0

    for m in meetings:
        date, vid = m.get("date") or "undated", m["video_id"]
        out = MEETINGS / f"{date}_{vid}.json"

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
            "schema": {"name": "meeting-findings", "version": 1},
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
