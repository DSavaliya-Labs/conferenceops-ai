"""Session projections in Elasticsearch, fed by the outbox (DATA-1).

Writes use external versioning keyed on the session revision, so a late or retried job can never overwrite newer
content. Documents hold public session data only: no persona preferences, no free text from attendees (PRIV-1)."""

import logging
import uuid
from collections.abc import Callable, Sequence
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Protocol

import openai
from elasticsearch import BadRequestError, ConflictError, ConnectionTimeout, Elasticsearch, NotFoundError
from elasticsearch import ConnectionError as EsConnectionError
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from conferenceops.models import Conference, OutboxJob, OutboxStatus, Room, Session

log = logging.getLogger("conferenceops.indexing")

ALIAS = "co-sessions"
INDEX = "co-sessions-v1"
DIMENSIONS = 1536
ENTITY = "session"  # the only outbox entity type this loop leases; other entity types wait for their own consumer
# A failing job retries with a backoff that doubles from 2 seconds up to 5 minutes. An outage never parks a job: while
# OpenAI or Elasticsearch is down, slow, out of quota or refusing the key, every job keeps retrying every 5 minutes, and
# is indexed once the provider is back. Only a failure of the job itself (see `_is_own_failure`) parks it as FAILED,
# after MAX_ATTEMPTS failures, about 13.5 minutes: retrying the same text or document can't help.
MAX_ATTEMPTS = 10
MAX_BACKOFF = timedelta(minutes=5)

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


class EmbeddingInputError(ValueError):
    """The model rejected an input (too long, for example). Retrying the same text can't help, and the other texts in
    its batch shouldn't fail with it."""


class UnknownJobKind(ValueError):
    """A session job whose kind this consumer doesn't handle. It is never marked done."""


class Embedder(Protocol):
    """Raises `EmbeddingInputError` when the model rejects an input; any other error means the provider failed."""

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class OpenAIEmbedder:
    """`text-embedding-3-small`, 1,536 dimensions, batched. The same model embeds queries (search) and documents."""

    def __init__(self, client, model: str = "text-embedding-3-small"):
        self.client = client
        self.model = model

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        try:
            response = self.client.embeddings.create(model=self.model, input=list(texts), dimensions=DIMENSIONS)
        except openai.BadRequestError as exc:
            raise EmbeddingInputError("the embedding model rejected an input") from exc
        return [item.embedding for item in response.data]


def ensure_index(es: Elasticsearch) -> None:
    if not es.indices.exists(index=INDEX):
        es.indices.create(index=INDEX, mappings=MAPPING, settings={"number_of_shards": 1, "number_of_replicas": 0})
    if not es.indices.exists_alias(name=ALIAS):
        es.indices.put_alias(index=INDEX, name=ALIAS)


def embedding_text(session: Session) -> str:
    return f"{session.title}\n\n{session.abstract or ''}".strip()


def _is_own_failure(exc: Exception) -> bool:
    """Whether the job itself failed, so that retrying it can't help: the model rejected its text, Elasticsearch
    rejected its document (400), or its kind is unknown. Anything else is an outage that a later retry gets past:
    OpenAI or Elasticsearch unreachable or timing out, a bad key (401, 403), quota or throttling (429), server errors
    (5xx), and errors we don't recognize."""
    return isinstance(exc, EmbeddingInputError | BadRequestError | UnknownJobKind)


def _backoff(attempts: int) -> timedelta:
    # The exponent is bounded, so a job that keeps failing through a long outage can't overflow timedelta.
    return min(timedelta(seconds=2 ** min(attempts, 16)), MAX_BACKOFF)


def _fail(job: OutboxJob, exc: Exception, now: Callable[[], datetime]) -> None:
    """Count the failure and back off. Only the job's own failure parks it, and only once it has failed MAX_ATTEMPTS
    times: nothing re-enqueues a parked job (an unchanged session enqueues nothing on re-import), so an outage must
    never park one."""
    job.attempts += 1
    job.last_error = type(exc).__name__
    parked = _is_own_failure(exc) and job.attempts >= MAX_ATTEMPTS
    job.status = OutboxStatus.FAILED if parked else OutboxStatus.PENDING
    job.next_attempt_at = now() + _backoff(job.attempts)
    log.warning(
        "indexing job failed",
        extra={"job": str(job.id), "attempts": job.attempts, "error": job.last_error, "parked": parked},
    )


def _embed(embedder: Embedder, sessions: Sequence[Session]) -> dict[uuid.UUID, list[float] | Exception]:
    """A vector or the error for each session, from one call for the whole batch. When the provider fails, every
    session gets that error and backs off. When the model rejects an input, the sessions are embedded one by one, so
    only the rejected one fails."""
    if not sessions:
        return {}
    try:
        vectors = embedder.embed([embedding_text(s) for s in sessions])
        return dict(zip([s.id for s in sessions], vectors, strict=True))
    except EmbeddingInputError as exc:
        if len(sessions) == 1:
            return {sessions[0].id: exc}
        return {s.id: _embed(embedder, [s])[s.id] for s in sessions}
    except Exception as exc:  # noqa: BLE001 - the provider is down or slow: the whole batch backs off
        return {s.id: exc for s in sessions}


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

    Jobs are leased with SKIP LOCKED, so two workers never take the same job. Only session jobs are leased: other
    entity types are left for their own consumer. A failing job backs off on its own. When the model is down, the
    batch's index jobs back off, but its delete jobs still run: they need no embedding. When Elasticsearch stops
    answering (a connection error or a timeout), the job that found out and the rest of the batch back off without
    another call: a pass then costs one timeout, not one per job, and stays inside the worker's iteration budget
    (worker/main.py), where one timeout per job would get it restarted by the liveness probe."""
    jobs = db.scalars(
        select(OutboxJob)
        .where(
            OutboxJob.entity_type == ENTITY,
            OutboxJob.status == OutboxStatus.PENDING,
            OutboxJob.next_attempt_at <= now(),
        )
        .order_by(OutboxJob.created_at)
        .limit(batch)
        .with_for_update(skip_locked=True)
    ).all()
    if not jobs:
        return 0
    session_ids = {job.entity_id for job in jobs if job.kind == "index"}
    sessions = {s.id: s for s in db.scalars(select(Session).where(Session.id.in_(session_ids)))} if session_ids else {}
    conferences = {c.id: c for c in db.scalars(select(Conference))}
    rooms = {r.id: r for r in db.scalars(select(Room).where(Room.id.in_([s.room_id for s in sessions.values()])))}
    vectors = _embed(embedder, list(sessions.values()))

    indexed = 0
    outage: Exception | None = None  # set once Elasticsearch stops answering: the rest of the batch backs off unasked
    for job in jobs:
        session = sessions.get(job.entity_id) if job.kind == "index" else None
        vector = vectors[session.id] if session is not None else None
        if isinstance(vector, Exception):  # the model failed, or rejected this session's text
            _fail(job, vector, now)
            continue
        if outage is not None:
            _fail(job, outage, now)
            continue
        try:
            if job.kind == "delete":
                with suppress(NotFoundError):  # never indexed, or already deleted
                    es.delete(
                        index=ALIAS,
                        id=str(job.entity_id),
                        version=job.entity_revision,
                        version_type="external_gte",
                    )
            elif job.kind != "index":
                raise UnknownJobKind("unsupported outbox job kind")
            elif session is not None:  # a session deleted since the job was written has nothing left to index
                es.index(
                    index=ALIAS,
                    id=str(session.id),
                    document=document(session, conferences[session.conference_id], rooms.get(session.room_id), vector),
                    version=session.revision,
                    version_type="external_gte",
                )
                indexed += 1
            job.status = OutboxStatus.DONE
        except ConflictError:
            job.status = OutboxStatus.DONE  # a newer revision is already indexed
        except (EsConnectionError, ConnectionTimeout) as exc:
            # Elasticsearch didn't answer, after waiting out the request timeout (30 s in the worker), so it won't
            # answer the next job either: the rest of the batch backs off with this error, unasked, as the batch's
            # index jobs do when the model is down. An error it does answer (a 429 or a 503) comes back at once.
            outage = exc
            _fail(job, exc, now)
        except Exception as exc:  # noqa: BLE001 - one bad job must not stop the batch
            _fail(job, exc, now)
    return indexed
