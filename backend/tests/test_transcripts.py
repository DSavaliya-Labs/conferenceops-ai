"""The publication boundary at core-api: chunks, status changes, the replay view and the organizer action."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from conferenceops.core_app.main import app
from conferenceops.models import Conference, Role, Session, Transcript, TranscriptStatus
from conferenceops.transcripts import (
    LABEL,
    Chunk,
    StatusChange,
    change_status,
    chunk_cues,
    chunk_id,
    get_transcript_store,
    parse_chunk_id,
    replay_view,
    transcript_text,
)

CUES = [
    {"start": 1.5, "end": 4.0, "text": "one two three"},
    {"start": 4.0, "end": 7.0, "text": "four five"},
    {"start": 7.0, "end": 9.5, "text": "six seven eight nine"},
]
CONFERENCE = "cloudbridge-summit-2026"
SESSION_ID = uuid.uuid4()
ORGANIZER, ATTENDEE, UNKNOWN = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


def _transcript(status=TranscriptStatus.PUBLISHED, revision=1, session_id=SESSION_ID) -> Transcript:
    return Transcript(
        id=uuid.uuid4(),
        session_id=session_id,
        speaker_name="Ananya Iyer",
        is_simulated=True,
        media_url="/media/cloudbridge/clip-3.mp4",
        captions_url="/media/cloudbridge/clip-3.vtt",
        duration_seconds=71.0,
        text=transcript_text(CUES),
        cues=CUES,
        status=status,
        revision=revision,
    )


def _session_rows():
    conference = Conference(id=uuid.uuid4(), slug=CONFERENCE, name="CloudBridge Summit 2026", is_fictional=True)
    starts = datetime(2026, 10, 8, 16, 0, tzinfo=UTC)
    session = Session(
        id=SESSION_ID,
        conference_id=conference.id,
        title="Tracing LLM Calls in Production",
        starts_at=starts,
        ends_at=starts + timedelta(minutes=45),
        topics=[],
        is_service=False,
        revision=1,
    )
    return session, conference


def test_chunks_group_whole_cues_up_to_the_word_limit_and_keep_their_replay_timings():
    assert chunk_cues(CUES, max_words=5) == [
        Chunk(0, 1.5, 7.0, "one two three four five"),
        Chunk(1, 7.0, 9.5, "six seven eight nine"),
    ]
    # A cue is never split, even when it alone is over the limit; the cut is deterministic.
    assert [c.text for c in chunk_cues(CUES, max_words=1)] == ["one two three", "four five", "six seven eight nine"]
    assert chunk_cues(CUES, max_words=1) == chunk_cues(CUES, max_words=1)
    assert chunk_cues([]) == []
    assert transcript_text(CUES) == "one two three four five six seven eight nine"


def test_chunk_ids_carry_the_transcript_its_revision_and_the_chunk_position():
    tid = uuid.uuid4()
    assert chunk_id(tid, 3, 1) == f"{tid}:3:1"
    assert parse_chunk_id(f"{tid}:3:1") == (tid, 3, 1)
    for bad in ("not-a-chunk", f"{tid}:x:1", str(tid), f"{tid}:1", f"{tid}:1:2:3"):
        assert parse_chunk_id(bad) is None, bad


class FakeDb:
    def __init__(self):
        self.added = []

    def add(self, row):
        self.added.append(row)


def test_a_status_change_is_a_new_revision_with_its_outbox_job_in_the_same_transaction():
    db = FakeDb()
    transcript = _transcript(status=TranscriptStatus.PROCESSING, revision=2)

    assert change_status(db, transcript, TranscriptStatus.PUBLISHED) is True

    assert (transcript.status, transcript.revision) == (TranscriptStatus.PUBLISHED, 3)
    [job] = db.added
    assert (job.entity_type, job.entity_id, job.entity_revision, job.kind) == ("transcript", transcript.id, 3, "index")
    # Already published: nothing changes and nothing is enqueued.
    assert change_status(db, transcript, TranscriptStatus.PUBLISHED) is False
    assert (transcript.revision, len(db.added)) == (3, 1)
    # Unpublishing is a new revision too, so no older job can bring the text back (DATA-1).
    assert change_status(db, transcript, TranscriptStatus.PROCESSING) is True
    assert (transcript.status, transcript.revision, db.added[1].entity_revision) == (TranscriptStatus.PROCESSING, 4, 4)


def test_a_published_transcript_is_shown_with_its_cues_its_text_and_the_simulated_label():
    session, conference = _session_rows()
    view = replay_view(_transcript(), session, conference, ["Ananya Iyer"])
    assert view.transcript.status == TranscriptStatus.PUBLISHED
    assert view.transcript.text == "one two three four five six seven eight nine"
    assert [(c.start, c.text) for c in view.transcript.cues] == [
        (1.5, "one two three"),
        (4.0, "four five"),
        (7.0, "six seven eight nine"),
    ]
    assert view.media.label == view.transcript.label == LABEL  # SPEAKER-1
    assert (view.media.url, view.media.captions_url) == (
        "/media/cloudbridge/clip-3.mp4",
        "/media/cloudbridge/clip-3.vtt",
    )
    assert (view.session.event, view.session.is_fictional, view.session.speakers) == (
        "CloudBridge Summit 2026",
        True,
        ["Ananya Iyer"],
    )
    assert view.session.starts_at_venue == "Thu Oct 8, 10:00 AM MDT"  # TIME-1: Denver, daylight time in October


def test_a_processing_transcript_shows_the_replay_but_not_a_word_of_its_text():
    session, conference = _session_rows()
    view = replay_view(
        _transcript(status=TranscriptStatus.PROCESSING, revision=4), session, conference, ["Ananya Iyer"]
    )
    assert (view.transcript.status, view.transcript.revision) == (TranscriptStatus.PROCESSING, 4)
    assert view.transcript.cues == [] and view.transcript.text is None
    assert view.media.url == "/media/cloudbridge/clip-3.mp4"  # the replay plays while processing (PLAN §9.1)
    assert "one two three" not in view.model_dump_json()


class FakeStore:
    """What the routes need, without PostgreSQL: one session with a transcript, and three personas."""

    def __init__(self):
        self.status = TranscriptStatus.PROCESSING
        self.revision = 1
        self.calls = []

    def replay(self, session_id):
        if session_id != SESSION_ID:
            return None
        session, conference = _session_rows()
        return replay_view(
            _transcript(status=self.status, revision=self.revision), session, conference, ["Ananya Iyer"]
        )

    def role_of(self, profile_id):
        return {ORGANIZER: Role.ORGANIZER, ATTENDEE: Role.ATTENDEE}.get(profile_id)

    def set_status(self, session_id, conference, status):
        self.calls.append((session_id, conference, status))
        if session_id != SESSION_ID or conference != CONFERENCE:
            return None
        changed = status != self.status
        if changed:
            self.status, self.revision = status, self.revision + 1
        return StatusChange(session_id, self.status, self.revision, changed)


@pytest.fixture
def api():
    store = FakeStore()
    app.dependency_overrides[get_transcript_store] = lambda: store
    try:
        yield TestClient(app), store
    finally:
        app.dependency_overrides.clear()


def _publish(client, session_id=SESSION_ID, conference=CONFERENCE, action="publish", **headers):
    return client.post(
        f"/api/v1/ops/transcripts/{session_id}/{action}", json={"conference": conference}, headers=headers
    )


def test_the_replay_endpoint_answers_in_camel_case_and_404s_a_session_without_a_replay(api):
    client, _ = api
    body = client.get(f"/api/v1/sessions/{SESSION_ID}/replay").json()
    assert body["media"]["captionsUrl"] == "/media/cloudbridge/clip-3.vtt"
    assert body["transcript"] == {"status": "processing", "revision": 1, "label": LABEL, "cues": [], "text": None}
    assert body["session"]["isFictional"] is True
    assert client.get(f"/api/v1/sessions/{uuid.uuid4()}/replay").status_code == 404


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"X-Demo-Profile-ID": "not-a-uuid"},
        {"X-Demo-Profile-ID": str(UNKNOWN)},
        {"X-Demo-Profile-ID": str(ATTENDEE)},
        {"X-Demo-Profile-ID": str(ATTENDEE), "X-Demo-Role": "organizer"},  # a client-sent role counts for nothing
    ],
    ids=["no persona", "malformed", "unknown", "attendee", "attendee claiming a role"],
)
def test_publishing_needs_an_organizer_persona_resolved_on_the_server(api, headers):
    client, store = api
    for action in ("publish", "unpublish"):
        assert _publish(client, action=action, **headers).status_code == 403, action  # AUTHZ-1
    assert store.calls == []  # nothing was attempted, let alone changed


def test_an_organizer_publishes_and_the_text_is_searchable_only_once_the_worker_projects_it(api):
    client, store = api
    organizer = {"X-Demo-Profile-ID": str(ORGANIZER)}

    first = _publish(client, **organizer)

    assert first.status_code == 200
    body = first.json()
    assert (body["sessionId"], body["status"], body["revision"], body["changed"]) == (
        str(SESSION_ID),
        "published",
        2,
        True,
    )
    assert body["note"].startswith("Queued")  # not "searchable": that takes the outbox job (TRANSCRIPT-1)
    again = _publish(client, **organizer).json()
    assert (again["revision"], again["changed"]) == (2, False)
    back = _publish(client, action="unpublish", **organizer).json()
    assert (back["status"], back["revision"], back["changed"]) == ("processing", 3, True)
    assert client.get(f"/api/v1/sessions/{SESSION_ID}/replay").json()["transcript"]["text"] is None


def test_a_session_in_another_conference_or_without_a_transcript_is_404_not_403(api):
    client, _ = api
    organizer = {"X-Demo-Profile-ID": str(ORGANIZER)}
    assert _publish(client, conference="kubecon-na-2026", **organizer).status_code == 404  # AUTHZ-1
    assert _publish(client, session_id=uuid.uuid4(), **organizer).status_code == 404
