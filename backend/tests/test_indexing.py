import uuid
from datetime import UTC, datetime, timedelta

from conferenceops.indexing import ALIAS, process_outbox
from conferenceops.models import Conference, OutboxJob, OutboxStatus, Session

NOW = datetime(2026, 11, 10, tzinfo=UTC)


class _Rows:
    def __init__(self, rows):
        self.rows = list(rows)

    def all(self):
        return self.rows

    def __iter__(self):
        return iter(self.rows)


class _Db:
    def __init__(self, jobs=(), sessions=(), conferences=()):
        self.jobs = list(jobs)
        self.sessions = list(sessions)
        self.conferences = list(conferences)

    def scalars(self, statement):
        entity = statement.column_descriptions[0]["entity"]
        return _Rows(
            {
                OutboxJob: self.jobs,
                Session: self.sessions,
                Conference: self.conferences,
            }.get(entity, [])
        )


class _Es:
    def __init__(self, delete_error=None, index_error=None):
        self.delete_error = delete_error
        self.index_error = index_error
        self.deleted = []
        self.indexed = []

    def delete(self, **kwargs):
        if self.delete_error:
            raise self.delete_error
        self.deleted.append(kwargs)

    def index(self, **kwargs):
        if self.index_error:
            raise self.index_error
        self.indexed.append(kwargs)


class _Embedder:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def embed(self, texts):
        self.calls.append(list(texts))
        if self.error:
            raise self.error
        return [[0.0] * 1536 for _ in texts]


def _job(*, kind="index", entity_type="session", entity_id=None):
    return OutboxJob(
        id=uuid.uuid4(),
        entity_type=entity_type,
        entity_id=entity_id or uuid.uuid4(),
        entity_revision=1,
        kind=kind,
        status=OutboxStatus.PENDING,
        attempts=0,
        next_attempt_at=NOW,
        created_at=NOW,
    )


def _session(session_id, conference_id):
    return Session(
        id=session_id,
        conference_id=conference_id,
        title="A conference session",
        abstract="Details",
        starts_at=NOW,
        ends_at=NOW + timedelta(hours=1),
        topics=[],
        is_service=False,
        revision=1,
    )


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


def test_unsupported_outbox_jobs_are_failed_not_marked_done():
    job = _job(entity_type="speaker")

    process_outbox(_Db([job]), _Es(), _Embedder(), now=lambda: NOW)

    assert job.status == OutboxStatus.PENDING
    assert job.attempts == 1
    assert job.last_error == "ValueError"
