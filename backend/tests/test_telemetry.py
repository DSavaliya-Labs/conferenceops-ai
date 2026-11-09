import json
import logging

from opentelemetry.sdk.trace import TracerProvider

from conferenceops.settings import Settings
from conferenceops.telemetry import JsonFormatter, resource
from conferenceops.web import _strip_query


def _record(message: str) -> logging.LogRecord:
    return logging.LogRecord("test", logging.INFO, __file__, 1, message, None, None)


def test_log_lines_are_json_and_carry_the_active_trace_ids():
    tracer = TracerProvider().get_tracer("test")
    with tracer.start_as_current_span("request") as span:
        line = JsonFormatter().format(_record("searched"))
        ctx = span.get_span_context()
    doc = json.loads(line)
    assert doc["message"] == "searched"
    assert doc["trace_id"] == format(ctx.trace_id, "032x")
    assert doc["span_id"] == format(ctx.span_id, "016x")


def test_log_lines_outside_a_span_have_no_trace_ids():
    doc = json.loads(JsonFormatter().format(_record("idle")))
    assert "trace_id" not in doc


def test_server_request_hook_strips_query_strings_from_span_url_attributes():
    class RecordingSpan:
        def __init__(self):
            self.attributes = {}

        def is_recording(self):
            return True

        def set_attribute(self, key, value):
            self.attributes[key] = value

    span = RecordingSpan()
    _strip_query(span, {"path": "/search", "query_string": b"q=private attendee text"})

    assert span.attributes == {
        "http.target": "/search",
        "url.path": "/search",
        "http.url": "/search",
        "url.query": "",
    }


def test_every_service_reports_the_same_namespace_and_its_version():
    attrs = resource("discovery-api", Settings(service_version="abc1234", environment="aks")).attributes
    assert attrs["service.name"] == "discovery-api"
    assert attrs["service.namespace"] == "conferenceops"
    assert attrs["service.version"] == "abc1234"
    assert attrs["deployment.environment.name"] == "aks"
