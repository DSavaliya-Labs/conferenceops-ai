"""What the attendee portal reads from a real PostgreSQL: both events with their counts, KubeCon's stored access facts
with their source link, and a persona's bookmarks and attended sessions across both events."""

import os
import uuid
from datetime import UTC, date, datetime

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from conferenceops.catalog import SessionData, upsert_session
from conferenceops.cloudbridge import load_cloudbridge, profile_id, read_fixture
from conferenceops.db import transaction
from conferenceops.kubecon import ACCESS_FACTS, ACCESS_PAGE, ACCESS_READ_ON
from conferenceops.models import AccessFact, AgendaItem, ClockMode, Conference, EventKind, Room
from conferenceops.portal import PostgresPortalData

pytestmark = pytest.mark.integration

BACKEND = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))


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
    """The CloudBridge archive, and a small KubeCon: one talk, one service session, the published access facts, and
    the talk on Aisha's agenda."""
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE outbox_jobs, conferences, profiles CASCADE"))
    with transaction(engine) as db:
        load_cloudbridge(db)
        kubecon = Conference(
            slug="kubecon-na-2026",
            name="KubeCon + CloudNativeCon North America 2026",
            timezone="America/Denver",
            starts_on=date(2026, 11, 9),
            ends_on=date(2026, 11, 12),
            kind=EventKind.IMPORTED,
            source="https://sessionize.com/api/v2/svi82w6c/view/All",
            clock_mode=ClockMode.WALL,
            is_fictional=False,
        )
        db.add(kubecon)
        db.flush()
        room = Room(conference_id=kubecon.id, external_id="87691", name="Salt Palace | Level 1 | Hall DE")
        db.add(room)
        db.flush()
        talk, _ = upsert_session(
            db,
            SessionData(
                conference_id=kubecon.id,
                external_id="100",
                title="Observability for AI SRE Teams",
                starts_at=datetime(2026, 11, 10, 17, 30, tzinfo=UTC),
                ends_at=datetime(2026, 11, 10, 18, 0, tzinfo=UTC),
                room_id=room.id,
                topics=["Observability"],
            ),
        )
        upsert_session(
            db,
            SessionData(
                conference_id=kubecon.id,
                external_id="101",
                title="Lunch",
                starts_at=datetime(2026, 11, 10, 19, 0, tzinfo=UTC),
                ends_at=datetime(2026, 11, 10, 20, 0, tzinfo=UTC),
                is_service=True,
            ),
        )
        for kind, fact_text, location in ACCESS_FACTS:
            db.add(
                AccessFact(
                    conference_id=kubecon.id,
                    kind=kind,
                    text=fact_text,
                    location=location,
                    source_url=ACCESS_PAGE,
                    read_on=ACCESS_READ_ON,
                )
            )
        db.add(AgendaItem(profile_id=profile_id("P1"), session_id=talk.id))
    return {"talk": str(talk.id)}


def test_both_events_are_listed_current_first_with_their_counts(seeded):
    events = PostgresPortalData().events()
    assert [e.slug for e in events] == ["kubecon-na-2026", "cloudbridge-summit-2026"]
    kubecon, cloudbridge = events
    assert (kubecon.is_fictional, kubecon.timezone) == (False, "America/Denver")
    assert (kubecon.sessions, kubecon.speakers, kubecon.rooms, kubecon.access_facts) == (
        1,
        0,
        1,
        7,
    )  # lunch not counted
    assert (cloudbridge.is_fictional, cloudbridge.has_transcripts) == (True, True)
    fixture = read_fixture()
    assert (cloudbridge.sessions, cloudbridge.speakers, cloudbridge.rooms, cloudbridge.access_facts) == (
        len(fixture.sessions),
        len(fixture.speakers),
        len(fixture.rooms),
        0,
    )


def test_the_stored_access_facts_come_with_their_source_link(seeded):
    found = PostgresPortalData().access_facts("kubecon-na-2026")
    assert found is not None
    assert found.name == "KubeCon + CloudNativeCon North America 2026"
    assert [f.kind for f in found.facts][:2] == ["captions", "reserved_seating"]
    assert len(found.facts) == len(ACCESS_FACTS)
    assert {f.source_url for f in found.facts} == {ACCESS_PAGE}
    assert {f.read_on for f in found.facts} == {ACCESS_READ_ON}
    archive = PostgresPortalData().access_facts("cloudbridge-summit-2026")
    assert archive is not None
    assert archive.facts == ()  # nothing published, nothing invented (ACCESS-1)
    assert PostgresPortalData().access_facts("no-such-event") is None


def test_aisha_s_agenda_spans_both_events_and_only_selectable_personas_resolve(seeded):
    fixture = {p.key: p for p in read_fixture().personas}["P1"]
    agenda = PostgresPortalData().agenda(profile_id("P1"))
    assert agenda is not None
    assert agenda.display_name == "Aisha Rahman"
    assert [s.title for s in agenda.attended] == [s.title for s in read_fixture().sessions if s.key in fixture.attended]
    assert {s.conference for s in agenda.attended} == {"cloudbridge-summit-2026"}
    kubecon = [s for s in agenda.bookmarked if s.conference == "kubecon-na-2026"]
    assert [s.session_id for s in kubecon] == [seeded["talk"]]
    assert kubecon[0].room == "Salt Palace | Level 1 | Hall DE"
    assert kubecon[0].timezone == "America/Denver"
    assert len(agenda.bookmarked) - len(kubecon) == len(fixture.bookmarked)  # her CloudBridge bookmarks
    assert all(s.is_fictional for s in agenda.attended)

    data = PostgresPortalData()
    assert data.agenda(profile_id(read_fixture().attendees[0].key)) is None  # a public profile, not a persona
    assert data.agenda(uuid.uuid4()) is None
