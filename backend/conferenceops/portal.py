"""The attendee portal's read endpoints on core-api: the events, each event's published access facts, and the
persona's agenda.

- `GET /api/v1/conferences`: both events with their dates, IANA time zone, whether they are fictional, and counts.
- `GET /api/v1/conferences/{slug}/access-facts`: the access facts stored for an event, each with its source link and
  the date it was read (ACCESS-1). Nothing is added to what the event publishes, and an unknown event is 404.
- `GET /api/v1/me/agenda`: the sessions the persona bookmarked and the ones she attended, public fields only. The
  persona comes from `X-Demo-Profile-ID` and is looked up in PostgreSQL (AUTHZ-1): a missing header is 400, an
  unknown or unselectable persona 404. Her preferences never leave the server (PRIV-1).

Every session time is a UTC instant with its venue wording in the event's own time zone (TIME-1)."""

import logging
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import Annotated, Literal, Protocol
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import Field
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from conferenceops.db import transaction
from conferenceops.models import AccessFact, AgendaItem, Attendance, Conference, Profile, Room, Session, Speaker
from conferenceops.personas import DEMO_PROFILE_OPENAPI, DEMO_PROFILE_RESPONSES, ErrorDetail, demo_profile_id
from conferenceops.search import ApiModel, venue_time

log = logging.getLogger("conferenceops.portal")


# --- What the data layer returns ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class EventRow:
    slug: str
    name: str
    starts_on: date
    ends_on: date
    timezone: str  # IANA name (TIME-1)
    is_fictional: bool
    has_transcripts: bool
    sessions: int  # talks, never service sessions such as breaks
    speakers: int
    rooms: int
    access_facts: int


@dataclass(frozen=True)
class FactRow:
    id: str
    kind: str
    text: str
    location: str | None
    source_url: str
    read_on: date


@dataclass(frozen=True)
class EventFacts:
    slug: str
    name: str
    is_fictional: bool
    facts: tuple[FactRow, ...]


@dataclass(frozen=True)
class AgendaSession:
    session_id: str
    title: str
    event: str
    conference: str
    timezone: str  # the event's IANA zone, for the venue wording
    is_fictional: bool
    starts_at: datetime
    ends_at: datetime
    room: str | None
    level: str | None
    topics: tuple[str, ...]


@dataclass(frozen=True)
class PersonaAgenda:
    """Public fields only: never her preferences (PRIV-1)."""

    profile_id: uuid.UUID
    display_name: str
    bookmarked: tuple[AgendaSession, ...]
    attended: tuple[AgendaSession, ...]


# A bookmark write: done (idempotent), or who was missing. AUTHZ-1: an unknown persona and an unknown session both
# answer 404, so the response never says which of the two exists.
BookmarkResult = Literal["done", "no_profile", "no_session"]


class PortalData(Protocol):
    def events(self) -> list[EventRow]: ...

    def access_facts(self, slug: str) -> EventFacts | None: ...

    def agenda(self, profile_id: uuid.UUID) -> PersonaAgenda | None: ...

    def bookmark(self, profile_id: uuid.UUID, session_id: uuid.UUID) -> BookmarkResult: ...

    def unbookmark(self, profile_id: uuid.UUID, session_id: uuid.UUID) -> BookmarkResult: ...


# --- The API models (camelCase on the wire) ----------------------------------------------------------------------


class EventCounts(ApiModel):
    sessions: int = Field(description="Talks; service sessions such as breaks aren't counted")
    speakers: int
    rooms: int
    access_facts: int = Field(description="Published access facts stored for the event (ACCESS-1)")


class EventCard(ApiModel):
    slug: str
    name: str
    starts_on: date
    ends_on: date
    timezone: str = Field(description="The venue's IANA time zone, e.g. America/Denver (TIME-1)")
    is_fictional: bool = Field(description="True for CloudBridge Summit, the fictional demo event")
    has_transcripts: bool
    counts: EventCounts


class EventList(ApiModel):
    events: list[EventCard]


class AccessFactCard(ApiModel):
    id: str
    kind: str = Field(description="captions, reserved_seating, quiet_room, prayer_room, staff_help, mobility_rental")
    text: str = Field(description="The event's published wording")
    location: str | None = None
    source_url: str = Field(description="Where the event publishes it (ACCESS-1)")
    read_on: date = Field(description="When the source was read")


class AccessFactList(ApiModel):
    conference: str
    event: str
    is_fictional: bool
    facts: list[AccessFactCard]


class AgendaEntry(ApiModel):
    session_id: str
    title: str
    event: str
    conference: str
    is_fictional: bool
    starts_at: datetime
    ends_at: datetime
    starts_at_venue: str = Field(description="Start in the event's time zone, e.g. 'Tue Nov 10, 10:30 AM MST'")
    room: str | None = None
    level: str | None = None
    topics: list[str] = Field(default_factory=list)


class AgendaResponse(ApiModel):
    profile_id: str
    display_name: str
    bookmarked: list[AgendaEntry] = Field(description="A bookmark is a personal plan; it never reserves a seat")
    attended: list[AgendaEntry] = Field(description="Sessions she attended, at any event")


def event_card(row: EventRow) -> EventCard:
    return EventCard(
        slug=row.slug,
        name=row.name,
        starts_on=row.starts_on,
        ends_on=row.ends_on,
        timezone=row.timezone,
        is_fictional=row.is_fictional,
        has_transcripts=row.has_transcripts,
        counts=EventCounts(
            sessions=row.sessions, speakers=row.speakers, rooms=row.rooms, access_facts=row.access_facts
        ),
    )


def fact_card(row: FactRow) -> AccessFactCard:
    return AccessFactCard(
        id=row.id, kind=row.kind, text=row.text, location=row.location, source_url=row.source_url, read_on=row.read_on
    )


def agenda_entry(row: AgendaSession) -> AgendaEntry:
    """The venue wording comes from the event's own zone, so an archive event in another zone reads right (TIME-1)."""
    return AgendaEntry(
        session_id=row.session_id,
        title=row.title,
        event=row.event,
        conference=row.conference,
        is_fictional=row.is_fictional,
        starts_at=row.starts_at,
        ends_at=row.ends_at,
        starts_at_venue=venue_time(row.starts_at, ZoneInfo(row.timezone)),
        room=row.room,
        level=row.level,
        topics=list(row.topics),
    )


# --- PostgreSQL --------------------------------------------------------------------------------------------------


class PostgresPortalData:
    def events(self) -> list[EventRow]:
        """Every event, the current one first (it starts last)."""

        def counts(column, *conditions) -> dict[uuid.UUID, int]:
            statement = select(column, func.count()).where(*conditions).group_by(column)
            return {conference_id: count for conference_id, count in db.execute(statement).all()}

        with transaction() as db:
            conferences = db.scalars(select(Conference).order_by(Conference.starts_on.desc(), Conference.slug)).all()
            sessions = counts(Session.conference_id, Session.is_service.is_(False))
            speakers = counts(Speaker.conference_id)
            rooms = counts(Room.conference_id)
            facts = counts(AccessFact.conference_id)
        return [
            EventRow(
                slug=conf.slug,
                name=conf.name,
                starts_on=conf.starts_on,
                ends_on=conf.ends_on,
                timezone=conf.timezone,
                is_fictional=conf.is_fictional,
                has_transcripts=conf.has_transcripts,
                sessions=sessions.get(conf.id, 0),
                speakers=speakers.get(conf.id, 0),
                rooms=rooms.get(conf.id, 0),
                access_facts=facts.get(conf.id, 0),
            )
            for conf in conferences
        ]

    def access_facts(self, slug: str) -> EventFacts | None:
        with transaction() as db:
            conf = db.scalars(select(Conference).where(Conference.slug == slug)).one_or_none()
            if conf is None:
                return None
            rows = db.scalars(
                select(AccessFact)
                .where(AccessFact.conference_id == conf.id)
                .order_by(AccessFact.kind, AccessFact.text, AccessFact.id)
            ).all()
        return EventFacts(
            slug=conf.slug,
            name=conf.name,
            is_fictional=conf.is_fictional,
            facts=tuple(
                FactRow(
                    id=str(fact.id),
                    kind=fact.kind.value,
                    text=fact.text,
                    location=fact.location,
                    source_url=fact.source_url,
                    read_on=fact.read_on,
                )
                for fact in rows
            ),
        )

    def agenda(self, profile_id: uuid.UUID) -> PersonaAgenda | None:
        """A selectable demo persona's agenda, or None (AUTHZ-1: the switcher offers only those)."""

        def sessions_of(link) -> tuple[AgendaSession, ...]:
            rows = db.execute(
                select(Session, Conference, Room)
                .join(link, link.session_id == Session.id)
                .join(Conference, Conference.id == Session.conference_id)
                .outerjoin(Room, Room.id == Session.room_id)
                .where(link.profile_id == profile_id)
                .order_by(Session.starts_at, Session.id)
            ).all()
            return tuple(
                AgendaSession(
                    session_id=str(session.id),
                    title=session.title,
                    event=conf.name,
                    conference=conf.slug,
                    timezone=conf.timezone,
                    is_fictional=conf.is_fictional,
                    starts_at=session.starts_at,
                    ends_at=session.ends_at,
                    room=room.name if room else None,
                    level=session.level.value if session.level else None,
                    topics=tuple(session.topics or ()),
                )
                for session, conf, room in rows
            )

        with transaction() as db:
            profile = db.scalars(
                select(Profile).where(Profile.id == profile_id, Profile.demo_selectable.is_(True))
            ).one_or_none()
            if profile is None:
                return None
            return PersonaAgenda(
                profile_id=profile.id,
                display_name=profile.display_name,
                bookmarked=sessions_of(AgendaItem),
                attended=sessions_of(Attendance),
            )

    def _selectable(self, db, profile_id: uuid.UUID) -> bool:
        found = db.scalars(select(Profile.id).where(Profile.id == profile_id, Profile.demo_selectable.is_(True)))
        return found.first() is not None

    def bookmark(self, profile_id: uuid.UUID, session_id: uuid.UUID) -> BookmarkResult:
        """Adds the session to the persona's agenda. A bookmark is intent only: it never reserves a seat."""
        with transaction() as db:
            if not self._selectable(db, profile_id):
                return "no_profile"
            if db.scalars(select(Session.id).where(Session.id == session_id)).first() is None:
                return "no_session"
            db.execute(
                pg_insert(AgendaItem)
                .values(profile_id=profile_id, session_id=session_id)
                .on_conflict_do_nothing(index_elements=["profile_id", "session_id"])
            )
            return "done"

    def unbookmark(self, profile_id: uuid.UUID, session_id: uuid.UUID) -> BookmarkResult:
        """Removes the session from the persona's agenda; removing a bookmark that isn't there is still done, but an
        unknown talk is 404 like it is for a bookmark."""
        with transaction() as db:
            if not self._selectable(db, profile_id):
                return "no_profile"
            if db.scalars(select(Session.id).where(Session.id == session_id)).first() is None:
                return "no_session"
            db.execute(
                delete(AgendaItem).where(AgendaItem.profile_id == profile_id, AgendaItem.session_id == session_id)
            )
            return "done"


def get_portal_data() -> PortalData:
    return PostgresPortalData()


# --- Routes ------------------------------------------------------------------------------------------------------

router = APIRouter(tags=["portal"])

Data = Annotated[PortalData, Depends(get_portal_data)]


def _unavailable(what: str, exc: Exception) -> HTTPException:
    # PRIV-1: the error's type only; a message could quote a query or a persona.
    log.error("%s failed: %s", what, type(exc).__name__)
    return HTTPException(status_code=503, detail=f"{what.capitalize()} aren't available right now")


@router.get(
    "/api/v1/conferences",
    response_model=EventList,
    response_model_by_alias=True,
    responses={503: {"model": ErrorDetail, "description": "Events aren't available right now"}},
)
def list_conferences(data: Data) -> EventList:
    """The events on the platform: the real, current KubeCon and the fictional, concluded CloudBridge Summit."""
    try:
        rows = data.events()
    except Exception as exc:
        raise _unavailable("events", exc) from exc
    return EventList(events=[event_card(row) for row in rows])


@router.get(
    "/api/v1/conferences/{slug}/access-facts",
    response_model=AccessFactList,
    response_model_by_alias=True,
    responses={
        404: {"model": ErrorDetail, "description": "No event has that slug"},
        503: {"model": ErrorDetail, "description": "Access facts aren't available right now"},
    },
)
def list_access_facts(slug: Annotated[str, Path(max_length=64)], data: Data) -> AccessFactList:
    """The access facts stored for an event, each with its source link and the date it was read (ACCESS-1). Nothing
    is inferred: an event that publishes none gets an empty list, and no route is ever labeled step-free."""
    try:
        found = data.access_facts(slug)
    except Exception as exc:
        raise _unavailable("access facts", exc) from exc
    if found is None:
        raise HTTPException(status_code=404, detail="Event not found")
    return AccessFactList(
        conference=found.slug,
        event=found.name,
        is_fictional=found.is_fictional,
        facts=[fact_card(row) for row in found.facts],
    )


@router.get(
    "/api/v1/me/agenda",
    response_model=AgendaResponse,
    response_model_by_alias=True,
    openapi_extra=DEMO_PROFILE_OPENAPI,  # the header is required, and the contract says so (API-1)
    responses={
        **DEMO_PROFILE_RESPONSES,
        503: {"model": ErrorDetail, "description": "The agenda isn't available right now"},
    },
)
def my_agenda(profile_id: Annotated[uuid.UUID, Depends(demo_profile_id)], data: Data) -> AgendaResponse:
    """The persona's bookmarks and the sessions she attended. The persona comes from X-Demo-Profile-ID (AUTHZ-1);
    public fields only, never her preferences (PRIV-1)."""
    try:
        found = data.agenda(profile_id)
    except Exception as exc:
        raise _unavailable("agenda", exc) from exc
    if found is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    return AgendaResponse(
        profile_id=str(found.profile_id),
        display_name=found.display_name,
        bookmarked=[agenda_entry(row) for row in found.bookmarked],
        attended=[agenda_entry(row) for row in found.attended],
    )


_BOOKMARK_RESPONSES = {
    **DEMO_PROFILE_RESPONSES,
    404: {"model": ErrorDetail, "description": "Profile or session not found"},
    503: {"model": ErrorDetail, "description": "The agenda isn't available right now"},
}


def _bookmark_outcome(result: BookmarkResult) -> None:
    if result != "done":
        # AUTHZ-1: one body for both cases, so the answer never says whether the persona or the talk was missing.
        raise HTTPException(status_code=404, detail="Not found")


@router.put(
    "/api/v1/me/agenda/{session_id}",
    status_code=204,
    openapi_extra=DEMO_PROFILE_OPENAPI,
    responses=_BOOKMARK_RESPONSES,
)
def add_to_agenda(
    profile_id: Annotated[uuid.UUID, Depends(demo_profile_id)],
    session_id: Annotated[uuid.UUID, Path(description="The session to bookmark")],
    data: Data,
) -> None:
    """Bookmarks a talk for the persona in X-Demo-Profile-ID (AUTHZ-1). Idempotent: bookmarking twice is one
    bookmark. A bookmark is a personal plan; it never reserves a seat."""
    try:
        result = data.bookmark(profile_id, session_id)
    except Exception as exc:
        raise _unavailable("agenda", exc) from exc
    _bookmark_outcome(result)


@router.delete(
    "/api/v1/me/agenda/{session_id}",
    status_code=204,
    openapi_extra=DEMO_PROFILE_OPENAPI,
    responses=_BOOKMARK_RESPONSES,
)
def remove_from_agenda(
    profile_id: Annotated[uuid.UUID, Depends(demo_profile_id)],
    session_id: Annotated[uuid.UUID, Path(description="The session to remove from the agenda")],
    data: Data,
) -> None:
    """Removes a bookmark for the persona in X-Demo-Profile-ID (AUTHZ-1). Idempotent."""
    try:
        result = data.unbookmark(profile_id, session_id)
    except Exception as exc:
        raise _unavailable("agenda", exc) from exc
    _bookmark_outcome(result)
