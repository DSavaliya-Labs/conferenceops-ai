---
title: "Frontend follows the API"
description: "When the API changes, the OpenAPI schema and the Angular client change in the same PR (API-1)"
type: check
when: "PRs that change FastAPI routes, request or response models under backend/conferenceops, or contracts/"
actions: "Fail the check and comment with the stale files when the schema or the generated client is out of date"
---

# Frontend follows the API

When a route, a request model or a response model changes:

1. `contracts/openapi.json` must be regenerated in the same PR.
2. The generated Angular client under `contracts/` must be regenerated from it.
3. Every Angular call site that uses a changed operation must be updated, so the app still compiles and the UI
   still sends and reads the right fields.

Fail when any of these is missing. Name the operation IDs that drifted and the files to regenerate. Pure refactors
that leave the exported schema byte-for-byte identical pass.
