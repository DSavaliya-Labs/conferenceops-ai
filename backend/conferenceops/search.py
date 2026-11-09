"""Hybrid search over the session projections: BM25 and kNN with identical filters, fused with reciprocal rank fusion
in Python (PLAN §8.2). If query embedding fails, search keeps working on BM25 and says so (AI-1, fail open)."""

import logging
from collections.abc import Sequence
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel
from sqlalchemy import select

from conferenceops.db import transaction
from conferenceops.indexing import ALIAS, Embedder
from conferenceops.models import Conference, Room
from conferenceops.models import Session as CatalogSession

log = logging.getLogger("conferenceops.search")

RRF_K = 60
CANDIDATES = 50
VENUE_TZ = ZoneInfo("America/Denver")


class ApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class SearchRequest(ApiModel):
    query: str = Field(min_length=1, max_length=300)
    conference: str | None = Field(default=None, description="Limit to one event, e.g. kubecon-na-2026")
    limit: int = Field(default=10, ge=1, le=25)


class SearchHit(ApiModel):
    id: str
    title: str
    event: str
    conference: str
    is_fictional: bool
    starts_at: datetime
    starts_at_venue: str = Field(description="Start in the event's time zone, e.g. 'Tue Nov 10, 10:30 AM MST'")
    room: str | None = None
    level: str | None = None
    topics: list[str] = []


class SearchResponse(ApiModel):
    results: list[SearchHit]
    retrieval_mode: Literal["hybrid", "lexical"]


class SearchBackend(Protocol):
    def search(self, request: SearchRequest) -> SearchResponse: ...


class SearchSessions(Protocol):
    def load_visible(
        self, ids: Sequence[str], conference: str | None
    ) -> dict[str, tuple[CatalogSession, Conference, Room | None]]: ...


class PostgresSearchSessions:
    def load_visible(
        self, ids: Sequence[str], conference: str | None
    ) -> dict[str, tuple[CatalogSession, Conference, Room | None]]:
        session_ids = []
        for doc_id in ids:
            try:
                session_id = UUID(doc_id)
            except ValueError:
                continue
            if session_id not in session_ids:
                session_ids.append(session_id)
        if not session_ids:
            return {}

        statement = (
            select(CatalogSession, Conference, Room)
            .join(Conference, Conference.id == CatalogSession.conference_id)
            .outerjoin(Room, Room.id == CatalogSession.room_id)
            .where(CatalogSession.id.in_(session_ids), CatalogSession.is_service.is_(False))
        )
        if conference:
            statement = statement.where(Conference.slug == conference)

        with transaction() as db:
            rows = db.execute(statement).all()
        return {str(session.id): (session, event, room) for session, event, room in rows}


def rrf_fuse(rankings: Sequence[Sequence[str]], k: int = RRF_K) -> list[str]:
    """Reciprocal rank fusion: score(d) = sum(1 / (k + rank)), ranks start at 1. Ties break by ID, so the order is
    deterministic."""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores, key=lambda d: (-scores[d], d))


def venue_time(instant: datetime) -> str:
    local = instant.astimezone(VENUE_TZ)
    return f"{local:%a %b} {local.day}, {local:%I:%M %p %Z}".replace(" 0", " ")


def _filters(request: SearchRequest) -> list[dict]:
    filters: list[dict] = [{"term": {"is_service": False}}]
    if request.conference:
        filters.append({"term": {"conference": request.conference}})
    return filters


class HybridSearch:
    def __init__(self, es, embedder: Embedder, sessions: SearchSessions):
        self.es = es
        self.embedder = embedder
        self.sessions = sessions

    def _bm25(self, request: SearchRequest) -> list[str]:
        body = {
            "bool": {
                "must": {"multi_match": {"query": request.query, "fields": ["title^3", "abstract", "topics^2"]}},
                "filter": _filters(request),
            }
        }
        hits = self.es.search(index=ALIAS, query=body, size=CANDIDATES, source=False)["hits"]["hits"]
        return [h["_id"] for h in hits]

    def _knn(self, request: SearchRequest, vector: list[float]) -> list[str]:
        knn = {
            "field": "embedding",
            "query_vector": vector,
            "k": CANDIDATES,
            "num_candidates": CANDIDATES * 4,
            "filter": _filters(request),
        }
        hits = self.es.search(index=ALIAS, knn=knn, size=CANDIDATES, source=False)["hits"]["hits"]
        return [h["_id"] for h in hits]

    def search(self, request: SearchRequest) -> SearchResponse:
        rankings = [self._bm25(request)]
        mode: Literal["hybrid", "lexical"] = "lexical"
        try:
            vector = self.embedder.embed([request.query])[0]
            rankings.append(self._knn(request, vector))
            mode = "hybrid"
        except Exception:
            # PRIV-1: log the failure, never the query.
            log.warning("query embedding failed; searching by keywords", exc_info=True)
        ids = rrf_fuse(rankings)
        if not ids:
            return SearchResponse(results=[], retrieval_mode=mode)
        rows = self.sessions.load_visible(ids, conference=request.conference)
        visible = [doc_id for doc_id in ids if doc_id in rows][: request.limit]
        results = [_hit_from_row(doc_id, *rows[doc_id]) for doc_id in visible]
        return SearchResponse(results=results, retrieval_mode=mode)


def _hit_from_row(doc_id: str, session: CatalogSession, conference: Conference, room: Room | None) -> SearchHit:
    starts = session.starts_at
    return SearchHit(
        id=doc_id,
        title=session.title,
        event=conference.name,
        conference=conference.slug,
        is_fictional=conference.is_fictional,
        starts_at=starts,
        starts_at_venue=venue_time(starts),
        room=room.name if room else None,
        level=session.level.value if session.level else None,
        topics=session.topics or [],
    )
