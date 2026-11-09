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
   a few failing traces with their logs, and the replicas and pods of `conferenceops-discovery`.
2. Decide which cause the evidence supports:
   - Admission saturation: discovery-api answers HTTP 503 with code `DISCOVERY_CAPACITY_EXHAUSTED` (its per-pod
     admission control is full) while Elasticsearch and OpenAI calls stay healthy and the available pods are all
     busy. The capacity of discovery is the bottleneck.
   - Upstream failure: Elasticsearch or OpenAI calls fail or time out, or the 5xx responses don't carry that code
     (for example 503 "Search isn't available right now" after a failed search).
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
   - In the description, give the cause and its evidence with Dash0 links (the failed check, the charts or queries,
     example traces); the change and how you chose the number; that the image, the admission limit and the load
     rate are unchanged; and how to verify recovery: `conferenceops-discovery` reaches the new number of available
     replicas, every pod serves traffic, the 5xx share stays below 1% for 60 seconds with at least 400 requests, the
     p95 of POST /api/v1/search stays below 1.5 s, and this check returns to healthy.
   - First look for an open pull request labeled `incident`. If there is one, add your evidence to it as a comment
     instead of opening a second pull request.
4. If the evidence points to another cause or is unclear, change nothing. Finish with a short report for a human:
   what you checked, the links, the cause you found, and why more discovery capacity would not fix it.

Never weaken a test, a check, a threshold or a review rule to make the failure go away, and never claim a recovery
you have not measured.
