# Meeting transcripts added by hand

The City streams Council meetings to YouTube, and YouTube auto-generates
captions for them. Those captions are the richest source we have — they carry
what was actually *said*, including public comment from residents, which never
appears in the minutes in any detail.

We do not fetch them automatically. YouTube's robots.txt disallows `/api/` and
`/youtubei/`, which is where every automated caption route lives, and the
official YouTube Data API only allows caption downloads to someone with edit
permission on the video. Both doors are closed on purpose, so we use neither.

Using the "Show transcript" button in the player yourself is ordinary use of
the site. So that step is manual, and everything after it is automatic.

## How to add a transcript

1. Open the meeting, e.g. https://www.youtube.com/watch?v=PV7CejNeCpo
2. Under the video, click **…more** → **Show transcript**.
3. Select the transcript panel and copy it.
4. Save it here as `YYYY-MM-DD_VIDEOID.txt`:

       2026-08-04_PV7CejNeCpo.txt

   The date is the meeting date; the video id is the part after `v=` in the
   URL. Both matter — the id is what lets every quote link to the exact second
   it was said.

5. Commit it. The next brief scans for lake and watershed discussion, keeps the
   surrounding sentences so a mention is not quoted out of context, and links
   each passage to that timestamp in the recording.

Either copy format works: `0:00 text` on one line, or a bare `0:00` line with
its text underneath. A file with no timestamps is reported as unreadable rather
than silently skipped.

## A caveat worth repeating in anything published

These are **auto-generated** captions. They mishear names, numbers, and
technical terms routinely — "microcystin" and "Mattoon" both come out wrong
often. Treat a transcript as a pointer to the moment in the recording, not as a
quotation of record. Verify anything specific by watching the linked timestamp
before repeating it publicly.
