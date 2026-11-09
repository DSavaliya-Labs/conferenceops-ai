# Security

The attack surface here is small and public: an anonymous web app, two FastAPI services behind nginx, PostgreSQL,
Elasticsearch, outbound calls to OpenAI and Dash0, and the GitHub Actions deploy to AKS. Judge a change by what it
exposes on that surface.

- **STD-SEC-01 Secrets never land in the repository, the image, the browser or the logs** (SEC-1). Keys come from
  Kubernetes Secrets and GitHub environment secrets; the browser receives only the ingest-only Dash0 token. A test
  fixture may hold a fake key only if it is obviously fake. Critical when a real secret is committed or printed.
- **STD-SEC-02 Input is validated at the boundary.** Every request body and query parameter is a Pydantic model or a
  typed `Query`/`Path` with bounds (`limit` has a maximum, text has a maximum length, UUIDs are UUIDs). A free
  string that reaches SQL, an Elasticsearch query, a shell command or a file path is a finding.
- **STD-SEC-03 Queries are built, not concatenated.** SQLAlchemy expressions with bound parameters; Elasticsearch
  queries as dictionaries from typed values, never from interpolated strings. `query_string`/`simple_query_string`
  on attendee input needs an explicit reason.
- **STD-SEC-04 Authorization is server-side** (AUTHZ-1). The persona comes from `X-Demo-Profile-ID`, ownership and
  role are resolved on the server, and a resource in another conference answers 404, a forbidden action 403. A
  client-supplied role, profile field or conference id used for a decision is Critical.
- **STD-SEC-05 No server-side fetch of attendee-supplied URLs.** The only outbound calls are to OpenAI, Dash0, the
  Sessionize feed and the cluster's own services, with fixed hosts and timeouts. A new outbound call names its host
  in the pull request.
- **STD-SEC-06 Uploads are bounded.** Dictation audio has a size cap on the server (`MAX_BYTES`, read with
  `read_capped` so a large upload never fills memory) and a duration cap in the browser, is held in memory only for
  the transcription call and is never written to disk or logs.
- **STD-SEC-07 Browser safety.** The Angular app never builds HTML from API strings (`innerHTML` with untrusted text is a
  finding); links to external sources open with `rel="noopener noreferrer"`; nginx serves the API on the same
  origin, so CORS stays off and a new cross-origin allowance needs a reason. nginx sends `Cache-Control: no-store`
  for `/config.json`; a Content-Security-Policy isn't set today, so a pull request that adds one is welcome and one
  that adds inline scripts says why.
- **STD-SEC-08 Dependencies.** A new package is pinned, maintained and needed; a version bump of a security-relevant
  package (crypto, HTTP, parsing) says why. Lock files change in the same pull request.
- **STD-SEC-09 Workloads are hardened** (K8S-1): non-root, no privilege escalation, dropped capabilities, read-only root
  filesystem, pinned images by commit SHA. A new container goes through `check-chart-policy.sh`.
- **STD-SEC-10 CI and deploy least privilege.** Workflow `permissions` are explicit and minimal, actions are pinned to a
  version, Azure sign-in is OIDC, and nothing prints a token. A workflow that reads a secret into a log or an
  artifact is Critical.

Grading: a confirmed exposure on the public surface is Critical; a weakness that needs an unlikely precondition is
Important; defense-in-depth suggestions are Minor.
