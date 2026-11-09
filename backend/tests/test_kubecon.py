import copy
import uuid
from datetime import UTC, datetime

from conferenceops.indexing import DIMENSIONS, MAPPING, embedding_text
from conferenceops.kubecon import ACCESS_FACTS, ACCESS_PAGE, SLUG, import_kubecon, parse_feed, venue_instant
from conferenceops.models import Conference, Level, OutboxJob, Room, Session, SessionSpeaker, Speaker

FEED = {
    "categories": [
        {"title": "Audience Level", "items": [{"id": 1, "name": "Beginner"}, {"id": 2, "name": "Any Level"}]},
        {"title": "SubTopic", "items": [{"id": 3, "name": "Observability"}]},
        {"title": "CNCF Projects", "items": [{"id": 4, "name": "OpenTelemetry"}]},
    ],
    "questions": [{"id": 10, "question": "Presentation Slides"}, {"id": 11, "question": "Company"}],
    "rooms": [{"id": 87691, "name": "Salt Palace | Level 1 | Hall DE"}],
    "speakers": [{"id": "sp-1", "fullName": "Real Speaker", "firstName": "Real", "lastName": "Speaker"}],
    "sessions": [
        {
            "id": "100",
            "title": " Tracing Everything ",
            "description": "OpenTelemetry for beginners.",
            "startsAt": "2026-11-10T10:30:00",
            "endsAt": "2026-11-10T11:00:00",
            "isServiceSession": False,
            "categoryItems": [1, 3, 4],
            "questionAnswers": [
                {"questionId": 10, "answerValue": "https://example.org/slides.pdf"},
                {"questionId": 11, "answerValue": "https://example.org/not-a-material"},
            ],
            "roomId": 87691,
            "speakers": ["sp-1"],
        },
        {
            "id": "101",
            "title": "Lunch",
            "description": None,
            "startsAt": "2026-11-10T12:00:00",
            "endsAt": "2026-11-10T13:00:00",
            "isServiceSession": True,
            "categoryItems": [2],
            "questionAnswers": [{"questionId": 10, "answerValue": "javascript:alert(1)"}],
            "roomId": None,
            "speakers": [],
        },
    ],
}


def test_venue_local_times_become_utc_instants_through_the_iana_zone():
    # TIME-1: Nov 10 is after the DST change (MST, UTC-7); a fixed -6 offset would be wrong.
    assert venue_instant("2026-11-10T10:30:00") == datetime(2026, 11, 10, 17, 30, tzinfo=UTC)
    # Before the change, in October, the same zone is UTC-6.
    assert venue_instant("2026-10-06T10:30:00") == datetime(2026, 10, 6, 16, 30, tzinfo=UTC)


def test_sessions_keep_the_feed_facts_and_nothing_invented():
    parsed = parse_feed(FEED)
    talk, lunch = parsed.sessions
    assert talk.title == "Tracing Everything"
    assert talk.level is Level.BEGINNER
    assert talk.topics == ["Observability", "OpenTelemetry"]
    assert talk.room_external_id == "87691"
    assert talk.speaker_external_ids == ["sp-1"]
    assert talk.materials == {"slides": "https://example.org/slides.pdf"}  # only the speaker's own material links
    assert lunch.is_service and lunch.level is Level.ALL
    assert lunch.materials == {}  # non-https values are never shown as links
    assert parsed.rooms == {"87691": "Salt Palace | Level 1 | Hall DE"}


def test_parsing_twice_gives_the_same_result_so_reimports_are_free():
    assert parse_feed(FEED) == parse_feed(FEED)


def test_every_access_fact_comes_from_the_official_page_and_none_claims_step_free():
    # ACCESS-1: facts are only what the Inclusion + Accessibility page publishes, each with its link.
    assert ACCESS_PAGE.startswith("https://events.linuxfoundation.org/")
    texts = " ".join(text for _, text, _ in ACCESS_FACTS).lower()
    assert "step-free" not in texts and "wheelchair accessible" not in texts
    assert any("wordly" in text.lower() for _, text, _ in ACCESS_FACTS)


def test_search_documents_carry_a_1536_dimension_cosine_vector_and_no_private_fields():
    props = MAPPING["properties"]
    assert props["embedding"] == {"type": "dense_vector", "dims": DIMENSIONS, "index": True, "similarity": "cosine"}
    assert DIMENSIONS == 1536
    assert not {"needs_step_free", "wants_captions", "excluded_levels", "query"} & set(props)


def test_the_embedded_text_is_the_title_and_abstract():
    session = Session(title="Tracing Everything", abstract="OpenTelemetry for beginners.")
    assert embedding_text(session) == "Tracing Everything\n\nOpenTelemetry for beginners."


class _Scalars:
    def __init__(self, rows):
        self.rows = list(rows)

    def all(self):
        return self.rows

    def one_or_none(self):
        assert len(self.rows) <= 1
        return self.rows[0] if self.rows else None

    def scalar_one_or_none(self):
        return self.one_or_none()

    def __iter__(self):
        return iter(self.rows)


class _ImportDb:
    def __init__(self, conference, rooms=(), speakers=(), sessions=(), session_speakers=()):
        self.conference = conference
        self.rooms = list(rooms)
        self.speakers = list(speakers)
        self.sessions = list(sessions)
        self.session_speakers = list(session_speakers)
        self.outbox = []

    def scalars(self, statement):
        entity = statement.column_descriptions[0]["entity"]
        rows = {
            Conference: [self.conference],
            Room: self.rooms,
            Speaker: self.speakers,
            Session: self.sessions,
            SessionSpeaker: self.session_speakers,
        }[entity]
        for criterion in statement._where_criteria:
            rows = [row for row in rows if getattr(row, criterion.left.key) == criterion.right.value]
        return _Scalars(rows)

    def add(self, row):
        if isinstance(row, OutboxJob):
            self.outbox.append(row)
        elif isinstance(row, SessionSpeaker):
            self.session_speakers.append(row)
        elif isinstance(row, Speaker):
            self.speakers.append(row)
        elif isinstance(row, Room):
            self.rooms.append(row)
        elif isinstance(row, Session):
            self.sessions.append(row)

    def delete(self, row):
        self.sessions.remove(row)

    def execute(self, statement):
        if statement.table.name == "session_speakers":
            self.session_speakers.clear()
        return _Scalars([])

    def flush(self):
        for session in self.sessions:
            if session.id is None:
                session.id = uuid.uuid4()


def _import_session(conference_id, room_id):
    return Session(
        id=uuid.uuid4(),
        conference_id=conference_id,
        external_id="100",
        title="Tracing Everything",
        abstract="OpenTelemetry for beginners.",
        starts_at=venue_instant("2026-11-10T10:30:00"),
        ends_at=venue_instant("2026-11-10T11:00:00"),
        level=Level.BEGINNER,
        topics=["Observability", "OpenTelemetry"],
        room_id=room_id,
        is_service=False,
        materials={"slides": "https://example.org/slides.pdf"},
        revision=3,
    )


def test_import_reconciles_speakers_even_when_session_fields_are_unchanged():
    conference_id, room_id, speaker_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    conference = Conference(id=conference_id, slug=SLUG)
    room = Room(id=room_id, conference_id=conference_id, external_id="87691", name="Salt Palace | Level 1 | Hall DE")
    speaker = Speaker(id=speaker_id, conference_id=conference_id, external_id="sp-1", name="Old Speaker Name")
    session = _import_session(conference_id, room_id)
    db = _ImportDb(conference, [room], [speaker], [session])
    feed = copy.deepcopy(FEED)
    feed["sessions"] = feed["sessions"][:1]

    result = import_kubecon(db, feed)

    assert result["changed"] == 0
    assert speaker.name == "Real Speaker"
    assert [(link.session_id, link.speaker_id) for link in db.session_speakers] == [(session.id, speaker_id)]

    feed["sessions"][0]["speakers"] = []
    import_kubecon(db, feed)
    assert db.session_speakers == []
    assert session.revision == 3


def test_import_deletes_sessions_absent_from_the_feed_and_enqueues_tombstones():
    conference_id = uuid.uuid4()
    conference = Conference(id=conference_id, slug=SLUG)
    stale = [
        Session(id=uuid.uuid4(), conference_id=conference_id, external_id=external_id, revision=revision)
        for external_id, revision in (("100", 4), ("101", 2))
    ]
    db = _ImportDb(conference, sessions=stale)
    feed = copy.deepcopy(FEED)
    feed["sessions"] = []

    counts = import_kubecon(db, feed)

    assert counts["sessions"] == 0
    assert db.sessions == []
    assert {(job.entity_id, job.entity_revision, job.kind) for job in db.outbox} == {
        (row.id, row.revision + 1, "delete") for row in stale
    }
