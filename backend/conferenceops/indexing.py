"""Session projections in Elasticsearch, fed by the outbox (DATA-1).

Writes use external versioning keyed on the session revision, so a late or retried job can never overwrite newer
content. Documents hold public session data only: no persona preferences, no free text from attendees (PRIV-1)."""

import logging
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Protocol

from elasticsearch import ConflictError, Elasticsearch
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from conferenceops.models import Conference, OutboxJob, OutboxStatus, Room, Session

log = logging.getLogger("conferenceops.indexing")

ALIAS = "co-sessions"
INDEX = "co-sessions-v1"
DIMENSIONS = 1536
MAX_ATTEMPTS = 5

MAPPING = {
    "properties": {
        "conference": {"type": "keyword"},
        "event_name": {"type": "keyword"},
        "is_fictional": {"type": "boolean"},
        "title": {"type": "text", "fields": {"raw": {"type": "keyword"}}},
        "abstract": {"type": "text"},
        "level": {"type": "keyword"},
        "topics": {"type": "keyword"},
        "room": {"type": "keyword"},
        "starts_at": {"type": "date"},
        "ends_at": {"type": "date"},
        "is_service": {"type": "boolean"},
        "revision": {"type": "long"},
        "embedding": {"type": "dense_vector", "dims": DIMENSIONS, "index": True, "similarity": "cosine"},
    }
}


class Embedder(Protocol):
    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class OpenAIEmbedder:
    """`text-embedding-3-small`, 1,536 dimensions, batched. The same model embeds queries (search) and documents."""

    def __init__(self, client, model: str = "text-embedding-3-small"):
        self.client = client
        self.model = model

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        response = self.client.embeddings.create(model=self.model, input=list(texts), dimensions=DIMENSIONS)
        return [item.embedding for item in response.data]


def ensure_index(es: Elasticsearch) -> None:
    if not es.indices.exists(index=INDEX):
        es.indices.create(index=INDEX, mappings=MAPPING, settings={"number_of_shards": 1, "number_of_replicas": 0})
    if not es.indices.exists_alias(name=ALIAS):
        es.indices.put_alias(index=INDEX, name=ALIAS)


def embedding_text(session: Session) -> str:
    return f"{session.title}\n\n{session.abstract or ''}".strip()


def document(session: Session, conference: Conference, room: Room | None, embedding: list[float]) -> dict:
    return {
        "conference": conference.slug,
        "event_name": conference.name,
        "is_fictional": conference.is_fictional,
        "title": session.title,
        "abstract": session.abstract,
        "level": session.level.value if session.level else None,
        "topics": session.topics,
        "room": room.name if room else None,
        "starts_at": session.starts_at.isoformat(),
        "ends_at": session.ends_at.isoformat(),
        "is_service": session.is_service,
        "revision": session.revision,
        "embedding": embedding,
    }


def process_outbox(
    db: DbSession,
    es: Elasticsearch,
    embedder: Embedder,
    batch: int = 64,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> int:
    """Index up to `batch` pending session jobs in the caller's transaction. Returns how many were indexed.
    Jobs are leased with SKIP LOCKED, so two workers never take the same job."""
    jobs = db.scalars(
        select(OutboxJob)
        .where(OutboxJob.status == OutboxStatus.PENDING, OutboxJob.next_attempt_at <= now())
        .order_by(OutboxJob.created_at)
        .limit(batch)
        .with_for_update(skip_locked=True)
    ).all()
    if not jobs:
        return 0
    sessions = {s.id: s for s in db.scalars(select(Session).where(Session.id.in_([j.entity_id for j in jobs])))}
    conferences = {c.id: c for c in db.scalars(select(Conference))}
    rooms = {r.id: r for r in db.scalars(select(Room).where(Room.id.in_([s.room_id for s in sessions.values()])))}

    live = [s for s in sessions.values()]
    vectors = (
        dict(zip([s.id for s in live], embedder.embed([embedding_text(s) for s in live]), strict=True)) if live else {}
    )
    indexed = 0
    for job in jobs:
        session = sessions.get(job.entity_id)
        try:
            if session is not None:
                es.index(
                    index=ALIAS,
                    id=str(session.id),
                    document=document(
                        session, conferences[session.conference_id], rooms.get(session.room_id), vectors[session.id]
                    ),
                    version=session.revision,
                    version_type="external_gte",
                )
                indexed += 1
            job.status = OutboxStatus.DONE
        except ConflictError:
            job.status = OutboxStatus.DONE  # a newer revision is already indexed
        except Exception as exc:  # noqa: BLE001 - one bad job must not stop the batch
            job.attempts += 1
            job.last_error = type(exc).__name__
            job.status = OutboxStatus.FAILED if job.attempts >= MAX_ATTEMPTS else OutboxStatus.PENDING
            job.next_attempt_at = now() + timedelta(seconds=2**job.attempts)
            log.warning("indexing job failed", extra={"job": str(job.id), "attempts": job.attempts})
    return indexed
