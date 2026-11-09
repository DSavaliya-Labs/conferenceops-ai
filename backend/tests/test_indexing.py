import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx2
import openai
import pytest
from elastic_transport import ApiResponseMeta, HttpHeaders, NodeConfig
from elasticsearch import ApiError, BadRequestError, ConflictError, NotFoundError
from elasticsearch import ConnectionError as EsConnectionError
from sqlalchemy.sql import operators

from conferenceops.indexing import (
    ALIAS,
    DIMENSIONS,
    MAX_ATTEMPTS,
    EmbeddingInputError,
    OpenAIEmbedder,
    process_outbox,
)
from conferenceops.models import Conference, OutboxJob, OutboxStatus, Room, Session

NOW = datetime(2026, 11, 10, tzinfo=UTC)
OPENAI_REQUEST = httpx2.Request("POST", "https://api.openai.com/v1/embeddings")


def _openai_error(cls, status):
    return cls(f"{status}", response=httpx2.Response(status, request=OPENAI_REQUEST), body=None)


class _Rows:
    def __init__(self, rows):
        self.rows = list(rows)

    def all(self):
        return self.rows

    def __iter__(self):
        return iter(self.rows)


def _matches(row, criterion) -> bool:
    """Evaluate one `column <op> value` criterion of a SELECT against an in-memory row."""
    actual, expected = getattr(row, criterion.left.key), criterion.right.value
    if criterion.operator is operators.in_op:
        return actual in expected
    return criterion.operator(actual, expected)


class _Db:
    """The rows process_outbox reads, filtered by each query's WHERE clause like PostgreSQL would."""

    def __init__(self, jobs=(), sessions=(), conferences=(), rooms=()):
        self.jobs = list(jobs)
        self.sessions = list(sessions)
        self.conferences = list(conferences)
        self.rooms = list(rooms)

    def scalars(self, statement):
        entity = statement.column_descriptions[0]["entity"]
        rows = {OutboxJob: self.jobs, Session: self.sessions, Conference: self.conferences, Room: self.rooms}[entity]
        return _Rows(row for row in rows if all(_matches(row, c) for c in statement._where_criteria))


def _es_error(cls, status):
    meta = ApiResponseMeta(
        status=status, http_version="1.1", headers=HttpHeaders(), duration=0.0, node=NodeConfig("http", "es", 9200)
    )
    return cls(f"{status}", meta, {})


class _Es:
    def __init__(self, delete_error=None, index_error=None, failing_ids=None):
        self.delete_error = delete_error
        self.index_error = index_error
        self.failing_ids = failing_ids  # when set, index_error is raised only for these document IDs
        self.deleted = []
        self.indexed = []

    def delete(self, **kwargs):
        if self.delete_error:
            raise self.delete_error
        self.deleted.append(kwargs)

    def index(self, **kwargs):
        if self.index_error and (self.failing_ids is None or kwargs["id"] in self.failing_ids):
            raise self.index_error
        self.indexed.append(kwargs)


class _Embedder:
    def __init__(self, error=None, rejects=None):
        self.error = error
        self.rejects = rejects  # a word the "model" rejects: any batch holding it fails with EmbeddingInputError
        self.calls = []

    def embed(self, texts):
        self.calls.append(list(texts))
        if self.error:
            raise self.error
        if self.rejects and any(self.rejects in text for text in texts):
            raise EmbeddingInputError("rejected")
        return [[0.0] * DIMENSIONS for _ in texts]


def _job(*, kind="index", entity_type="session", entity_id=None, attempts=0, revision=1):
    return OutboxJob(
        id=uuid.uuid4(),
        entity_type=entity_type,
        entity_id=entity_id or uuid.uuid4(),
        entity_revision=revision,
        kind=kind,
        status=OutboxStatus.PENDING,
        attempts=attempts,
        next_attempt_at=NOW,
        created_at=NOW,
    )


def _session(session_id, conference_id, *, title="A conference session", revision=1, room_id=None):
    return Session(
        id=session_id,
        conference_id=conference_id,
        room_id=room_id,
        title=title,
        abstract="Details",
        starts_at=NOW,
        ends_at=NOW + timedelta(hours=1),
        topics=[],
        is_service=False,
        revision=revision,
    )


def _indexable(n=1, **session_kwargs):
    """`n` index jobs for `n` sessions of one conference, and the database holding them."""
    conference = Conference(id=uuid.uuid4(), slug="kubecon-na-2026", name="KubeCon", is_fictional=False)
    sessions = [_session(uuid.uuid4(), conference.id, **session_kwargs) for _ in range(n)]
    jobs = [_job(entity_id=s.id, revision=s.revision) for s in sessions]
    return jobs, sessions, _Db(jobs, sessions, [conference])


def test_embedding_failure_counts_attempt_and_schedules_backoff_for_the_batch():
    conference_id, session_id = uuid.uuid4(), uuid.uuid4()
    job = _job(entity_id=session_id)
    embedder = _Embedder(RuntimeError("provider unavailable"))

    indexed = process_outbox(
        _Db([job], [_session(session_id, conference_id)], [Conference(id=conference_id, slug="kubecon")]),
        _Es(),
        embedder,
        now=lambda: NOW,
    )

    assert indexed == 0
    assert job.attempts == 1
    assert job.status == OutboxStatus.PENDING
    assert job.next_attempt_at == NOW + timedelta(seconds=2)
    assert job.last_error == "RuntimeError"


def test_delete_jobs_remove_the_search_document_and_complete():
    session_id = uuid.uuid4()
    job = _job(kind="delete", entity_id=session_id)
    es = _Es()

    indexed = process_outbox(_Db([job]), es, _Embedder(), now=lambda: NOW)

    assert indexed == 0
    assert es.deleted == [
        {
            "index": ALIAS,
            "id": str(session_id),
            "version": 1,
            "version_type": "external_gte",
        }
    ]
    assert job.status == OutboxStatus.DONE


def test_deleting_a_document_that_was_never_indexed_completes_the_job():
    job = _job(kind="delete")

    process_outbox(_Db([job]), _Es(delete_error=_es_error(NotFoundError, 404)), _Embedder(), now=lambda: NOW)

    assert (job.status, job.attempts) == (OutboxStatus.DONE, 0)


def test_delete_jobs_still_run_when_the_embedding_model_is_down():
    jobs, sessions, db = _indexable()
    delete_job = _job(kind="delete")
    db.jobs.append(delete_job)
    es = _Es()

    process_outbox(db, es, _Embedder(RuntimeError("provider unavailable")), now=lambda: NOW)

    assert delete_job.status == OutboxStatus.DONE
    assert [call["id"] for call in es.deleted] == [str(delete_job.entity_id)]
    assert (jobs[0].status, jobs[0].attempts, jobs[0].last_error) == (OutboxStatus.PENDING, 1, "RuntimeError")


def test_other_entity_types_are_left_for_their_own_consumer():
    # Leasing them here would burn their attempts and park them as FAILED before their consumer ever saw them.
    job = _job(entity_type="transcript")

    process_outbox(_Db([job]), _Es(), _Embedder(), now=lambda: NOW)

    assert (job.status, job.attempts, job.last_error) == (OutboxStatus.PENDING, 0, None)


def test_unknown_session_job_kinds_are_failed_not_marked_done():
    job = _job(kind="reindex-everything")

    process_outbox(_Db([job]), _Es(), _Embedder(), now=lambda: NOW)

    assert job.status == OutboxStatus.PENDING
    assert job.attempts == 1
    assert job.last_error == "UnknownJobKind"


def test_an_index_job_writes_the_session_at_its_revision_with_external_versioning():
    room = Room(id=uuid.uuid4(), name="Salt Palace | Level 1 | Hall DE")
    jobs, sessions, db = _indexable(title="Tracing Everything", revision=3, room_id=room.id)
    db.rooms.append(room)
    es = _Es()

    indexed = process_outbox(db, es, _Embedder(), now=lambda: NOW)

    assert indexed == 1
    assert jobs[0].status == OutboxStatus.DONE
    [call] = es.indexed
    assert (call["index"], call["id"], call["version"], call["version_type"]) == (
        ALIAS,
        str(sessions[0].id),
        3,
        "external_gte",
    )
    assert call["document"]["title"] == "Tracing Everything"
    assert call["document"]["room"] == "Salt Palace | Level 1 | Hall DE"
    assert call["document"]["revision"] == 3
    assert len(call["document"]["embedding"]) == DIMENSIONS


def test_a_version_conflict_means_a_newer_revision_is_indexed_so_the_job_is_done():
    # DATA-1: a late job loses to the newer document and isn't retried.
    jobs, _, db = _indexable()

    indexed = process_outbox(db, _Es(index_error=_es_error(ConflictError, 409)), _Embedder(), now=lambda: NOW)

    assert indexed == 0
    assert (jobs[0].status, jobs[0].attempts) == (OutboxStatus.DONE, 0)


def test_one_failing_job_does_not_stop_the_batch():
    jobs, sessions, db = _indexable(2)
    es = _Es(index_error=RuntimeError("shard unavailable"), failing_ids={str(sessions[0].id)})

    indexed = process_outbox(db, es, _Embedder(), now=lambda: NOW)

    assert indexed == 1
    assert (jobs[0].status, jobs[0].attempts, jobs[0].last_error) == (OutboxStatus.PENDING, 1, "RuntimeError")
    assert jobs[1].status == OutboxStatus.DONE
    assert [call["id"] for call in es.indexed] == [str(sessions[1].id)]


def test_a_rejected_text_fails_only_its_own_job():
    jobs, sessions, db = _indexable(2)
    sessions[0].abstract = "an abstract the model rejects: POISON"
    embedder = _Embedder(rejects="POISON")

    indexed = process_outbox(db, _Es(), embedder, now=lambda: NOW)

    assert indexed == 1
    assert (jobs[0].status, jobs[0].attempts, jobs[0].last_error) == (OutboxStatus.PENDING, 1, "EmbeddingInputError")
    assert jobs[1].status == OutboxStatus.DONE


def test_the_openai_embedder_reports_a_rejected_input_as_an_input_error():
    def reject(**_):
        raise _openai_error(openai.BadRequestError, 400)

    client = SimpleNamespace(embeddings=SimpleNamespace(create=reject))

    with pytest.raises(EmbeddingInputError):
        OpenAIEmbedder(client).embed(["a very long abstract"])


def test_a_job_is_parked_as_failed_only_on_its_last_attempt():
    jobs, sessions, db = _indexable(2)
    for session in sessions:
        session.abstract = "an abstract the model rejects: POISON"
    jobs[0].attempts, jobs[1].attempts = MAX_ATTEMPTS - 2, MAX_ATTEMPTS - 1

    process_outbox(db, _Es(), _Embedder(rejects="POISON"), now=lambda: NOW)

    assert (jobs[0].attempts, jobs[0].status) == (MAX_ATTEMPTS - 1, OutboxStatus.PENDING)
    assert (jobs[1].attempts, jobs[1].status) == (MAX_ATTEMPTS, OutboxStatus.FAILED)


def test_the_backoff_doubles_and_is_capped_at_five_minutes():
    jobs, _, db = _indexable(2)
    jobs[0].attempts, jobs[1].attempts = 2, 8

    process_outbox(db, _Es(), _Embedder(RuntimeError("provider unavailable")), now=lambda: NOW)

    assert jobs[0].next_attempt_at == NOW + timedelta(seconds=8)  # 2 ** 3
    assert jobs[1].next_attempt_at == NOW + timedelta(minutes=5)  # 2 ** 9 = 512 s, capped


OUTAGES = {
    "openai-unreachable": (_Embedder(openai.APIConnectionError(request=OPENAI_REQUEST)), _Es()),
    "openai-bad-key-401": (_Embedder(_openai_error(openai.AuthenticationError, 401)), _Es()),
    "openai-quota-429": (_Embedder(_openai_error(openai.RateLimitError, 429)), _Es()),
    "openai-server-error-500": (_Embedder(_openai_error(openai.InternalServerError, 500)), _Es()),
    "unrecognized-error": (_Embedder(RuntimeError("provider unavailable")), _Es()),
    "elasticsearch-unreachable": (_Embedder(), _Es(index_error=EsConnectionError("connection refused"))),
    "elasticsearch-throttling-429": (_Embedder(), _Es(index_error=_es_error(ApiError, 429))),
    "elasticsearch-unavailable-503": (_Embedder(), _Es(index_error=_es_error(ApiError, 503))),
}


@pytest.mark.parametrize("embedder, es", OUTAGES.values(), ids=OUTAGES.keys())
def test_an_outage_never_parks_a_job_and_its_session_is_indexed_once_the_outage_ends(embedder, es):
    # Nothing re-enqueues a FAILED job (an unchanged session enqueues nothing on re-import). If an outage parked jobs,
    # their sessions would stay out of search for good, however soon the key or quota was fixed.
    jobs, sessions, db = _indexable()
    clock = NOW
    while clock - NOW < timedelta(minutes=30):
        clock = jobs[0].next_attempt_at
        process_outbox(db, es, embedder, now=lambda at=clock: at)
        assert jobs[0].status == OutboxStatus.PENDING
    assert jobs[0].attempts > MAX_ATTEMPTS
    assert jobs[0].next_attempt_at - clock == timedelta(minutes=5)  # retried every 5 minutes, not given up on

    healthy = _Es()
    indexed = process_outbox(db, healthy, _Embedder(), now=lambda: jobs[0].next_attempt_at)

    assert indexed == 1
    assert jobs[0].status == OutboxStatus.DONE
    assert [call["id"] for call in healthy.indexed] == [str(sessions[0].id)]


def _rejected_text(jobs, sessions):
    sessions[0].abstract = "an abstract the model rejects: POISON"
    return _Embedder(rejects="POISON"), _Es()


def _rejected_document(jobs, sessions):
    return _Embedder(), _Es(index_error=_es_error(BadRequestError, 400))


def _unknown_kind(jobs, sessions):
    jobs[0].kind = "reindex-everything"
    return _Embedder(), _Es()


@pytest.mark.parametrize("own_failure", [_rejected_text, _rejected_document, _unknown_kind])
def test_a_job_that_fails_for_its_own_reason_is_parked_after_max_attempts(own_failure):
    jobs, sessions, db = _indexable()
    embedder, es = own_failure(jobs, sessions)

    while jobs[0].status == OutboxStatus.PENDING:
        process_outbox(db, es, embedder, now=lambda at=jobs[0].next_attempt_at: at)

    assert (jobs[0].status, jobs[0].attempts) == (OutboxStatus.FAILED, MAX_ATTEMPTS)


def test_a_job_failing_through_a_long_outage_still_backs_off_five_minutes():
    # A day-long outage is about 300 failures; 2 ** attempts seconds must not overflow timedelta.
    jobs, _, db = _indexable()
    jobs[0].attempts = 1000

    process_outbox(db, _Es(), _Embedder(RuntimeError("provider unavailable")), now=lambda: NOW)

    assert (jobs[0].status, jobs[0].attempts) == (OutboxStatus.PENDING, 1001)
    assert jobs[0].next_attempt_at == NOW + timedelta(minutes=5)
