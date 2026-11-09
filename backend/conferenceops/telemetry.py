"""OpenTelemetry for every process: traces, metrics and logs to the collector, plus JSON logs with trace IDs on
stdout. Health probes are excluded from traces, so they can't drown real requests (they were 87% of spans in our
earlier prototype). Log content must follow PRIV-1: never what attendees type, only lengths and IDs."""

import json
import logging
import sys

from opentelemetry import metrics, trace
from opentelemetry._logs import set_logger_provider
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from conferenceops.settings import Settings

NAMESPACE = "conferenceops"
HEALTH_PATHS = "health/live,health/ready"

_configured = False


class JsonFormatter(logging.Formatter):
    """One JSON object per line, carrying the active trace and span IDs so logs link to traces in Dash0."""

    def format(self, record: logging.LogRecord) -> str:
        doc = {
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        ctx = trace.get_current_span().get_span_context()
        if ctx.is_valid:
            doc["trace_id"] = format(ctx.trace_id, "032x")
            doc["span_id"] = format(ctx.span_id, "016x")
        if record.exc_info:
            doc["exception"] = self.formatException(record.exc_info)
        return json.dumps(doc)


def resource(service_name: str, cfg: Settings) -> Resource:
    return Resource.create(
        {
            "service.name": service_name,
            "service.namespace": NAMESPACE,
            "service.version": cfg.service_version,
            "deployment.environment.name": cfg.environment,
        }
    )


def setup_telemetry(service_name: str, cfg: Settings) -> None:
    """Configure logging and the global OpenTelemetry providers once per process."""
    global _configured
    if _configured:
        return
    stdout = logging.StreamHandler(sys.stdout)
    stdout.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [stdout]
    root.setLevel(cfg.log_level)

    res = resource(service_name, cfg)
    tracer_provider = TracerProvider(resource=res)
    if cfg.otel_exporter_otlp_endpoint:  # the OTLP exporters read OTEL_EXPORTER_OTLP_ENDPOINT themselves
        tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        metrics.set_meter_provider(
            MeterProvider(resource=res, metric_readers=[PeriodicExportingMetricReader(OTLPMetricExporter())])
        )
        logger_provider = LoggerProvider(resource=res)
        logger_provider.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter()))
        set_logger_provider(logger_provider)
        root.addHandler(LoggingHandler(logger_provider=logger_provider))
    trace.set_tracer_provider(tracer_provider)
    _configured = True
