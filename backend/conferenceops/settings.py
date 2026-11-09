"""Configuration from environment variables (12-factor). Secrets arrive the same way, from Kubernetes Secrets."""

from functools import lru_cache

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
    import_interval_seconds: float = 1800.0  # how often the worker re-imports the KubeCon feed
    log_level: str = "INFO"
    host: str = "0.0.0.0"  # noqa: S104 - containers listen on all interfaces; the Kubernetes Service limits exposure
    port: int = 8080


@lru_cache
def settings() -> Settings:
    return Settings()
