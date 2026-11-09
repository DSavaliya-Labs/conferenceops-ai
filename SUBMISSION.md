# ConferenceOps AI: submission (Cloud Native Hackathon, Salt Lake City, November 9, 2026)

**Team:** Dhruv Savaliya (`DSavaliya-gh`), solo.
**Repository:** https://github.com/DSavaliya-Labs/conferenceops-ai (public, Apache-2.0, every change after the first commit through a pull request).
**Live:** http://conferenceops-ai.westus3.cloudapp.azure.com (AKS, plain HTTP). QR code: [`docs/public-url-qr.svg`](docs/public-url-qr.svg).
**Demo video:** _add the link_.
**Built today:** 30+ pull requests merged since 11:00 AM MST, each reviewed by Gitar and deployed to AKS on merge ([Build log, issue #1](https://github.com/DSavaliya-Labs/conferenceops-ai/issues/1)).

## What it is

Attendees find the next talk worth their time, with honest accessibility information, and the platform runs itself through an AI operations loop.

- **Two events on one platform.** KubeCon + CloudNativeCon NA 2026 (real: 600 sessions imported from the official schedule's public feed) and the CloudBridge Summit (fictional, concluded a month earlier: 24 sessions, 8 demo personas, captioned replays and published transcripts; labeled fictional everywhere).
- **Two questions from one Ask bar.** "Which talk matches my interests?" (hybrid search) and "Where should I go next?" (hard rules first, then ranking, with checkable reasons and the official access facts with their source links). Spoken questions through dictation; cited answers from the archive's published transcripts.
- **An AI operations team.** Dash0 watches what attendees experience; when its check fails, Agent0 investigates and writes the fix PR; Gitar reviews and merges it; GitHub Actions deploys it; Radar's AI SRE investigates the cluster and its findings become PRs too.

## How it makes conferences easier

A conference is 600 talks and 21 of them start in the same minute. The app answers the attendee's actual question ("where should I go next?") with only talks they can still reach, in the venue's time zone, ranked by what they said they care about and what they attended before, and it tells the truth about access: the seven facts the Linux Foundation publishes, with links, and the words "step-free routes aren't published; ask staff at registration" instead of a guess.

## One pipeline, every tool

```mermaid
flowchart LR
  A["Attendee: accessible portal (Merge Forward)"] --> B["Elasticsearch: hybrid search, cited answers"]
  B --> C["AKS: Helm, GitHub Actions deploy on every merge; Radar watching"]
  C --> D["Dash0: traces, logs, metrics, RUM, checks"]
  D -->|"check fails → Agent0 investigates"| E["Fix PR (replicas only)"]
  C -->|"Radar AI SRE finding"| E
  E --> F["Gitar: review, auto-apply, approve, merge"]
  F --> C
```

The incident on November 9 at 21:27–21:52 UTC went around this loop once, measured (details under Dash0).

## Track evidence, against the published criteria

### Gitar: "Never Merge Broken"

- *"Gitar is part of how you built, with features landing through PRs it reviewed, not one PR at the end."* 34+ PRs today, every one reviewed; one entry per PR in [issue #1](https://github.com/DSavaliya-Labs/conferenceops-ai/issues/1) with Gitar's verdict, risk and the CI result. Low- and medium-risk PRs auto-merged after Gitar's approval; high-risk ones waited for the builder.
- *"Project-aware setup, with review instructions and repository rules that reflect how your team actually works."* [`AGENTS.md`](AGENTS.md) carries 14 rule IDs (PRIV-1, ACCESS-1, TIME-1, OPS-1, …) that findings cite; [`.gitar/review/`](.gitar/review), [`.gitar/rules/`](.gitar/rules) (API client in sync, honest attendee facts, attendee privacy, incident scope, PR description, tests for behavior changes) and [`.gitar/config/`](.gitar/config) (risk, apply, approve, merge criteria). Incident PRs may change only the discovery replicas or resources: enforced by Gitar's rule and by the `incident-scope` CI check.
- *"Using CI failure diagnosis and auto-apply to fix real problems."* Real findings Gitar fixed with tests: a PRIV-1 span leak ([#2](https://github.com/DSavaliya-Labs/conferenceops-ai/pull/2), `29da8e8`), a CI health check that passed when it shouldn't, an upsert race and a first-insert race ([#3](https://github.com/DSavaliya-Labs/conferenceops-ai/pull/3)), the migration hook order and a floating image tag ([#5](https://github.com/DSavaliya-Labs/conferenceops-ai/pull/5)), nginx failing to start when upstreams don't resolve ([#7](https://github.com/DSavaliya-Labs/conferenceops-ai/pull/7)). One Gitar fix was wrong (Docker's DNS resolver on Kubernetes) and the builder corrected it in `c2d301c`: its commits are reviewed too. A planted defect, [#28](https://github.com/DSavaliya-Labs/conferenceops-ai/pull/28) (a fixed UTC offset), passed CI and was **blocked by Gitar** with the exact consequence; it is held open as evidence and never merged.

### Dash0: "Stop Flying Blind"

- *"Depth of instrumentation…"* OpenTelemetry on every service (traces with the query stripped, PRIV-1; structured logs with trace IDs; Kubernetes cluster metrics and events through the collector; browser monitoring with the Dash0 Web SDK: page views and web vitals from real visitors), deploy markers (`dash0.deployment`, succeeded or failed) on every deploy. [`observability/dash0/`](observability/dash0) holds the dashboard, two check rules and the Agent0 automation as code, applied idempotently by `scripts/dash0-apply.sh`.
- *"Using Agent0 or the MCP server to find and fix something real during the day…"* Two real finds. (1) Running the check rule's own PromQL against live data showed the spans carried the old `http_status_code` label: the rule could never fire; fixed at the source in [#31](https://github.com/DSavaliya-Labs/conferenceops-ai/pull/31) with a regression test. (2) **The incident.** A measured capacity test (one pod handles 64 requests/s, p95 411 ms; at 96/s it was restarted by its liveness probe) set the admission limit (24 in flight, [#36](https://github.com/DSavaliya-Labs/conferenceops-ai/pull/36)). At 80 requests/s the pod answered fast `503 DISCOVERY_CAPACITY_EXHAUSTED` and stayed healthy; the check **failed at 21:29:06 (26.6%)**; Agent0 investigated (every 5xx an admission rejection from the only pod at 24/24 slots, Elasticsearch ~14 ms with 0 errors, OpenAI 200s, replicas 1/1) and wrote the fix: replicas 1 → 3, sized from the evidence. [#44](https://github.com/DSavaliya-Labs/conferenceops-ai/pull/44) was approved by Gitar 2½ minutes later, merged, deployed; **21:50–21:51: 4,802 requests/min, 0 5xx, p95 0.33 s**, three pods serving; the check resolved at 21:50:06. Honest note: the automation's `failed_check.new` trigger didn't fire (Dash0 reused the issue id from the calibration failure), so the investigation was started by hand with the automation's own prompt and the PR pushed unchanged from Agent0's thread.
- *"Observability that's part of the product experience…"* The check is "attendees' searches are failing", the dashboard is "attendee impact", and the incident PR's acceptance is written as what attendees see (0 5xx for 60 s, p95 under 1.5 s).

### Radar (Skyhook): "AI SRE"

- *"Your project runs on Kubernetes with Radar connected to it."* AKS (Azure CNI overlay, Entra RBAC), Helm chart with the APIs, worker, PostgreSQL, Elasticsearch (ECK) and the collector; Radar 1.20.1 connected read-only (`-mcp-read-only -disable-exec -disable-helm-write`) with its own timeline.
- *"Using Radar's AI SRE to find and fix something real during the day…"* Radar's investigation of the worker (run `run-daceea5bca`, Claude Code as the agent, safeguarded profile) found it had no liveness probe: a hung worker would look healthy. The bridge ([`scripts/radar-investigate.sh`](scripts/radar-investigate.sh), the [`radar-fix`](.claude/skills/radar-fix/SKILL.md) skill) turned the verdict into [#35](https://github.com/DSavaliya-Labs/conferenceops-ai/pull/35): a heartbeat per loop and an exec probe; merged, deployed; `--verify` ran Radar again: **healthy, confidence 0.9**, posted on the PR. Radar never patched the cluster: the AI that reads the cluster can't change it, and the AI that writes the fix can't touch the cluster.

### Elasticsearch: "Find the Right Talk"

- *"Elasticsearch is the core data layer…"* PostgreSQL is canonical; Elasticsearch holds the searchable projections through an outbox with external versioning (a late event never resurrects hidden content). Two indexes: sessions (624 documents, 1,536-dimension embeddings) and published transcript chunks. ECK on the cluster.
- *"Smart use of search…"* Hybrid BM25 + kNN with the same filters on both channels, fused with reciprocal rank fusion in Python; lexical fallback when the model is slow (fail open). Golden query: "something about eBPF and security" returns "Kernel Eyes", whose title never says eBPF. The transcript boundary: only published transcripts are searchable or citable, enforced in the query and re-validated after retrieval; cited answers pick only from retrieved passages.

### Merge Forward: "Conferences for Everyone"

Honest access facts only (the seven the Linux Foundation publishes, each with its source link; nothing is ever labeled step-free; a hard requirement is never softened to fill a list); captioned replays with captions on by default, a pause control and a transcript panel; dictation for spoken questions; a keyboard-first portal with visible focus, labels, restrained live regions, reduced-motion support and reflow to 320 px; two quiet rooms and the prayer room among the facts.

## Known limitations

No social features (posts, follows); three replay clips rather than full talks; dictation needs an HTTPS origin to demo in a browser; the incident ran once, with the trigger caveat above; the secondary CPU incident from the plan isn't built. Full list in the [README](README.md#known-limitations).

## Disclosure

AI assistants used, as the rules allow: Claude Code (building, under the builder's direction), Gitar (review and fixes), Agent0 (the investigation), Radar's investigator. Prepared before the event: the plan, notes, accounts and tooling; no code or configuration written before the start was copied in. OpenAI models power embeddings, dictation and the cited answers. Details in the [README](README.md).
