"""The CloudBridge seed against a real PostgreSQL. The first load writes the archive with one outbox job per session. A
plain re-run, as a deploy hook runs it, adds only what is missing and keeps what organizers and personas changed. A
reset is the explicit full reseed: the fixture wins, and what left the fixture is deleted with its delete job (DATA-1).
"""

import os
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import UTC, date, datetime, timedelta
from threading import Event

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, delete, func, select, text

from conferenceops import cloudbridge
from conferenceops.catalog import SessionData, upsert_session
from conferenceops.cloudbridge import RoomSpec, SessionSpec, SpeakerSpec, load_cloudbridge, profile_id, read_fixture
from conferenceops.db import enqueue, transaction
from conferenceops.models import (
    AgendaItem,
    Attendance,
    ClockMode,
    Conference,
    EventKind,
    Level,
    OutboxJob,
    Profile,
    ProfilePreferences,
    Room,
    Session,
    SessionSpeaker,
    Speaker,
)

pytestmark = pytest.mark.integration

BACKEND = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
SEEDED = (Conference, Room, Speaker, Session, SessionSpeaker, Profile, ProfilePreferences, Attendance, AgendaItem)
EDITED_TITLE = "OpenTelemetry From Zero to Traces (moved to the afternoon)"


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
def empty(engine):
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE outbox_jobs, conferences, profiles CASCADE"))
    return engine


def _load(engine, fixture=None, reset=False):
    with transaction(engine) as db:
        return load_cloudbridge(db, fixture, reset=reset)


def _counts(engine) -> dict[str, int]:
    with transaction(engine) as db:
        return {m.__tablename__: db.scalar(select(func.count()).select_from(m)) for m in (*SEEDED, OutboxJob)}


def _snapshot(engine) -> dict[str, list[str]]:
    """Every row of every table the seed touches, with its timestamps and revisions."""
    with engine.connect() as conn:
        return {
            model.__tablename__: sorted(repr(tuple(row)) for row in conn.execute(select(model.__table__)))
            for model in (*SEEDED, OutboxJob)
        }


def _bookmarks(engine, persona: str) -> set[str]:
    with transaction(engine) as db:
        return set(
            db.scalars(
                select(Session.external_id)
                .join(AgendaItem, AgendaItem.session_id == Session.id)
                .where(AgendaItem.profile_id == profile_id(persona))
            )
        )


def _edit_like_the_api(engine) -> None:
    """What people may change through the API: an organizer edits session 3 (a new revision and its outbox job,
    DATA-1), Grace (P4) turns captions on, and Aisha (P1) drops bookmark 17 and bookmarks session 3 instead."""
    aisha = profile_id("P1")
    with transaction(engine) as db:
        three = db.scalars(select(Session).where(Session.external_id == "3")).one()
        three.title = EDITED_TITLE
        three.revision += 1
        enqueue(db, "session", three.id, three.revision)
        grace = db.get(ProfilePreferences, profile_id("P4"))
        grace.wants_captions = True
        grace.revision += 1
        seventeen = db.scalar(select(Session.id).where(Session.external_id == "17"))
        db.execute(delete(AgendaItem).where(AgendaItem.profile_id == aisha, AgendaItem.session_id == seventeen))
        db.add(AgendaItem(profile_id=aisha, session_id=three.id))


def _a_kubecon_bookmark(engine, persona: str) -> None:
    """A bookmark at the other event, which no CloudBridge load may touch."""
    with transaction(engine) as db:
        kubecon = Conference(
            slug="kubecon-test",
            name="KubeCon test",
            timezone="America/Denver",
            starts_on=date(2026, 11, 10),
            ends_on=date(2026, 11, 12),
            kind=EventKind.IMPORTED,
            source="test",
            clock_mode=ClockMode.WALL,
            is_fictional=False,
        )
        db.add(kubecon)
        db.flush()
        start = datetime(2026, 11, 10, 17, 0, tzinfo=UTC)
        talk, _ = upsert_session(db, SessionData(kubecon.id, "k1", "A KubeCon talk", start, start + timedelta(hours=1)))
        db.add(AgendaItem(profile_id=profile_id(persona), session_id=talk.id))


def test_the_first_load_seeds_the_archive_and_projects_each_session_once(empty):
    result = _load(empty)

    assert _counts(empty) == {
        "conferences": 1,
        "rooms": 4,
        "speakers": 12,
        "sessions": 24,
        "session_speakers": 26,  # the closing panel has three speakers
        "profiles": 39,  # 8 personas, 20 public attendees and 11 speakers (speaker S2 is persona P5)
        "profile_preferences": 8,
        "attendance": 18,
        "agenda_items": 2,
        "outbox_jobs": 24,
    }
    assert result.written["sessions"] == 24
    with transaction(empty) as db:
        conf = db.scalars(select(Conference)).one()
        jobs = db.scalars(select(OutboxJob)).all()
        session_ids = set(db.scalars(select(Session.id)))
        nine = db.get(Profile, profile_id("S9"))
    assert (conf.slug, conf.kind, conf.clock_mode, conf.source) == (
        "cloudbridge-summit-2026",
        EventKind.AUTHORED,
        ClockMode.FIXED,
        "fixture",
    )
    assert conf.is_fictional and conf.has_transcripts
    assert {(job.entity_type, job.kind, job.entity_revision) for job in jobs} == {("session", "index", 1)}
    assert {job.entity_id for job in jobs} == session_ids
    assert (nine.display_name, nine.role, nine.demo_selectable) == ("Ananya Iyer", "speaker", False)


def test_aisha_rahman_s_history_and_private_preferences_are_where_where_next_reads_them(empty):
    _load(empty)
    aisha = profile_id("P1")
    with transaction(empty) as db:
        attended = db.scalars(
            select(Session.external_id)
            .join(Attendance, Attendance.session_id == Session.id)
            .where(Attendance.profile_id == aisha)
        ).all()
        profile = db.get(Profile, aisha)
        prefs = db.get(ProfilePreferences, aisha)
        keynote = db.scalars(select(Session).where(Session.external_id == "1")).one()
    assert sorted(attended, key=int) == ["2", "9", "15", "23"]
    assert _bookmarks(empty, "P1") == {"17", "18"}
    assert (profile.display_name, profile.demo_selectable) == ("Aisha Rahman", True)
    assert (prefs.needs_step_free, prefs.wants_captions, prefs.reduced_motion) == (True, True, False)
    # 10:00 in Denver on Oct 6 is daylight time (UTC-6): stored as a UTC instant (TIME-1).
    assert keynote.starts_at == datetime(2026, 10, 6, 16, 0, tzinfo=UTC)


@pytest.mark.parametrize("reset", [False, True], ids=["seed", "reset"])
def test_a_second_load_changes_nothing_and_enqueues_nothing(empty, reset):
    _load(empty)
    before = _snapshot(empty)

    again = _load(empty, reset=reset)

    assert again.written == Counter()
    assert not again.changed
    assert _snapshot(empty) == before  # same rows, revisions and timestamps; still 24 outbox jobs
    assert _counts(empty)["outbox_jobs"] == 24


def test_a_re_run_keeps_what_organizers_and_personas_changed(empty):
    # A deploy runs the seed again. It must not revert an organizer's edit, Grace's preferences or Aisha's bookmarks.
    _load(empty)
    _edit_like_the_api(empty)
    before = _snapshot(empty)

    again = _load(empty)

    assert again.written == Counter()
    assert _snapshot(empty) == before
    with transaction(empty) as db:
        three = db.scalars(select(Session).where(Session.external_id == "3")).one()
        grace = db.get(ProfilePreferences, profile_id("P4"))
    assert (three.title, three.revision) == (EDITED_TITLE, 2)
    assert (grace.wants_captions, grace.revision) == (True, 2)
    assert _bookmarks(empty, "P1") == {"3", "18"}  # the bookmark she removed doesn't come back
    assert _counts(empty)["outbox_jobs"] == 25  # the seed's 24 and the organizer's edit; none from the re-run


def test_a_reset_puts_back_what_the_fixture_owns_and_nothing_else(empty):
    _load(empty)
    _edit_like_the_api(empty)
    _a_kubecon_bookmark(empty, "P1")

    result = _load(empty, reset=True)

    assert result.written == Counter({"sessions": 1, "profile_preferences": 1, "agenda_items": 2})
    with transaction(empty) as db:
        three = db.scalars(select(Session).where(Session.external_id == "3")).one()
        grace = db.get(ProfilePreferences, profile_id("P4"))
        jobs = db.scalars(select(OutboxJob.entity_revision).where(OutboxJob.entity_id == three.id)).all()
    assert (three.title, three.revision) == ("OpenTelemetry From Zero to Traces in One Afternoon", 3)
    assert sorted(jobs) == [1, 2, 3]  # the reset's revision is projected too (DATA-1)
    assert (grace.wants_captions, grace.revision) == (False, 3)  # revisions only move forward
    # Her CloudBridge bookmarks are the fixture's again; her KubeCon agenda isn't the fixture's to touch.
    assert _bookmarks(empty, "P1") == {"17", "18", "k1"}


def test_a_changed_abstract_reaches_the_database_only_through_a_reset(empty):
    fixture = read_fixture()
    _load(empty, fixture)
    edited = fixture.model_copy(
        update={
            "sessions": [
                s.model_copy(update={"abstract": f"{s.abstract} Now with slides."}) if s.key == "5" else s
                for s in fixture.sessions
            ]
        }
    )

    assert _load(empty, edited).written == Counter()  # a deploy never overwrites a session someone may have edited
    result = _load(empty, edited, reset=True)

    assert result.written == Counter({"sessions": 1})
    with transaction(empty) as db:
        five = db.scalars(select(Session).where(Session.external_id == "5")).one()
        new_jobs = db.scalars(select(OutboxJob).where(OutboxJob.entity_revision > 1)).all()
    assert five.revision == 2
    assert five.abstract.endswith("Now with slides.")
    assert [(job.entity_id, job.entity_revision) for job in new_jobs] == [(five.id, 2)]
    assert _counts(empty)["outbox_jobs"] == 25


def test_a_reset_removes_sessions_rooms_and_speakers_that_left_the_fixture(empty):
    fixture = read_fixture()
    cancelled = SessionSpec(
        key="25",
        title="A Talk That Was Cancelled",
        abstract="It was on the program. It was cancelled.",
        level=Level.ALL,
        topics=["community"],
        speakers=["S13"],
        room="annex",
        starts_local=datetime(2026, 10, 8, 16, 0),
        ends_local=datetime(2026, 10, 8, 16, 45),
    )
    bigger = fixture.model_copy(
        update={
            "rooms": [*fixture.rooms, RoomSpec(key="annex", name="Annex")],
            "speakers": [*fixture.speakers, SpeakerSpec(key="S13", name="Robin Vale", title="Engineer", company=None)],
            "sessions": [*fixture.sessions, cancelled],
            "personas": [
                p.model_copy(update={"bookmarked": [*p.bookmarked, "25"]}) if p.key == "P1" else p
                for p in fixture.personas
            ],
        }
    )
    _load(empty, bigger)
    with transaction(empty) as db:
        cancelled_id = db.scalar(select(Session.id).where(Session.external_id == "25"))

    assert _load(empty, fixture).written == Counter()  # a deploy only adds: what left the fixture stays
    result = _load(empty, fixture, reset=True)

    assert result.written == Counter({"sessions": 1, "rooms": 1, "speakers": 1})
    with transaction(empty) as db:
        gone = db.get(Session, cancelled_id)
        rooms = set(db.scalars(select(Room.external_id)))
        speakers = set(db.scalars(select(Speaker.external_id)))
        deletes = [
            (job.entity_id, job.entity_revision)
            for job in db.scalars(select(OutboxJob).where(OutboxJob.kind == "delete"))
        ]
    assert gone is None
    assert rooms == {r.key for r in fixture.rooms}
    assert speakers == {s.key for s in fixture.speakers}
    assert deletes == [(cancelled_id, 2)]  # one above its index job, so search drops it and no older job revives it
    assert _bookmarks(empty, "P1") == {"17", "18"}  # her bookmark went with the session
    counts = _counts(empty)
    assert (counts["sessions"], counts["session_speakers"], counts["outbox_jobs"]) == (24, 26, 26)
    assert counts["profiles"] == 40  # profiles are never deleted: they're people, and may own posts


def test_a_reset_changes_a_session_s_speakers_without_reprojecting_it(empty):
    fixture = read_fixture()
    _load(empty, fixture)
    edited = fixture.model_copy(
        update={
            "sessions": [
                s.model_copy(update={"speakers": ["S1", "S2", "S5"]}) if s.key == "24" else s for s in fixture.sessions
            ]
        }
    )

    assert _load(empty, edited).written == Counter()  # a deploy leaves an existing session's speakers alone
    result = _load(empty, edited, reset=True)

    assert result.written == Counter({"session_speakers": 2})  # S1 added, S12 removed
    with transaction(empty) as db:
        panel = set(
            db.scalars(
                select(Speaker.external_id)
                .join(SessionSpeaker, SessionSpeaker.speaker_id == Speaker.id)
                .join(Session, Session.id == SessionSpeaker.session_id)
                .where(Session.external_id == "24")
            )
        )
    assert panel == {"S1", "S2", "S5"}
    assert _counts(empty)["outbox_jobs"] == 24  # no session field changed, so there's nothing to re-project


def test_concurrent_loads_wait_for_each_other_instead_of_failing(empty):
    # A deploy hook and a manual run, or two pods: without the advisory lock, the second insert of the conference
    # fails on conferences_slug_key once the first commits.
    first_loaded = Event()
    release = Event()

    def first():
        with transaction(empty) as db:
            load_cloudbridge(db)
            first_loaded.set()
            assert release.wait(timeout=10)

    def second():
        assert first_loaded.wait(timeout=10)
        with transaction(empty) as db:
            return load_cloudbridge(db).written

    with ThreadPoolExecutor(max_workers=2) as pool:
        try:
            one = pool.submit(first)
            two = pool.submit(second)
            assert first_loaded.wait(timeout=10)
            done, _ = wait([two], timeout=0.5)
            assert not done  # the second load waits for the first one's transaction to end
        finally:
            release.set()
        one.result(timeout=10)
        written = two.result(timeout=10)

    assert written == Counter()  # it found the first load's rows instead of colliding with them
    counts = _counts(empty)
    assert (counts["conferences"], counts["sessions"], counts["outbox_jobs"]) == (1, 24, 24)


def test_the_cli_loads_the_packaged_fixture_into_database_url(empty):
    assert cloudbridge.main([]) == 0
    assert cloudbridge.main([]) == 0  # as a deploy hook would, on every upgrade
    assert cloudbridge.main(["--reset"]) == 0
    counts = _counts(empty)
    assert (counts["sessions"], counts["profiles"], counts["outbox_jobs"]) == (24, 39, 24)
