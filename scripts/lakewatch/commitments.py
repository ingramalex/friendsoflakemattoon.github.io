#!/usr/bin/env python3
"""Track lake subjects across meetings, and surface the ones that went quiet.

A single meeting write-up tells you what happened that night. The useful
question is the one only a corpus can answer: what was raised, what did anyone
commit to, and did it ever come back?

Division of work, for the same reason as everywhere else in this project:

  Claude   groups items into threads. Deciding that "bowfishing ordinance
           review" in July and "invasive species" in September are the same
           subject is a judgment, and judgments are what a model is for.
  This code computes every number. Meetings elapsed, days open, last-seen
           dates -- all derived from the findings files, never from the model,
           because a fabricated "silent for 5 months" is the one error that
           would discredit the whole effort.

On language, deliberately: a thread with no later mention is reported as "no
further mention in the meetings we have reviewed." It is NOT reported as a
broken promise. The city may well have acted outside a council meeting, and we
would not know. The honest claim is about our record, not their conduct — and
it is the honest version that survives contact with a city official.

Usage:
    ANTHROPIC_API_KEY=... python3 scripts/lakewatch/commitments.py
    python3 scripts/lakewatch/commitments.py --dry-run
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
DATA = REPO_ROOT / "data"
TRANSCRIPTS = DATA / "transcripts"
OUT = DATA / "commitments.json"

MODEL = "claude-opus-5"

SYSTEM = """You organize records of public meetings for Friends of Lake Mattoon, \
a volunteer group in central Illinois tracking how the City of Mattoon manages \
Lake Mattoon.

You are given items pulled from recorded City Council meetings: topics raised, \
concerns from residents, commitments made by officials, and votes. Each carries \
the meeting date it came from.

Your job is to group these into THREADS. A thread is one continuing subject —
for example "septic systems around the lakes" or "water treatment plant ozone \
upgrade" — that may appear at several meetings under different wording.

Rules:
1. Group by subject, not by wording. The same issue described differently at two \
meetings is ONE thread.
2. Every thread must cite the item ids it is built from. Never invent an item.
3. Do not editorialize. Describe the subject and what happened; do not \
characterize the council's conduct, motives, or diligence.
4. If an item does not fit with any other, it is a thread of one. That is normal \
and expected.
5. Mark has_commitment true only when an official actually undertook to do \
something. A vote to spend money is a commitment. "We'll look into it" is a \
commitment. "That's a fair point" is not.

Return ONLY a JSON array, no prose:

[
  {
    "thread": "<short subject name>",
    "summary": "<2-3 sentences on what this is and how it developed>",
    "item_ids": ["<id>", "<id>"],
    "has_commitment": true or false,
    "commitment": "<what was undertaken, or null>",
    "category": "water quality" | "watershed" | "recreation" | "infrastructure" | "other"
  }
]"""


def load_items() -> tuple[list[dict], list[str]]:
    """Flatten every finding into addressable items with stable ids."""
    items: list[dict] = []
    meeting_dates: list[str] = []

    for path in sorted(TRANSCRIPTS.glob("*.json")):
        try:
            rec = json.loads(path.read_text())
        except Exception:
            continue

        date = rec.get("meeting_date")
        if not date:
            continue
        meeting_dates.append(date)
        url = rec.get("url", "")
        f = rec.get("findings", {})

        def add(kind: str, idx: int, text: str, stamp: str | None, extra: dict):
            if not text:
                return
            items.append({
                "id": f"{date}-{kind}-{idx}",
                "meeting_date": date,
                "kind": kind,
                "timestamp": stamp,
                "text": text,
                "link": f"{url}&t={to_seconds(stamp)}s" if url and stamp else url,
                **extra,
            })

        for i, t in enumerate(f.get("topics", [])):
            add("topic", i, f"{t.get('topic','')}: {t.get('what_happened','')}",
                t.get("timestamp"), {"importance": t.get("importance")})
        for i, c in enumerate(f.get("citizen_concerns", [])):
            add("concern", i, c.get("concern", ""), c.get("timestamp"),
                {"official_response": c.get("response")})
        for i, a in enumerate(f.get("action_items", [])):
            add("commitment", i, a.get("action", ""), a.get("timestamp"),
                {"owner": a.get("owner"), "status": a.get("status")})
        for i, v in enumerate(f.get("votes", [])):
            add("vote", i, f"{v.get('measure','')} — {v.get('outcome','')}",
                v.get("timestamp"), {})

    return items, sorted(set(meeting_dates))


def to_seconds(stamp: str | None) -> int:
    if not stamp:
        return 0
    try:
        parts = [int(p) for p in str(stamp).split(":")]
    except ValueError:
        return 0
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    return 0


def group_threads(items: list[dict]) -> list[dict]:
    import anthropic

    client = anthropic.Anthropic()
    payload = [{"id": i["id"], "meeting_date": i["meeting_date"],
                "kind": i["kind"], "text": i["text"]} for i in items]

    with client.messages.stream(
        model=MODEL,
        max_tokens=8000,
        system=SYSTEM,
        thinking={"type": "adaptive"},
        output_config={"effort": "high"},
        messages=[{"role": "user", "content":
                   "Group these meeting items into threads.\n\n"
                   + json.dumps(payload, indent=1)}],
    ) as stream:
        msg = stream.get_final_message()

    if msg.stop_reason == "refusal":
        raise RuntimeError("Claude declined to group the threads.")

    text = "".join(b.text for b in msg.content if b.type == "text")
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1:
        raise RuntimeError(f"No JSON array returned: {text[:400]}")
    return json.loads(text[start:end + 1])


def enrich(threads: list[dict], items: list[dict], meetings: list[str]) -> list[dict]:
    """Attach the computed facts.

    Everything numeric is derived here from the findings files. The model
    grouped the threads; it does not get to decide how long something has been
    quiet, because that number is the entire point and has to be checkable.
    """
    by_id = {i["id"]: i for i in items}
    today = dt.date.today().isoformat()
    out = []

    for t in threads:
        sources = [by_id[i] for i in t.get("item_ids", []) if i in by_id]
        if not sources:
            continue  # cited nothing real; drop rather than publish it

        dates = sorted({s["meeting_date"] for s in sources})
        last = dates[-1]
        since = [m for m in meetings if m > last]

        try:
            days = (dt.date.fromisoformat(today) - dt.date.fromisoformat(last)).days
        except ValueError:
            days = None

        out.append({
            "thread": t.get("thread", "Untitled"),
            "summary": t.get("summary", ""),
            "category": t.get("category", "other"),
            "has_commitment": bool(t.get("has_commitment")),
            "commitment": t.get("commitment"),
            "first_raised": dates[0],
            "last_mentioned": last,
            "meetings_mentioned": len(dates),
            "meetings_since_last_mention": len(since),
            "days_since_last_mention": days,
            # Said carefully on purpose. We can only speak to what appears in
            # the meetings we have reviewed; the City may have acted elsewhere.
            "status": (
                "active" if not since else
                f"no further mention in the {len(since)} meeting"
                f"{'s' if len(since) != 1 else ''} we have reviewed since"
            ),
            "needs_follow_up": bool(t.get("has_commitment")) and len(since) >= 2,
            "sources": [{
                "meeting_date": s["meeting_date"],
                "kind": s["kind"],
                "timestamp": s["timestamp"],
                "text": s["text"][:400],
                "link": s["link"],
            } for s in sources],
        })

    # Commitments that have gone quiet longest come first — that is the list
    # someone would actually act on.
    out.sort(key=lambda t: (not t["needs_follow_up"],
                            -t["meetings_since_last_mention"]))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="show the items without calling the API")
    args = ap.parse_args()

    items, meetings = load_items()
    if not items:
        print("No findings in data/transcripts/. Run transcribe.py first.",
              file=sys.stderr)
        return 1

    print(f"{len(items)} items across {len(meetings)} meetings "
          f"({meetings[0]} to {meetings[-1]})")

    if args.dry_run:
        for i in items:
            print(f"  {i['id']:28} {i['kind']:11} {i['text'][:88]}")
        return 0

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set.", file=sys.stderr)
        return 1

    threads = enrich(group_threads(items), items, meetings)

    OUT.write_text(json.dumps({
        "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "meetings_reviewed": meetings,
        "meeting_count": len(meetings),
        "item_count": len(items),
        "thread_count": len(threads),
        "needs_follow_up": sum(1 for t in threads if t["needs_follow_up"]),
        "caveat": ("Built from AI review of auto-captioned recordings. 'No further "
                   "mention' describes the meetings we have reviewed, not what the "
                   "City did — action may have been taken outside a council meeting. "
                   "Verify against the linked recording before repeating anything."),
        "threads": threads,
    }, indent=2) + "\n")

    print(f"\nWrote {OUT.relative_to(REPO_ROOT)} — {len(threads)} threads, "
          f"{sum(1 for t in threads if t['needs_follow_up'])} needing follow-up.\n")
    for t in threads[:10]:
        flag = "!" if t["needs_follow_up"] else " "
        print(f" {flag} {t['thread'][:52]:52} last {t['last_mentioned']} "
              f"({t['meetings_since_last_mention']} mtgs since)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
