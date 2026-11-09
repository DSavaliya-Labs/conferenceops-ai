# API and data standards

## HTTP API (core-api, discovery-api)

- **STD-API-01 The contract is committed** (API-1): `contracts/openapi.json` is regenerated with
  `python -m conferenceops.export_openapi` and `contracts/api.d.ts` with `openapi-typescript`, in the same pull
  request as the route change. The contract test in `backend/tests/` lists the route.
- **STD-API-02 Changes are additive.** A new optional field is fine; renaming or removing a field, changing a type or a
  status code, or making an optional field required needs a reason, the client updated in the same pull request,
  and a note in the description. There is one version of the API and both apps deploy together.
- **STD-API-03 Status codes mean one thing.** 200 with a body, 201 for a created resource, 204 for an idempotent write
  with nothing to return, 400 for a malformed request, 403 forbidden, 404 for unknown or not-yours (AUTHZ-1), 409
  for a conflict, 422 for a body that doesn't validate, 503 with `Retry-After` when capacity is exhausted. Errors
  carry one shape: `{"detail": …}` from FastAPI or `{"code": …, …}` for the documented domain errors.
- **STD-API-04 Idempotent where retried.** `PUT` and `DELETE` are safe to repeat; a `POST` that a client may retry says
  what happens on the second call.
- **STD-API-05 Bounded responses.** Every list takes a `limit` with a maximum and documents the order. A route that can
  return the whole table is a finding.
- **STD-API-06 Public fields only** (PRIV-1). Response models are explicit; a route never returns an ORM object or
  `model_dump()` of a domain model that carries private fields.
- **STD-API-07 Headers are typed and documented.** `X-Demo-Profile-ID` is declared in OpenAPI where required; a new
  header is added the same way.

## Data (PostgreSQL canonical, Elasticsearch projections)

- **STD-DATA-01 Migrations are additive first.** Add the column or table, deploy, backfill, then switch reads, then drop
  in a later pull request. A migration that drops or rewrites data is high risk and says how to roll back.
- **STD-DATA-02 Every migration runs on an empty database and on the seeded one,** in CI, forward only; the migration
  hook runs before the seed Job.
- **STD-DATA-03 Indexes match the queries.** A new filter or sort on a table of unbounded size comes with an index, and a
  new index on a large table says how long it takes.
- **STD-DATA-04 The outbox is the only path to Elasticsearch** (DATA-1): a canonical write and its outbox job commit
  together; the worker projects with external versioning so an old event never resurrects hidden content. Writing to
  Elasticsearch directly from a request handler is a finding.
- **STD-DATA-05 Projections hold public fields only** (PRIV-1) and the publication flag (TRANSCRIPT-1); the mapping
  change and the reindex plan are in the pull request.
- **STD-DATA-06 Seeds are idempotent.** The seed Job adds what is missing and never changes a row a demo may have
  touched; fixtures are labeled fictional.
