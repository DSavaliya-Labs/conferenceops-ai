"""The FastAPI app factory shared by the core and discovery APIs."""

from fastapi import APIRouter, FastAPI
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

from conferenceops.settings import settings
from conferenceops.telemetry import HEALTH_PATHS, setup_telemetry

health = APIRouter(tags=["health"])


@health.get("/health/live")
def live() -> dict[str, str]:
    """The process is running. Never calls PostgreSQL, Elasticsearch or OpenAI (K8S-1)."""
    return {"status": "ok"}


@health.get("/health/ready")
def ready() -> dict[str, str]:
    """The process can take traffic. Downstream outages show up as explicit errors, not as unready pods (K8S-1)."""
    return {"status": "ready"}


def create_app(service_name: str) -> FastAPI:
    cfg = settings()
    setup_telemetry(service_name, cfg)
    app = FastAPI(title=f"ConferenceOps AI {service_name}", version=cfg.service_version)
    app.include_router(health)
    FastAPIInstrumentor.instrument_app(app, excluded_urls=HEALTH_PATHS)
    return app
