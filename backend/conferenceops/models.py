"""The canonical data model (PostgreSQL). Elasticsearch only holds projections of it, fed through the outbox.

Two events live side by side (PLAN §1.4): KubeCon NA 2026, imported from its public feed, and the fictional
CloudBridge Summit, authored here. Private persona preferences live in their own table so they can never leak into
public DTOs, search documents or telemetry (PRIV-1)."""

import enum
import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _enum(cls: type[enum.Enum]) -> Enum:
    return Enum(cls, name=cls.__name__.lower(), values_callable=lambda e: [m.value for m in e])


class EventKind(enum.StrEnum):
    AUTHORED = "authored"  # created on our platform (CloudBridge Summit)
    IMPORTED = "imported"  # imported from an external schedule (KubeCon)


class ClockMode(enum.StrEnum):
    FIXED = "fixed"  # a concluded event: its clock never advances
    WALL = "wall"  # a current event: real time in its time zone


class Level(enum.StrEnum):
    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"
    ALL = "all"


class Role(enum.StrEnum):
    ATTENDEE = "attendee"
    SPEAKER = "speaker"
    PARTNER = "partner"
    ORGANIZER = "organizer"
    HOST = "host"


class AccessFactKind(enum.StrEnum):
    CAPTIONS = "captions"
    RESERVED_SEATING = "reserved_seating"
    QUIET_ROOM = "quiet_room"
    PRAYER_ROOM = "prayer_room"
    STAFF_HELP = "staff_help"
    MOBILITY_RENTAL = "mobility_rental"


class OutboxStatus(enum.StrEnum):
    PENDING = "pending"
    DONE = "done"
    FAILED = "failed"


class TranscriptStatus(enum.StrEnum):
    PROCESSING = "processing"  # concluded, not published: the replay plays, but search and Ask never see the text
    PUBLISHED = "published"  # the only state whose text reaches Elasticsearch and the model (TRANSCRIPT-1)


def _uuid() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _created() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Conference(Base):
    __tablename__ = "conferences"
    id: Mapped[uuid.UUID] = _uuid()
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    timezone: Mapped[str] = mapped_column(String(64))  # IANA name, never a fixed offset (TIME-1)
    starts_on: Mapped[date] = mapped_column(Date)
    ends_on: Mapped[date] = mapped_column(Date)
    kind: Mapped[EventKind] = mapped_column(_enum(EventKind))
    source: Mapped[str] = mapped_column(Text)  # "fixture" or the feed URL
    clock_mode: Mapped[ClockMode] = mapped_column(_enum(ClockMode))
    is_fictional: Mapped[bool] = mapped_column(Boolean)
    has_transcripts: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = _created()


class Room(Base):
    __tablename__ = "rooms"
    __table_args__ = (UniqueConstraint("conference_id", "external_id"),)
    id: Mapped[uuid.UUID] = _uuid()
    conference_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conferences.id", ondelete="CASCADE"), index=True)
    external_id: Mapped[str | None] = mapped_column(String(64))  # the feed's room ID for imported events
    name: Mapped[str] = mapped_column(String(200))


class Speaker(Base):
    __tablename__ = "speakers"
    __table_args__ = (UniqueConstraint("conference_id", "external_id"),)
    id: Mapped[uuid.UUID] = _uuid()
    conference_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conferences.id", ondelete="CASCADE"), index=True)
    external_id: Mapped[str | None] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(200))
    company: Mapped[str | None] = mapped_column(String(200))
    title: Mapped[str | None] = mapped_column(String(200))


class Session(Base):
    """A talk. `revision` increases on every change, so a late projection can never overwrite a newer one."""

    __tablename__ = "sessions"
    __table_args__ = (UniqueConstraint("conference_id", "external_id"),)
    id: Mapped[uuid.UUID] = _uuid()
    conference_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conferences.id", ondelete="CASCADE"), index=True)
    room_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("rooms.id", ondelete="SET NULL"))
    external_id: Mapped[str | None] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(Text)
    abstract: Mapped[str | None] = mapped_column(Text)
    level: Mapped[Level | None] = mapped_column(_enum(Level))
    topics: Mapped[list[str]] = mapped_column(ARRAY(String(100)), default=list)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))  # UTC instant (TIME-1)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    is_service: Mapped[bool] = mapped_column(Boolean, default=False)  # breaks, registration: never recommended
    materials: Mapped[dict] = mapped_column(JSONB, default=dict)  # the speaker's own links: slides, repo, demo
    revision: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SessionSpeaker(Base):
    __tablename__ = "session_speakers"
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), primary_key=True)
    speaker_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("speakers.id", ondelete="CASCADE"), primary_key=True)


class AccessFact(Base):
    """A published accessibility fact, shown only with its source link (ACCESS-1)."""

    __tablename__ = "access_facts"
    id: Mapped[uuid.UUID] = _uuid()
    conference_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conferences.id", ondelete="CASCADE"), index=True)
    kind: Mapped[AccessFactKind] = mapped_column(_enum(AccessFactKind))
    text: Mapped[str] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(String(200))
    source_url: Mapped[str] = mapped_column(Text)
    read_on: Mapped[date] = mapped_column(Date)


class Profile(Base):
    """A demo persona or public profile. Public fields only; private preferences are in `profile_preferences`."""

    __tablename__ = "profiles"
    id: Mapped[uuid.UUID] = _uuid()
    role: Mapped[Role] = mapped_column(_enum(Role))
    display_name: Mapped[str] = mapped_column(String(200))
    company: Mapped[str | None] = mapped_column(String(200))
    interests: Mapped[list[str]] = mapped_column(ARRAY(String(100)), default=list)
    demo_selectable: Mapped[bool] = mapped_column(Boolean, default=False)
    is_fictional: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = _created()


class ProfilePreferences(Base):
    """Private, explicit choices; never inferred, never in public DTOs, search documents or telemetry (PRIV-1)."""

    __tablename__ = "profile_preferences"
    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"), primary_key=True)
    needs_step_free: Mapped[bool] = mapped_column(Boolean, default=False)
    wants_captions: Mapped[bool] = mapped_column(Boolean, default=False)
    reduced_motion: Mapped[bool] = mapped_column(Boolean, default=False)
    excluded_levels: Mapped[list[str]] = mapped_column(ARRAY(String(20)), default=list)
    revision: Mapped[int] = mapped_column(Integer, default=1)


class Attendance(Base):
    """Sessions a persona attended at a past event: the history behind "where should I go next?"."""

    __tablename__ = "attendance"
    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"), primary_key=True)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), primary_key=True)


class AgendaItem(Base):
    """A personal plan or bookmark. Intent only: it never reserves a seat."""

    __tablename__ = "agenda_items"
    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"), primary_key=True)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), primary_key=True)
    created_at: Mapped[datetime] = _created()


class Transcript(Base):
    """A concluded session's transcript with its captioned replay: the publication manifest of TRANSCRIPT-1.

    `cues` are the caption cues (start, end, text in seconds); `text` is the same words as one string. `revision`
    moves forward on every change, publishing and unpublishing included, and a search chunk carries the revision it
    was built from: retrieval only returns a chunk whose transcript is published at that same revision, so an older
    or superseded revision never resurfaces (DATA-1). Only the fictional CloudBridge Summit has transcripts, and
    they are simulated excerpts, labeled as such wherever they are shown (SPEAKER-1)."""

    __tablename__ = "transcripts"
    id: Mapped[uuid.UUID] = _uuid()
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), unique=True)
    speaker_name: Mapped[str] = mapped_column(String(200))
    is_simulated: Mapped[bool] = mapped_column(Boolean, default=True)
    media_url: Mapped[str] = mapped_column(Text)  # the replay video, served by the web container
    captions_url: Mapped[str] = mapped_column(Text)  # its WebVTT captions
    duration_seconds: Mapped[float] = mapped_column(Float)
    text: Mapped[str] = mapped_column(Text)
    cues: Mapped[list[dict]] = mapped_column(JSONB, default=list)
    status: Mapped[TranscriptStatus] = mapped_column(_enum(TranscriptStatus))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    created_at: Mapped[datetime] = _created()


class OutboxJob(Base):
    """Work for the worker, written in the same transaction as the canonical change it describes (DATA-1)."""

    __tablename__ = "outbox_jobs"
    id: Mapped[uuid.UUID] = _uuid()
    entity_type: Mapped[str] = mapped_column(String(50))
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    entity_revision: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(50))  # e.g. "index", "delete"
    status: Mapped[OutboxStatus] = mapped_column(_enum(OutboxStatus), default=OutboxStatus.PENDING, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
