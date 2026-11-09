"""Transcripts and captioned replays of concluded CloudBridge sessions, and their publication boundary (PLAN §9).

The `transcripts` row is the publication manifest (TRANSCRIPT-1): its `status` says whether the text may reach search
and Ask, and its `revision` moves forward on every change. Search chunks are cut from the cues here, deterministically,
and each chunk's ID carries the transcript's revision, so retrieval can check a hit against the manifest: a chunk is
only valid while its transcript is published at the revision it was built from.

core-api serves two things from here:
- `GET /api/v1/sessions/{id}/replay`: the replay (video and captions) with the transcript when it is published, or
  the processing state without a word of text. The media is a simulated excerpt and says so (SPEAKER-1).
- `POST /api/v1/ops/transcripts/{id}/publish` and `.../unpublish`: the organizer action that moves a transcript
  across the boundary. The role comes from the persona in `X-Demo-Profile-ID`, resolved here; a client-sent role
  is never trusted (AUTHZ-1). The status change and its outbox job commit together (DATA-1); the text becomes
  searchable only once the worker has projected it, and an unpublish removes it the same way."""

import logging
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Annotated, Protocol

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from conferenceops.db import enqueue, transaction
from conferenceops.models import Conference, Profile, Role, Session, SessionSpeaker, Speaker, Transcript
from conferenceops.models import TranscriptStatus as Status
from conferenceops.search import ApiModel, venue_time

log = logging.getLogger("conferenceops.transcripts")

LABEL = "Simulated excerpt"
ENTITY = "transcript"  # the outbox entity type; transcript_indexing.py consumes it
CHUNK_WORDS = 80  # the excerpts are 60-90 s long, so this gives 2-4 citable chunks per clip
DEMO_PROFILE_HEADER = "X-Demo-Profile-ID"


# --- Chunks: the unit of retrieval and citation ---------------------------------------------------------------------


@dataclass(frozen=True)
class Chunk:
    index: int
    start: float  # seconds into the replay, from the first cue in the chunk
    end: float
    text: str


def chunk_cues(cues: Sequence[Mapping], max_words: int = CHUNK_WORDS) -> list[Chunk]:
    """Cut the caption cues into consecutive chunks of at most `max_words` words (a cue is never split), each keeping
    the replay timings of its cues. Deterministic, so the indexer and the Ask validation cut the same chunks."""
    chunks: list[Chunk] = []
    group: list[Mapping] = []
    words = 0
    for cue in cues:
        count = len(str(cue["text"]).split())
        if group and words + count > max_words:
            chunks.append(_chunk(len(chunks), group))
            group, words = [], 0
        group.append(cue)
        words += count
    if group:
        chunks.append(_chunk(len(chunks), group))
    return chunks


def _chunk(index: int, cues: Sequence[Mapping]) -> Chunk:
    return Chunk(
        index=index,
        start=float(cues[0]["start"]),
        end=float(cues[-1]["end"]),
        text=" ".join(str(cue["text"]).strip() for cue in cues),
    )


def chunk_id(transcript_id: uuid.UUID, revision: int, index: int) -> str:
    """The search document ID: transcript, revision and chunk, so two revisions never share a document (PLAN §8.1)."""
    return f"{transcript_id}:{revision}:{index}"


def parse_chunk_id(doc_id: str) -> tuple[uuid.UUID, int, int] | None:
    """The parts of a chunk ID, or None for anything that isn't one."""
    parts = doc_id.split(":")
    if len(parts) != 3:
        return None
    try:
        return uuid.UUID(parts[0]), int(parts[1]), int(parts[2])
    except ValueError:
        return None


def transcript_text(cues: Sequence[Mapping]) -> str:
    return " ".join(str(cue["text"]).strip() for cue in cues)


def change_status(db: DbSession, transcript: Transcript, status: Status) -> bool:
    """Move a transcript across the publication boundary in the caller's transaction: a new revision and the outbox
    job that projects it commit together (DATA-1). Returns False when it was already in that state."""
    if transcript.status == status:
        return False
    transcript.status = status
    transcript.revision += 1
    enqueue(db, ENTITY, transcript.id, transcript.revision)
    return True


# --- API models -----------------------------------------------------------------------------------------------------


class Cue(ApiModel):
    start: float = Field(description="Seconds into the replay")
    end: float
    text: str


class ReplaySession(ApiModel):
    id: str
    title: str
    speakers: list[str]
    event: str
    conference: str
    is_fictional: bool
    starts_at_venue: str


class ReplayMedia(ApiModel):
    url: str
    captions_url: str
    duration_seconds: float
    label: str = Field(description=f"'{LABEL}': the media is synthesized for the fictional event (SPEAKER-1)")


class TranscriptView(ApiModel):
    status: Status
    revision: int
    label: str
    cues: list[Cue] = Field(default_factory=list, description="Empty until the transcript is published")
    text: str | None = Field(default=None, description="The whole transcript; only when published (TRANSCRIPT-1)")


class ReplayView(ApiModel):
    session: ReplaySession
    media: ReplayMedia
    transcript: TranscriptView


class PublishRequest(ApiModel):
    conference: str = Field(description="The conference the session belongs to, e.g. cloudbridge-summit-2026")


class PublishResponse(ApiModel):
    session_id: str
    status: Status
    revision: int
    changed: bool = Field(description="False when the transcript was already in that state")
    note: str


# --- Storage --------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class StatusChange:
    session_id: uuid.UUID
    status: Status
    revision: int
    changed: bool


class TranscriptStore(Protocol):
    def replay(self, session_id: uuid.UUID) -> ReplayView | None: ...

    def role_of(self, profile_id: uuid.UUID) -> Role | None: ...

    def set_status(self, session_id: uuid.UUID, conference: str, status: Status) -> StatusChange | None:
        """None when the session has no transcript, or isn't in that conference (404, AUTHZ-1)."""


def replay_view(
    transcript: Transcript, session: Session, conference: Conference, speakers: Sequence[str]
) -> ReplayView:
    """The replay as the API shows it. Text and cues are included only for a published transcript (TRANSCRIPT-1)."""
    label = LABEL if transcript.is_simulated else "Recording"
    published = transcript.status == Status.PUBLISHED
    return ReplayView(
        session=ReplaySession(
            id=str(session.id),
            title=session.title,
            speakers=list(speakers),
            event=conference.name,
            conference=conference.slug,
            is_fictional=conference.is_fictional,
            starts_at_venue=venue_time(session.starts_at),
        ),
        media=ReplayMedia(
            url=transcript.media_url,
            captions_url=transcript.captions_url,
            duration_seconds=transcript.duration_seconds,
            label=label,
        ),
        transcript=TranscriptView(
            status=transcript.status,
            revision=transcript.revision,
            label=label,
            cues=[Cue(start=c["start"], end=c["end"], text=c["text"]) for c in transcript.cues] if published else [],
            text=transcript.text if published else None,
        ),
    )


class PostgresTranscriptStore:
    def replay(self, session_id: uuid.UUID) -> ReplayView | None:
        with transaction() as db:
            row = db.execute(
                select(Transcript, Session, Conference)
                .join(Session, Session.id == Transcript.session_id)
                .join(Conference, Conference.id == Session.conference_id)
                .where(Transcript.session_id == session_id)
            ).one_or_none()
            if row is None:
                return None
            transcript, session, conference = row
            speakers = db.scalars(
                select(Speaker.name)
                .join(SessionSpeaker, SessionSpeaker.speaker_id == Speaker.id)
                .where(SessionSpeaker.session_id == session.id)
                .order_by(Speaker.name)
            ).all()
            return replay_view(transcript, session, conference, speakers)

    def role_of(self, profile_id: uuid.UUID) -> Role | None:
        with transaction() as db:
            profile = db.get(Profile, profile_id)
            return profile.role if profile else None

    def set_status(self, session_id: uuid.UUID, conference: str, status: Status) -> StatusChange | None:
        with transaction() as db:
            transcript = db.scalars(
                select(Transcript)
                .join(Session, Session.id == Transcript.session_id)
                .join(Conference, Conference.id == Session.conference_id)
                .where(Transcript.session_id == session_id, Conference.slug == conference)
                .with_for_update(of=Transcript)
            ).one_or_none()
            if transcript is None:
                return None
            changed = change_status(db, transcript, status)
            return StatusChange(session_id, transcript.status, transcript.revision, changed)


def get_transcript_store() -> TranscriptStore:
    return PostgresTranscriptStore()


# --- Routes ---------------------------------------------------------------------------------------------------------

router = APIRouter(tags=["transcripts"])


def organizer(
    store: Annotated[TranscriptStore, Depends(get_transcript_store)],
    x_demo_profile_id: Annotated[str | None, Header(alias=DEMO_PROFILE_HEADER)] = None,
) -> uuid.UUID:
    """The acting persona, who must be an organizer. The role is looked up in PostgreSQL; the header carries only an
    ID (AUTHZ-1). Anything else is 403: no persona, an unknown one, or one without the role."""
    try:
        profile_id = uuid.UUID((x_demo_profile_id or "").strip())
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="Choose an organizer persona first") from exc
    if store.role_of(profile_id) != Role.ORGANIZER:
        raise HTTPException(status_code=403, detail="Only an organizer can publish or unpublish a transcript")
    return profile_id


@router.get("/api/v1/sessions/{session_id}/replay", response_model=ReplayView, response_model_by_alias=True)
def get_replay(session_id: uuid.UUID, store: Annotated[TranscriptStore, Depends(get_transcript_store)]) -> ReplayView:
    """The captioned replay of a concluded session, with its transcript only once published (TRANSCRIPT-1)."""
    view = store.replay(session_id)
    if view is None:
        raise HTTPException(status_code=404, detail="This session has no replay")
    return view


def _set(session_id: uuid.UUID, body: PublishRequest, store: TranscriptStore, status: Status) -> PublishResponse:
    change = store.set_status(session_id, body.conference, status)
    if change is None:
        raise HTTPException(status_code=404, detail="No transcript for this session in that conference")
    note = (
        "Queued: searchable and citable once the worker projects this revision"
        if status == Status.PUBLISHED
        else "Queued: the worker removes this transcript from search; it is not citable"
    )
    log.info(
        "transcript status set",
        extra={"session_id": str(session_id), "status": status.value, "changed": change.changed},
    )
    return PublishResponse(
        session_id=str(session_id),
        status=change.status,
        revision=change.revision,
        changed=change.changed,
        note=note if change.changed else f"Already {change.status.value}; nothing to do",
    )


@router.post(
    "/api/v1/ops/transcripts/{session_id}/publish", response_model=PublishResponse, response_model_by_alias=True
)
def publish_transcript(
    session_id: uuid.UUID,
    body: PublishRequest,
    store: Annotated[TranscriptStore, Depends(get_transcript_store)],
    _actor: Annotated[uuid.UUID, Depends(organizer)],
) -> PublishResponse:
    """Organizer only (AUTHZ-1). Publishes the transcript: a new revision and its outbox job commit together, and
    the text becomes searchable once the worker has indexed it (TRANSCRIPT-1, DATA-1)."""
    return _set(session_id, body, store, Status.PUBLISHED)


@router.post(
    "/api/v1/ops/transcripts/{session_id}/unpublish", response_model=PublishResponse, response_model_by_alias=True
)
def unpublish_transcript(
    session_id: uuid.UUID,
    body: PublishRequest,
    store: Annotated[TranscriptStore, Depends(get_transcript_store)],
    _actor: Annotated[uuid.UUID, Depends(organizer)],
) -> PublishResponse:
    """Organizer only (AUTHZ-1). Takes the transcript back to processing, as a correction does: a new revision whose
    projection removes every chunk from search, and no older job can bring them back (DATA-1)."""
    return _set(session_id, body, store, Status.PROCESSING)
