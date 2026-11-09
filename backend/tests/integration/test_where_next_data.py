"""What "where should I go next?" and the persona switcher read from a real PostgreSQL: the seeded CloudBridge personas
with their private preferences and history, a KubeCon agenda, the published access facts, and the revalidation that
drops service sessions and other events' sessions (PLAN §8.3 rule 7)."""

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
from conferenceops.personas import PostgresPersonaDirectory
from conferenceops.where_next_data import PostgresWhereNextData

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
    """The CloudBridge archive, and a small KubeCon: one talk, one service session, the published access facts, and a
    KubeCon talk on Aisha's agenda."""
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE outbox_jobs, conferences, profiles CASCADE"))
    with transaction(engine) as db:
        cloudbridge = load_cloudbridge(db).conference_id
        kubecon = Conference(
            slug="kubecon-na-2026",
            name="KubeCon + CloudNativeCon North America 2026",
            timezone="America/Denver",
            starts_on=date(2026, 11, 8),
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
            ),
        )
        lunch, _ = upsert_session(
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
        archive_talk = db.scalars(
            text("SELECT id FROM sessions WHERE conference_id = :c LIMIT 1").bindparams(c=cloudbridge)
        ).first()
    return {"kubecon": kubecon.id, "talk": str(talk.id), "lunch": str(lunch.id), "archive": str(archive_talk)}


def test_aisha_s_private_preferences_history_and_agenda_are_read_server_side(seeded):
    fixture = {p.key: p for p in read_fixture().personas}["P1"]
    persona = PostgresWhereNextData().persona(profile_id("P1"))
    assert persona is not None
    assert persona.display_name == "Aisha Rahman"
    assert persona.interests == tuple(fixture.interests)
    assert (persona.needs_step_free, persona.wants_captions) == (True, True)
    assert len(persona.attended) == len(fixture.attended)
    assert {p.event for p in persona.attended} == {"CloudBridge Summit 2026"}
    on_kubecon = [p for p in persona.planned if p.conference_id == seeded["kubecon"]]
    assert [p.session_id for p in on_kubecon] == [seeded["talk"]]
    assert len(persona.planned) - len(on_kubecon) == len(fixture.bookmarked)  # her CloudBridge bookmarks


def test_only_selectable_personas_resolve(seeded):
    data = PostgresWhereNextData()
    public_attendee = profile_id(read_fixture().attendees[0].key)
    assert data.persona(public_attendee) is None  # a public profile, not a demo persona (AUTHZ-1)
    assert data.persona(uuid.uuid4()) is None


def test_the_event_comes_with_its_published_facts_and_their_source_link(seeded):
    event = PostgresWhereNextData().event("kubecon-na-2026")
    assert event is not None
    assert event.timezone == "America/Denver"
    assert [f.kind for f in event.facts][:2] == ["captions", "reserved_seating"]
    assert {f.source_url for f in event.facts} == {ACCESS_PAGE}
    assert PostgresWhereNextData().event("no-such-event") is None


def test_revalidation_keeps_only_the_event_s_current_non_service_sessions(seeded):
    rows = PostgresWhereNextData().sessions(
        [seeded["talk"], seeded["lunch"], seeded["archive"], "not-a-uuid", str(uuid.uuid4())], seeded["kubecon"]
    )
    assert list(rows) == [seeded["talk"]]
    assert rows[seeded["talk"]].room == "Salt Palace | Level 1 | Hall DE"
    assert rows[seeded["talk"]].starts_at == datetime(2026, 11, 10, 17, 30, tzinfo=UTC)


def test_the_switcher_lists_the_eight_personas_with_public_fields_only(seeded):
    personas = PostgresPersonaDirectory().selectable()
    assert len(personas) == len(read_fixture().personas) == 8
    assert [p.display_name for p in personas] == sorted(p.display_name for p in personas)
    assert all(p.is_fictional for p in personas)
    aisha = next(p for p in personas if p.display_name == "Aisha Rahman")
    assert aisha.id == str(profile_id("P1"))
    assert set(aisha.model_dump()) == {"id", "display_name", "role", "company", "interests", "is_fictional"}
