"""Admission control for discovery-api (PLAN.md §15.2): under overload, a pod answers the requests above its capacity
with an immediate 503 instead of queueing them until every request times out.

Each pod lets at most `DISCOVERY_MAX_INFLIGHT` requests to its API routes (`/api/v1/...`) be in progress at once. A
request that finds every slot taken waits at most `DISCOVERY_ADMISSION_WAIT_MS` for one to free up. Then it gets a 503
with the code DISCOVERY_CAPACITY_EXHAUSTED and a Retry-After header, before any parsing, dependency or retrieval work.
Health probes are never limited (K8S-1): an overloaded pod stays Ready and keeps serving the requests it admitted.

The limit counts requests in progress rather than requests per second, so it follows what the pod can really do: when
searches get slower, the same limit admits fewer of them, and admitted work never piles up in the thread pool.

Every rejection shows up three ways, and none of them carries what the attendee typed (PRIV-1):
- the server span ends with HTTP 503 and span status ERROR, so Dash0's 5xx-ratio check counts it, and it carries the
  attribute `conferenceops.error.code` = DISCOVERY_CAPACITY_EXHAUSTED;
- the counter `conferenceops.admission.rejections` with `reason` = `capacity_exhausted`, next to the gauges
  `conferenceops.admission.in_flight` and `conferenceops.admission.limit`;
- one warning log line with the counts.
"""

import logging
import uuid
from collections.abc import Iterable

import anyio
from fastapi import FastAPI
from opentelemetry import metrics, trace
from opentelemetry.metrics import CallbackOptions, Counter, MeterProvider, Observation
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from conferenceops.settings import Settings

log = logging.getLogger("conferenceops.admission")

CAPACITY_CODE = "DISCOVERY_CAPACITY_EXHAUSTED"
REASON = "capacity_exhausted"  # the bounded `reason` on the rejection counter
ERROR_CODE_ATTRIBUTE = "conferenceops.error.code"  # our own attribute: the instrumentation may overwrite error.type
RETRY_AFTER_SECONDS = 1
API_PREFIX = "/api/v1"


def is_limited(path: str) -> bool:
    """The API routes are limited. Health probes, the OpenAPI document and the docs pass straight through."""
    return path == API_PREFIX or path.startswith(API_PREFIX + "/")


class AdmissionLimiter:
    """At most `limit` requests in progress on this pod. A request waits at most `max_wait` seconds for a slot.

    anyio's CapacityLimiter hands a freed slot straight to the longest-waiting request, so a newcomer can't take it
    first and the limit is never exceeded. Each request holds its slot in its own task, from admission until its
    response is sent."""

    def __init__(self, limit: int, max_wait: float) -> None:
        if limit < 1:
            raise ValueError("the admission limit must be at least 1")
        if max_wait < 0:
            raise ValueError("the admission wait can't be negative")
        self.limit = limit
        self.max_wait = max_wait
        self._slots = anyio.CapacityLimiter(limit)

    @property
    def in_flight(self) -> int:
        """Requests holding a slot. Safe to read from the metrics thread: it is the length of a set."""
        return self._slots.borrowed_tokens

    @property
    def waiting(self) -> int:
        """Requests waiting for a slot. Read it on the event loop only."""
        return self._slots.statistics().tasks_waiting

    async def acquire(self) -> bool:
        """Take a slot for the current task: True once it has one, False when none freed up within `max_wait`."""
        try:
            self._slots.acquire_nowait()
            return True
        except anyio.WouldBlock:
            if self.max_wait == 0:
                return False
        # A slot handed over just as the wait runs out goes on to the next request in line (anyio handles that race).
        with anyio.move_on_after(self.max_wait):
            await self._slots.acquire()
            return True
        return False

    def release(self) -> None:
        """Give the current task's slot back, or hand it to the longest-waiting request."""
        self._slots.release()


class AdmissionControl:
    """Pure ASGI middleware, so a rejection costs no body parsing, no dependency and no worker thread. FastAPI's
    OpenTelemetry instrumentation wraps every middleware, so the server span is current here."""

    def __init__(self, app: ASGIApp, limiter: AdmissionLimiter, rejections: Counter) -> None:
        self.app = app
        self.limiter = limiter
        self.rejections = rejections
        # Starlette builds its middleware when the server starts, not at import, so this line stays out of the
        # OpenAPI export's stdout.
        log.info(
            "Admission control: at most %d requests in progress on %s routes, waiting up to %d ms for a slot",
            limiter.limit,
            API_PREFIX,
            round(limiter.max_wait * 1000),
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not is_limited(scope["path"]):
            await self.app(scope, receive, send)
            return
        if not await self.limiter.acquire():
            await self.reject(scope, receive, send)
            return
        try:
            await self.app(scope, receive, send)
        finally:
            self.limiter.release()

    async def reject(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Record the rejection before the response goes out: the server span ends with the last body message.
        span = trace.get_current_span()
        span.set_attribute(ERROR_CODE_ATTRIBUTE, CAPACITY_CODE)
        self.rejections.add(1, {"reason": REASON})
        in_flight, waiting, limit = self.limiter.in_flight, self.limiter.waiting, self.limiter.limit
        # Counts only. The request body, which holds what the attendee typed, is never read here (PRIV-1).
        log.warning(
            "Discovery is at capacity: answered 503 %s, %d of %d slots in use, %d waiting for a slot",
            CAPACITY_CODE,
            in_flight,
            limit,
            waiting,
            extra={
                ERROR_CODE_ATTRIBUTE: CAPACITY_CODE,
                "conferenceops.admission.in_flight": in_flight,
                "conferenceops.admission.limit": limit,
                "conferenceops.admission.waiting": waiting,
            },
        )
        context = span.get_span_context()
        request_id = format(context.trace_id, "032x") if context.is_valid else uuid.uuid4().hex
        response = JSONResponse(
            status_code=503,
            content={
                "code": CAPACITY_CODE,
                "message": "This discovery server is at capacity. Try again in a moment.",
                "retry_after_seconds": RETRY_AFTER_SECONDS,
                "request_id": request_id,
            },
            headers={"Retry-After": str(RETRY_AFTER_SECONDS), "Cache-Control": "no-store"},
        )
        await response(scope, receive, send)


def add_admission_control(app: FastAPI, cfg: Settings, meter_provider: MeterProvider | None = None) -> AdmissionLimiter:
    """Limit the app's API routes per pod. Returns the limiter, which is also `app.state.admission`."""
    limiter = AdmissionLimiter(cfg.discovery_max_inflight, cfg.discovery_admission_wait_ms / 1000)
    meter = metrics.get_meter(__name__, meter_provider=meter_provider)
    rejections = meter.create_counter(
        "conferenceops.admission.rejections",
        unit="{request}",
        description="Requests answered 503 because every admission slot on the pod was taken",
    )

    def in_flight(_: CallbackOptions) -> Iterable[Observation]:
        return [Observation(limiter.in_flight)]

    def limit(_: CallbackOptions) -> Iterable[Observation]:
        return [Observation(limiter.limit)]

    meter.create_observable_gauge(
        "conferenceops.admission.in_flight",
        callbacks=[in_flight],
        unit="{request}",
        description="Requests in progress on the pod's admission-limited routes",
    )
    meter.create_observable_gauge(
        "conferenceops.admission.limit",
        callbacks=[limit],
        unit="{request}",
        description="The most requests the pod admits at once (DISCOVERY_MAX_INFLIGHT)",
    )
    app.add_middleware(AdmissionControl, limiter=limiter, rejections=rejections)
    app.state.admission = limiter
    return limiter
