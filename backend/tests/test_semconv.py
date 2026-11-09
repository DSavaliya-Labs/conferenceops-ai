"""Request spans carry the stable HTTP semantic conventions. Found on AKS: the live discovery-api spans carried only
the old names (http.status_code), so the Dash0 check rule "Discovery is rejecting searches", which counts
http.response.status_code=~"5..", matched nothing, and the incident could never start."""

from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from conferenceops.web import instrument


def _server_span(path: str, status: int):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    app = FastAPI()

    @app.get("/search")
    def search() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/full")
    def full() -> None:
        from fastapi import HTTPException

        raise HTTPException(status_code=503, detail="capacity")

    instrument(app, tracer_provider=provider)
    assert TestClient(app).get(path).status_code == status
    server = [s for s in exporter.get_finished_spans() if s.kind.name == "SERVER"]
    assert len(server) == 1, [s.name for s in exporter.get_finished_spans()]
    return server[0]


def test_request_spans_use_the_stable_http_attribute_names():
    attrs = dict(_server_span("/search", 200).attributes or {})
    assert attrs.get("http.response.status_code") == 200
    assert attrs.get("http.request.method") == "GET"
    assert attrs.get("url.path") == "/search"
    assert "http.status_code" not in attrs, "the old name would make Dash0's status-code matchers miss the span"


def test_a_rejection_is_counted_as_a_5xx_under_the_stable_name():
    attrs = dict(_server_span("/full", 503).attributes or {})
    assert attrs.get("http.response.status_code") == 503
