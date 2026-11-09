"""Configuration from environment variables (12-factor). Secrets arrive the same way, from Kubernetes Secrets."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    environment: str = "local"  # deployment.environment.name in telemetry
    service_version: str = "dev"  # the image tag (commit SHA) in Kubernetes
    otel_exporter_otlp_endpoint: str | None = None  # telemetry is exported only when this is set
    database_url: str | None = None  # postgresql+psycopg://...; a Kubernetes Secret in the cluster
    elasticsearch_url: str | None = None
    elasticsearch_username: str | None = None
    elasticsearch_password: str | None = None
    openai_api_key: str | None = None
    openai_embedding_model: str = "text-embedding-3-small"
    openai_transcribe_model: str = "gpt-transcribe"  # dictation.py; whisper-1 and gpt-4o-transcribe are deprecated
    # Ask's reasoning model (Responses API, structured outputs) and how long one answer may take before the
    # deterministic fallback answers instead (AI-1). gpt-6-luna: OpenAI's efficient model for focused tasks.
    openai_model: str = "gpt-6-luna"
    openai_ask_timeout_seconds: float = 10.0
    import_interval_seconds: float = 1800.0  # how often the worker re-imports the KubeCon feed
    # Where the worker's loops write their heartbeat files and the liveness probe reads them (worker/liveness.py).
    worker_heartbeat_dir: str = "/tmp/worker-heartbeat"  # noqa: S108 - the chart's emptyDir, private to the pod
    # discovery-api's admission control (admission.py): requests in progress per pod on its /api/v1 routes, set from
    # measured capacity (ops/load/README.md). At most 40, the worker threads that run its routes: above that, requests
    # would queue for a thread, which is the queueing admission control exists to prevent.
    discovery_max_inflight: int = Field(default=8, ge=1, le=40)
    # How long a request may wait for a free slot before its 503: enough to absorb jitter, too short to be a queue.
    discovery_admission_wait_ms: int = Field(default=50, ge=0, le=1000)
    log_level: str = "INFO"
    host: str = "0.0.0.0"  # noqa: S104 - containers listen on all interfaces; the Kubernetes Service limits exposure
    port: int = 8080


@lru_cache
def settings() -> Settings:
    return Settings()
