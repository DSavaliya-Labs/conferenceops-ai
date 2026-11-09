"""What "where should I go next?" reads from PostgreSQL, the canonical store.

- The persona, with her private preferences. They stay on the server: never in a response DTO, a log line, a span or
  a metric label (PRIV-1).
- The event and the access facts it publishes, each with its source link (ACCESS-1).
- The current rows of the sessions retrieval returned, so every hard rule runs on canonical data, never on a stale
  search document (PLAN §8.3 rule 7)."""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from sqlalchemy import select

from conferenceops.db import transaction
from conferenceops.models import (
    AccessFact,
    AgendaItem,
    Attendance,
    Conference,
    Profile,
    ProfilePreferences,
    Room,
    Session,
)


@dataclass(frozen=True)
class PastSession:
    """A session the persona attended or bookmarked, from any event: the history behind her recommendations."""

    session_id: str
    title: str
    event: str
    kind: Literal["attended", "bookmarked"]


@dataclass(frozen=True)
class PlannedSession:
    """A session on the persona's agenda. Intent only: it never reserves a seat."""

    session_id: str
    conference_id: uuid.UUID
    title: str
    event: str
    starts_at: datetime
    ends_at: datetime


@dataclass(frozen=True)
class Persona:
    """Server-side only. Her interests, preferences and history never reach logs or telemetry (PRIV-1)."""

    profile_id: uuid.UUID
    display_name: str
    interests: tuple[str, ...]
    needs_step_free: bool = False
    wants_captions: bool = False
    excluded_levels: frozenset[str] = frozenset()
    attended: tuple[PastSession, ...] = ()
    planned: tuple[PlannedSession, ...] = ()


@dataclass(frozen=True)
class PublishedFact:
    """An access fact as the event publishes it, with its source link (ACCESS-1)."""

    id: str
    kind: str
    text: str
    location: str | None
    source_url: str


@dataclass(frozen=True)
class Event:
    id: uuid.UUID
    slug: str
    name: str
    timezone: str  # IANA name (TIME-1)
    is_fictional: bool
    facts: tuple[PublishedFact, ...] = ()


@dataclass(frozen=True)
class SessionRow:
    """The canonical row of a retrieved session."""

    session_id: str
    title: str
    abstract: str | None
    topics: tuple[str, ...]
    level: str | None
    starts_at: datetime
    ends_at: datetime
    room: str | None
    revision: int


class WhereNextData(Protocol):
    def persona(self, profile_id: uuid.UUID) -> Persona | None: ...

    def event(self, slug: str) -> Event | None: ...

    def sessions(self, ids: Sequence[str], event_id: uuid.UUID) -> dict[str, SessionRow]: ...


def _uuids(ids: Sequence[str]) -> list[uuid.UUID]:
    out: list[uuid.UUID] = []
    for value in ids:
        try:
            parsed = uuid.UUID(value)
        except ValueError:
            continue
        if parsed not in out:
            out.append(parsed)
    return out


class PostgresWhereNextData:
    def persona(self, profile_id: uuid.UUID) -> Persona | None:
        """A selectable demo persona, or None (AUTHZ-1: the switcher offers only those)."""
        with transaction() as db:
            profile = db.scalars(
                select(Profile).where(Profile.id == profile_id, Profile.demo_selectable.is_(True))
            ).one_or_none()
            if profile is None:
                return None
            prefs = db.get(ProfilePreferences, profile_id)
            attended = db.execute(
                select(Session.id, Session.title, Conference.name)
                .join(Attendance, Attendance.session_id == Session.id)
                .join(Conference, Conference.id == Session.conference_id)
                .where(Attendance.profile_id == profile_id)
                .order_by(Session.starts_at, Session.id)
            ).all()
            planned = db.execute(
                select(
                    Session.id,
                    Session.conference_id,
                    Session.title,
                    Conference.name,
                    Session.starts_at,
                    Session.ends_at,
                )
                .join(AgendaItem, AgendaItem.session_id == Session.id)
                .join(Conference, Conference.id == Session.conference_id)
                .where(AgendaItem.profile_id == profile_id)
                .order_by(Session.starts_at, Session.id)
            ).all()
            return Persona(
                profile_id=profile.id,
                display_name=profile.display_name,
                interests=tuple(profile.interests or ()),
                needs_step_free=bool(prefs and prefs.needs_step_free),
                wants_captions=bool(prefs and prefs.wants_captions),
                excluded_levels=frozenset(prefs.excluded_levels or ()) if prefs else frozenset(),
                attended=tuple(
                    PastSession(session_id=str(sid), title=title, event=event, kind="attended")
                    for sid, title, event in attended
                ),
                planned=tuple(
                    PlannedSession(
                        session_id=str(sid),
                        conference_id=conference_id,
                        title=title,
                        event=event,
                        starts_at=starts_at,
                        ends_at=ends_at,
                    )
                    for sid, conference_id, title, event, starts_at, ends_at in planned
                ),
            )

    def event(self, slug: str) -> Event | None:
        with transaction() as db:
            conf = db.scalars(select(Conference).where(Conference.slug == slug)).one_or_none()
            if conf is None:
                return None
            facts = db.scalars(
                select(AccessFact)
                .where(AccessFact.conference_id == conf.id)
                .order_by(AccessFact.kind, AccessFact.text, AccessFact.id)
            ).all()
            return Event(
                id=conf.id,
                slug=conf.slug,
                name=conf.name,
                timezone=conf.timezone,
                is_fictional=conf.is_fictional,
                facts=tuple(
                    PublishedFact(
                        id=str(f.id), kind=f.kind.value, text=f.text, location=f.location, source_url=f.source_url
                    )
                    for f in facts
                ),
            )

    def sessions(self, ids: Sequence[str], event_id: uuid.UUID) -> dict[str, SessionRow]:
        """The event's current, non-service rows among `ids`. Anything else retrieval returned is dropped."""
        session_ids = _uuids(ids)
        if not session_ids:
            return {}
        statement = (
            select(Session, Room)
            .outerjoin(Room, Room.id == Session.room_id)
            .where(
                Session.id.in_(session_ids),
                Session.conference_id == event_id,
                Session.is_service.is_(False),
            )
        )
        with transaction() as db:
            rows = db.execute(statement).all()
        return {
            str(session.id): SessionRow(
                session_id=str(session.id),
                title=session.title,
                abstract=session.abstract,
                topics=tuple(session.topics or ()),
                level=session.level.value if session.level else None,
                starts_at=session.starts_at,
                ends_at=session.ends_at,
                room=room.name if room else None,
                revision=session.revision,
            )
            for session, room in rows
        }
