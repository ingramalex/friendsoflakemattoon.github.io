#!/usr/bin/env python3
"""Count how often the lake comes up at Council, and why it came up.

The question this answers: is the lake on the City's agenda, or does it only
come up when someone raises it? Each lake topic in the meeting findings gets
one of three origins:

  agenda            a formal item the Council took up: a decision request,
                    resolution, ordinance, bid award, or scheduled report
  public_comment    raised by a member of the public
  council_or_staff  raised by a commissioner, the mayor or staff outside any
                    agenda item, e.g. in closing comments

Division of work, as in commitments.py:

  Claude    labels each topic's origin. Telling "the council considered
            Resolution 2026-3352" from "a resident asked about septic systems"
            is a judgment.
  This code does all the counting. The model never produces a number.

Meetings on the City's channel that have not been reviewed yet are listed as
pending, never counted as zero. "We have not checked" and "the lake did not
come up" are different claims, and the chart has to keep them apart.

Labels are cached in the output, so a rerun only asks about new meetings.

Usage:
    ANTHROPIC_API_KEY=... python3 scripts/lakewatch/attention.py
    python3 scripts/lakewatch/attention.py --dry-run
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
MEETINGS = REPO_ROOT / "data" / "lake-watch" / "meetings"
OUT = REPO_ROOT / "data" / "lake-watch" / "attention.json"

MODEL = "claude-opus-5"
ORIGINS = ("agenda", "public_comment", "council_or_staff")

SYSTEM = """You label records of Mattoon City Council meetings for Friends of \
Lake Mattoon. Each record is one lake-related topic from a meeting: a short \
label, what happened, and who spoke.

Give each topic exactly one origin:

- "agenda": the Council took it up as a formal item. Council decision \
requests, resolutions, ordinances, bid awards, contracts, purchases, and \
scheduled staff reports or presentations all count. When a topic was on the \
agenda AND residents spoke about it, it is still "agenda".
- "public_comment": a member of the public brought it up, usually in public \
comment, and it was not an agenda item that night.
- "council_or_staff": a commissioner, the mayor, the city manager or other \
staff brought it up outside any agenda item, for example in closing comments \
or an unprompted update.

Base the label only on the text given. When the text does not make the origin \
clear, choose "agenda" if it mentions a numbered measure, vote, bid or \
contract, and otherwise the origin the text best supports.

Return ONLY a JSON array of objects {"id": "<id>", "origin": "<origin>"}, one \
per topic, in any order."""


def meeting_files() -> list[dict]:
    out = []
    for path in sorted(MEETINGS.glob("*.json")):
        try:
            rec = json.loads(path.read_text())
        except Exception:
            continue
        if rec.get("meeting_date"):
            rec["_file"] = path.name
            out.append(rec)
    return out


def topic_items(recs: list[dict]) -> list[dict]:
    items = []
    for rec in recs:
        for i, t in enumerate((rec.get("findings") or {}).get("topics") or []):
            items.append({
                "id": f"{rec['_file']}#{i}",
                "meeting_date": rec["meeting_date"],
                "topic": t.get("topic", ""),
                "what_happened": t.get("what_happened", ""),
                "speakers": t.get("speakers", ""),
            })
    return items


def label(items: list[dict]) -> dict[str, str]:
    import anthropic

    client = anthropic.Anthropic()
    with client.messages.stream(
        model=MODEL,
        max_tokens=8000,
        system=SYSTEM,
        thinking={"type": "adaptive"},
        output_config={"effort": "medium"},
        messages=[{"role": "user", "content":
                   "Label these topics.\n\n" + json.dumps(items, indent=1)}],
    ) as stream:
        msg = stream.get_final_message()

    if msg.stop_reason == "refusal":
        raise RuntimeError("Claude declined to label the topics.")
    text = "".join(b.text for b in msg.content if b.type == "text")
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1:
        raise RuntimeError(f"No JSON array returned: {text[:400]}")
    wanted = {i["id"] for i in items}
    out = {}
    for row in json.loads(text[start:end + 1]):
        if row.get("id") in wanted and row.get("origin") in ORIGINS:
            out[row["id"]] = row["origin"]
    return out


def channel_meetings() -> list[dict]:
    """The City's recordings, so unreviewed meetings show as pending."""
    try:
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
        import brief  # noqa: E402
        return brief.channel_index()
    except Exception:
        return []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="list topics without calling the API")
    args = ap.parse_args()

    recs = meeting_files()
    items = topic_items(recs)
    cached = {}
    if OUT.exists():
        try:
            cached = json.loads(OUT.read_text()).get("labels", {})
        except Exception:
            pass
    todo = [i for i in items if i["id"] not in cached]
    print(f"{len(recs)} meetings, {len(items)} lake topics, {len(todo)} needing a label")

    if args.dry_run:
        for i in todo:
            print(f"  {i['id']:40} {i['topic'][:70]}")
        return 0

    labels = {k: v for k, v in cached.items() if k in {i["id"] for i in items}}
    if todo:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            print("ANTHROPIC_API_KEY is not set.", file=sys.stderr)
            return 1
        labels.update(label(todo))
    missing = [i["id"] for i in items if i["id"] not in labels]
    if missing:
        print(f"{len(missing)} topics came back unlabelled; they are counted as unlabelled.")

    reviewed = {r["meeting_date"] for r in recs}
    meetings = []
    for rec in recs:
        n = len((rec.get("findings") or {}).get("topics") or [])
        counts = {o: 0 for o in ORIGINS}
        counts["unlabelled"] = 0
        for i in range(n):
            counts[labels.get(f"{rec['_file']}#{i}", "unlabelled")] += 1
        meetings.append({
            "date": rec["meeting_date"],
            "video_id": rec.get("video_id"),
            "lake_discussed": bool((rec.get("findings") or {}).get("lake_discussed")),
            "topics": n,
            **counts,
            "resident_concerns": len((rec.get("findings") or {}).get("citizen_concerns") or []),
            "votes": len((rec.get("findings") or {}).get("votes") or []),
        })
    pending = sorted({m["date"] for m in channel_meetings() if m.get("date") and m["date"] not in reviewed})

    totals = {k: sum(m[k] for m in meetings)
              for k in ("topics", *ORIGINS, "unlabelled", "resident_concerns", "votes")}
    totals["meetings_reviewed"] = len(meetings)
    totals["meetings_with_lake"] = sum(1 for m in meetings if m["topics"])

    doc = {
        "schema": {"name": "attention", "version": 1},
        "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "caveat": ("Counts of lake topics in AI reviews of recorded Council meetings. "
                   "Each topic's origin is an AI judgment from its description; the "
                   "counts themselves are computed directly from the findings."),
        "totals": totals,
        "meetings": meetings,
        "pending": pending,
        "labels": labels,
    }
    # Only rewrite when something other than the timestamp changed, so a
    # quiet scheduled run leaves nothing to commit.
    try:
        prev = json.loads(OUT.read_text())
        prev.pop("generated", None)
    except Exception:
        prev = None
    if prev == {k: v for k, v in doc.items() if k != "generated"}:
        print("Counts unchanged.")
        return 0
    OUT.write_text(json.dumps(doc, indent=2) + "\n")

    print(f"Wrote {OUT.relative_to(REPO_ROOT)}")
    print(f"  {totals['meetings_with_lake']}/{totals['meetings_reviewed']} meetings mentioned the lake; "
          f"{totals['topics']} topics: {totals['agenda']} agenda, {totals['public_comment']} public comment, "
          f"{totals['council_or_staff']} council/staff, {totals['unlabelled']} unlabelled")
    if pending:
        print(f"  not yet reviewed: {', '.join(pending)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
