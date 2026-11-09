"""discovery-api: search, "where should I go next?" and cited answers. A separate process so it can scale out on
its own during an overload while the core API keeps working."""

import logging
from functools import lru_cache
from typing import Annotated

import uvicorn
from elasticsearch import Elasticsearch
from fastapi import Depends, HTTPException
from openai import OpenAI

from conferenceops.admission import add_admission_control
from conferenceops.indexing import OpenAIEmbedder
from conferenceops.search import HybridSearch, PostgresSearchSessions, SearchBackend, SearchRequest, SearchResponse
from conferenceops.settings import settings
from conferenceops.web import create_app

log = logging.getLogger("conferenceops.discovery")

app = create_app("discovery-api")
add_admission_control(app, settings())  # per-pod limit on /api/v1 requests in progress; 503 above it (PLAN §15.2)


@lru_cache
def get_search() -> SearchBackend:
    cfg = settings()
    if not cfg.elasticsearch_url:
        raise HTTPException(status_code=503, detail="Search isn't configured")
    auth = (cfg.elasticsearch_username, cfg.elasticsearch_password) if cfg.elasticsearch_username else None
    es = Elasticsearch(cfg.elasticsearch_url, basic_auth=auth, request_timeout=5)
    # A short timeout: a slow model must not hold a search hostage; search falls back to keywords (AI-1).
    client = OpenAI(api_key=cfg.openai_api_key or "missing", timeout=3, max_retries=0)
    return HybridSearch(es, OpenAIEmbedder(client, cfg.openai_embedding_model), PostgresSearchSessions())


@app.post("/api/v1/search", response_model=SearchResponse, response_model_by_alias=True)
def search(request: SearchRequest, backend: Annotated[SearchBackend, Depends(get_search)]) -> SearchResponse:
    """Which talks match what I'm interested in? The query arrives in the body, never in the URL (PRIV-1)."""
    try:
        return backend.search(request)
    except HTTPException:
        raise
    except Exception as exc:
        log.exception("search failed", extra={"query_length": len(request.query)})
        raise HTTPException(status_code=503, detail="Search isn't available right now") from exc


def run() -> None:
    cfg = settings()
    # No access log: request lines could carry query strings, and our own logs carry trace IDs (PRIV-1).
    uvicorn.run(app, host=cfg.host, port=cfg.port, log_config=None, access_log=False)
