"""Import KubeCon + CloudNativeCon NA 2026 from its public Sessionize feed (the one the official schedule page
embeds: sessionizeApiCode svi82w6c). Real data only: we never add facts the feed or the official accessibility page
doesn't publish (ACCESS-1), and never generate words for real speakers (SPEAKER-1)."""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import delete, select
from sqlalchemy.orm import Session as DbSession

from conferenceops.catalog import SessionData, upsert_session
from conferenceops.db import enqueue
from conferenceops.models import (
    AccessFact,
    AccessFactKind,
    ClockMode,
    Conference,
    EventKind,
    Level,
    Room,
    Session,
    SessionSpeaker,
    Speaker,
)

FEED_URL = "https://sessionize.com/api/v2/svi82w6c/view/All"
SLUG = "kubecon-na-2026"
VENUE_TZ = ZoneInfo("America/Denver")
ACCESS_PAGE = "https://events.linuxfoundation.org/kubecon-cloudnativecon-north-america/attend/inclusion-accessibility/"
ACCESS_READ_ON = date(2026, 11, 6)

LEVELS = {"Beginner": Level.BEGINNER, "Advanced": Level.ADVANCED, "Any Level": Level.ALL}
MATERIAL_QUESTIONS = {"Presentation Slides": "slides", "GitHub Repository": "repository", "Project Demo": "demo"}
TOPIC_CATEGORIES = ("SubTopic", "CNCF Projects")

# Published on the official Inclusion + Accessibility page, read on Nov 6 2026. Nothing else is claimed (ACCESS-1).
ACCESS_FACTS: tuple[tuple[AccessFactKind, str, str | None], ...] = (
    (
        AccessFactKind.CAPTIONS,
        "Captioning in 60+ languages through Wordly, with instructions in each breakout room.",
        None,
    ),
    (AccessFactKind.RESERVED_SEATING, "Reserved seating in each session for attendees with accessibility needs.", None),
    (AccessFactKind.QUIET_ROOM, "All-Attendee Quiet Room.", "Salt Palace, Level 2, room 260A"),
    (AccessFactKind.QUIET_ROOM, "Women + Non-Binary Quiet Room.", "Salt Palace, Level 2, room 260B"),
    (AccessFactKind.PRAYER_ROOM, "Prayer Room.", "Hyatt Regency, Level 5, Zion"),
    (
        AccessFactKind.STAFF_HELP,
        "For help locating an elevator or other accessibility questions, ask Linux Foundation staff at the "
        "registration or information desks.",
        None,
    ),
    (AccessFactKind.MOBILITY_RENTAL, "Wheelchair and scooter rental is available.", None),
)


def venue_instant(local: str) -> datetime:
    """The feed's times are venue-local without an offset; attach the IANA zone, then store UTC (TIME-1)."""
    return datetime.fromisoformat(local).replace(tzinfo=VENUE_TZ).astimezone(UTC)


@dataclass
class ParsedSession:
    external_id: str
    title: str
    abstract: str | None
    starts_at: datetime
    ends_at: datetime
    level: Level | None
    topics: list[str]
    room_external_id: str | None
    speaker_external_ids: list[str]
    is_service: bool
    materials: dict[str, str] = field(default_factory=dict)


@dataclass
class ParsedFeed:
    rooms: dict[str, str]  # external id -> name
    speakers: dict[str, str]  # external id -> full name
    sessions: list[ParsedSession]


def parse_feed(feed: dict) -> ParsedFeed:
    categories = {item["id"]: (cat["title"], item["name"]) for cat in feed["categories"] for item in cat["items"]}
    questions = {q["id"]: q["question"] for q in feed.get("questions", [])}
    sessions = []
    for s in feed["sessions"]:
        cats = [categories[i] for i in s.get("categoryItems", []) if i in categories]
        level = next((LEVELS[name] for title, name in cats if title == "Audience Level" and name in LEVELS), None)
        topics = sorted({name for title, name in cats if title in TOPIC_CATEGORIES})
        materials = {}
        for answer in s.get("questionAnswers") or []:
            kind = MATERIAL_QUESTIONS.get(questions.get(answer.get("questionId"), ""))
            value = (answer.get("answerValue") or "").strip()
            if kind and value.startswith("https://"):
                materials[kind] = value
        sessions.append(
            ParsedSession(
                external_id=str(s["id"]),
                title=s["title"].strip(),
                abstract=(s.get("description") or "").strip() or None,
                starts_at=venue_instant(s["startsAt"]),
                ends_at=venue_instant(s["endsAt"]),
                level=level,
                topics=topics,
                room_external_id=str(s["roomId"]) if s.get("roomId") else None,
                speaker_external_ids=[str(x) for x in s.get("speakers") or []],
                is_service=bool(s.get("isServiceSession")),
                materials=materials,
            )
        )
    return ParsedFeed(
        rooms={str(r["id"]): r["name"] for r in feed["rooms"]},
        speakers={str(p["id"]): p.get("fullName") or f"{p['firstName']} {p['lastName']}" for p in feed["speakers"]},
        sessions=sessions,
    )


def fetch_feed(url: str = FEED_URL, timeout: float = 30.0) -> dict:
    response = httpx.get(url, timeout=timeout)
    response.raise_for_status()
    return response.json()


def _conference(db: DbSession) -> Conference:
    conf = db.scalars(select(Conference).where(Conference.slug == SLUG)).one_or_none()
    if conf is None:
        conf = Conference(
            slug=SLUG,
            name="KubeCon + CloudNativeCon North America 2026",
            timezone="America/Denver",
            starts_on=date(2026, 11, 8),
            ends_on=date(2026, 11, 12),
            kind=EventKind.IMPORTED,
            source=FEED_URL,
            clock_mode=ClockMode.WALL,
            is_fictional=False,
            has_transcripts=False,
        )
        db.add(conf)
        db.flush()
    return conf


def _by_external(db: DbSession, model, conference_id: uuid.UUID) -> dict[str, object]:
    return {row.external_id: row for row in db.scalars(select(model).where(model.conference_id == conference_id))}


def import_kubecon(db: DbSession, feed: dict) -> dict[str, int]:
    """Upsert the whole feed in the caller's transaction. Idempotent: unchanged sessions enqueue nothing."""
    parsed = parse_feed(feed)
    conf = _conference(db)

    rooms = _by_external(db, Room, conf.id)
    for ext, name in parsed.rooms.items():
        if ext not in rooms:
            rooms[ext] = Room(conference_id=conf.id, external_id=ext, name=name)
            db.add(rooms[ext])
        elif rooms[ext].name != name:
            rooms[ext].name = name
    speakers = _by_external(db, Speaker, conf.id)
    for ext, name in parsed.speakers.items():
        if ext not in speakers:
            speakers[ext] = Speaker(conference_id=conf.id, external_id=ext, name=name)
            db.add(speakers[ext])
        elif speakers[ext].name != name:
            speakers[ext].name = name
    db.flush()

    existing_sessions = _by_external(db, Session, conf.id)
    feed_session_ids = {session.external_id for session in parsed.sessions}
    for ext, row in existing_sessions.items():
        if ext not in feed_session_ids:
            enqueue(db, "session", row.id, row.revision + 1, kind="delete")
            db.delete(row)

    changed = 0
    for s in parsed.sessions:
        room = rooms.get(s.room_external_id) if s.room_external_id else None
        row, did_change = upsert_session(
            db,
            SessionData(
                conference_id=conf.id,
                external_id=s.external_id,
                title=s.title,
                starts_at=s.starts_at,
                ends_at=s.ends_at,
                abstract=s.abstract,
                level=s.level,
                topics=s.topics,
                room_id=room.id if room else None,
                is_service=s.is_service,
                materials=s.materials,
            ),
        )
        changed += did_change
        current_speaker_ids = {
            link.speaker_id for link in db.scalars(select(SessionSpeaker).where(SessionSpeaker.session_id == row.id))
        }
        desired_speaker_ids = {speakers[ext].id for ext in s.speaker_external_ids if ext in speakers}
        if current_speaker_ids != desired_speaker_ids:
            db.execute(delete(SessionSpeaker).where(SessionSpeaker.session_id == row.id))
            for speaker_id in desired_speaker_ids:
                db.add(SessionSpeaker(session_id=row.id, speaker_id=speaker_id))

    db.execute(delete(AccessFact).where(AccessFact.conference_id == conf.id))
    for kind, text, location in ACCESS_FACTS:
        db.add(
            AccessFact(
                conference_id=conf.id,
                kind=kind,
                text=text,
                location=location,
                source_url=ACCESS_PAGE,
                read_on=ACCESS_READ_ON,
            )
        )
    return {"sessions": len(parsed.sessions), "changed": changed, "rooms": len(parsed.rooms)}
