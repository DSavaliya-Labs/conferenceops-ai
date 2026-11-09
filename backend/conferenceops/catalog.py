"""Writes to the session catalog. Every change bumps the session's revision and enqueues its projection in the
same transaction, so search never misses a committed change and never sees an uncommitted one (DATA-1)."""

import uuid
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session as DbSession

from conferenceops.db import enqueue
from conferenceops.models import Level, Session


@dataclass(frozen=True)
class SessionData:
    conference_id: uuid.UUID
    external_id: str
    title: str
    starts_at: datetime
    ends_at: datetime
    abstract: str | None = None
    level: Level | None = None
    topics: list[str] = field(default_factory=list)
    room_id: uuid.UUID | None = None
    is_service: bool = False
    materials: dict = field(default_factory=dict)


_FIELDS = ("title", "abstract", "level", "topics", "starts_at", "ends_at", "room_id", "is_service", "materials")


def upsert_session(db: DbSession, data: SessionData) -> tuple[Session, bool]:
    """Insert or update a session by (conference, external ID). Returns the row and whether anything changed.
    An unchanged session keeps its revision and enqueues nothing, so re-running an import is free."""
    stmt = (
        insert(Session)
        .values(
            conference_id=data.conference_id,
            external_id=data.external_id,
            revision=1,
            **{name: getattr(data, name) for name in _FIELDS},
        )
        .on_conflict_do_nothing(index_elements=["conference_id", "external_id"])
        .returning(Session.id)
    )
    inserted_id = db.execute(stmt).scalar_one_or_none()
    row = db.scalars(
        select(Session)
        .where(Session.conference_id == data.conference_id, Session.external_id == data.external_id)
        .with_for_update()
    ).one()
    if inserted_id is not None:
        enqueue(db, "session", row.id, row.revision)
        return row, True

    changed = False
    for name in _FIELDS:
        if getattr(row, name) != getattr(data, name):
            setattr(row, name, getattr(data, name))
            changed = True
    if changed:
        row.revision += 1
        db.flush()
        enqueue(db, "session", row.id, row.revision)
    return row, changed
