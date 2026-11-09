"""The transcript projection: only published text reaches Elasticsearch, and no late job brings removed text back."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.sql import operators

from conferenceops.indexing import DIMENSIONS, EmbeddingInputError
from conferenceops.models import Conference, OutboxJob, OutboxStatus, Session, Transcript, TranscriptStatus
from conferenceops.transcript_indexing import ALIAS, process_transcript_outbox
from conferenceops.transcripts import transcript_text

NOW = datetime(2026, 11, 10, tzinfo=UTC)
# Two cues of 50 words: over the 80-word chunk limit together, so the transcript becomes two chunks.
CUES = [
    {"start": 1.5, "end": 20.0, "text": " ".join(f"alpha{i}" for i in range(50))},
    {"start": 20.0, "end": 40.0, "text": " ".join(f"beta{i}" for i in range(50))},
]


class _Rows:
    def __init__(self, rows):
        self.rows = list(rows)

    def all(self):
        return self.rows

    def __iter__(self):
        return iter(self.rows)


def _matches(row, criterion) -> bool:
    actual, expected = getattr(row, criterion.left.key), criterion.right.value
    if criterion.operator is operators.in_op:
        return actual in expected
    return criterion.operator(actual, expected)


class _Db:
    """The rows the projection reads, filtered by each query's WHERE clause like PostgreSQL would."""

    def __init__(self, jobs=(), transcripts=(), sessions=(), conferences=()):
        self.jobs = list(jobs)
        self.transcripts = list(transcripts)
        self.sessions = list(sessions)
        self.conferences = list(conferences)

    def scalars(self, statement):
        entity = statement.column_descriptions[0]["entity"]
        rows = {
            OutboxJob: self.jobs,
            Transcript: self.transcripts,
            Session: self.sessions,
            Conference: self.conferences,
        }[entity]
        return _Rows(row for row in rows if all(_matches(row, c) for c in statement._where_criteria))


class _Es:
    def __init__(self, index_error=None):
        self.index_error = index_error
        self.indexed = []
        self.deleted = []

    def index(self, **kwargs):
        if self.index_error:
            raise self.index_error
        self.indexed.append(kwargs)

    def delete_by_query(self, **kwargs):
        self.deleted.append(kwargs)


class _Embedder:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def embed(self, texts):
        self.calls.append(list(texts))
        if self.error:
            raise self.error
        return [[0.0] * DIMENSIONS for _ in texts]


def _job(entity_id, revision, entity_type="transcript", attempts=0):
    return OutboxJob(
        id=uuid.uuid4(),
        entity_type=entity_type,
        entity_id=entity_id,
        entity_revision=revision,
        kind="index",
        status=OutboxStatus.PENDING,
        attempts=attempts,
        next_attempt_at=NOW,
        created_at=NOW,
    )


def _world(status=TranscriptStatus.PUBLISHED, revision=2):
    conference = Conference(id=uuid.uuid4(), slug="cloudbridge-summit-2026", name="CloudBridge", is_fictional=True)
    session = Session(
        id=uuid.uuid4(),
        conference_id=conference.id,
        title="AI SRE in Practice",
        starts_at=NOW,
        ends_at=NOW + timedelta(minutes=45),
        topics=[],
        is_service=False,
        revision=1,
    )
    transcript = Transcript(
        id=uuid.uuid4(),
        session_id=session.id,
        speaker_name="Daniel Kowalczyk",
        is_simulated=True,
        media_url="/media/cloudbridge/clip-1.mp4",
        captions_url="/media/cloudbridge/clip-1.vtt",
        duration_seconds=68.0,
        text=transcript_text(CUES),
        cues=CUES,
        status=status,
        revision=revision,
    )
    return transcript, _Db([], [transcript], [session], [conference])


def _only_filter(call):
    return call["query"]["bool"]["filter"]


def test_a_published_transcript_is_indexed_as_chunks_of_its_revision_and_older_revisions_are_dropped():
    transcript, db = _world(revision=2)
    job = _job(transcript.id, 2)
    db.jobs.append(job)
    es, embedder = _Es(), _Embedder()

    indexed = process_transcript_outbox(db, es, embedder, now=lambda: NOW)

    assert indexed == 1
    assert [call["id"] for call in es.indexed] == [f"{transcript.id}:2:0", f"{transcript.id}:2:1"]
    assert {(call["version"], call["version_type"], call["index"]) for call in es.indexed} == {
        (2, "external_gte", ALIAS)
    }
    first = es.indexed[0]["document"]
    assert (first["status"], first["conference"], first["revision"], first["chunk"]) == (
        "published",
        "cloudbridge-summit-2026",
        2,
        0,
    )
    assert (first["start_seconds"], first["end_seconds"], first["speaker"]) == (1.5, 20.0, "Daniel Kowalczyk")
    assert first["text"].startswith("alpha0 alpha1") and first["is_simulated"] is True
    assert embedder.calls == [[CUES[0]["text"], CUES[1]["text"]]]  # one batched call for the transcript's chunks
    # The chunks of every older revision go, so a correction never leaves superseded text searchable.
    [deleted] = es.deleted
    assert _only_filter(deleted) == [
        {"term": {"transcript_id": str(transcript.id)}},
        {"range": {"revision": {"lt": 2}}},
    ]
    assert job.status == OutboxStatus.DONE


def test_an_unpublished_transcript_has_every_chunk_removed_and_nothing_embedded_or_indexed():
    transcript, db = _world(status=TranscriptStatus.PROCESSING, revision=3)
    job = _job(transcript.id, 3)
    db.jobs.append(job)
    es, embedder = _Es(), _Embedder()

    assert process_transcript_outbox(db, es, embedder, now=lambda: NOW) == 0

    assert es.indexed == [] and embedder.calls == []  # processing text never reaches the model or the index
    [deleted] = es.deleted
    assert _only_filter(deleted) == [{"term": {"transcript_id": str(transcript.id)}}]
    assert job.status == OutboxStatus.DONE


def test_a_transcript_whose_row_is_gone_has_its_chunks_removed():
    gone = uuid.uuid4()
    job = _job(gone, 2)
    es = _Es()

    process_transcript_outbox(_Db([job]), es, _Embedder(), now=lambda: NOW)

    assert [_only_filter(call) for call in es.deleted] == [[{"term": {"transcript_id": str(gone)}}]]
    assert job.status == OutboxStatus.DONE


def test_a_late_older_job_projects_the_current_state_so_it_never_resurrects_unpublished_text():
    # DATA-1: the transcript was published at revision 1 and unpublished at revision 2. A job for revision 1 that
    # runs late finds the row processing, and deletes instead of indexing the old text.
    transcript, db = _world(status=TranscriptStatus.PROCESSING, revision=2)
    late = _job(transcript.id, 1)
    db.jobs.append(late)
    es, embedder = _Es(), _Embedder()

    process_transcript_outbox(db, es, embedder, now=lambda: NOW)

    assert es.indexed == [] and embedder.calls == []
    assert len(es.deleted) == 1 and late.status == OutboxStatus.DONE


def test_a_late_older_job_for_a_republished_transcript_indexes_the_current_revision_only():
    transcript, db = _world(status=TranscriptStatus.PUBLISHED, revision=3)
    late = _job(transcript.id, 1)
    db.jobs.append(late)
    es = _Es()

    process_transcript_outbox(db, es, _Embedder(), now=lambda: NOW)

    assert {call["version"] for call in es.indexed} == {3}  # never revision 1's documents
    assert all(call["id"].startswith(f"{transcript.id}:3:") for call in es.indexed)


def test_an_embedding_failure_backs_the_job_off_without_indexing_anything():
    transcript, db = _world()
    job = _job(transcript.id, 2)
    db.jobs.append(job)
    es = _Es()

    process_transcript_outbox(db, es, _Embedder(RuntimeError("provider unavailable")), now=lambda: NOW)

    assert es.indexed == [] and es.deleted == []
    assert (job.status, job.attempts, job.last_error) == (OutboxStatus.PENDING, 1, "RuntimeError")
    assert job.next_attempt_at == NOW + timedelta(seconds=2)


def test_a_rejected_text_fails_only_that_transcript_in_the_batch():
    rejected, db = _world()
    fine, other_db = _world()
    fine.cues = [{"start": 1.5, "end": 9.0, "text": "gamma words the model accepts"}]
    db.transcripts += other_db.transcripts
    db.sessions += other_db.sessions
    db.conferences += other_db.conferences
    jobs = [_job(rejected.id, 2), _job(fine.id, 2)]
    db.jobs += jobs
    poison = rejected.cues[0]["text"]

    class Picky(_Embedder):
        def embed(self, texts):
            self.calls.append(list(texts))
            if any(text == poison for text in texts):
                raise EmbeddingInputError("rejected")
            return [[0.0] * DIMENSIONS for _ in texts]

    es, embedder = _Es(), Picky()
    process_transcript_outbox(db, es, embedder, now=lambda: NOW)

    assert len(embedder.calls) == 3  # the whole batch, then each transcript on its own
    assert (jobs[0].status, jobs[0].last_error) == (OutboxStatus.PENDING, "EmbeddingInputError")
    assert jobs[1].status == OutboxStatus.DONE
    assert [call["id"] for call in es.indexed] == [f"{fine.id}:2:0"]


def test_only_transcript_jobs_are_leased():
    transcript, db = _world()
    session_job = _job(transcript.session_id, 1, entity_type="session")
    db.jobs.append(session_job)
    es = _Es()

    assert process_transcript_outbox(db, es, _Embedder(), now=lambda: NOW) == 0
    assert session_job.status == OutboxStatus.PENDING and es.indexed == [] and es.deleted == []
