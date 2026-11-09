"""Dictation, the spoken half of the Ask bar (PLAN §9.2): a short recording comes in, the recognized text goes out,
and the attendee reviews it in the Ask bar before anything is searched.

PRIV-1: the audio and the text never reach logs, traces or metrics; only sizes, durations and lengths do. The audio
lives in memory for the length of the request and is never written anywhere. SEC-1: the OpenAI key stays here; the
browser only ever talks to this route."""

import logging
import time
from functools import lru_cache
from typing import Annotated, Any, Protocol

from fastapi import APIRouter, Depends, HTTPException, Request
from openai import OpenAI
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from conferenceops.settings import settings

log = logging.getLogger("conferenceops.dictation")

router = APIRouter(tags=["dictation"])

MAX_BYTES = 10 * 1024 * 1024  # the app's own bound (PLAN §9.2), far below the provider's 25 MB
MAX_SECONDS = 60  # enforced by the browser, which stops the recorder; the server bounds bytes, not time
TIMEOUT_SECONDS = 20.0  # one attempt, no retries: a slow model fails fast and the attendee types instead

# The containers current browsers record (webm/opus in Chrome and Firefox, mp4/aac in Safari, ogg/opus as Firefox's
# fallback), plus the common file types, mapped to the extension the transcription API recognizes the container by.
AUDIO_TYPES: dict[str, str] = {
    "audio/webm": "webm",
    "audio/mp4": "mp4",
    "audio/x-m4a": "m4a",
    "audio/m4a": "m4a",
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/wave": "wav",
    "audio/ogg": "ogg",
}

# Static context for the model: the event's own words, so "KubeCon" and "eBPF" don't come back as something else.
# Never anything the attendee said or typed (PRIV-1).
TRANSCRIPTION_PROMPT = (
    "An attendee asks which conference talks to go to, at KubeCon + CloudNativeCon: Kubernetes, cloud native, "
    "observability, security, platform engineering, AI."
)
TRANSCRIPTION_KEYWORDS = [
    "KubeCon",
    "CloudNativeCon",
    "Kubernetes",
    "eBPF",
    "OpenTelemetry",
    "GitOps",
    "WebAssembly",
    "Istio",
    "Cilium",
    "Argo CD",
]

REQUEST_BODY: dict[str, Any] = {
    "required": True,
    "description": f"The recording itself, at most {MAX_BYTES // (1024 * 1024)} MB and {MAX_SECONDS} seconds.",
    "content": {media_type: {"schema": {"type": "string", "format": "binary"}} for media_type in AUDIO_TYPES},
}


class DictationResponse(BaseModel):
    text: str = Field(description="The recognized text, for the attendee to review; empty when nothing was recognized")


class Transcriber(Protocol):
    def transcribe(self, audio: bytes, media_type: str) -> str: ...


class OpenAITranscriber:
    """gpt-transcribe through the official SDK: one attempt with a short timeout, nothing cached or stored."""

    def __init__(self, client: OpenAI, model: str):
        self.client = client
        self.model = model

    def transcribe(self, audio: bytes, media_type: str) -> str:
        filename = f"dictation.{AUDIO_TYPES[media_type]}"
        result = self.client.audio.transcriptions.create(
            model=self.model,
            file=(filename, audio, media_type),
            prompt=TRANSCRIPTION_PROMPT,
            keywords=TRANSCRIPTION_KEYWORDS,
            response_format="json",
        )
        return result.text.strip()


@lru_cache
def get_transcriber() -> Transcriber:
    cfg = settings()
    if not cfg.openai_api_key:
        raise HTTPException(status_code=503, detail="Dictation isn't configured")
    client = OpenAI(api_key=cfg.openai_api_key, timeout=TIMEOUT_SECONDS, max_retries=0)
    return OpenAITranscriber(client, cfg.openai_transcribe_model)


def media_type_of(content_type: str) -> str:
    """`audio/webm;codecs=opus` -> `audio/webm`."""
    return content_type.split(";", 1)[0].strip().lower()


async def read_capped(request: Request, limit: int) -> bytes:
    """Read the body into memory, giving up as soon as it passes the limit, so a large upload never fills memory."""
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > limit:
            raise HTTPException(status_code=413, detail=f"The recording is too large; dictation takes {MAX_SECONDS}s")
        chunks.append(chunk)
    return b"".join(chunks)


@router.post(
    "/api/v1/dictation",
    response_model=DictationResponse,
    openapi_extra={"requestBody": REQUEST_BODY},
    responses={
        400: {"description": "The recording is empty"},
        413: {"description": "The recording is larger than the app's limit"},
        415: {"description": "The body isn't an audio type the recorder produces"},
        503: {"description": "Transcription isn't configured or isn't available; typing still works"},
    },
)
async def dictate(request: Request, transcriber: Annotated[Transcriber, Depends(get_transcriber)]) -> DictationResponse:
    """Transcribe one short recording, sent as the request body with its audio media type. The text comes back for the
    attendee to review and edit in the Ask bar; nothing is searched until they submit (PLAN §9.2)."""
    media_type = media_type_of(request.headers.get("content-type", ""))
    if media_type not in AUDIO_TYPES:
        raise HTTPException(
            status_code=415,
            detail="Send the recording as audio/webm, audio/mp4, audio/mpeg or audio/wav",
        )
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_BYTES:
        raise HTTPException(status_code=413, detail=f"The recording is too large; dictation takes {MAX_SECONDS}s")
    audio = await read_capped(request, MAX_BYTES)
    if not audio:
        raise HTTPException(status_code=400, detail="The recording is empty")

    started = time.perf_counter()
    try:
        text = await run_in_threadpool(transcriber.transcribe, audio, media_type)
    except Exception as exc:
        # PRIV-1 and SEC-1: the kind of failure and the sizes, never the audio, the text or the provider's message.
        log.warning(
            "transcription failed: %s (status %s) after %.2fs for %d bytes of %s",
            type(exc).__name__,
            getattr(exc, "status_code", None),
            time.perf_counter() - started,
            len(audio),
            media_type,
        )
        raise HTTPException(status_code=503, detail="Transcription isn't available right now") from exc
    log.info(
        "transcribed %d bytes of %s into %d characters in %.2fs",
        len(audio),
        media_type,
        len(text),
        time.perf_counter() - started,
    )
    return DictationResponse(text=text)
