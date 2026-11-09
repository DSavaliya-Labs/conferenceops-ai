# Testing, reliability and observability

## Testing

- **STD-TEST-01 Tests in proportion to risk** (TEST-1). A behavior change has a test that fails without it; a bug fix has
  a regression test; a refactor keeps the existing tests green and adds none. Security, permissions, money-like
  state (bookmarks, publication), time zones and concurrency get the most.
- **STD-TEST-02 Test the contract, not the implementation.** Backend tests drive routes through the FastAPI test client
  with the fakes in `backend/tests/`; web specs render components with `TestBed` and assert on what a user or a
  screen reader gets (`text()`, roles, ARIA state), not on private fields.
- **STD-TEST-03 Fakes for our own code, real services in integration tests.** Mocking the module under test is a
  finding. PostgreSQL and Elasticsearch behavior runs under `-m integration` in CI against real instances.
- **STD-TEST-04 Deterministic.** No sleeps, no wall-clock reads without a fixed clock, no network. A flaky test is fixed
  or deleted, not retried.
- **STD-TEST-05 Accessibility is tested** (A11Y-1): a new control has a spec for its name, state and keyboard path; a
  new page is covered by the `ui-smoke-accessibility` job.
- **STD-TEST-06 The contract test lists every route** with its status codes and required headers; a route missing from
  it is a finding (API-1).

## Reliability

- **STD-OPS-01 Every outbound call has a timeout** shorter than the caller's deadline: OpenAI, Elasticsearch, PostgreSQL,
  Dash0, Sessionize. A call without one is Important.
- **STD-OPS-02 Retries are bounded and jittered,** only for transient failures, and never wrap a non-idempotent write.
  Fail open where `AGENTS.md` says so (AI-1): the deterministic answer, the lexical fallback.
- **STD-OPS-03 Overload is rejected, not absorbed.** The admission limit on discovery-api stays the measured one; a change
  to it comes with a measurement. Probes don't depend on downstream services (K8S-1).
- **STD-OPS-04 Shutdown is graceful:** handlers finish in-flight requests, the worker beats its heartbeat per loop, and a
  Deployment change keeps `maxUnavailable` at 0 for the web and the APIs.

## Observability

- **STD-OPS-10 Content never reaches telemetry** (PRIV-1): spans carry route templates, ids, lengths and counts; logs are
  structured and carry the trace id; metric labels are bounded. A new span attribute or log field is checked for
  attendee text.
- **STD-OPS-11 Log once, where handled,** with the exception attached; no log-and-rethrow chains, no logs at `INFO`
  inside hot loops.
- **STD-OPS-12 A new path is visible:** a new route or job emits a server span, its failures emit an error log with the
  operation name, and a new user-facing failure mode has a metric or a check rule in `observability/dash0/`.
- **STD-OPS-13 Deploys are marked** in Dash0 (`dash0.deployment`, succeeded or failed) and a change to the markers or
  the check rules comes with the `scripts/dash0-apply.sh` run that applied it.
