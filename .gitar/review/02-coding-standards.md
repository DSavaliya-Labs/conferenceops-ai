# Coding standards

The linters own formatting. These are the things a linter can't see.

## Python (`backend/`)

- **CODE-01 Types at the boundaries.** Public functions, route handlers and service methods have type hints;
  request and response shapes are Pydantic models; `Any` and untyped dictionaries crossing a module boundary are a
  finding.
- **CODE-02 Errors are explicit.** No bare `except:` and no `except Exception` that swallows; catch the exception
  you can handle, re-raise or convert to an `HTTPException` with the documented status, and log once (OPS-03).
- **CODE-03 No blocking in the event loop.** A `def` route is fine (Starlette runs it in a thread); an `async def`
  route must not call a synchronous client. Thread-pool-bound work respects the admission limit.
- **CODE-04 Time is `datetime` with `tzinfo`, stored as UTC** and rendered in the event's IANA zone (TIME-1).
  `datetime.now()` without `timezone.utc`, naive comparisons and fixed offsets are findings.
- **CODE-05 Configuration through settings**, not scattered `os.environ` reads; every setting has a default that
  works locally and a name listed where the chart sets it.
- **CODE-06 Small, named functions.** A function that does three things gets three names; a module over roughly
  500 lines or a function over roughly 60 is a signal, not a rule. Dead code and commented-out code go.

## TypeScript and Angular (`apps/web/`)

- **CODE-10 Signals and standalone components,** as the rest of the app: `input()`/`output()`, `computed`, `inject()`,
  zoneless-safe change detection. No `NgModule`, no `any`, no `!` non-null assertions on API data.
- **CODE-11 API types come from `contracts/api.d.ts`.** A hand-written interface that mirrors a response is a
  finding (API-1).
- **CODE-12 Subscriptions and effects are cleaned up** (`takeUntilDestroyed`, `DestroyRef`), and an `effect` never
  writes a signal it also reads.
- **CODE-13 Templates stay simple:** control flow with `@if`/`@for` with a `track`, no business logic in the
  template, and user-visible strings live in one place per component.
- **CODE-14 Accessibility is code** (A11Y-1): every control has a name, state is in ARIA attributes
  (`aria-pressed`, `aria-expanded`), focus is managed after a removal, and live regions are few and polite.

## Shell, YAML and Helm

- **CODE-20 Scripts** start with `set -euo pipefail`, quote every expansion, pass shellcheck, print what they did,
  and end with `main "$@"; exit` when they delete anything.
- **CODE-21 Chart values are documented** in `values.yaml` next to their default; `deploy/env/aks/values.yaml`
  holds only what differs on AKS. Templates don't hard-code what a value should carry.
- **CODE-22 Pinned versions everywhere:** images, Helm charts, actions, tools downloaded in CI. `latest` is a
  finding.
