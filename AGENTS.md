# AGENTS.md: rules for every agent and human in this repository

`CLAUDE.md` is a symlink to this file, and Gitar reads it too. Keep it the single source of truth: when a
convention changes, change it here in the same pull request.

## What we are building

**ConferenceOps AI** helps attendees find the next talk worth their time, with honest accessibility information,
and shows a platform run by an AI operations team.

- **Two events on one platform.**
  - **KubeCon + CloudNativeCon NA 2026** is real and current, imported from its public Sessionize feed.
  - **CloudBridge Summit** is fictional, concluded about a month earlier, and labeled as fictional in the UI. It
    holds the archive: captioned replays, published transcripts and history for the demo personas.
- **Two questions from one Ask bar.**
  - A typed or spoken query answers "which talk matches my interests?" (hybrid search).
  - The "Where should I go next?" action answers "which talk can I actually go to?" (hard rules first, then
    ranking).
- **The operations loop.** Dash0 sees users hurting and Agent0 opens a fix pull request. Gitar reviews and merges it.
  GitHub Actions deploys it to AKS. Radar's AI SRE investigates the cluster.

## Stack

| Layer | Choice |
|---|---|
| Web | Angular 22 single-page app (`apps/web`), keyboard-first, Dash0 Web SDK |
| API and jobs | Python 3.12, FastAPI, one package with `core-api`, `discovery-api` and `worker` entry points (`backend/conferenceops`) |
| Data | PostgreSQL 17 is canonical; Elasticsearch 9 holds the searchable projections, via an outbox |
| AI | OpenAI only: Responses API with structured outputs for reasoning, `text-embedding-3-small` (1,536 dimensions), transcription for dictation. Anthropic is a backup only |
| Telemetry | OpenTelemetry SDKs → collector → Dash0 (dataset `conferenceops`); browser monitoring via the Dash0 Web SDK |
| Runtime | AKS (public URL) deployed by GitHub Actions with Helm; Docker Desktop Kubernetes for development |

## Layout

| Path | What lives there |
|---|---|
| `apps/web/` | Angular app |
| `backend/conferenceops/` | `core_app`, `discovery_app`, `worker`, shared domain, services and repositories |
| `backend/migrations/` | Alembic migrations |
| `backend/tests/` | Unit, integration (real PostgreSQL and Elasticsearch), contract and permission tests |
| `contracts/` | Committed OpenAPI schema and the generated Angular client |
| `seed/` | The CloudBridge Summit fixture and its media and caption files |
| `deploy/chart/` | Helm chart |
| `deploy/env/aks/values.yaml` | The values deployed to AKS. The only file an incident pull request may change (OPS-1) |
| `ops/load/` | k6 load tests |
| `scripts/` | Bash scripts (`set -euo pipefail`, shellcheck-clean) |
| `.github/workflows/` | CI (`ci.yml`), deploy, Build log |
| `.gitar/` | Gitar review instructions, rules and the risk, apply, approve and merge criteria |

When you add a module, a route or a top-level directory, add it here in the same pull request.

## Rules

Review findings cite these IDs.

- **ACCESS-1, honest access.** Show only access facts stored in `access_facts`, each with its source link. No event
  has route data, so nothing is ever labeled step-free. Unknown stays unknown, and a hard requirement is never
  softened to fill a list.
- **SPEAKER-1, real people.** Never generate transcripts, quotes or live text for real KubeCon speakers. Generated
  media and transcripts exist only for the fictional CloudBridge Summit, and are labeled "Simulated excerpt".
- **TRANSCRIPT-1, the publication boundary.** Only concluded, processed and published transcripts reach search and
  Ask. Search filters and post-retrieval validation both enforce the publication manifest.
- **AI-1, grounded answers.** The model picks only from items retrieval returned. Validate every ID and fact against
  server data, fall back to a deterministic answer, and fail open when the model is slow or down.
- **PRIV-1, privacy.** What attendees type or say, their accessibility preferences, transcript text and prompts
  never reach telemetry, logs, metric labels or public search documents. Log lengths, not content.
- **AUTHZ-1, permissions.** The server resolves role and ownership from `X-Demo-Profile-ID`; never trust a role
  sent by the client. Return 403 for a forbidden action and 404 for a resource in another conference.
- **DATA-1, outbox.** A canonical write and its outbox job commit in one transaction. Revisions are monotonic, and a
  late or older event never resurrects hidden content.
- **TIME-1, time.** Store UTC instants and display them in the event's IANA time zone (`America/Denver`). Never use
  a fixed UTC offset.
- **API-1, contract.** The committed OpenAPI schema and the generated Angular client change in the same pull request
  as the API.
- **A11Y-1, accessibility.** Works with the keyboard alone: visible focus, labels, restrained live regions, pause
  controls, captions, reduced motion, contrast and reflow.
- **SEC-1, secrets.** No secrets in the repository, the browser, logs or commit messages. The browser gets only the
  ingest-only Dash0 token.
- **K8S-1, workloads.** Run as non-root, with no privilege escalation, dropped capabilities and a read-only root
  filesystem. Set requests and a memory limit. Probes don't depend on downstream services. Pin versions, and pass
  `--reset-values` to `helm upgrade`.
- **OPS-1, incident scope.** An incident pull request changes only the discovery workload's replicas or resources
  in `deploy/env/aks/values.yaml`. It never changes review policy, CI gates, the load rate or acceptance thresholds.
- **TEST-1, tests.** A behavior change comes with tests that prove it. A bug fix comes with a regression test that
  fails without the fix, unless the PR says why one isn't practical and how the fix was verified.

## Pull requests

- **One PR, one outcome,** with its implementation, tests and docs. Never split by file or by frontend and backend,
  and never leave a broken intermediate state.
- **Branch from `main`** unless the order truly matters. Then stack with `gh stack` (`--base main`), at most three
  layers, merged from the bottom up.
- **Every PR description says** why, what, how it was verified, and what it doesn't cover. A stacked PR also names
  the PR it sits on and the merge order.
- **Open PRs with the `gitar-managed` label,** so Gitar can push fixes. Answer each finding in its thread, and check
  Gitar's commits: its fixes can be wrong.
- **Merging:**
  - Gitar approves clean reviews and auto-merges low- and medium-risk PRs whose base is `main`.
  - High-risk PRs wait for the builder.
  - Merge commits only; the branch is deleted after merge.
  - After a stacked PR merges, check the next PR's base before deleting any branch.

## Never

- Commit or print secrets.
- Use any Azure subscription other than the hackathon sandbox. Always pass `--subscription`.
- Edit a script while it is running. Scripts that delete things end with `main "$@"; exit`.
- Force-push a shared branch, or merge without Gitar's approval and green checks.
- Claim a recovery, a finding or an accessibility fact without evidence.
