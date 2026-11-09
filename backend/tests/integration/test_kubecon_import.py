"""The KubeCon import and the outbox lease against a real PostgreSQL: the SQL the unit tests' in-memory fakes can't
show (ON CONFLICT, ON DELETE CASCADE, row locks, FOR UPDATE SKIP LOCKED) and the ORM identity map."""

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event, select, text

from conferenceops.db import enqueue, transaction
from conferenceops.indexing import DIMENSIONS, process_outbox
from conferenceops.kubecon import import_kubecon
from conferenceops.models import AgendaItem, OutboxJob, OutboxStatus, Profile, Role, Session, SessionSpeaker

pytestmark = pytest.mark.integration

BACKEND = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))


def _feed(sessions: int = 2) -> dict:
    """A small Sessionize-shaped feed: one room, one speaker, and `sessions` talks with external IDs 100, 101, ..."""
    return {
        "categories": [{"title": "Audience Level", "items": [{"id": 1, "name": "Beginner"}]}],
        "questions": [],
        "rooms": [{"id": 87691, "name": "Salt Palace | Level 1 | Hall DE"}],
        "speakers": [{"id": "sp-1", "fullName": "Real Speaker", "firstName": "Real", "lastName": "Speaker"}],
        "sessions": [
            {
                "id": str(100 + n),
                "title": f"Talk {100 + n}",
                "description": "OpenTelemetry for beginners.",
                "startsAt": "2026-11-10T10:30:00",
                "endsAt": "2026-11-10T11:00:00",
                "isServiceSession": False,
                "categoryItems": [1],
                "questionAnswers": [],
                "roomId": 87691,
                "speakers": ["sp-1"],
            }
            for n in range(sessions)
        ],
    }


@pytest.fixture(scope="module")
def engine():
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL is not set")
    cfg = Config(os.path.join(BACKEND, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(BACKEND, "migrations"))
    command.upgrade(cfg, "head")
    return create_engine(url)


@pytest.fixture(autouse=True)
def empty(engine):
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE outbox_jobs, sessions, conferences, profiles CASCADE"))


def _talk(db, external_id: str = "100") -> Session:
    return db.scalars(select(Session).where(Session.external_id == external_id)).one()


def _revisions(db, session_id) -> list[int]:
    return sorted(db.scalars(select(OutboxJob.entity_revision).where(OutboxJob.entity_id == session_id)))


def test_importing_the_feed_twice_enqueues_one_index_job_per_session_then_none(engine):
    with transaction(engine) as db:
        first = import_kubecon(db, _feed())
    with transaction(engine) as db:
        again = import_kubecon(db, _feed())
        sessions = db.execute(select(Session.id, Session.revision)).all()
        jobs = db.execute(select(OutboxJob.entity_id, OutboxJob.entity_revision, OutboxJob.kind)).all()
        links = db.scalars(select(SessionSpeaker)).all()

    assert (first["changed"], again["changed"], again["removed"]) == (2, 0, 0)
    assert sorted(jobs) == sorted((row.id, 1, "index") for row in sessions)
    assert [row.revision for row in sessions] == [1, 1]
    assert len(links) == 2  # reconciled, not duplicated


def test_a_dropped_session_takes_its_agenda_items_with_it_and_gets_a_delete_job_at_the_next_revision(engine):
    with transaction(engine) as db:
        import_kubecon(db, _feed())
        talk = _talk(db)
        persona = Profile(role=Role.ATTENDEE, display_name="Demo attendee", is_fictional=True)
        db.add(persona)
        db.flush()
        db.add(AgendaItem(profile_id=persona.id, session_id=talk.id))
        talk_id, revision = talk.id, talk.revision
    feed = _feed()
    feed["sessions"] = [s for s in feed["sessions"] if s["id"] != "100"]

    with transaction(engine) as db:
        counts = import_kubecon(db, feed)
    with transaction(engine) as db:
        gone = db.get(Session, talk_id)
        agenda = db.scalars(select(AgendaItem)).all()
        links = db.scalars(select(SessionSpeaker).where(SessionSpeaker.session_id == talk_id)).all()
        deletes = db.execute(
            select(OutboxJob.entity_id, OutboxJob.entity_revision).where(OutboxJob.kind == "delete")
        ).all()

    assert counts["removed"] == 1
    assert (gone, agenda, links) == (None, [], [])  # ON DELETE CASCADE
    assert deletes == [(talk_id, revision + 1)]


def test_a_write_racing_an_import_waits_for_it_so_revisions_stay_distinct(engine):
    # DATA-1. The import used to read its sessions as ORM objects before locking them. The locking re-select then
    # returned those cached objects, so a write committed in between was overwritten and its revision number reused.
    with transaction(engine) as db:
        import_kubecon(db, _feed())
    feed = _feed()
    feed["sessions"][0]["description"] = "OpenTelemetry for beginners, updated."
    other_writer = create_engine(engine.url)  # its own engine, so the pause below never fires for it
    writer_done, paused = Event(), Event()
    writes = []

    def organizer_edit():  # like the planned PATCH /sessions/{id}: lock the row, change it, bump its revision
        with transaction(other_writer) as db:
            row = db.scalars(select(Session).where(Session.external_id == "100").with_for_update()).one()
            row.title = "Talk 100, moved to the keynote hall"
            row.revision += 1
            enqueue(db, "session", row.id, row.revision)
        writer_done.set()

    with ThreadPoolExecutor(max_workers=1) as executor:

        def pause_before_the_first_upsert(_conn, _cursor, statement, _parameters, _context, _executemany):
            if statement.lstrip().upper().startswith("INSERT INTO SESSIONS") and not paused.is_set():
                paused.set()
                writes.append(executor.submit(organizer_edit))
                writer_done.wait(timeout=0.5)  # the writer finishes here unless the import holds the row lock

        event.listen(engine, "before_cursor_execute", pause_before_the_first_upsert)
        try:
            with transaction(engine) as db:
                import_kubecon(db, feed)
            [write] = writes
            write.result(timeout=10)
        finally:
            event.remove(engine, "before_cursor_execute", pause_before_the_first_upsert)
            other_writer.dispose()

    with transaction(engine) as db:
        talk = _talk(db)
        revisions = _revisions(db, talk.id)

    assert paused.is_set()
    assert revisions == [1, 2, 3]  # one revision per write, none reused
    assert (talk.revision, talk.title, talk.abstract) == (
        3,
        "Talk 100, moved to the keynote hall",
        "OpenTelemetry for beginners, updated.",
    )


class _Es:
    def __init__(self):
        self.indexed: list[str] = []

    def index(self, **kwargs):
        self.indexed.append(kwargs["id"])

    def delete(self, **kwargs):
        raise AssertionError("no delete jobs in this test")


class _Embedder:
    def __init__(self, barrier: Barrier):
        self.barrier = barrier

    def embed(self, texts):
        self.barrier.wait(timeout=10)  # hold this pass's lease until the other pass has leased its own jobs
        return [[0.0] * DIMENSIONS for _ in texts]


def test_two_concurrent_outbox_passes_lease_disjoint_jobs(engine):
    with transaction(engine) as db:
        import_kubecon(db, _feed(sessions=6))
        session_ids = {str(session_id) for session_id in db.scalars(select(Session.id))}
    barrier = Barrier(2)

    def one_pass():
        es = _Es()
        with transaction(engine) as db:
            indexed = process_outbox(db, es, _Embedder(barrier), batch=3)
        return indexed, es.indexed

    with ThreadPoolExecutor(max_workers=2) as executor:
        (count_a, ids_a), (count_b, ids_b) = executor.map(lambda _: one_pass(), range(2))
    with transaction(engine) as db:
        statuses = set(db.scalars(select(OutboxJob.status)))

    assert (count_a, count_b) == (3, 3)
    assert not set(ids_a) & set(ids_b)  # SKIP LOCKED: no job was leased twice
    assert set(ids_a) | set(ids_b) == session_ids
    assert statuses == {OutboxStatus.DONE}
