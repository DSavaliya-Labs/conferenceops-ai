"""The CloudBridge seed against a real PostgreSQL: the first load writes the archive with one outbox job per session,
and a second load of the same fixture changes nothing and enqueues nothing (DATA-1)."""

import os
from collections import Counter
from datetime import UTC, datetime

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, func, select, text

from conferenceops import cloudbridge
from conferenceops.cloudbridge import load_cloudbridge, profile_id, read_fixture
from conferenceops.db import transaction
from conferenceops.models import (
    AgendaItem,
    Attendance,
    ClockMode,
    Conference,
    EventKind,
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


def _load(engine, fixture=None):
    with transaction(engine) as db:
        return load_cloudbridge(db, fixture)


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


def test_the_first_load_seeds_the_archive_and_projects_each_session_once(empty):
    result = _load(empty)

    assert _counts(empty) == {
        "conferences": 1,
        "rooms": 4,
        "speakers": 12,
        "sessions": 24,
        "session_speakers": 26,  # the closing panel has three speakers
        "profiles": 28,  # 8 personas and 20 public attendees
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
    assert (conf.slug, conf.kind, conf.clock_mode, conf.source) == (
        "cloudbridge-summit-2026",
        EventKind.AUTHORED,
        ClockMode.FIXED,
        "fixture",
    )
    assert conf.is_fictional and conf.has_transcripts
    assert {(job.entity_type, job.kind, job.entity_revision) for job in jobs} == {("session", "index", 1)}
    assert {job.entity_id for job in jobs} == session_ids


def test_aisha_rahman_s_history_and_private_preferences_are_where_where_next_reads_them(empty):
    _load(empty)
    aisha = profile_id("P1")
    with transaction(empty) as db:
        attended = db.scalars(
            select(Session.external_id)
            .join(Attendance, Attendance.session_id == Session.id)
            .where(Attendance.profile_id == aisha)
        ).all()
        bookmarked = db.scalars(
            select(Session.external_id)
            .join(AgendaItem, AgendaItem.session_id == Session.id)
            .where(AgendaItem.profile_id == aisha)
        ).all()
        profile = db.get(Profile, aisha)
        prefs = db.get(ProfilePreferences, aisha)
        keynote = db.scalars(select(Session).where(Session.external_id == "1")).one()
    assert sorted(attended, key=int) == ["2", "9", "15", "23"]
    assert sorted(bookmarked, key=int) == ["17", "18"]
    assert (profile.display_name, profile.demo_selectable) == ("Aisha Rahman", True)
    assert (prefs.needs_step_free, prefs.wants_captions, prefs.reduced_motion) == (True, True, False)
    # 10:00 in Denver on Oct 6 is daylight time (UTC-6): stored as a UTC instant (TIME-1).
    assert keynote.starts_at == datetime(2026, 10, 6, 16, 0, tzinfo=UTC)


def test_a_second_load_changes_nothing_and_enqueues_nothing(empty):
    _load(empty)
    before = _snapshot(empty)

    again = _load(empty)

    assert again.written == Counter()
    assert not again.changed
    assert _snapshot(empty) == before  # same rows, revisions and timestamps; still 24 outbox jobs
    assert _counts(empty)["outbox_jobs"] == 24


def test_a_changed_abstract_is_projected_again_and_nothing_else_moves(empty):
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

    result = _load(empty, edited)

    assert result.written == Counter({"sessions": 1})
    with transaction(empty) as db:
        five = db.scalars(select(Session).where(Session.external_id == "5")).one()
        new_jobs = db.scalars(select(OutboxJob).where(OutboxJob.entity_revision > 1)).all()
    assert five.revision == 2
    assert [(job.entity_id, job.entity_revision) for job in new_jobs] == [(five.id, 2)]
    assert _counts(empty)["outbox_jobs"] == 25


def test_a_drifted_persona_is_restored_and_keeps_her_own_bookmarks(empty):
    _load(empty)
    aisha = profile_id("P1")
    with transaction(empty) as db:
        db.get(ProfilePreferences, aisha).wants_captions = False
        otel_intro = db.scalars(select(Session.id).where(Session.external_id == "3")).one()
        db.add(AgendaItem(profile_id=aisha, session_id=otel_intro))

    result = _load(empty)

    assert result.written == Counter({"profile_preferences": 1})
    with transaction(empty) as db:
        prefs = db.get(ProfilePreferences, aisha)
        bookmarks = db.scalar(select(func.count()).select_from(AgendaItem).where(AgendaItem.profile_id == aisha))
    assert (prefs.wants_captions, prefs.revision) == (True, 2)
    assert bookmarks == 3  # her own bookmark survives the re-run


def test_the_cli_loads_the_packaged_fixture_into_database_url(empty):
    assert cloudbridge.main() == 0
    assert cloudbridge.main() == 0  # as a deploy Job would, on every upgrade
    counts = _counts(empty)
    assert (counts["sessions"], counts["profiles"], counts["outbox_jobs"]) == (24, 28, 24)
