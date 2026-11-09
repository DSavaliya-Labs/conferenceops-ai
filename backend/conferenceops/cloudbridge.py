"""Seed CloudBridge Summit 2026, the fictional event that holds the platform's archive (PLAN §1.4, §6.1).

The data is the packaged fixture `fixtures/cloudbridge.json`, generated from CLOUDBRIDGE-SKETCH.md. Every person,
company and room in it is invented, and the conference is flagged fictional so the UI can label it.

Loading is idempotent: it converges the rows the fixture owns and leaves everything else alone.
- The conference is matched by slug, its rooms and speakers by their fixture keys (stored as external IDs).
- Sessions go through `catalog.upsert_session`, so a new or changed session is projected to search through the
  outbox in the same transaction, and an unchanged one enqueues nothing (DATA-1).
- Profiles get deterministic IDs (uuid5 of their fixture key), so a re-run finds the rows it wrote. Persona
  preferences go to `profile_preferences` only (PRIV-1). The personas' CloudBridge history becomes attendance rows
  (attended) and agenda items (bookmarked); history is only added, never deleted, so a persona's own activity
  survives a re-run.
A second load of an unchanged fixture writes nothing at all.

Run it with `python -m conferenceops.cloudbridge`: it loads the packaged fixture into DATABASE_URL in one
transaction."""

import logging
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from importlib import resources
from typing import Any, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, field_validator, model_validator
from sqlalchemy import delete, select
from sqlalchemy.orm import Session as DbSession

from conferenceops.catalog import SessionData, upsert_session
from conferenceops.db import transaction
from conferenceops.models import (
    AgendaItem,
    Attendance,
    ClockMode,
    Conference,
    EventKind,
    Level,
    Profile,
    ProfilePreferences,
    Role,
    Room,
    SessionSpeaker,
    Speaker,
)
from conferenceops.settings import settings
from conferenceops.telemetry import setup_telemetry

log = logging.getLogger("conferenceops.cloudbridge")

SOURCE = "fixture"
# Never change this: every fixture profile would get a new ID, and the next load would duplicate them all.
PROFILE_NAMESPACE = uuid.UUID("5d69fc74-3f71-41cc-b56f-3ae195504322")


def profile_id(key: str) -> uuid.UUID:
    """The profile ID of a fixture person, such as "P1" (Aisha Rahman). The same in every database and process."""
    return uuid.uuid5(PROFILE_NAMESPACE, key)


def to_utc(local: datetime, zone: ZoneInfo) -> datetime:
    """A venue wall-clock time as a UTC instant. The IANA zone gets daylight saving right; an offset can't (TIME-1)."""
    return local.replace(tzinfo=zone).astimezone(UTC)


class _Spec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ConferenceSpec(_Spec):
    slug: str
    name: str
    timezone: str  # an IANA zone name, never a fixed offset (TIME-1)
    starts_on: date
    ends_on: date

    @field_validator("timezone")
    @classmethod
    def _iana_zone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"not an IANA time zone: {value!r}") from exc
        return value


class CompanySpec(_Spec):
    name: str
    partner: bool


class RoomSpec(_Spec):
    key: str
    name: str


class SpeakerSpec(_Spec):
    key: str
    name: str
    title: str
    company: str | None


class SessionSpec(_Spec):
    key: str
    title: str
    abstract: str
    level: Level
    topics: list[str]
    speakers: list[str]
    room: str
    starts_local: datetime  # venue wall-clock time without an offset: the conference time zone applies
    ends_local: datetime

    @field_validator("starts_local", "ends_local")
    @classmethod
    def _wall_clock(cls, value: datetime) -> datetime:
        if value.tzinfo is not None:
            raise ValueError("write venue-local times without a UTC offset; the conference time zone applies (TIME-1)")
        return value


class Preferences(_Spec):
    """Private, explicit choices, never inferred (PRIV-1). All three are required, so "none" is written down."""

    needs_step_free: bool
    wants_captions: bool
    reduced_motion: bool


class PersonaSpec(_Spec):
    """One of the eight selectable demo personas."""

    key: str
    display_name: str
    role: Role
    company: str | None
    interests: list[str]  # public
    preferences: Preferences  # private
    attended: list[str]  # CloudBridge session keys
    bookmarked: list[str]
    # Kept from the sketch for later work; profiles have no columns for them yet.
    experience: Level | None = None
    speaker: str | None = None  # the speaker this persona is at CloudBridge (P5 is S2)
    hosts_room: str | None = None


class AttendeeSpec(_Spec):
    """A public attendee profile: not selectable, no stored preferences, no history."""

    key: str
    display_name: str
    company: str | None
    interests: list[str]


class Fixture(_Spec):
    about: str
    conference: ConferenceSpec
    companies: list[CompanySpec]
    rooms: list[RoomSpec]
    speakers: list[SpeakerSpec]
    sessions: list[SessionSpec]
    personas: list[PersonaSpec]
    attendees: list[AttendeeSpec]

    @model_validator(mode="after")
    def _references_resolve(self) -> Self:
        problems: list[str] = []

        def unique(kind: str, keys: list[str]) -> set[str]:
            duplicates = sorted(key for key, count in Counter(keys).items() if count > 1)
            if duplicates:
                problems.append(f"duplicate {kind}: {duplicates}")
            return set(keys)

        companies = unique("company", [c.name for c in self.companies])
        rooms = unique("room", [r.key for r in self.rooms])
        speakers = unique("speaker", [s.key for s in self.speakers])
        sessions = unique("session", [s.key for s in self.sessions])
        unique("profile", [p.key for p in self.personas] + [a.key for a in self.attendees])

        conf = self.conference
        if conf.starts_on > conf.ends_on:
            problems.append("the conference ends before it starts")
        for person in [*self.speakers, *self.personas, *self.attendees]:
            if person.company is not None and person.company not in companies:
                problems.append(f"{person.key}: unknown company {person.company!r}")
        for s in self.sessions:
            if s.room not in rooms:
                problems.append(f"session {s.key}: unknown room {s.room!r}")
            if not s.speakers or not set(s.speakers) <= speakers:
                problems.append(f"session {s.key}: unknown or missing speakers {s.speakers}")
            if s.ends_local <= s.starts_local:
                problems.append(f"session {s.key}: ends before it starts")
            if not conf.starts_on <= s.starts_local.date() <= conf.ends_on:
                problems.append(f"session {s.key}: outside the conference dates")
        for p in self.personas:
            for key in [*p.attended, *p.bookmarked]:
                if key not in sessions:
                    problems.append(f"persona {p.key}: unknown session {key!r}")
            if p.speaker is not None and p.speaker not in speakers:
                problems.append(f"persona {p.key}: unknown speaker {p.speaker!r}")
            if p.hosts_room is not None and p.hosts_room not in rooms:
                problems.append(f"persona {p.key}: unknown room {p.hosts_room!r}")
        if problems:
            raise ValueError("; ".join(problems))
        return self


def read_fixture() -> Fixture:
    """The fixture packaged with the code: it ships in the wheel and the image, not only in a checkout."""
    path = resources.files("conferenceops").joinpath("fixtures", "cloudbridge.json")
    return Fixture.model_validate_json(path.read_text(encoding="utf-8"))


def conference_values(spec: ConferenceSpec) -> dict[str, Any]:
    """The conference row: authored here, fictional, concluded (a fixed clock), with an archive of transcripts."""
    return {
        "name": spec.name,
        "timezone": spec.timezone,
        "starts_on": spec.starts_on,
        "ends_on": spec.ends_on,
        "kind": EventKind.AUTHORED,
        "source": SOURCE,
        "clock_mode": ClockMode.FIXED,
        "is_fictional": True,
        "has_transcripts": True,
    }


def session_data(spec: SessionSpec, conference_id: uuid.UUID, room_id: uuid.UUID, zone: ZoneInfo) -> SessionData:
    """What `upsert_session` stores for a fixture session: venue-local times become UTC instants (TIME-1)."""
    return SessionData(
        conference_id=conference_id,
        external_id=spec.key,
        title=spec.title,
        starts_at=to_utc(spec.starts_local, zone),
        ends_at=to_utc(spec.ends_local, zone),
        abstract=spec.abstract,
        level=spec.level,
        topics=list(spec.topics),
        room_id=room_id,
    )


def public_profile(person: PersonaSpec | AttendeeSpec) -> dict[str, Any]:
    """The public `profiles` columns. Preferences are deliberately absent: they live in `profile_preferences`."""
    persona = isinstance(person, PersonaSpec)
    return {
        "role": person.role if persona else Role.ATTENDEE,
        "display_name": person.display_name,
        "company": person.company,
        "interests": list(person.interests),
        "demo_selectable": persona,
        "is_fictional": True,
    }


@dataclass
class LoadResult:
    conference_id: uuid.UUID
    written: Counter[str] = field(default_factory=Counter)  # rows inserted, updated or deleted, per table

    @property
    def changed(self) -> bool:
        return sum(self.written.values()) > 0


def _assign(row: Any, values: dict[str, Any]) -> bool:
    """Set the fields that differ. True when anything changed."""
    changed = False
    for name, value in values.items():
        if getattr(row, name) != value:
            setattr(row, name, value)
            changed = True
    return changed


def _upsert(db: DbSession, written: Counter[str], model: Any, key: dict[str, Any], values: dict[str, Any]) -> Any:
    row = db.scalars(select(model).filter_by(**key)).one_or_none()
    if row is None:
        row = model(**key, **values)
        db.add(row)
        written[model.__tablename__] += 1
    elif _assign(row, values):
        written[model.__tablename__] += 1
    return row


def _add_missing(db: DbSession, written: Counter[str], model: Any, profile: uuid.UUID, session: uuid.UUID) -> None:
    if db.get(model, {"profile_id": profile, "session_id": session}) is None:
        db.add(model(profile_id=profile, session_id=session))
        written[model.__tablename__] += 1


def _set_speakers(db: DbSession, written: Counter[str], session_id: uuid.UUID, desired: set[uuid.UUID]) -> None:
    current = set(db.scalars(select(SessionSpeaker.speaker_id).where(SessionSpeaker.session_id == session_id)))
    for speaker_id in desired - current:
        db.add(SessionSpeaker(session_id=session_id, speaker_id=speaker_id))
        written["session_speakers"] += 1
    stale = current - desired
    if stale:
        db.execute(
            delete(SessionSpeaker).where(SessionSpeaker.session_id == session_id, SessionSpeaker.speaker_id.in_(stale))
        )
        written["session_speakers"] += len(stale)


def load_cloudbridge(db: DbSession, fixture: Fixture | None = None) -> LoadResult:
    """Converge the database to the fixture in the caller's transaction, so the whole seed commits or none of it."""
    fx = fixture if fixture is not None else read_fixture()
    zone = ZoneInfo(fx.conference.timezone)
    written: Counter[str] = Counter()

    conf = _upsert(db, written, Conference, {"slug": fx.conference.slug}, conference_values(fx.conference))
    db.flush()  # IDs are assigned on insert, and everything below needs the conference's
    rooms = {
        r.key: _upsert(db, written, Room, {"conference_id": conf.id, "external_id": r.key}, {"name": r.name})
        for r in fx.rooms
    }
    speakers = {
        s.key: _upsert(
            db,
            written,
            Speaker,
            {"conference_id": conf.id, "external_id": s.key},
            {"name": s.name, "title": s.title, "company": s.company},
        )
        for s in fx.speakers
    }
    db.flush()

    sessions = {}
    for spec in fx.sessions:
        row, changed = upsert_session(db, session_data(spec, conf.id, rooms[spec.room].id, zone))
        if changed:
            written["sessions"] += 1
        sessions[spec.key] = row
        _set_speakers(db, written, row.id, {speakers[key].id for key in spec.speakers})

    for person in [*fx.personas, *fx.attendees]:
        _upsert(db, written, Profile, {"id": profile_id(person.key)}, public_profile(person))
    db.flush()  # profiles before the rows that reference them

    for persona in fx.personas:
        pid = profile_id(persona.key)
        prefs = db.get(ProfilePreferences, pid)
        wanted = persona.preferences.model_dump()
        if prefs is None:
            db.add(ProfilePreferences(profile_id=pid, excluded_levels=[], revision=1, **wanted))
            written["profile_preferences"] += 1
        elif _assign(prefs, wanted):
            prefs.revision += 1
            written["profile_preferences"] += 1
        for key in persona.attended:
            _add_missing(db, written, Attendance, pid, sessions[key].id)
        for key in persona.bookmarked:
            _add_missing(db, written, AgendaItem, pid, sessions[key].id)
    db.flush()
    return LoadResult(conference_id=conf.id, written=written)


def main() -> int:
    cfg = settings()
    setup_telemetry("seed", cfg)
    if not cfg.database_url:
        log.error("DATABASE_URL is not set; nothing was loaded")
        return 2
    with transaction() as db:
        result = load_cloudbridge(db)
    # Counts per table only: never names, interests or preferences (PRIV-1).
    summary = ", ".join(f"{table} {count}" for table, count in sorted(result.written.items())) or "none"
    log.info("CloudBridge Summit fixture loaded; rows written: %s", summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
