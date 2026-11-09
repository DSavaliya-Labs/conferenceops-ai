"""The FastAPI app factory shared by the core and discovery APIs."""

import os

# Emit the stable HTTP semantic conventions (http.response.status_code, http.request.method, url.path) on request
# spans. Without this, the FastAPI instrumentation emits the old names (http.status_code, http.method), and the Dash0
# check rules and dashboard, which query the stable names, match nothing: the rejection check could never fire.
# Dash0's dataset-level upgrade didn't rename them at ingestion, so the names are stable at the source. An explicit
# OTEL_SEMCONV_STABILITY_OPT_IN in the environment still wins; the instrumentation reads it when it initializes.
os.environ.setdefault("OTEL_SEMCONV_STABILITY_OPT_IN", "http")

from fastapi import APIRouter, FastAPI  # noqa: E402
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor  # noqa: E402
from opentelemetry.trace import Span, TracerProvider  # noqa: E402

from conferenceops.settings import settings  # noqa: E402
from conferenceops.telemetry import HEALTH_PATHS, setup_telemetry  # noqa: E402

health = APIRouter(tags=["health"])

# Span attributes that can carry the request URL, query string included. PRIV-1: they keep the path only.
URL_ATTRIBUTES = ("http.target", "http.url", "url.full", "url.path")


@health.get("/health/live")
def live() -> dict[str, str]:
    """The process is running. Never calls PostgreSQL, Elasticsearch or OpenAI (K8S-1)."""
    return {"status": "ok"}


@health.get("/health/ready")
def ready() -> dict[str, str]:
    """The process can take traffic. Downstream outages show up as explicit errors, not as unready pods (K8S-1)."""
    return {"status": "ready"}


def strip_query(span: Span, scope: dict) -> None:
    """Overwrite the URL attributes with the path, so a query string never reaches telemetry (PRIV-1)."""
    if span and span.is_recording():
        path = scope.get("path", "")
        for name in URL_ATTRIBUTES:
            span.set_attribute(name, path)
        span.set_attribute("url.query", "")


def instrument(app: FastAPI, tracer_provider: TracerProvider | None = None) -> None:
    FastAPIInstrumentor.instrument_app(
        app, excluded_urls=HEALTH_PATHS, server_request_hook=strip_query, tracer_provider=tracer_provider
    )


def create_app(service_name: str) -> FastAPI:
    cfg = settings()
    setup_telemetry(service_name, cfg)
    app = FastAPI(title=f"ConferenceOps AI {service_name}", version=cfg.service_version)
    app.include_router(health)
    instrument(app)
    return app
