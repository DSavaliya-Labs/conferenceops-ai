"""Seed CloudBridge Summit 2026, the fictional event that holds the platform's archive (PLAN §1.4, §6.1).

The data is the packaged fixture `fixtures/cloudbridge.json`, generated from CLOUDBRIDGE-SKETCH.md. Every person,
company and room in it is invented, and the conference is flagged fictional so the UI can label it.

A load runs in the caller's transaction, so it commits whole or not at all. There are two kinds:
- **Seed**, the default, is safe to run on every deploy. It adds what is missing and never changes or removes a row
  that exists, so what people changed survives: an organizer's edit to a session or a room, a persona's preferences,
  a bookmark she removed. A persona's fixture history is added together with her profile, or with the session it
  points at, and not otherwise, so a removed bookmark doesn't come back. What left the fixture stays.
- **Reset** (`reset=True`, or `--reset` on the command line) is the explicit full reseed of PLAN §6.3, never part of
  a deploy. The fixture wins: everything it owns gets the fixture's values back, each persona's preferences and
  CloudBridge history become the fixture's again, and the sessions, rooms and speakers that left the fixture are
  deleted, each deleted session with a delete job for its search document (DATA-1). Rows the fixture doesn't own are
  left alone, such as the personas' KubeCon agendas. Profiles are never deleted.

Either way:
- The conference is matched by slug, its rooms and speakers by their fixture keys (stored as external IDs).
- Sessions go through `catalog.upsert_session`, so a new or changed session is projected to search through the
  outbox in the same transaction, and an unchanged one enqueues nothing (DATA-1).
- Profiles get deterministic IDs (uuid5 of their fixture key), so a load finds the rows an earlier one wrote. The
  8 personas, the public attendees, and a public profile for each speaker who isn't a persona (S2 is persona P5):
  39 profiles, the "about 40" of PLAN §6.1. Persona preferences go to `profile_preferences` only (PRIV-1).
- A transaction-scoped advisory lock serializes loads: a second load at the same time (a deploy hook and a manual
  run, or two pods) waits for the first to commit and then finds its rows, instead of failing on a duplicate key.
A second load of an unchanged fixture, seed or reset, writes nothing at all.

Run it with `python -m conferenceops.cloudbridge [--reset]`: it loads the packaged fixture into DATABASE_URL in one
transaction."""

import argparse
import logging
import uuid
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from importlib import resources
from typing import Any, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session as DbSession

from conferenceops.catalog import SessionData, upsert_session
from conferenceops.db import enqueue, transaction
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
    Session,
    SessionSpeaker,
    Speaker,
    Transcript,
    TranscriptStatus,
)
from conferenceops.settings import settings
from conferenceops.telemetry import setup_telemetry
from conferenceops.transcripts import ENTITY as TRANSCRIPT_ENTITY
from conferenceops.transcripts import transcript_text

log = logging.getLogger("conferenceops.cloudbridge")

SOURCE = "fixture"
MEDIA_PREFIX = "/media/cloudbridge"  # apps/web/public/media/cloudbridge, served statically by the web container
# Never change this: every fixture profile would get a new ID, and the next load would duplicate them all.
PROFILE_NAMESPACE = uuid.UUID("5d69fc74-3f71-41cc-b56f-3ae195504322")
# The advisory lock every CloudBridge load takes ("CLOUDBRG" in ASCII), held until the caller's transaction ends.
LOCK_KEY = 0x434C4F5544425247


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


class CueSpec(_Spec):
    start: float = Field(ge=0)  # seconds into the replay
    end: float = Field(gt=0)
    text: str = Field(min_length=1)


class TranscriptSpec(_Spec):
    """A session's captioned replay and transcript: a simulated excerpt generated by scripts/cloudbridge_media.py
    (SPEAKER-1). `status` is the transcript's state when the demo starts (PLAN §9): clip 3 starts processing and is
    published during the demo, which is when it becomes searchable."""

    session: str  # session key
    speaker: str  # speaker key; must speak at that session
    media: str
    captions: str
    duration_seconds: float = Field(gt=0)
    status: TranscriptStatus
    cues: list[CueSpec] = Field(min_length=1)

    @field_validator("media", "captions")
    @classmethod
    def _served_by_the_web_app(cls, value: str) -> str:
        if not value.startswith(MEDIA_PREFIX):
            raise ValueError(f"media is served by the web container under {MEDIA_PREFIX}/: {value!r}")
        return value

    @model_validator(mode="after")
    def _cues_in_order(self) -> Self:
        previous = 0.0
        for cue in self.cues:
            if cue.start < previous or cue.end <= cue.start:
                raise ValueError(f"transcript {self.session}: cues must be in order and non-overlapping")
            previous = cue.end
        if previous > self.duration_seconds + 0.5:
            raise ValueError(f"transcript {self.session}: cues run past the end of the media")
        return self


class Fixture(_Spec):
    about: str
    conference: ConferenceSpec
    companies: list[CompanySpec]
    # A reset deletes what isn't listed, so an empty list would wipe the program: refuse it.
    rooms: list[RoomSpec] = Field(min_length=1)
    speakers: list[SpeakerSpec] = Field(min_length=1)
    sessions: list[SessionSpec] = Field(min_length=1)
    personas: list[PersonaSpec]
    attendees: list[AttendeeSpec]
    transcripts: list[TranscriptSpec] = Field(default_factory=list)

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
        as_persona = unique("persona speaker", [p.speaker for p in self.personas if p.speaker is not None])
        # Profile IDs come from these keys, so they must be distinct across personas, attendees and speakers.
        unique(
            "profile",
            [p.key for p in self.personas]
            + [a.key for a in self.attendees]
            + [s.key for s in self.speakers if s.key not in as_persona],
        )

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
        unique("transcript session", [t.session for t in self.transcripts])
        speakers_of = {s.key: set(s.speakers) for s in self.sessions}
        for t in self.transcripts:
            if t.session not in sessions:
                problems.append(f"transcript {t.session}: unknown session")
            elif t.speaker not in speakers_of[t.session]:
                problems.append(f"transcript {t.session}: {t.speaker!r} doesn't speak at that session")
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


def transcript_values(spec: TranscriptSpec, speaker_name: str) -> dict[str, Any]:
    """The `transcripts` columns the fixture owns, status and revision apart: the replay, the captions and the cues,
    with the transcript text derived from the cues. Always a simulated excerpt (SPEAKER-1)."""
    cues = [cue.model_dump() for cue in spec.cues]
    return {
        "speaker_name": speaker_name,
        "is_simulated": True,
        "media_url": spec.media,
        "captions_url": spec.captions,
        "duration_seconds": spec.duration_seconds,
        "text": transcript_text(cues),
        "cues": cues,
    }


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


def speaker_profile(spec: SpeakerSpec) -> dict[str, Any]:
    """The public profile of a speaker who isn't a persona: not selectable, no preferences, no history."""
    return {
        "role": Role.SPEAKER,
        "display_name": spec.name,
        "company": spec.company,
        "interests": [],
        "demo_selectable": False,
        "is_fictional": True,
    }


def fixture_profiles(fx: Fixture) -> dict[str, dict[str, Any]]:
    """Every profile the fixture owns, by key: the personas, the public attendees, and one for each speaker who isn't
    a persona. The profile ID is `profile_id(key)`."""
    profiles = {person.key: public_profile(person) for person in [*fx.personas, *fx.attendees]}
    as_persona = {p.speaker for p in fx.personas if p.speaker is not None}
    profiles |= {s.key: speaker_profile(s) for s in fx.speakers if s.key not in as_persona}
    return profiles


def speaker_profile_id(fx: Fixture, speaker: str) -> uuid.UUID:
    """The profile of a fixture speaker, such as "S9": the persona who is that speaker (S2 is P5), or their own."""
    persona = next((p.key for p in fx.personas if p.speaker == speaker), None)
    return profile_id(persona or speaker)


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


def _put(
    db: DbSession, written: Counter[str], model: Any, key: dict[str, Any], values: dict[str, Any], reset: bool
) -> tuple[Any, bool]:
    """Find a row by its key and create it when it's missing. Only a reset changes a row that exists.
    Returns the row and whether it was created."""
    row = db.scalars(select(model).filter_by(**key)).one_or_none()
    if row is None:
        row = model(**key, **values)
        db.add(row)
        written[model.__tablename__] += 1
        return row, True
    if reset and _assign(row, values):
        written[model.__tablename__] += 1
    return row, False


def _converge_links(
    db: DbSession,
    written: Counter[str],
    model: Any,
    owner: Any,
    owner_id: uuid.UUID,
    linked: Any,
    desired: set[uuid.UUID],
    scope: set[uuid.UUID] | None = None,
) -> None:
    """Make the IDs in column `linked` of the rows whose `owner` is `owner_id` exactly `desired`. With a `scope`, only
    links inside it count, so links to anything else (such as KubeCon sessions in a persona's agenda) stay."""
    current = set(db.scalars(select(linked).where(owner == owner_id)))
    if scope is not None:
        current &= scope
    for item in desired - current:
        db.add(model(**{owner.key: owner_id, linked.key: item}))
        written[model.__tablename__] += 1
    stale = current - desired
    if stale:
        db.execute(delete(model).where(owner == owner_id, linked.in_(stale)))
        written[model.__tablename__] += len(stale)


def _add_missing(db: DbSession, written: Counter[str], model: Any, profile: uuid.UUID, session: uuid.UUID) -> None:
    if db.get(model, {"profile_id": profile, "session_id": session}) is None:
        db.add(model(profile_id=profile, session_id=session))
        written[model.__tablename__] += 1


def _remove_sessions_not_in(db: DbSession, written: Counter[str], conference_id: uuid.UUID, keys: set[str]) -> None:
    """Delete the conference's sessions that left the fixture. Each gets a delete job one revision above its last
    index job, so search drops its document and an older job can't bring it back (DATA-1)."""
    gone = db.scalars(
        select(Session)
        .where(Session.conference_id == conference_id, Session.external_id.not_in(sorted(keys)))
        .with_for_update()
    ).all()
    for row in gone:
        enqueue(db, "session", row.id, row.revision + 1, kind="delete")
        transcript = db.scalars(select(Transcript).where(Transcript.session_id == row.id)).one_or_none()
        if transcript is not None:  # its job finds no row and removes every chunk from search (TRANSCRIPT-1)
            enqueue(db, TRANSCRIPT_ENTITY, transcript.id, transcript.revision + 1)
        db.delete(row)  # its speaker links, attendance, agenda items and transcript go with it (ON DELETE CASCADE)
        written["sessions"] += 1
    db.flush()


def _remove_not_in(db: DbSession, written: Counter[str], model: Any, conference_id: uuid.UUID, keys: set[str]) -> None:
    """Delete the conference's rooms or speakers that left the fixture."""
    stmt = delete(model).where(model.conference_id == conference_id, model.external_id.not_in(sorted(keys)))
    removed = db.execute(stmt).rowcount
    if removed:
        written[model.__tablename__] += removed


def load_cloudbridge(db: DbSession, fixture: Fixture | None = None, *, reset: bool = False) -> LoadResult:
    """Load the fixture in the caller's transaction, so the whole load commits or none of it. A seed by default: it
    only adds what is missing. `reset=True` is the explicit full reseed (see the module docstring)."""
    fx = fixture if fixture is not None else read_fixture()
    zone = ZoneInfo(fx.conference.timezone)
    written: Counter[str] = Counter()
    # Wait for any other load to commit first; the lock is released when the caller's transaction ends.
    db.execute(text("SELECT pg_advisory_xact_lock(CAST(:key AS bigint))"), {"key": LOCK_KEY})

    conf, _ = _put(db, written, Conference, {"slug": fx.conference.slug}, conference_values(fx.conference), reset)
    db.flush()  # IDs are assigned on insert, and everything below needs the conference's
    rooms = {
        r.key: _put(db, written, Room, {"conference_id": conf.id, "external_id": r.key}, {"name": r.name}, reset)[0]
        for r in fx.rooms
    }
    speakers = {
        s.key: _put(
            db,
            written,
            Speaker,
            {"conference_id": conf.id, "external_id": s.key},
            {"name": s.name, "title": s.title, "company": s.company},
            reset,
        )[0]
        for s in fx.speakers
    }
    db.flush()

    if reset:
        _remove_sessions_not_in(db, written, conf.id, {s.key for s in fx.sessions})
    session_ids: dict[str, uuid.UUID] = {}
    new_sessions: set[str] = set()
    for spec in fx.sessions:
        # Only the ID: loading the row here would hand `upsert_session` a cached copy instead of the locked one.
        existing = db.scalar(
            select(Session.id).where(Session.conference_id == conf.id, Session.external_id == spec.key)
        )
        if existing is not None and not reset:
            session_ids[spec.key] = existing  # seed: an existing session keeps any edit, and its speakers
            continue
        row, changed = upsert_session(db, session_data(spec, conf.id, rooms[spec.room].id, zone))
        session_ids[spec.key] = row.id
        if existing is None:
            new_sessions.add(spec.key)
        if changed:
            written["sessions"] += 1
        desired = {speakers[key].id for key in spec.speakers}
        _converge_links(
            db, written, SessionSpeaker, SessionSpeaker.session_id, row.id, SessionSpeaker.speaker_id, desired
        )
    if reset:
        db.flush()  # sessions point at the fixture's rooms now, so the rooms that left have no sessions left
        _remove_not_in(db, written, Room, conf.id, set(rooms))
        _remove_not_in(db, written, Speaker, conf.id, set(speakers))

    for spec in fx.transcripts:
        # A transcript's status is the demo's state machine (PLAN §9): the seed never touches an existing row, so a
        # deploy can't unpublish what an organizer published; a reset puts the fixture's state back, as a new
        # revision whose job re-projects it (DATA-1).
        session_id = session_ids[spec.session]
        values = transcript_values(spec, speakers[spec.speaker].name)
        transcript = db.scalars(select(Transcript).where(Transcript.session_id == session_id)).one_or_none()
        if transcript is None:
            transcript = Transcript(session_id=session_id, status=spec.status, revision=1, **values)
            db.add(transcript)
            db.flush()
            enqueue(db, TRANSCRIPT_ENTITY, transcript.id, transcript.revision)
            written["transcripts"] += 1
        elif reset and (_assign(transcript, values) | _assign(transcript, {"status": spec.status})):
            transcript.revision += 1
            enqueue(db, TRANSCRIPT_ENTITY, transcript.id, transcript.revision)
            written["transcripts"] += 1

    new_profiles: set[str] = set()
    for key, values in fixture_profiles(fx).items():
        _, created = _put(db, written, Profile, {"id": profile_id(key)}, values, reset)
        if created:
            new_profiles.add(key)
    db.flush()  # profiles before the rows that reference them

    archive = set(session_ids.values())
    for persona in fx.personas:
        pid = profile_id(persona.key)
        wanted = {**persona.preferences.model_dump(), "excluded_levels": []}
        prefs, _ = _put(
            db,
            written,
            ProfilePreferences,
            {"profile_id": pid},
            {**wanted, "revision": 1},
            reset=False,  # an existing row is handled below, so its revision moves on instead of going back to 1
        )
        if reset and _assign(prefs, wanted):
            prefs.revision += 1  # the persona's own revision-aware edits (PATCH /me/preferences) see the change
            written["profile_preferences"] += 1
        for model, keys in ((Attendance, persona.attended), (AgendaItem, persona.bookmarked)):
            if reset:  # her CloudBridge history becomes the fixture's; her KubeCon agenda isn't in scope
                desired = {session_ids[key] for key in keys}
                _converge_links(db, written, model, model.profile_id, pid, model.session_id, desired, archive)
                continue
            for key in keys:  # seed: only with a new profile or a new session, so a removed bookmark stays removed
                if persona.key in new_profiles or key in new_sessions:
                    _add_missing(db, written, model, pid, session_ids[key])
    db.flush()
    return LoadResult(conference_id=conf.id, written=written)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m conferenceops.cloudbridge",
        description="Load the CloudBridge Summit fixture into DATABASE_URL. By default it only adds what is missing.",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="the explicit full reseed: put back everything the fixture owns, including what people changed, and "
        "delete the sessions, rooms and speakers that left the fixture",
    )
    args = parser.parse_args(argv)
    cfg = settings()
    setup_telemetry("seed", cfg)
    if not cfg.database_url:
        log.error("DATABASE_URL is not set; nothing was loaded")
        return 2
    with transaction() as db:
        result = load_cloudbridge(db, reset=args.reset)
    # Counts per table only: never names, interests or preferences (PRIV-1).
    summary = ", ".join(f"{table} {count}" for table, count in sorted(result.written.items())) or "none"
    log.info("CloudBridge Summit fixture %s; rows written: %s", "reset" if args.reset else "seeded", summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
