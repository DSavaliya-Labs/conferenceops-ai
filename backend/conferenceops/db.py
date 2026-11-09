"""PostgreSQL access: one engine per process, short transactions, and the outbox written in the same transaction as
the change it describes (DATA-1)."""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session as DbSession
from sqlalchemy.orm import sessionmaker

from conferenceops.models import OutboxJob
from conferenceops.settings import settings


@lru_cache
def engine() -> Engine:
    url = settings().database_url
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    return create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5)


@contextmanager
def transaction(bind: Engine | None = None) -> Iterator[DbSession]:
    """Commit on success, roll back on any exception. Everything written inside commits or fails together."""
    factory = sessionmaker(bind=bind or engine(), expire_on_commit=False)
    with factory.begin() as db:
        yield db


def enqueue(db: DbSession, entity_type: str, entity_id: uuid.UUID, revision: int, kind: str = "index") -> OutboxJob:
    """Add an outbox job to the caller's transaction; it becomes visible only if that transaction commits."""
    job = OutboxJob(entity_type=entity_type, entity_id=entity_id, entity_revision=revision, kind=kind)
    db.add(job)
    return job
