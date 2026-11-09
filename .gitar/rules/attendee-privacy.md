---
title: "Attendee privacy in telemetry"
description: "What attendees type or say, and their accessibility preferences, never reach telemetry or logs (PRIV-1)"
type: check
when: "PRs that add or change logging, spans, span attributes, metrics, the Dash0 Web SDK setup, or code that handles queries, dictation, prompts or preferences"
actions: "Fail the check and point to the field that carries private content"
---

# Attendee privacy in telemetry

Fail when any of these can reach a log record, span attribute, span event, metric label or browser telemetry event:

- search or Ask text, dictation audio or its transcription;
- prompts sent to, or completions returned by, a model;
- accessibility preferences or any private profile field;
- transcript text.

Lengths, counts, durations, bounded enums and IDs are fine. Also fail when a metric label can take free text, titles
or user IDs, because those are unbounded. Query text must travel in request bodies, never in URLs.
