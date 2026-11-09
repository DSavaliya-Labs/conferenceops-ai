"""Transcript chunks in Elasticsearch (alias `co-transcripts`), fed by the outbox (DATA-1, TRANSCRIPT-1).

A transcript job doesn't carry text; it says "make search match this transcript's current state". The worker reads
the row and either indexes its chunks (published) or deletes everything it ever indexed for it (anything else, or the
row is gone). So a late or duplicated job is harmless: it projects the state at the time it runs, and the same job
run twice writes the same documents. Each chunk document's ID carries the revision it was cut from, its external
version is that revision, and indexing a revision drops the chunks of every older one, so a correction never leaves
the superseded text searchable.

Only published text is ever written here, and every document says `status: published` so the Ask query can filter on
it. Ask still rechecks each hit against PostgreSQL afterwards (ask.py): a chunk whose transcript was unpublished since
it was indexed is dropped before it reaches the model (TRANSCRIPT-1)."""

import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from elasticsearch import ConflictError, Elasticsearch
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from conferenceops.indexing import DIMENSIONS, Embedder, EmbeddingInputError, _fail
from conferenceops.models import Conference, OutboxJob, OutboxStatus, Session, Transcript, TranscriptStatus
from conferenceops.transcripts import ENTITY, Chunk, chunk_cues, chunk_id

log = logging.getLogger("conferenceops.transcript_indexing")

ALIAS = "co-transcripts"
INDEX = "co-transcripts-v1"

MAPPING = {
    "properties": {
        "transcript_id": {"type": "keyword"},
        "session_id": {"type": "keyword"},
        "conference": {"type": "keyword"},
        "status": {"type": "keyword"},  # always "published": the query filters on it anyway (TRANSCRIPT-1)
        "revision": {"type": "long"},
        "chunk": {"type": "integer"},
        "speaker": {"type": "keyword"},
        "session_title": {"type": "text"},
        "text": {"type": "text"},
        "start_seconds": {"type": "float"},
        "end_seconds": {"type": "float"},
        "is_simulated": {"type": "boolean"},
        "embedding": {"type": "dense_vector", "dims": DIMENSIONS, "index": True, "similarity": "cosine"},
    }
}


def ensure_index(es: Elasticsearch) -> None:
    if not es.indices.exists(index=INDEX):
        es.indices.create(index=INDEX, mappings=MAPPING, settings={"number_of_shards": 1, "number_of_replicas": 0})
    if not es.indices.exists_alias(name=ALIAS):
        es.indices.put_alias(index=INDEX, name=ALIAS)


def chunk_document(
    transcript: Transcript, session: Session, conference: Conference, chunk: Chunk, embedding: list[float]
) -> dict:
    return {
        "transcript_id": str(transcript.id),
        "session_id": str(session.id),
        "conference": conference.slug,
        "status": TranscriptStatus.PUBLISHED.value,
        "revision": transcript.revision,
        "chunk": chunk.index,
        "speaker": transcript.speaker_name,
        "session_title": session.title,
        "text": chunk.text,
        "start_seconds": chunk.start,
        "end_seconds": chunk.end,
        "is_simulated": transcript.is_simulated,
        "embedding": embedding,
    }


def _embed_all(
    embedder: Embedder, chunks: dict[uuid.UUID, list[Chunk]]
) -> dict[uuid.UUID, list[list[float]] | Exception]:
    """Vectors for every published transcript's chunks, from one call for the whole batch. A provider failure fails
    them all (they back off); a rejected input is retried transcript by transcript, so only that one fails."""
    if not chunks:
        return {}
    order = list(chunks)
    texts = [chunk.text for tid in order for chunk in chunks[tid]]
    try:
        vectors = embedder.embed(texts)
    except EmbeddingInputError as exc:
        if len(order) == 1:
            return {order[0]: exc}
        return {tid: _embed_all(embedder, {tid: chunks[tid]})[tid] for tid in order}
    except Exception as exc:  # noqa: BLE001 - the provider is down or slow: the whole batch backs off
        return {tid: exc for tid in order}
    result: dict[uuid.UUID, list[list[float]] | Exception] = {}
    offset = 0
    for tid in order:
        count = len(chunks[tid])
        result[tid] = vectors[offset : offset + count]
        offset += count
    return result


def _delete_chunks(es: Elasticsearch, transcript_id: uuid.UUID, below_revision: int | None = None) -> None:
    """Remove a transcript's chunks from search: all of them, or only those of revisions below `below_revision`."""
    filters: list[dict] = [{"term": {"transcript_id": str(transcript_id)}}]
    if below_revision is not None:
        filters.append({"range": {"revision": {"lt": below_revision}}})
    es.delete_by_query(index=ALIAS, query={"bool": {"filter": filters}}, conflicts="proceed", refresh=True)


def process_transcript_outbox(
    db: DbSession,
    es: Elasticsearch,
    embedder: Embedder,
    batch: int = 16,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> int:
    """Project up to `batch` pending transcript jobs in the caller's transaction. Returns how many transcripts were
    indexed (published ones); the rest were removed from search. Jobs are leased with SKIP LOCKED, and only
    transcript jobs: session jobs have their own consumer (indexing.py)."""
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
    ids = {job.entity_id for job in jobs}
    transcripts = {t.id: t for t in db.scalars(select(Transcript).where(Transcript.id.in_(ids)))}
    published = {t.id: t for t in transcripts.values() if t.status == TranscriptStatus.PUBLISHED}
    sessions = (
        {s.id: s for s in db.scalars(select(Session).where(Session.id.in_([t.session_id for t in published.values()])))}
        if published
        else {}
    )
    conferences = {c.id: c for c in db.scalars(select(Conference))} if published else {}
    chunks = {tid: chunk_cues(t.cues) for tid, t in published.items()}
    vectors = _embed_all(embedder, chunks)

    indexed = 0
    for job in jobs:
        transcript = transcripts.get(job.entity_id)
        try:
            if transcript is None or transcript.status != TranscriptStatus.PUBLISHED:
                # Unpublished, or the row is gone: nothing of it may stay searchable, whatever revision was indexed.
                _delete_chunks(es, job.entity_id)
            else:
                embedded = vectors[transcript.id]
                if isinstance(embedded, Exception):  # the model failed, or rejected this transcript's text
                    _fail(job, embedded, now)
                    continue
                session = sessions[transcript.session_id]
                conference = conferences[session.conference_id]
                for chunk, vector in zip(chunks[transcript.id], embedded, strict=True):
                    es.index(
                        index=ALIAS,
                        id=chunk_id(transcript.id, transcript.revision, chunk.index),
                        document=chunk_document(transcript, session, conference, chunk, vector),
                        version=transcript.revision,
                        version_type="external_gte",
                    )
                _delete_chunks(es, transcript.id, below_revision=transcript.revision)
                indexed += 1
            job.status = OutboxStatus.DONE
        except ConflictError:
            job.status = OutboxStatus.DONE  # that revision's chunk is already there
        except Exception as exc:  # noqa: BLE001 - one bad job must not stop the batch
            _fail(job, exc, now)
    return indexed
