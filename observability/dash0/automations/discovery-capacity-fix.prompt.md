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
   - The code `DISCOVERY_CAPACITY_EXHAUSTED` is in the body of discovery-api's 503 responses, and response bodies
     are not in telemetry. Look for the code where discovery-api records it: an attribute of the failing server
     spans, or the reason on its admission-rejection metric. Where it isn't recorded, its absence is no evidence
     either way.
   - Admission control turns away only the requests above a pod's limit, before any retrieval work. A rejected
     request's server span is short, has no Elasticsearch child span, and no failed search is logged for its trace,
     while the same pod keeps answering other searches.
   - Elasticsearch calls are traced: client spans with `db.system.name` `elasticsearch`. OpenAI and PostgreSQL
     calls may have no spans. Judge them from discovery-api's logs, which carry trace IDs, and treat missing OpenAI
     or PostgreSQL spans as no evidence either way. discovery-api is built to fail open when OpenAI is slow or down
     (AI-1 in `AGENTS.md`), so an OpenAI problem alone should show as warnings in its logs, not as 5xx.
2. Decide which cause the evidence supports:
   - Admission saturation: the 5xx are 503 admission rejections, shown by the recorded code or by the shape above,
     on every available pod, while Elasticsearch calls stay healthy and discovery-api's logs show no failing
     dependency. The capacity of discovery is the bottleneck.
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
   - Change nothing else: not the image, the admission limit or any other value, and no other file. Never change
     tests, CI (`.github/`), review policy (`.gitar/`, `AGENTS.md`), the load test or its rate (`ops/load/`), check
     rules, thresholds or dashboards (`observability/`), or credentials.
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
