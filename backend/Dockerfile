# syntax=docker/dockerfile:1
# One image for all three processes; the Kubernetes Deployment picks the command (core-api, discovery-api, worker).

FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY conferenceops ./conferenceops
COPY alembic.ini ./
COPY migrations ./migrations
RUN uv sync --frozen --no-dev

FROM python:3.12-slim
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin app
WORKDIR /app
COPY --from=build /app /app
# Stable HTTP semantic conventions on request spans: the Dash0 check rules and dashboard query those names.
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 OTEL_SEMCONV_STABILITY_OPT_IN=http
USER 10001
EXPOSE 8080
CMD ["core-api"]
