"""Configuration from environment variables (12-factor). Secrets arrive the same way, from Kubernetes Secrets."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    environment: str = "local"  # deployment.environment.name in telemetry
    service_version: str = "dev"  # the image tag (commit SHA) in Kubernetes
    otel_exporter_otlp_endpoint: str | None = None  # telemetry is exported only when this is set
    log_level: str = "INFO"
    host: str = "0.0.0.0"  # noqa: S104 - containers listen on all interfaces; the Kubernetes Service limits exposure
    port: int = 8080


@lru_cache
def settings() -> Settings:
    return Settings()
