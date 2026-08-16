#!/usr/bin/env python3
"""Regenerate data/lake-watch/index.json, the dataset's front door.

Lake Watch is meant to outlive the site it currently sits on. A consumer that
hard-codes filenames breaks the first time one is renamed; a consumer that
reads one manifest keeps working. So everything published carries a schema
name and version, and this file lists what exists and where.

The manifest is derived, never hand-edited. Run it after anything writes to
data/lake-watch/.

Usage:
    python3 scripts/lakewatch/manifest.py
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
PUBLISHED = REPO_ROOT / "data" / "lake-watch"
INDEX = PUBLISHED / "index.json"

# Bump a version when a field changes meaning or disappears. Adding an optional
# field does not need a bump; consumers that ignore it keep working.
DATASETS = [
    ("water-quality", "water-quality.json", 1,
     "Every microcystin reading the City of Mattoon has published for treated "
     "drinking water, with the EPA advisory thresholds."),
    ("sources", "sources.json", 1,
     "Government and institutional sources this project monitors, each recording "
     "how it can actually be retrieved, plus those that are known to be blocked."),
    ("brief", "brief.json", 1,
     "The most recent weekly brief: the article text plus structured upcoming "
     "meetings."),
    ("commitments", "commitments.json", 1,
     "Lake subjects tracked across recorded meetings, with what was undertaken "
     "and how long since each was last mentioned."),
]

MEETINGS_SCHEMA = 1


def describe(path: pathlib.Path) -> dict:
    try:
        doc = json.loads(path.read_text())
    except Exception:
        return {}
    out = {}
    for key in ("generated", "date", "meeting_date"):
        if isinstance(doc.get(key), str):
            out["updated"] = doc[key]
            break
    return out


def main() -> int:
    if not PUBLISHED.exists():
        print(f"{PUBLISHED} does not exist.", file=sys.stderr)
        return 1

    datasets = []
    for name, filename, version, description in DATASETS:
        path = PUBLISHED / filename
        entry = {
            "id": name,
            "path": filename,
            "schema_version": version,
            "description": description,
            "available": path.exists(),
        }
        if path.exists():
            entry["bytes"] = path.stat().st_size
            entry.update(describe(path))
        datasets.append(entry)

    meetings = sorted((PUBLISHED / "meetings").glob("*.json")) \
        if (PUBLISHED / "meetings").exists() else []
    datasets.append({
        "id": "meetings",
        "path": "meetings/",
        "schema_version": MEETINGS_SCHEMA,
        "description": ("Per-meeting findings from recorded City Council sessions: "
                        "lake topics, resident concerns, commitments, and votes, each "
                        "timestamped to the recording. One file per meeting, named "
                        "<meeting-date>_<youtube-video-id>.json."),
        "available": bool(meetings),
        "count": len(meetings),
        "files": [p.name for p in meetings],
    })

    INDEX.write_text(json.dumps({
        "schema": {"name": "lake-watch-index", "version": 1},
        "project": "Lake Watch",
        "description": (
            "A public record of how Lake Mattoon and its watershed are governed: "
            "drinking water readings, the meetings where decisions are made, and "
            "what officials undertook to do. Assembled from government sources and "
            "recorded public meetings."
        ),
        "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "provenance": (
            "Readings and documents come from government sources directly. Meeting "
            "findings come from AI review of auto-captioned recordings and are "
            "reviewed by a person before publication. Anything derived from captions "
            "should be verified against the linked recording before being quoted."
        ),
        "datasets": datasets,
    }, indent=2) + "\n")

    have = sum(1 for d in datasets if d.get("available"))
    print(f"Wrote {INDEX.relative_to(REPO_ROOT)} — {have}/{len(datasets)} datasets present"
          + (f", {len(meetings)} meetings" if meetings else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
