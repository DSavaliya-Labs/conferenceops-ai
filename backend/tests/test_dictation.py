"""POST /api/v1/dictation on core-api: a short recording in, recognized text out, with a fake transcriber. PRIV-1 is
proven here: neither the audio nor the text ever reaches a log line or a span attribute."""

import logging
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from starlette.requests import Request

from conferenceops.core_app.main import app
from conferenceops.dictation import (
    MAX_BYTES,
    TRANSCRIPTION_KEYWORDS,
    OpenAITranscriber,
    get_transcriber,
    media_type_of,
    read_capped,
    router,
)
from conferenceops.settings import Settings
from conferenceops.web import instrument

SPOKEN = "which talks cover kubernetes security for beginners"
# Bytes that look like a WebM header, followed by a marker that must never show up in logs or spans.
AUDIO = b"\x1a\x45\xdf\xa3" + b"private-attendee-audio" * 40


class FakeTranscriber:
    def __init__(self, text: str = SPOKEN, fail: Exception | None = None):
        self.text = text
        self.fail = fail
        self.calls: list[tuple[int, str]] = []

    def transcribe(self, audio: bytes, media_type: str) -> str:
        self.calls.append((len(audio), media_type))
        if self.fail:
            raise self.fail
        return self.text


@pytest.fixture
def transcriber():
    fake = FakeTranscriber()
    app.dependency_overrides[get_transcriber] = lambda: fake
    try:
        yield fake
    finally:
        app.dependency_overrides.clear()


def post(client: TestClient, body: bytes = AUDIO, content_type: str = "audio/webm;codecs=opus", **headers):
    return client.post("/api/v1/dictation", content=body, headers={"content-type": content_type, **headers})


def test_a_recording_comes_back_as_text_for_the_attendee_to_review(transcriber):
    response = post(TestClient(app))
    assert response.status_code == 200
    assert response.json() == {"text": SPOKEN}
    assert transcriber.calls == [(len(AUDIO), "audio/webm")]  # the codecs parameter is stripped


def test_neither_the_audio_nor_the_text_reaches_the_logs(transcriber, caplog):
    with caplog.at_level(logging.INFO, logger="conferenceops.dictation"):
        assert post(TestClient(app)).status_code == 200
    assert caplog.records, "the request logged nothing at all"
    assert SPOKEN not in caplog.text
    assert "private-attendee-audio" not in caplog.text
    assert str(len(AUDIO)) in caplog.text and str(len(SPOKEN)) in caplog.text  # lengths, not content (PRIV-1)


def test_a_provider_failure_is_503_and_logs_only_the_kind_of_failure(transcriber, caplog):
    transcriber.fail = TimeoutError(f"gave up on {SPOKEN}")  # a message carrying content must not be copied
    with caplog.at_level(logging.WARNING, logger="conferenceops.dictation"):
        response = post(TestClient(app))
    assert response.status_code == 503
    assert response.json()["detail"] == "Transcription isn't available right now"
    assert "TimeoutError" in caplog.text
    assert SPOKEN not in caplog.text
    assert "private-attendee-audio" not in caplog.text


def test_an_oversize_recording_is_413_before_the_model_is_called(transcriber):
    client = TestClient(app)
    assert post(client, body=b"x" * (MAX_BYTES + 1)).status_code == 413
    # A lying Content-Length header is refused up front, without reading the body.
    assert post(client, **{"content-length": str(MAX_BYTES + 1)}).status_code == 413
    assert transcriber.calls == []


def test_a_body_that_grows_past_the_limit_without_a_length_is_cut_off_at_413():
    async def receive():
        return {"type": "http.request", "body": b"x" * 1024, "more_body": True}

    request = Request({"type": "http", "method": "POST", "path": "/api/v1/dictation", "headers": []}, receive)

    async def run():
        await read_capped(request, limit=4096)

    import asyncio

    with pytest.raises(Exception) as excinfo:
        asyncio.run(run())
    assert getattr(excinfo.value, "status_code", None) == 413


def test_the_body_must_be_audio_and_must_not_be_empty(transcriber):
    client = TestClient(app)
    assert post(client, body=b'{"text": "hi"}', content_type="application/json").status_code == 415
    assert post(client, body=b"", content_type="audio/mp4").status_code == 400
    assert transcriber.calls == []


def test_without_an_api_key_dictation_says_it_is_not_configured(monkeypatch):
    app.dependency_overrides.clear()
    get_transcriber.cache_clear()
    monkeypatch.setattr("conferenceops.dictation.settings", lambda: Settings(openai_api_key=None))
    response = post(TestClient(app))
    assert response.status_code == 503
    assert response.json()["detail"] == "Dictation isn't configured"


def test_media_type_parsing_drops_parameters_and_case():
    assert media_type_of("audio/webm;codecs=opus") == "audio/webm"
    assert media_type_of("Audio/MP4") == "audio/mp4"
    assert media_type_of("") == ""


def test_the_sdk_gets_the_recording_as_a_named_file_with_the_configured_model():
    calls = []

    class FakeTranscriptions:
        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(text=f"  {SPOKEN} \n")

    client = SimpleNamespace(audio=SimpleNamespace(transcriptions=FakeTranscriptions()))
    text = OpenAITranscriber(client, "gpt-transcribe").transcribe(AUDIO, "audio/mp4")

    assert text == SPOKEN
    (call,) = calls
    assert call["model"] == "gpt-transcribe"
    assert call["file"] == ("dictation.mp4", AUDIO, "audio/mp4")
    assert call["keywords"] == TRANSCRIPTION_KEYWORDS
    assert call["response_format"] == "json"


def test_the_request_span_carries_no_audio_and_no_text():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    isolated = FastAPI()
    isolated.include_router(router)
    isolated.dependency_overrides[get_transcriber] = lambda: FakeTranscriber()
    instrument(isolated, tracer_provider=provider)

    assert post(TestClient(isolated)).status_code == 200

    spans = exporter.get_finished_spans()
    assert spans, "the request produced no span"
    for span in spans:
        for name, value in (span.attributes or {}).items():
            assert SPOKEN not in str(value), f"{span.name} attribute {name} carries the text"
            assert "private-attendee-audio" not in str(value), f"{span.name} attribute {name} carries the audio"
