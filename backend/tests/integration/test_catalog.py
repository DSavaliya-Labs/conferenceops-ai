"""DATA-1 against a real PostgreSQL: a session change and its outbox job commit together or not at all."""

import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from threading import Event

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, func, select, text

from conferenceops.catalog import SessionData, upsert_session
from conferenceops.db import transaction
from conferenceops.models import ClockMode, Conference, EventKind, OutboxJob, Session

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
def conference_id(engine):
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE outbox_jobs, sessions, conferences CASCADE"))
    with transaction(engine) as db:
        conf = Conference(
            slug=f"test-{uuid.uuid4().hex[:8]}",
            name="Test Event",
            timezone="America/Denver",
            starts_on=date(2026, 11, 9),
            ends_on=date(2026, 11, 12),
            kind=EventKind.IMPORTED,
            source="test",
            clock_mode=ClockMode.WALL,
            is_fictional=False,
        )
        db.add(conf)
        db.flush()
        return conf.id


def _data(conference_id, title="Operating Kubernetes Agents Safely") -> SessionData:
    start = datetime(2026, 11, 10, 17, 0, tzinfo=UTC)
    return SessionData(conference_id, "ext-1", title, start, start + timedelta(minutes=30))


def _counts(engine):
    with transaction(engine) as db:
        return db.scalar(select(func.count(Session.id))), db.scalar(select(func.count(OutboxJob.id)))


def test_a_new_session_and_its_outbox_job_commit_together(engine, conference_id):
    with transaction(engine) as db:
        row, changed = upsert_session(db, _data(conference_id))
    assert changed and row.revision == 1
    with transaction(engine) as db:
        job = db.scalars(select(OutboxJob)).one()
    assert (job.entity_id, job.entity_revision, job.kind) == (row.id, 1, "index")


def test_a_failed_transaction_leaves_neither_the_session_nor_its_job(engine, conference_id):
    with pytest.raises(RuntimeError), transaction(engine) as db:
        upsert_session(db, _data(conference_id))
        raise RuntimeError("crash after the write, before commit")
    assert _counts(engine) == (0, 0)


def test_reimporting_unchanged_data_is_free_and_a_change_bumps_the_revision(engine, conference_id):
    with transaction(engine) as db:
        upsert_session(db, _data(conference_id))
    with transaction(engine) as db:
        _, changed = upsert_session(db, _data(conference_id))
    assert not changed
    assert _counts(engine) == (1, 1)

    with transaction(engine) as db:
        row, changed = upsert_session(db, _data(conference_id, title="Operating Kubernetes Agents Safely, v2"))
    assert changed and row.revision == 2
    with transaction(engine) as db:
        revisions = sorted(db.scalars(select(OutboxJob.entity_revision)))
    assert revisions == [1, 2]


def test_concurrent_updates_lock_the_row_and_get_distinct_revisions(engine, conference_id):
    with transaction(engine) as db:
        upsert_session(db, _data(conference_id))

    update_started = Event()

    def update_to_v3():
        with transaction(engine) as db:
            row, changed = upsert_session(db, _data(conference_id, title="Operating Kubernetes Agents Safely, v3"))
            update_started.set()
            return row.revision, changed

    with ThreadPoolExecutor(max_workers=1) as executor:
        with transaction(engine) as db:
            row, changed = upsert_session(db, _data(conference_id, title="Operating Kubernetes Agents Safely, v2"))
            assert changed and row.revision == 2
            update = executor.submit(update_to_v3)
            assert not update_started.wait(timeout=0.2)

        assert update.result(timeout=5) == (3, True)

    with transaction(engine) as db:
        row = db.scalars(select(Session)).one()
        revisions = sorted(db.scalars(select(OutboxJob.entity_revision)))
    assert row.revision == 3
    assert revisions == [1, 2, 3]
