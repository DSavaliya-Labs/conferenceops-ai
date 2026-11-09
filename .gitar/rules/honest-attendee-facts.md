---
title: "Honest attendee facts"
description: "No invented access claims, no synthetic data on real rooms, no generated words for real speakers (ACCESS-1, SPEAKER-1)"
type: check
when: "PRs that touch recommendations, session pages, access facts, seed data, media, transcripts or the Angular views that show them"
actions: "Fail the check with the exact line and the rule it breaks"
---

# Honest attendee facts

Fail when a change does any of these:

- Shows an accessibility or venue claim that doesn't come from `access_facts` with its source link, or labels any
  session or room "step-free" (no event has route data).
- Treats unknown access as acceptable for a persona with a hard requirement, or drops the "ask staff" statement for
  a persona who needs step-free routes.
- Shows synthetic crowd, occupancy or room-full data on a KubeCon room.
- Generates, stores or shows a transcript, quote, summary of what was said, or live text for a real KubeCon talk or
  speaker.
- Shows generated CloudBridge media or transcripts without the "Simulated excerpt" label, or seeds posts or
  profiles under real people's names.
