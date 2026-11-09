"""The publication boundary against a real PostgreSQL: the seed's transcripts, the organizer action and the
post-retrieval validation that only lets published chunks through (TRANSCRIPT-1, DATA-1, AUTHZ-1)."""

import os

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select, text

from conferenceops.ask import PostgresPublishedPassages
from conferenceops.cloudbridge import load_cloudbridge, profile_id
from conferenceops.db import transaction
from conferenceops.models import OutboxJob, Role, Session, Transcript, TranscriptStatus
from conferenceops.transcripts import LABEL, PostgresTranscriptStore, chunk_cues, chunk_id

pytestmark = pytest.mark.integration

BACKEND = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
CONFERENCE = "cloudbridge-summit-2026"


@pytest.fixture(scope="module")
def engine():
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL is not set")
    cfg = Config(os.path.join(BACKEND, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(BACKEND, "migrations"))
    command.upgrade(cfg, "head")
    return create_engine(url)


@pytest.fixture
def seeded(engine):
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE outbox_jobs, conferences, profiles CASCADE"))
    with transaction(engine) as db:
        load_cloudbridge(db)
    return engine


def _transcript(engine, session_key: str) -> Transcript:
    with transaction(engine) as db:
        return db.scalars(
            select(Transcript)
            .join(Session, Session.id == Transcript.session_id)
            .where(Session.external_id == session_key)
        ).one()


def _jobs(engine, transcript_id) -> list[int]:
    with transaction(engine) as db:
        return sorted(db.scalars(select(OutboxJob.entity_revision).where(OutboxJob.entity_id == transcript_id)))


def test_the_seed_creates_the_three_clips_with_clip_3_processing_and_a_job_each(seeded):
    one, two, three = (_transcript(seeded, key) for key in ("2", "10", "18"))
    assert (one.status, two.status, three.status) == (
        TranscriptStatus.PUBLISHED,
        TranscriptStatus.PUBLISHED,
        TranscriptStatus.PROCESSING,
    )
    assert (one.speaker_name, three.speaker_name) == ("Daniel Kowalczyk", "Ananya Iyer")
    assert one.is_simulated and one.text.startswith(one.cues[0]["text"])
    assert one.media_url == "/media/cloudbridge/clip-1.mp4" and one.captions_url == "/media/cloudbridge/clip-1.vtt"
    for transcript in (one, two, three):
        assert transcript.revision == 1
        assert _jobs(seeded, transcript.id) == [1]  # the projection commits with the row (DATA-1)


def test_the_replay_view_hides_a_processing_transcript_s_text_and_shows_a_published_one(seeded):
    store = PostgresTranscriptStore()
    three = store.replay(_transcript(seeded, "18").session_id)
    assert three.transcript.status == TranscriptStatus.PROCESSING
    assert three.transcript.cues == [] and three.transcript.text is None
    assert three.media.label == LABEL and three.session.is_fictional and three.session.speakers == ["Ananya Iyer"]
    one = store.replay(_transcript(seeded, "2").session_id)
    assert one.transcript.status == TranscriptStatus.PUBLISHED and len(one.transcript.cues) == 17
    assert one.session.starts_at_venue == "Tue Oct 6, 10:00 AM MDT"


def test_roles_come_from_postgres_not_the_client(seeded):
    store = PostgresTranscriptStore()
    assert store.role_of(profile_id("P7")) == Role.ORGANIZER  # Sam Rivera
    assert store.role_of(profile_id("P1")) == Role.ATTENDEE  # Aisha Rahman
    assert store.role_of(profile_id("nobody")) is None


def test_publishing_clip_3_is_a_new_revision_with_its_job_and_unpublishing_another(seeded):
    store = PostgresTranscriptStore()
    three = _transcript(seeded, "18")

    change = store.set_status(three.session_id, CONFERENCE, TranscriptStatus.PUBLISHED)

    assert (change.status, change.revision, change.changed) == (TranscriptStatus.PUBLISHED, 2, True)
    assert _jobs(seeded, three.id) == [1, 2]
    again = store.set_status(three.session_id, CONFERENCE, TranscriptStatus.PUBLISHED)
    assert (again.revision, again.changed) == (2, False)
    assert _jobs(seeded, three.id) == [1, 2]  # nothing to project
    back = store.set_status(three.session_id, CONFERENCE, TranscriptStatus.PROCESSING)
    assert (back.status, back.revision) == (TranscriptStatus.PROCESSING, 3)
    assert _jobs(seeded, three.id) == [1, 2, 3]
    # Another conference, or a session without a transcript: not found (AUTHZ-1), nothing changed.
    assert store.set_status(three.session_id, "kubecon-na-2026", TranscriptStatus.PUBLISHED) is None
    one = _transcript(seeded, "2")
    assert store.set_status(one.session_id, CONFERENCE, TranscriptStatus.PUBLISHED).changed is False
    assert _transcript(seeded, "18").revision == 3


def test_only_chunks_of_a_published_transcript_at_their_revision_pass_validation(seeded):
    one, three = _transcript(seeded, "2"), _transcript(seeded, "18")
    valid = chunk_id(one.id, 1, 0)
    wanted = [
        valid,
        chunk_id(one.id, 1, 99),  # no such chunk
        chunk_id(one.id, 7, 0),  # a revision that was never published
        chunk_id(three.id, 1, 0),  # processing: never cited (TRANSCRIPT-1)
        "garbage",
    ]

    found = PostgresPublishedPassages().load_published(wanted, CONFERENCE)

    assert list(found) == [valid]
    passage = found[valid]
    assert passage.text == chunk_cues(one.cues)[0].text  # rebuilt from the canonical cues, not the index
    assert (passage.speaker, passage.event, passage.is_fictional) == (
        "Daniel Kowalczyk",
        "CloudBridge Summit 2026",
        True,
    )
    assert PostgresPublishedPassages().load_published(wanted, "kubecon-na-2026") == {}
    # Publish clip 3: its revision-2 chunks become valid, its revision-1 ones never were.
    PostgresTranscriptStore().set_status(three.session_id, CONFERENCE, TranscriptStatus.PUBLISHED)
    after = PostgresPublishedPassages().load_published([chunk_id(three.id, 2, 0), chunk_id(three.id, 1, 0)], None)
    assert list(after) == [chunk_id(three.id, 2, 0)]


def test_a_reset_puts_clip_3_back_to_processing_as_a_new_revision_and_a_seed_leaves_it_published(seeded):
    three = _transcript(seeded, "18")
    PostgresTranscriptStore().set_status(three.session_id, CONFERENCE, TranscriptStatus.PUBLISHED)

    with transaction(seeded) as db:
        seed = load_cloudbridge(db)
    assert seed.written["transcripts"] == 0  # a deploy never unpublishes what an organizer published
    assert _transcript(seeded, "18").status == TranscriptStatus.PUBLISHED

    with transaction(seeded) as db:
        reset = load_cloudbridge(db, reset=True)
    assert reset.written["transcripts"] == 1
    three = _transcript(seeded, "18")
    assert (three.status, three.revision) == (TranscriptStatus.PROCESSING, 3)
    assert _jobs(seeded, three.id) == [1, 2, 3]  # the reset's revision is projected too: the chunks go (DATA-1)
