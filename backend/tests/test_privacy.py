"""PRIV-1: what attendees type never reaches telemetry. Found by Gitar's review of PR #2: by default the request span
recorded the full URL, query string included."""

from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from conferenceops.web import instrument

TYPED_QUERY = "where-can-i-find-a-quiet-room"


def _spans_for(url: str):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    app = FastAPI()

    @app.get("/search")
    def search() -> dict[str, str]:
        return {"status": "ok"}

    instrument(app, tracer_provider=provider)
    assert TestClient(app).get(url).status_code == 200
    return exporter.get_finished_spans()


def test_the_query_string_never_reaches_span_attributes():
    spans = _spans_for(f"/search?q={TYPED_QUERY}")
    assert spans, "the request produced no span"
    for span in spans:
        for name, value in (span.attributes or {}).items():
            assert TYPED_QUERY not in str(value), f"{span.name} attribute {name} carries the query"


def test_the_path_is_still_recorded():
    spans = _spans_for(f"/search?q={TYPED_QUERY}")
    server = [s for s in spans if (s.attributes or {}).get("url.path") or (s.attributes or {}).get("http.target")]
    assert server
    attrs = server[0].attributes
    assert attrs.get("url.path", attrs.get("http.target")) == "/search"
