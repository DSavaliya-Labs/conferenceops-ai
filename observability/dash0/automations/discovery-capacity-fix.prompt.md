The check "{{check.name}}" ({{check.id}}) failed for {{label.service}} at {{now}}. It fails when more than 10% of
discovery-api's server requests return a 5xx within one minute, with at least 100 requests. Attendees' searches
(POST /api/v1/search) and "where should I go next?" answers are failing.

Context: ConferenceOps AI runs on AKS in namespace `conferenceops`, and its telemetry is in Dash0 dataset
`{{dash0.dataset}}` with service.namespace `conferenceops` and services `web`, `core-api`, `discovery-api` and
`worker`. discovery-api runs as the Deployment `conferenceops-discovery`. The collector also sends Kubernetes cluster
metrics (`k8s.deployment.desired`, `k8s.deployment.available`, `k8s.pod.phase`, `k8s.container.restarts`) and
Kubernetes events (as logs). The Dash0 dashboard "ConferenceOps: attendee impact" shows the main signals.

1. Investigate with telemetry before you change anything, over the 30 minutes up to the failure: discovery-api's
   request rate, its 5xx share by status code and by pod, the p95 of POST /api/v1/search, its Elasticsearch calls,
   a few failing traces with their logs, and the replicas and pods of `conferenceops-discovery`. Know what the
   telemetry can and can't show:
   - Admission control is a per-pod limit on discovery-api requests in progress (`DISCOVERY_MAX_INFLIGHT`, the chart
     value `components.discovery.maxInflight`). A request that finds every slot taken waits at most a moment, then
     gets a 503 with the code `DISCOVERY_CAPACITY_EXHAUSTED` in its body, before any retrieval work. Response bodies
     are not in telemetry, so discovery-api records each rejection three ways:
     - its server span has HTTP status 503, span status ERROR and the attribute `conferenceops.error.code` =
       `DISCOVERY_CAPACITY_EXHAUSTED` (the label `conferenceops_error_code` on `dash0.spans`);
     - the counter `conferenceops.admission.rejections` counts it with `reason` = `capacity_exhausted`, next to the
       gauges `conferenceops.admission.in_flight` and `conferenceops.admission.limit`;
     - one warning log line per rejection, "Discovery is at capacity: answered 503 DISCOVERY_CAPACITY_EXHAUSTED, N of
       N slots in use, ...", with the trace ID.
     A 503 without that attribute is not an admission rejection. The status code is the label
     `http_status_code` or `http_response_status_code` on `dash0.spans`, depending on the dataset's semantic
     convention upgrades: check both.
   - A rejected request's server span is short and has no Elasticsearch child span, and no failed search is logged
     for its trace, while the same pod keeps answering other searches.
   - The limit counts requests in progress, so slower requests fill it too. If Elasticsearch calls got slower or
     started failing before the rejections began, the cause is upstream: more replicas would only send it more load.
   - Elasticsearch calls are traced: client spans with `db.system.name` `elasticsearch`. OpenAI and PostgreSQL
     calls may have no spans. Judge them from discovery-api's logs, which carry trace IDs, and treat missing OpenAI
     or PostgreSQL spans as no evidence either way. discovery-api is built to fail open when OpenAI is slow or down
     (AI-1 in `AGENTS.md`), so an OpenAI problem alone should show as warnings in its logs, not as 5xx.
2. Decide which cause the evidence supports:
   - Admission saturation: the 5xx are 503 admission rejections, shown by `conferenceops.error.code` or the
     rejection counter, on every available pod, while Elasticsearch latency and errors stay at their level from
     before the overload and discovery-api's logs show no failing dependency. The capacity of discovery is the
     bottleneck.
   - Upstream failure: Elasticsearch calls fail or time out, or discovery-api's logs tie the 5xx to a failed
     dependency (Elasticsearch, PostgreSQL or OpenAI), for example 503 "Search isn't available right now" after a
     failed search.
   - Scheduling or resource failure: available replicas stay below desired, pods are Pending, or containers
     restart or are OOMKilled.
3. Only if the evidence shows admission saturation, open one pull request in the GitHub repository
   `DSavaliya-Labs/conferenceops-ai`, from a new branch against `main`:
   - Change only `components.discovery.replicas` or `components.discovery.resources` in
     `deploy/env/aks/values.yaml`. Size replicas from the evidence: the offered request rate divided by what one pod
     accepts, rounded up, plus one pod of headroom. Keep replicas between 1 and 5, the chart's bounds.
   - Change nothing else: not the image, the admission limit (`components.discovery.maxInflight`) or any other
     value, and no other file. Never change tests, CI (`.github/`), review policy (`.gitar/`, `AGENTS.md`), the load
     test or its rate (`ops/load/`), check rules, thresholds or dashboards (`observability/`), or credentials.
   - Add the labels `incident` and `gitar-managed`.
   - Write the description in these four sections. The repository's review fails a pull request without them:
     - `## Why`: the cause and its evidence, with Dash0 links (the failed check, the charts or queries, example
       traces).
     - `## What`: the value you changed and how you sized it, and that the image, the admission limit and the load
       rate are unchanged.
     - `## How it was verified`: the Dash0 queries you ran and what each one showed.
     - `## Not covered`: recovery isn't measured yet. List the checks that will show it: `conferenceops-discovery`
       reaches the new number of available replicas, every pod serves traffic, the 5xx share stays below 1% for 60
       seconds with at least 400 requests, the p95 of POST /api/v1/search stays below 1.5 s, and this check returns
       to healthy.
   - First look for an open pull request labeled `incident`. If there is one, add your evidence to it as a comment
     instead of opening a second pull request.
4. If the evidence points to another cause or is unclear, change nothing. Finish with a short report for a human:
   what you checked, the links, the cause you found, and why more discovery capacity would not fix it.

Never weaken a test, a check, a threshold or a review rule to make the failure go away, and never claim a recovery
you have not measured.
