"""Admission control on discovery-api (PLAN.md §15.2): above the per-pod limit, requests get an immediate 503
DISCOVERY_CAPACITY_EXHAUSTED with Retry-After instead of queueing, and every rejection is visible in its server span,
a metric and one log line, without what the attendee typed (PRIV-1).

The tests send real concurrent requests through the ASGI app (httpx's ASGI transport on one event loop). An API route
holds its slot until the test opens a gate, so the number of requests in progress is exact, not a matter of timing."""

import asyncio
import json
import logging
import threading
import time
from collections.abc import Callable

import httpx
import pytest
from fastapi import FastAPI
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, StatusCode
from pydantic import ValidationError

from conferenceops.admission import CAPACITY_CODE, ERROR_CODE_ATTRIBUTE, add_admission_control, is_limited
from conferenceops.discovery_app.main import app as discovery_app
from conferenceops.discovery_app.main import get_search
from conferenceops.search import SearchResponse
from conferenceops.settings import Settings
from conferenceops.telemetry import JsonFormatter
from conferenceops.web import health, instrument

TYPED_QUERY = "where-can-i-find-a-quiet-room"
SEARCH = "/api/v1/search"
# How long a test waits for an answer. A request admitted by mistake would wait on the gate forever: with a deadline,
# that regression fails the test in seconds instead of hanging the suite.
DEADLINE = 5.0


class Discovery:
    """A discovery-like app: the health routes, the same OpenTelemetry instrumentation and admission control, and
    POST /api/v1/search, which holds its slot until `gate` opens."""

    def __init__(self, limit: int, wait_ms: int):
        self.spans = InMemorySpanExporter()
        tracer_provider = TracerProvider()
        tracer_provider.add_span_processor(SimpleSpanProcessor(self.spans))
        self.metrics = InMemoryMetricReader()
        self.gate = asyncio.Event()
        self.inside = 0
        self.peak = 0
        self.entered = 0
        self.app = FastAPI()
        self.app.include_router(health)

        @self.app.post(SEARCH)
        async def search(body: dict) -> dict:
            self.entered += 1
            self.inside += 1
            self.peak = max(self.peak, self.inside)
            try:
                await self.gate.wait()
            finally:
                self.inside -= 1
            return {"results": [], "queryLength": len(body.get("query", ""))}

        @self.app.post("/api/v1/broken")
        async def broken() -> dict:
            raise RuntimeError("the handler failed")

        instrument(self.app, tracer_provider=tracer_provider)
        settings = Settings(discovery_max_inflight=limit, discovery_admission_wait_ms=wait_ms)
        self.limiter = add_admission_control(
            self.app, settings, meter_provider=MeterProvider(metric_readers=[self.metrics])
        )

    def client(self) -> httpx.AsyncClient:
        transport = httpx.ASGITransport(app=self.app, raise_app_exceptions=False)
        return httpx.AsyncClient(transport=transport, base_url="http://discovery")

    def server_spans(self):
        return [s for s in self.spans.get_finished_spans() if s.kind == SpanKind.SERVER]

    def metric_points(self, name: str) -> list:
        for resource in self.metrics.get_metrics_data().resource_metrics:
            for scope in resource.scope_metrics:
                for metric in scope.metrics:
                    if metric.name == name:
                        return list(metric.data.data_points)
        return []


def search(client: httpx.AsyncClient) -> asyncio.Task:
    return asyncio.create_task(client.post(SEARCH, json={"query": TYPED_QUERY}))


async def answers(tasks: list[asyncio.Task]) -> list[httpx.Response]:
    """The responses to these requests, which must all arrive within DEADLINE."""
    _, pending = await asyncio.wait(tasks, timeout=DEADLINE)
    for task in pending:
        task.cancel()
    assert not pending, f"{len(pending)} of {len(tasks)} requests got no answer: admitted while every slot was taken?"
    return [task.result() for task in tasks]


async def until(condition: Callable[[], bool]) -> None:
    deadline = time.monotonic() + DEADLINE
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("timed out waiting for the requests to arrive")
        await asyncio.sleep(0.005)


def status_code(attributes) -> int | None:
    # The instrumentation writes http.status_code by default, http.response.status_code with the stable conventions.
    return attributes.get("http.response.status_code", attributes.get("http.status_code"))


async def overload(d: Discovery, extra: int):
    """Fill every slot, then send `extra` more requests while the first ones are still in progress."""
    async with d.client() as client:
        held = [search(client) for _ in range(d.limiter.limit)]
        try:
            await until(lambda: d.inside == d.limiter.limit)
            started = time.monotonic()
            rejected = await answers([search(client) for _ in range(extra)])
            elapsed = time.monotonic() - started
            still_held = sum(not task.done() for task in held)
        finally:
            d.gate.set()  # never leave a request on the gate, even after a failed assertion
        admitted = await answers(held)
    return admitted, rejected, elapsed, still_held


def test_the_limit_holds_under_concurrency_and_the_excess_is_rejected_fast():
    d = Discovery(limit=3, wait_ms=50)
    admitted, rejected, elapsed, still_held = asyncio.run(overload(d, extra=4))

    # Exactly `limit` requests reached the handler, never more at once: the boundary is the configured limit.
    assert d.entered == 3
    assert d.peak == 3
    assert [r.status_code for r in admitted] == [200, 200, 200]
    assert [r.status_code for r in rejected] == [503, 503, 503, 503]
    # Rejected while the admitted requests were still in progress: a short wait, then an answer, never a queue.
    assert still_held == 3
    assert elapsed < 1.0
    # Every slot is free again afterwards.
    assert d.limiter.in_flight == 0


def test_the_rejection_is_a_503_with_the_capacity_code_and_retry_after():
    d = Discovery(limit=1, wait_ms=10)
    _, rejected, _, _ = asyncio.run(overload(d, extra=1))

    response = rejected[0]
    assert response.status_code == 503
    assert response.headers["retry-after"] == "1"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-type"].startswith("application/json")
    body = response.json()
    assert body["code"] == "DISCOVERY_CAPACITY_EXHAUSTED"
    assert body["retry_after_seconds"] == 1
    assert body["message"]
    # The request ID is the trace ID of the rejected request's server span, so it leads straight to the trace.
    [span] = [s for s in d.server_spans() if s.attributes.get(ERROR_CODE_ATTRIBUTE)]
    assert body["request_id"] == format(span.context.trace_id, "032x")


def test_a_request_gets_a_slot_that_frees_up_within_the_wait():
    async def scenario(d: Discovery) -> list[int]:
        async with d.client() as client:
            first = search(client)
            try:
                await until(lambda: d.inside == 1)
                second = search(client)
                await until(lambda: d.limiter.waiting == 1)
            finally:
                d.gate.set()  # the first request finishes and hands its slot to the waiting one
            return [r.status_code for r in await answers([first, second])]

    d = Discovery(limit=1, wait_ms=1000)
    assert asyncio.run(scenario(d)) == [200, 200]
    assert d.entered == 2
    assert d.peak == 1


def test_with_no_wait_a_full_pod_rejects_at_once():
    d = Discovery(limit=1, wait_ms=0)
    admitted, rejected, _, _ = asyncio.run(overload(d, extra=2))
    assert [r.status_code for r in admitted] == [200]
    assert [r.status_code for r in rejected] == [503, 503]


def test_health_probes_are_never_limited():
    async def scenario(d: Discovery) -> list[int]:
        async with d.client() as client:
            held = [search(client) for _ in range(2)]
            try:
                await until(lambda: d.inside == 2)
                probes = [asyncio.create_task(client.get(path)) for path in ("/health/live", "/health/ready")]
                responses = await answers([*probes, search(client)])
            finally:
                d.gate.set()
            await answers(held)
        return [r.status_code for r in responses]

    # Every slot is taken, so an API request is rejected, while both probes still answer 200 (K8S-1).
    assert asyncio.run(scenario(Discovery(limit=2, wait_ms=10))) == [200, 200, 503]


def test_a_failing_request_gives_its_slot_back():
    async def scenario(d: Discovery) -> list[int]:
        async with d.client() as client:
            failed = await client.post("/api/v1/broken")
            in_flight_after_failure = d.limiter.in_flight
            d.gate.set()
            ok = await client.post(SEARCH, json={"query": TYPED_QUERY})
        return [failed.status_code, in_flight_after_failure, ok.status_code]

    assert asyncio.run(scenario(Discovery(limit=1, wait_ms=0))) == [500, 0, 200]


def test_the_rejected_server_span_ends_with_503_and_carries_the_code():
    d = Discovery(limit=2, wait_ms=10)
    asyncio.run(overload(d, extra=3))

    spans = d.server_spans()
    rejected = [s for s in spans if s.attributes.get(ERROR_CODE_ATTRIBUTE) == CAPACITY_CODE]
    admitted = [s for s in spans if ERROR_CODE_ATTRIBUTE not in s.attributes]
    assert len(rejected) == 3
    assert len(admitted) == 2
    for span in rejected:
        # Dash0's 5xx-ratio check counts the status code; the span status and the route come with it.
        assert status_code(span.attributes) == 503
        assert span.status.status_code == StatusCode.ERROR
        assert span.attributes["http.route"] == SEARCH
        assert span.name == f"POST {SEARCH}"
    for span in admitted:
        assert status_code(span.attributes) == 200
        assert span.status.status_code != StatusCode.ERROR


def test_rejections_are_counted_by_reason_with_the_limit_and_requests_in_progress():
    async def scenario(d: Discovery):
        async with d.client() as client:
            held = [search(client) for _ in range(2)]
            try:
                await until(lambda: d.inside == 2)
                rejected = await answers([search(client) for _ in range(3)])
                in_flight = d.metric_points("conferenceops.admission.in_flight")
                limit = d.metric_points("conferenceops.admission.limit")
            finally:
                d.gate.set()
            await answers(held)
        assert [r.status_code for r in rejected] == [503, 503, 503]
        return in_flight, limit

    d = Discovery(limit=2, wait_ms=0)
    in_flight, limit = asyncio.run(scenario(d))
    assert [p.value for p in in_flight] == [2]
    assert [p.value for p in limit] == [2]
    [rejections] = d.metric_points("conferenceops.admission.rejections")
    assert rejections.value == 3
    assert dict(rejections.attributes) == {"reason": "capacity_exhausted"}  # a bounded label, nothing per request
    assert [p.value for p in d.metric_points("conferenceops.admission.in_flight")] == [0]


def test_each_rejection_logs_one_warning_with_counts_and_never_the_query(caplog):
    caplog.set_level(logging.WARNING, logger="conferenceops.admission")
    d = Discovery(limit=2, wait_ms=0)
    asyncio.run(overload(d, extra=2))

    warnings = [r for r in caplog.records if r.name == "conferenceops.admission" and r.levelno == logging.WARNING]
    assert len(warnings) == 2
    for record in warnings:
        assert record.getMessage() == (
            "Discovery is at capacity: answered 503 DISCOVERY_CAPACITY_EXHAUSTED, 2 of 2 slots in use, "
            "0 waiting for a slot"
        )
        # PRIV-1: not in the message, not in any field the OpenTelemetry log handler exports, not in the JSON line.
        assert TYPED_QUERY not in json.dumps(vars(record), default=str)
        assert TYPED_QUERY not in JsonFormatter().format(record)
    for span in d.spans.get_finished_spans():
        for name, value in (span.attributes or {}).items():
            assert TYPED_QUERY not in str(value), f"{span.name} attribute {name} carries the query"


def test_discovery_limits_its_search_route_with_the_configured_limit():
    """The real discovery app: its search route runs in a worker thread, and the next request above the limit gets
    the 503 while the admitted ones are still in progress."""
    limit = discovery_app.state.admission.limit
    assert limit == Settings().discovery_max_inflight
    release = threading.Event()
    entered = threading.Semaphore(0)

    class SlowSearch:
        def search(self, request):
            entered.release()
            release.wait(timeout=10)
            return SearchResponse(results=[], retrieval_mode="lexical")

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=discovery_app), base_url="http://discovery"
        ) as client:
            held = [search(client) for _ in range(limit)]
            try:
                for _ in range(limit):  # every admitted search is running in its worker thread
                    assert await asyncio.to_thread(entered.acquire, True, DEADLINE)
                [over, probe] = await answers([search(client), asyncio.create_task(client.get("/health/ready"))])
            finally:
                release.set()
            return over, probe, await answers(held)

    discovery_app.dependency_overrides[get_search] = SlowSearch
    try:
        over, probe, admitted = asyncio.run(scenario())
    finally:
        release.set()
        discovery_app.dependency_overrides.pop(get_search, None)
    assert over.status_code == 503
    assert over.json()["code"] == CAPACITY_CODE
    assert probe.status_code == 200
    assert [r.status_code for r in admitted] == [200] * limit
    assert discovery_app.state.admission.in_flight == 0


@pytest.mark.parametrize(
    ("path", "limited"),
    [
        ("/api/v1/search", True),
        ("/api/v1/recommendations/next", True),
        ("/api/v1", True),
        ("/health/live", False),
        ("/health/ready", False),
        ("/openapi.json", False),
        ("/api/v10/search", False),
    ],
)
def test_only_the_api_routes_are_limited(path, limited):
    assert is_limited(path) is limited


def test_the_limit_and_the_wait_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("DISCOVERY_MAX_INFLIGHT", "12")
    monkeypatch.setenv("DISCOVERY_ADMISSION_WAIT_MS", "80")
    limiter = add_admission_control(FastAPI(), Settings(), meter_provider=MeterProvider())
    assert (limiter.limit, limiter.max_wait) == (12, 0.08)


@pytest.mark.parametrize(
    "values",
    [
        {"discovery_max_inflight": 0},
        {"discovery_max_inflight": 41},  # above the 40 worker threads, requests would queue for a thread
        {"discovery_admission_wait_ms": -1},
        {"discovery_admission_wait_ms": 1001},  # a wait that long would be a queue
    ],
)
def test_out_of_range_settings_fail_at_startup(values):
    with pytest.raises(ValidationError):
        Settings(**values)
