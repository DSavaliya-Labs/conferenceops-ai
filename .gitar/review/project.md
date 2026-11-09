# What to look for in this repository

Read `AGENTS.md` first: its rules (ACCESS-1 to TEST-1) are the review standard. Points that are easy to miss here:

- **Attendee-facing honesty** comes before everything else.
  - "Where should I go next?" may only show access statements that exist in `access_facts`, with their link. When a
    persona needs step-free routes, it must say routes aren't published and point to staff.
  - No synthetic crowd or occupancy data is ever shown on a real KubeCon room.
- **Real and fictional data stay apart.** KubeCon data comes from the public Sessionize feed. Seeded posts,
  profiles and conversations use fictional personas only. Anything generated (media, transcripts) belongs to
  CloudBridge Summit and carries the "Simulated excerpt" label.
- **The model never decides facts.** Check that every ID or fact the model returns is validated against server data
  before a user sees it, and that a slow or failed model call falls back without an error page.
- **Privacy in telemetry:** span attributes, log fields and metric labels must not hold query text, transcript
  text, prompts or accessibility preferences. Metric labels must be bounded: no free text, titles or user IDs.
- **Search:** both retrieval channels (BM25 and kNN) apply the same conference, visibility and publication filters,
  and results are revalidated against PostgreSQL before they reach the browser or the model.
- **Kubernetes and delivery:** readiness and liveness probes must not call Elasticsearch, PostgreSQL or OpenAI. Image
  tags are commit SHAs, never `latest`.
- **Incident PRs** (label `incident`, or authored by `dash0-app[bot]`) are judged against OPS-1. Check that the
  change matches the evidence in the description.
