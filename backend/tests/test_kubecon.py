import copy
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql import operators

from conferenceops.indexing import DIMENSIONS, MAPPING, embedding_text
from conferenceops.kubecon import (
    ACCESS_FACTS,
    ACCESS_PAGE,
    MAX_REMOVED_SESSIONS,
    SLUG,
    import_kubecon,
    parse_feed,
    published_speakers,
    venue_instant,
)
from conferenceops.models import (
    AccessFact,
    AccessFactKind,
    Conference,
    Level,
    OutboxJob,
    Room,
    Session,
    SessionSpeaker,
    Speaker,
)

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


def test_the_mobility_rental_fact_names_the_vendor_and_promises_no_availability():
    # ACCESS-1: the page says "you can rent them from Mobility City. Please contact them directly for rates and
    # availability." It doesn't say equipment is available.
    [text] = [text for kind, text, _ in ACCESS_FACTS if kind is AccessFactKind.MOBILITY_RENTAL]
    assert "Mobility City" in text
    assert "contact them directly for rates and availability" in text
    assert "is available" not in text


def test_a_speaker_name_display_override_keeps_only_the_speakers_it_names():
    # SPEAKER-1: "Welcome + Opening Remarks" (1302801) links six speakers, and the schedule names two.
    names = {"a": "Jonathan Bryce", "b": "Chris Aniszczyk", "c": "Rita Zhang", "d": "Ann Lee"}
    assert published_speakers(["a", "b", "c"], names, "Jonathan Bryce, Chris Aniszczyk") == ["a", "b"]
    assert published_speakers(["a", "b", "c"], names, None) == ["a", "b", "c"]  # no override: the feed's links
    assert published_speakers(["a"], names, "jonathan bryce") == ["a"]  # case doesn't matter
    assert published_speakers(["d"], names, "Joann Leeds") == []  # whole names only


def test_an_override_naming_none_of_the_linked_speakers_links_nobody():
    # 1370908 links Mike Swift, but its override reads "Peter Frank", who isn't a speaker in the feed. Nobody is
    # linked from the override text alone, and Mike Swift isn't shown for a session the schedule gives to someone else.
    feed = copy.deepcopy(FEED)
    feed["questions"].append({"id": 12, "question": "Speaker Name Display"})
    feed["sessions"][0]["questionAnswers"].append({"questionId": 12, "answerValue": "Peter Frank"})

    talk, _ = parse_feed(feed).sessions

    assert talk.speaker_external_ids == []
    assert talk.materials == {"slides": "https://example.org/slides.pdf"}  # the other answers still count


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

    def one(self):
        assert len(self.rows) == 1
        return self.rows[0]

    def one_or_none(self):
        assert len(self.rows) <= 1
        return self.rows[0] if self.rows else None

    def scalar_one_or_none(self):
        return self.one_or_none()

    def __iter__(self):
        return iter(self.rows)


class _ImportDb:
    """Just enough of an ORM session for import_kubecon and the catalog's upsert, kept in memory: equality and IN
    WHERE clauses, column selects, deletes, and INSERT ... ON CONFLICT (conference_id, external_id) DO NOTHING
    RETURNING id. Row locks and the identity map aren't modeled: the integration tests cover them."""

    def __init__(self, conference, rooms=(), speakers=(), sessions=(), session_speakers=()):
        self.conference = conference
        self.rooms = list(rooms)
        self.speakers = list(speakers)
        self.sessions = list(sessions)
        self.session_speakers = list(session_speakers)
        self.access_facts = []
        self.outbox = []

    def _table(self, model):
        return {
            Conference: [self.conference],
            Room: self.rooms,
            Speaker: self.speakers,
            Session: self.sessions,
            SessionSpeaker: self.session_speakers,
            AccessFact: self.access_facts,
            OutboxJob: self.outbox,
        }[model]

    @staticmethod
    def _matches(row, criterion) -> bool:
        actual, expected = getattr(row, criterion.left.key), criterion.right.value
        return actual in expected if criterion.operator is operators.in_op else actual == expected

    def _where(self, rows, statement):
        return [row for row in rows if all(self._matches(row, c) for c in statement._where_criteria)]

    def scalars(self, statement):
        return _Scalars(self._where(self._table(statement.column_descriptions[0]["entity"]), statement))

    def add(self, row):
        self._table(type(row)).append(row)

    def delete(self, row):
        self._table(type(row)).remove(row)

    def execute(self, statement):
        if statement.is_select:  # a column select: rows with just those attributes
            columns = [c["name"] for c in statement.column_descriptions]
            rows = self._where(self._table(statement.column_descriptions[0]["entity"]), statement)
            return _Scalars(SimpleNamespace(**{name: getattr(row, name) for name in columns}) for row in rows)
        model = statement.entity_description["entity"]
        if statement.is_insert:
            values = statement.compile(dialect=postgresql.dialect()).params
            key = (values["conference_id"], values["external_id"])
            if any((row.conference_id, row.external_id) == key for row in self._table(model)):
                return _Scalars([])
            row = model(**{**values, "id": uuid.uuid4()})
            self.add(row)
            return _Scalars([row.id])
        rows = self._table(model)
        for row in self._where(rows, statement):
            rows.remove(row)
        return _Scalars([])

    def flush(self):
        for rows in (self.rooms, self.speakers, self.sessions):
            for row in rows:
                if row.id is None:
                    row.id = uuid.uuid4()


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


def test_a_first_import_stores_the_feed_and_a_reimport_changes_nothing():
    conference = Conference(id=uuid.uuid4(), slug=SLUG)
    db = _ImportDb(conference)

    first = import_kubecon(db, FEED)

    assert first == {"sessions": 2, "changed": 2, "reindexed": 0, "removed": 0, "rooms": 1}
    talk = next(row for row in db.sessions if row.external_id == "100")
    assert (talk.title, talk.revision) == ("Tracing Everything", 1)
    assert talk.starts_at == venue_instant("2026-11-10T10:30:00")
    assert talk.room_id == db.rooms[0].id
    assert [(link.session_id, link.speaker_id) for link in db.session_speakers] == [(talk.id, db.speakers[0].id)]
    assert {(job.entity_id, job.entity_revision, job.kind) for job in db.outbox} == {
        (row.id, 1, "index") for row in db.sessions
    }
    assert len(db.access_facts) == len(ACCESS_FACTS)
    assert {fact.source_url for fact in db.access_facts} == {ACCESS_PAGE}  # ACCESS-1: every fact has its link

    again = import_kubecon(db, copy.deepcopy(FEED))

    assert again == {"sessions": 2, "changed": 0, "reindexed": 0, "removed": 0, "rooms": 1}
    assert len(db.outbox) == 2  # an unchanged session enqueues nothing
    assert talk.revision == 1
    assert len(db.access_facts) == len(ACCESS_FACTS)  # replaced, not duplicated


def test_import_deletes_sessions_absent_from_the_feed_and_enqueues_tombstones():
    conference_id = uuid.uuid4()
    conference = Conference(id=conference_id, slug=SLUG)
    withdrawn = Session(id=uuid.uuid4(), conference_id=conference_id, external_id="999", revision=4)
    other_event = Session(id=uuid.uuid4(), conference_id=uuid.uuid4(), external_id="999", revision=2)
    db = _ImportDb(conference, sessions=[withdrawn, other_event])

    counts = import_kubecon(db, FEED)

    assert counts["removed"] == 1
    assert withdrawn not in db.sessions
    assert other_event in db.sessions  # another event's sessions are never touched
    assert {row.external_id for row in db.sessions if row.conference_id == conference_id} == {"100", "101"}
    assert [(job.entity_id, job.entity_revision) for job in db.outbox if job.kind == "delete"] == [(withdrawn.id, 5)]


def test_an_empty_feed_is_refused_and_deletes_nothing():
    # One broken fetch must not delete the whole schedule and, through ON DELETE CASCADE, every agenda item.
    conference_id = uuid.uuid4()
    session = _import_session(conference_id, room_id=None)
    db = _ImportDb(Conference(id=conference_id, slug=SLUG), sessions=[session])
    feed = copy.deepcopy(FEED)
    feed["sessions"] = []

    with pytest.raises(ValueError, match="no sessions"):
        import_kubecon(db, feed)

    assert db.sessions == [session]
    assert db.outbox == []


def _stored_sessions(conference_id, count):
    return [
        Session(id=uuid.uuid4(), conference_id=conference_id, external_id=f"9{n:03}", revision=1) for n in range(count)
    ]


def test_a_feed_that_would_remove_too_many_sessions_is_refused_and_changes_nothing():
    # A partial feed (one day's sessions, say) must not delete the rest of the schedule, and with it attendees' agenda
    # items (ON DELETE CASCADE).
    conference_id = uuid.uuid4()
    stored = _stored_sessions(conference_id, MAX_REMOVED_SESSIONS + 1)
    db = _ImportDb(Conference(id=conference_id, slug=SLUG), sessions=list(stored))

    with pytest.raises(ValueError, match=f"would remove {MAX_REMOVED_SESSIONS + 1} of {MAX_REMOVED_SESSIONS + 1}"):
        import_kubecon(db, FEED)

    assert db.sessions == stored
    assert (db.outbox, db.rooms, db.speakers) == ([], [], [])


def test_removals_up_to_the_limit_go_through_and_a_deliberate_cleanup_can_raise_it():
    conference_id = uuid.uuid4()
    db = _ImportDb(Conference(id=conference_id, slug=SLUG), sessions=_stored_sessions(conference_id, 3))
    assert import_kubecon(db, FEED, max_removed=3)["removed"] == 3

    db = _ImportDb(Conference(id=conference_id, slug=SLUG), sessions=_stored_sessions(conference_id, 4))
    with pytest.raises(ValueError, match="more than 3"):
        import_kubecon(db, FEED, max_removed=3)


def test_renaming_a_room_reindexes_its_unchanged_sessions_at_a_new_revision():
    # DATA-1: the room's name is in each of its sessions' search documents, which would otherwise keep the old name.
    db = _ImportDb(Conference(id=uuid.uuid4(), slug=SLUG))
    import_kubecon(db, FEED)
    talk = next(row for row in db.sessions if row.external_id == "100")
    feed = copy.deepcopy(FEED)
    feed["rooms"][0]["name"] = "Salt Palace | Level 1 | Hall DE (renamed)"

    counts = import_kubecon(db, feed)

    assert db.rooms[0].name == "Salt Palace | Level 1 | Hall DE (renamed)"
    assert (counts["changed"], counts["reindexed"]) == (0, 1)
    assert talk.revision == 2
    assert [(job.entity_id, job.entity_revision, job.kind) for job in db.outbox[2:]] == [(talk.id, 2, "index")]


def test_a_session_that_changed_in_a_renamed_room_gets_one_new_revision():
    db = _ImportDb(Conference(id=uuid.uuid4(), slug=SLUG))
    import_kubecon(db, FEED)
    talk = next(row for row in db.sessions if row.external_id == "100")
    feed = copy.deepcopy(FEED)
    feed["rooms"][0]["name"] = "Salt Palace | Level 1 | Hall DE (renamed)"
    feed["sessions"][0]["title"] = "Tracing Everything, Revised"

    counts = import_kubecon(db, feed)

    assert (counts["changed"], counts["reindexed"]) == (1, 0)
    assert talk.revision == 2
    assert [(job.entity_id, job.entity_revision) for job in db.outbox[2:]] == [(talk.id, 2)]
