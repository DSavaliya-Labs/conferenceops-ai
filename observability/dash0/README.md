# Dash0 as code

The Dash0 side of the operations loop: what attendees feel, when that becomes an incident, and the Agent0
automation that turns the incident into a pull request. Everything here goes to the Dash0 dataset `conferenceops`
through `scripts/dash0-apply.sh`.

## What's here

| File | What it is |
|---|---|
| `dataset.yaml` | The `conferenceops` dataset's processing setting: semantic convention upgrades to v1.36.0, as in the `default` dataset (see [Label names](#label-names)). Other dataset settings are left alone |
| `dashboards/attendee-impact.yaml` | Dashboard **ConferenceOps: attendee impact**. Discovery: requests by status, 5xx and 503 share (10% line), p50 and p95 of POST /api/v1/search (1.5 s line), traffic and 503s per pod, Elasticsearch call latency and failures. Core API: requests by status and 5xx share. Browser (service `web`): page views and errors per minute, LCP, INP and CLS at p75. Kubernetes: desired and available replicas per Deployment, container restarts |
| `checks/discovery-rejecting-searches.yaml` | Check rule **Discovery is rejecting searches**, critical: more than 10% of discovery-api's server requests return a 5xx over one minute, with at least 100 requests; two failing evaluations in a row. Label `service: discovery-api` |
| `checks/search-is-slow.yaml` | Check rule **Search is slow**, degraded: the p95 of POST /api/v1/search on discovery-api stays above 1.5 s for 2 minutes |
| `automations/discovery-capacity-fix.json` | Agent0 automation **ConferenceOps: fix discovery capacity**: trigger, model, guardrails and sandbox |
| `automations/discovery-capacity-fix.prompt.md` | Its prompt. The apply script inserts it into the definition |

The check rules count Dash0's span metrics (`dash0.spans`, `dash0.spans.duration`), so they work before the
services define metrics of their own.

## The loop

```mermaid
flowchart LR
  L["Load at the estimated peak"] --> D["discovery-api answers 503"]
  D --> C["Check fails: Discovery is rejecting searches"]
  C --> A["Agent0 automation investigates"]
  A -->|"admission saturation"| P["PR: discovery replicas or resources only<br/>labels incident, gitar-managed"]
  A -->|"another cause or unclear"| H["Report for a human, no change"]
  P --> G["Gitar review and incident-scope CI (OPS-1)"]
  G --> M["Merge and deploy to AKS"]
  M --> R["More discovery replicas; the check recovers"]
```

## The automation's limits

| Setting | Value |
|---|---|
| Trigger | A new failure of **Discovery is rejecting searches** only (`failed_check.new` with that rule's id) |
| Prompt | Investigate with telemetry; tell admission saturation (503 with code `DISCOVERY_CAPACITY_EXHAUSTED`) from upstream failures (Elasticsearch, OpenAI) and scheduling or resource failures. Only for saturation, open one PR in `DSavaliya-Labs/conferenceops-ai` that changes only `components.discovery.replicas` or `components.discovery.resources` in `deploy/env/aks/values.yaml`, replicas within 1..5, labeled `incident` and `gitar-managed`, with evidence links and recovery checks. Never tests, CI, review policy, the load test, thresholds or credentials. Otherwise, report the mismatch for a human |
| Duplicates | Concurrency policy `skip` with one run at a time; the prompt adds evidence to an open `incident` PR instead of opening a second one |
| Model | `class:advanced`, high reasoning effort |
| Budget and time | 50 credits per rolling day (warning at 75%), 15-minute timeout |
| Network | Trusted hosts only, which includes github.com for `git` and `gh` |
| Tools | Dash0's default tool policy: query tools are allowed, write tools only for a matching Slack, GitHub or Linear trigger, and destructive tools need an opt-in. `git` and `gh` run in the sandbox |

The repository checks the same scope twice. The `incident-scope` CI job rejects an incident PR that changes any other
file, and checks the first key on each changed line. Gitar's incident rule reviews the values themselves.

## Apply

```bash
. ~/.config/conferenceops/env.sh       # DASH0_API_URL and DASH0_AUTH_TOKEN; the script never prints them
scripts/dash0-apply.sh --dry-run       # validate every document, change nothing
scripts/dash0-apply.sh                 # apply, then read each asset back and print its name and id
```

Re-running updates in place:

- the dashboard and check rules carry fixed ids (`metadata.dash0Extensions.id`, `id`), which `dash0 apply` uses as
  upsert keys;
- `dash0 apply` has no automation kind, so the script upserts the automation with
  `PUT /api/agentic-workflows/{origin}` (through `dash0 api`, the same credentials), keyed by its
  `dash0.com/origin` label `dash0-cli_conferenceops-discovery-capacity-fix`;
- the dataset is keyed by its slug.

Change these files, not the Dash0 UI: the next apply overwrites edits made there. The script never deletes. To
remove an asset, delete it in Dash0 too (`dash0 dashboards delete <id>`, `dash0 check-rules delete <id>`,
`dash0 -X api DELETE /api/agentic-workflows/<origin>`).

## Manual steps

The automations API has no repository setting: Agent0 reaches GitHub through the organization's GitHub integration,
and the prompt names the repository. Check once that the integration covers this repository:

1. Open Dash0 **Settings → Integrations** (<https://app.dash0.com/settings/integrations>) and select **GitHub**.
2. Check that it is connected for `DSavaliya-Labs`. Select **Manage Repositories on GitHub** and check that the app
   has access to **All repositories** or to `conferenceops-ai`; if not, add the repository, save, and return to
   Dash0.
3. If GitHub isn't connected at all: **+ Add → GitHub → Connect to GitHub**, install the Dash0 GitHub App on
   `DSavaliya-Labs` with access to `conferenceops-ai`, review its permissions (Contents and Pull requests, read and
   write), and select **Install**.

Outside Dash0, Gitar must review the bot's pull requests: its **Allowed bots** list must contain `dash0-app[bot]`.

A run's result, including the report for a human when it opens no PR, is in Dash0 under **Agent0 → Automations →
ConferenceOps: fix discovery capacity**. If the automation doesn't fire during the incident, open the failed check
in Agent0 Chat and ask it to raise the PR (TASKS.md, fallbacks).

## Label names

The queries use label names checked on real data before they were written:

- **Spans** (`dash0.spans`, `dash0.spans.duration`): `service_name`, `service_namespace`, `otel_span_kind`,
  `http_route`, `http_request_method`, `http_response_status_code`, `otel_span_status_code`, `k8s_pod_name` and
  `db_system_name`, on the `default` dataset's data from an earlier prototype. That prototype ran the same stack:
  Python, OpenTelemetry SDK 1.45.2 and the FastAPI instrumentation.
- **Kubernetes** (`k8s.deployment.desired`, `k8s.deployment.available`, `k8s.container.restarts`):
  `k8s_namespace_name`, `k8s_deployment_name`, `k8s_pod_name` and `k8s_container_name`, on the `dash0-demo`
  dataset. They come from the collector's Kubernetes cluster receiver, which our chart enables with its
  `clusterMetrics` preset.
- **Browser** (`dash0.web.page_views`, `dash0.web.errors`, `dash0.web.vitals.*`): `service_name` and
  `service_namespace`, on the prototype's browser data. Our Web SDK reports service `web` in namespace
  `conferenceops`.

The FastAPI instrumentation emits the old HTTP attribute names unless `OTEL_SEMCONV_STABILITY_OPT_IN=http` is set.
The `default` dataset upgrades them to the stable names at ingestion. The `conferenceops` dataset had upgrades
switched off, so the status-code, method and Elasticsearch matchers would have matched nothing there.
`dataset.yaml` switches them on.

## Not covered

- The deploy workflow doesn't run the apply yet; that wiring comes after the deploy pull request (#5) merges.
- No notification channels: checks show in Dash0 and start the automation, and nobody is paged.
- The services don't trace their OpenAI calls yet, so an OpenAI failure shows only as discovery 5xx and logs.
- The 503 code `DISCOVERY_CAPACITY_EXHAUSTED` and a rejection metric arrive with admission control (task C1).
- No CPU or memory usage panels: the collector has no kubelet stats receiver.
