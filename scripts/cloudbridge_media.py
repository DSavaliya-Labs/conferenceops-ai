#!/usr/bin/env python3
"""Generate the three captioned replay clips of the fictional CloudBridge Summit 2026 (PLAN §6.1, §9.3).

Every clip is a simulated excerpt: a short text written for the demo in a fictional speaker's voice, read by an
OpenAI text-to-speech voice over a title slide. Nothing here is a real person or a real talk (SPEAKER-1), and the
clips are labeled "Simulated excerpt" in the slide, the captions, the fixture and the UI.

How a clip is made, from its cue texts in CLIPS:
1. Speech: `gpt-4o-mini-tts` reads the cues (`POST /v1/audio/speech`). The model is deprecated and shuts down on
   2027-01-06; OpenAI's replacement, `gpt-realtime-2.1-mini`, needs the Realtime API, a different workflow. The
   generated files are committed, so nothing at run time depends on the model.
2. Audio: ffmpeg adds 1.5 s of silence before the speech (the title slide and the label cue show first) and 1 s after.
3. Cue timings, derived from the audio, not from the text: `whisper-1` transcribes the final audio with word
   timestamps (`verbose_json`, `timestamp_granularities=["word"]`), and each cue is aligned to the spoken words
   (difflib on normalized words). A cue starts at its first matched word and ends at its last one; when the gap to
   the next cue is under 1 s the cue stays on screen until the next one starts. The manifest records how many of
   the written words the transcription matched (alignment_coverage); a run fails below 85%.
4. Captions: WebVTT with the speaker in `<v>` voice tags and a leading non-speech cue carrying the label.
5. Video: a 640x360 title slide (Pillow) plus the audio, assembled by ffmpeg into a small H.264/AAC mp4.
6. manifest.json records the provenance (models, voices, durations, coverage, checksums). With --write-fixture, the
   clips also become the `transcripts` entries of backend/conferenceops/fixtures/cloudbridge.json: clips 1 and 2
   published, clip 3 processing (it publishes during the demo, PLAN §9).

Model calls are cached under .cache/cloudbridge-media/ (git-ignored), so a re-run costs nothing unless a cue text
changed. Run it through scripts/generate-cloudbridge-media.sh, which checks the tools and the key. `--self-test`
checks the alignment and caption code on canned data and needs no key, no network and no extra package."""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
import subprocess
import sys
import textwrap
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MEDIA_DIR = ROOT / "apps" / "web" / "public" / "media" / "cloudbridge"
CACHE_DIR = ROOT / ".cache" / "cloudbridge-media"
FIXTURE = ROOT / "backend" / "conferenceops" / "fixtures" / "cloudbridge.json"
PUBLIC_PREFIX = "/media/cloudbridge"

TTS_MODEL = "gpt-4o-mini-tts"
TRANSCRIBE_MODEL = "whisper-1"
TTS_INSTRUCTIONS = (
    "You are giving a conference talk to engineers. Speak clearly at a natural, unhurried pace, with short pauses "
    "between sentences. Warm and confident, never salesy."
)
LEAD_SILENCE = 1.5
TRAIL_SILENCE = 1.0
GAP_TO_BRIDGE = 1.0  # a cue stays on screen across a pause shorter than this
MIN_COVERAGE = 0.85
WIDTH, HEIGHT = 640, 360
FPS = 10
LABEL = "Simulated excerpt"
EVENT = "CloudBridge Summit 2026"
FONT_DIR = Path("/System/Library/Fonts/Supplemental")


@dataclass(frozen=True)
class Clip:
    number: int
    session: str  # the fixture's session key
    title: str
    speaker_key: str
    speaker: str
    role: str
    voice: str
    status: str  # the transcript's state when the demo starts: published or processing
    cues: tuple[str, ...]  # what is said, one caption cue each; written for the demo, not real speech


CLIPS = (
    Clip(
        number=1,
        session="2",
        title="AI SRE in Practice: What Our Agent Got Wrong",
        speaker_key="S2",
        speaker="Daniel Kowalczyk",
        role="Principal engineer, Quillon Labs",
        voice="cedar",
        status="published",
        cues=(
            "Let me show you the morning our AI SRE agent got it wrong.",
            "At six twelve, the checkout latency alarm fired.",
            "Within forty seconds the agent had a diagnosis: the primary database was saturated,",
            "and it proposed a failover. It wrote a confident summary, with graphs.",
            "The database was fine.",
            "What the agent had seen was a jump in connection errors.",
            "What it had ignored was a config push nine minutes earlier",
            "that set the connection pool to zero on three of our twelve pods.",
            "The failover would have cost us about nine minutes of write downtime, for nothing.",
            "The guardrail that caught it was boring, and that is the point.",
            "Every change the agent proposes becomes a pull request with evidence links,",
            "and a human has to approve it.",
            "Our on-call engineer opened the first link, saw that the errors came from three pods only,",
            "and rejected the fix in one click.",
            "So here is my checklist.",
            "Demand evidence links, not summaries. Keep every change reviewable.",
            "And make it cheap for a human to say no.",
        ),
    ),
    Clip(
        number=2,
        session="10",
        title="Search That Understands Your Docs: Hybrid Retrieval for Internal Portals",
        speaker_key="S9",
        speaker="Ananya Iyer",
        role="Search engineer, Orbital Weave",
        voice="marin",
        status="published",
        cues=(
            "Our internal developer portal had a search box that everyone ignored. Here is why.",
            "Keyword search was great when you knew the exact words.",
            "Type the error code E4031 and the runbook came up first.",
            "But ask why does my deploy hang after the health check, and you got nothing,",
            "because the runbook says readiness probe timeout.",
            "Semantic search was the mirror image. It understood the question,",
            "but it would rank a general page about probes above the one runbook that mentioned E4031.",
            "So we stopped choosing. We run both queries in Elasticsearch:",
            "BM25 for the words, and a k-nearest-neighbors query over embeddings for the meaning,",
            "with the same filters on both.",
            "Then we fuse the two ranked lists with reciprocal rank fusion.",
            "Each document scores one over sixty plus its rank in each list, and the sums decide the order.",
            "No tuning, no learned weights.",
            "On our forty golden queries, the right page landed in the top five",
            "sixty-one percent of the time with keywords alone, and eighty-four percent with the hybrid.",
            "The cost is two queries instead of one, and that was worth it.",
        ),
    ),
    Clip(
        number=3,
        session="18",
        title="Tracing LLM Calls in Production",
        speaker_key="S9",
        speaker="Ananya Iyer",
        role="Search engineer, Orbital Weave",
        voice="marin",
        status="processing",
        cues=(
            "A language model call is the slowest, most expensive and least predictable dependency",
            "most of us have ever added to a request path. So trace it like one.",
            "Every call in our service gets its own span.",
            "We record the model name, the input and output token counts, an estimated cost,",
            "and whether the response was served from cache. The latency is the span itself.",
            "What we never record is the prompt.",
            "The prompt carries the user's own words, and a trace is not a safe place for them.",
            "We also keep the model's answer out of the span attributes.",
            "If we need to debug the content, we do it with a sampled, access-controlled store, not with telemetry.",
            "With that in place, a slow answer stops being a mystery.",
            "Last month one feature doubled its p95 latency.",
            "The spans showed retries: every timeout was being retried twice,",
            "so one slow model call turned into three.",
            "We also roll the token counts into a metric: cost per feature, per hour.",
            "The first week it ran, it found an internal tool that spent more than the product did.",
        ),
    ),
)


@dataclass(frozen=True)
class Timing:
    start: float
    end: float


# --- Pure functions (covered by --self-test) --------------------------------------------------------------------------


def normalize(word: str) -> str:
    """A spoken or written word as the aligner compares it: lower case, letters, digits and apostrophes only."""
    return re.sub(r"[^a-z0-9']", "", word.lower())


def align(cues: list[str] | tuple[str, ...], words: list[dict[str, Any]], lead: float = LEAD_SILENCE):
    """Time each cue from the transcription's word timestamps. Returns the timings and the alignment coverage: the
    share of the written words the transcription matched in order.

    A cue starts at its first matched word and ends at its last. A cue with no matched word (the model heard it
    differently) runs from the previous cue's end to the next cue's start. Timings never go backwards."""
    written = [(index, normalize(word)) for index, cue in enumerate(cues) for word in cue.split()]
    written = [(index, word) for index, word in written if word]
    spoken = [(normalize(w["word"]), float(w["start"]), float(w["end"])) for w in words]
    spoken = [item for item in spoken if item[0]]
    matcher = difflib.SequenceMatcher(a=[w for _, w in written], b=[w for w, _, _ in spoken], autojunk=False)
    starts: dict[int, float] = {}
    ends: dict[int, float] = {}
    matched = 0
    for a, b, size in matcher.get_matching_blocks():
        for k in range(size):
            index = written[a + k][0]
            _, start, end = spoken[b + k]
            starts.setdefault(index, start)
            ends[index] = end
            matched += 1
    coverage = matched / len(written) if written else 0.0

    timings: list[Timing] = []
    previous_end = lead
    for index in range(len(cues)):
        start = max(starts.get(index, previous_end), previous_end)
        end = ends.get(index)
        if end is None:
            following = [starts[j] for j in range(index + 1, len(cues)) if j in starts]
            end = following[0] if following else start + 2.0
        end = max(end, start + 0.3)
        timings.append(Timing(round(start, 3), round(end, 3)))
        previous_end = end
    return timings, coverage


def bridge_gaps(timings: list[Timing], gap: float = GAP_TO_BRIDGE) -> list[Timing]:
    """Keep a cue on screen across a short pause: its end moves to the next cue's start when the gap is under `gap`."""
    bridged: list[Timing] = []
    for current, following in zip(timings, [*timings[1:], None], strict=True):
        end = current.end
        if following is not None and 0 < following.start - current.end < gap:
            end = following.start
        bridged.append(Timing(current.start, end))
    return bridged


def vtt_time(seconds: float) -> str:
    """A WebVTT timestamp, hh:mm:ss.mmm, from seconds."""
    whole, millis = divmod(round(seconds * 1000), 1000)
    return f"{whole // 3600:02d}:{whole % 3600 // 60:02d}:{whole % 60:02d}.{millis:03d}"


def webvtt(speaker: str, cues: list[str] | tuple[str, ...], timings: list[Timing]) -> str:
    """The caption file: a leading non-speech cue with the label, then one cue per line of the excerpt, each with the
    speaker in a voice tag (PLAN §9.3)."""
    first_speech = timings[0].start if timings else LEAD_SILENCE
    lines = [
        "WEBVTT",
        "Kind: captions",
        "Language: en",
        "",
        f"NOTE {LABEL}: a synthesized voice reading text written for the demo. {EVENT} is a fictional event.",
        "",
        "1",
        f"{vtt_time(0.0)} --> {vtt_time(first_speech)}",
        f"[{LABEL}: synthesized voice, fictional speaker]",
        "",
    ]
    for number, (text, timing) in enumerate(zip(cues, timings, strict=True), start=2):
        lines += [str(number), f"{vtt_time(timing.start)} --> {vtt_time(timing.end)}", f"<v {speaker}>{text}", ""]
    return "\n".join(lines)


def self_test() -> None:
    words = [
        {"word": "Hello", "start": 1.5, "end": 1.9},
        {"word": "there,", "start": 1.9, "end": 2.2},
        {"word": "everyone.", "start": 2.3, "end": 2.8},
        {"word": "The", "start": 4.0, "end": 4.1},  # a pause of 1.2 s before this cue: not bridged
        {"word": "DB", "start": 4.1, "end": 4.4},  # "database" misheard: the cue still gets timed from "was fine"
        {"word": "was", "start": 4.4, "end": 4.6},
        {"word": "fine.", "start": 4.6, "end": 5.0},
        {"word": "Thank", "start": 5.3, "end": 5.5},
        {"word": "you.", "start": 5.5, "end": 5.8},
    ]
    cues = ["Hello there, everyone.", "The database was fine.", "Thank you."]
    timings, coverage = align(cues, words)
    assert timings == [Timing(1.5, 2.8), Timing(4.0, 5.0), Timing(5.3, 5.8)], timings
    assert abs(coverage - 8 / 9) < 1e-9, coverage
    assert bridge_gaps(timings) == [Timing(1.5, 2.8), Timing(4.0, 5.3), Timing(5.3, 5.8)]
    # A cue the model didn't hear at all runs from the previous end to the next start, and time never goes back.
    timings, coverage = align(["Hello there, everyone.", "Something unheard.", "Thank you."], words)
    assert timings[1] == Timing(2.8, 5.3), timings
    assert timings[2].start >= timings[1].end
    assert vtt_time(0) == "00:00:00.000" and vtt_time(61.2341) == "00:01:01.234" and vtt_time(3599.9996) == "01:00:00.000"
    text = webvtt("Ada Example", cues, bridge_gaps(timings))
    assert text.startswith("WEBVTT\nKind: captions\nLanguage: en\n\nNOTE Simulated excerpt")
    assert f"[{LABEL}: synthesized voice, fictional speaker]" in text
    assert "00:00:01.500 --> 00:00:02.800\n<v Ada Example>Hello there, everyone." in text
    assert text.endswith("<v Ada Example>Thank you.\n")
    print("self-test ok")


# --- Generation (OpenAI, ffmpeg, Pillow) ------------------------------------------------------------------------------


def run(*args: str) -> None:
    subprocess.run(args, check=True)  # noqa: S603 - fixed tool names, arguments built here


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def duration_of(path: Path) -> float:
    out = subprocess.run(  # noqa: S603
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],  # noqa: S607
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return round(float(out), 3)


def cache_key(clip: Clip) -> str:
    """The speech depends on the cue text, the voice and the model: any change makes a new recording."""
    material = json.dumps([TTS_MODEL, clip.voice, TTS_INSTRUCTIONS, list(clip.cues)])
    return hashlib.sha256(material.encode()).hexdigest()[:12]


def speech(client, clip: Clip) -> Path:
    path = CACHE_DIR / f"clip-{clip.number}-{cache_key(clip)}.speech.mp3"
    if path.exists():
        return path
    print(f"clip {clip.number}: synthesizing speech with {TTS_MODEL} ({clip.voice})")
    with client.audio.speech.with_streaming_response.create(
        model=TTS_MODEL,
        voice=clip.voice,
        input="\n\n".join(clip.cues),
        instructions=TTS_INSTRUCTIONS,
        response_format="mp3",
    ) as response:
        response.stream_to_file(path)
    return path


def padded_audio(speech_path: Path, clip: Clip) -> Path:
    path = CACHE_DIR / f"clip-{clip.number}-{cache_key(clip)}.audio.wav"
    if not path.exists():
        lead = int(LEAD_SILENCE * 1000)
        run(
            "ffmpeg", "-y", "-loglevel", "error", "-i", str(speech_path),
            "-af", f"adelay={lead}:all=1,apad=pad_dur={TRAIL_SILENCE}",
            "-ac", "1", "-ar", "24000", str(path),
        )  # fmt: skip
    return path


def transcription_words(client, audio: Path, clip: Clip) -> list[dict[str, Any]]:
    path = CACHE_DIR / f"clip-{clip.number}-{cache_key(clip)}.words.json"
    if path.exists():
        return json.loads(path.read_text())
    print(f"clip {clip.number}: transcribing with {TRANSCRIBE_MODEL} for word timestamps")
    with audio.open("rb") as handle:
        result = client.audio.transcriptions.create(
            model=TRANSCRIBE_MODEL,
            file=handle,
            response_format="verbose_json",
            timestamp_granularities=["word"],
            language="en",
        )
    words = [{"word": w.word, "start": w.start, "end": w.end} for w in (result.words or [])]
    path.write_text(json.dumps(words, indent=1))
    return words


def font(name: str, size: int):
    from PIL import ImageFont

    return ImageFont.truetype(str(FONT_DIR / name), size)


def title_slide(clip: Clip) -> Path:
    from PIL import Image, ImageDraw

    path = CACHE_DIR / f"clip-{clip.number}.slide.png"
    image = Image.new("RGB", (WIDTH, HEIGHT), "#1a1a2e")
    draw = ImageDraw.Draw(image)
    draw.rectangle([(0, 0), (WIDTH, 6)], fill="#4f8cff")
    draw.text((32, 28), EVENT, font=font("Arial Bold.ttf", 22), fill="#ffffff")
    draw.text((32, 58), f"Fictional demo event · {LABEL}", font=font("Arial.ttf", 16), fill="#b8c1ec")
    y = 110
    for line in textwrap.wrap(clip.title, width=40):
        draw.text((32, y), line, font=font("Arial Bold.ttf", 26), fill="#ffffff")
        y += 34
    y += 12
    draw.text((32, y), clip.speaker, font=font("Arial.ttf", 20), fill="#ffffff")
    draw.text((32, y + 28), clip.role, font=font("Arial.ttf", 16), fill="#b8c1ec")
    draw.text(
        (32, HEIGHT - 56),
        f"Synthesized voice (OpenAI {TTS_MODEL}). Text written for the demo; not a real talk.",
        font=font("Arial.ttf", 13),
        fill="#8d99c4",
    )
    draw.text((32, HEIGHT - 36), f"Captions: English (WebVTT) · Clip {clip.number}", font=font("Arial.ttf", 13), fill="#8d99c4")
    image.save(path)
    return path


def assemble(slide: Path, audio: Path, out: Path) -> None:
    run(
        "ffmpeg", "-y", "-loglevel", "error",
        "-loop", "1", "-framerate", str(FPS), "-i", str(slide), "-i", str(audio),
        "-map_metadata", "-1", "-fflags", "+bitexact", "-flags:v", "+bitexact", "-flags:a", "+bitexact",
        "-c:v", "libx264", "-preset", "medium", "-tune", "stillimage", "-pix_fmt", "yuv420p", "-r", str(FPS), "-g", "50",
        "-c:a", "aac", "-b:a", "64k", "-ac", "1", "-shortest", "-movflags", "+faststart", str(out),
    )  # fmt: skip


def generate(write_fixture: bool) -> None:
    from openai import OpenAI

    client = OpenAI()  # OPENAI_API_KEY from the environment; never printed (SEC-1)
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    manifest_clips: list[dict[str, Any]] = []
    fixture_entries: list[dict[str, Any]] = []
    for clip in CLIPS:
        audio = padded_audio(speech(client, clip), clip)
        words = transcription_words(client, audio, clip)
        timings, coverage = align(clip.cues, words)
        if coverage < MIN_COVERAGE:
            raise SystemExit(f"clip {clip.number}: only {coverage:.0%} of the written words were matched; check the audio")
        timings = bridge_gaps(timings)
        captions = MEDIA_DIR / f"clip-{clip.number}.vtt"
        captions.write_text(webvtt(clip.speaker, clip.cues, timings), encoding="utf-8")
        video = MEDIA_DIR / f"clip-{clip.number}.mp4"
        assemble(title_slide(clip), audio, video)
        duration = duration_of(video)
        print(f"clip {clip.number}: {duration:.1f} s, {video.stat().st_size // 1024} KiB, alignment {coverage:.0%}")
        cues = [{"start": t.start, "end": t.end, "text": text} for text, t in zip(clip.cues, timings, strict=True)]
        manifest_clips.append(
            {
                **{k: v for k, v in asdict(clip).items() if k != "cues"},
                "cue_count": len(cues),
                "media": f"{PUBLIC_PREFIX}/{video.name}",
                "captions": f"{PUBLIC_PREFIX}/{captions.name}",
                "duration_seconds": duration,
                "alignment_coverage": round(coverage, 3),
                "sha256": {video.name: sha256(video), captions.name: sha256(captions)},
            }
        )
        fixture_entries.append(
            {
                "session": clip.session,
                "speaker": clip.speaker_key,
                "media": f"{PUBLIC_PREFIX}/{video.name}",
                "captions": f"{PUBLIC_PREFIX}/{captions.name}",
                "duration_seconds": duration,
                "status": clip.status,
                "cues": cues,
            }
        )
    manifest = {
        "about": f"{LABEL}s for the fictional {EVENT}: synthesized speech reading text written for the demo. Not real "
        "talks, not real people (SPEAKER-1). Generated by scripts/generate-cloudbridge-media.sh.",
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "tts_model": TTS_MODEL,
        "transcription_model": TRANSCRIBE_MODEL,
        "cue_timing": "each cue is aligned to whisper-1 word timestamps of the generated audio; a cue stays on screen "
        f"across a pause shorter than {GAP_TO_BRIDGE} s; {LEAD_SILENCE} s of silence precedes the speech",
        "clips": manifest_clips,
    }
    (MEDIA_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    if write_fixture:
        write_transcripts(fixture_entries)
        print(f"fixture updated: {FIXTURE.relative_to(ROOT)}")


def write_transcripts(entries: list[dict[str, Any]]) -> None:
    """Replace the fixture's `transcripts` key, leaving the rest of the file byte for byte as it was."""
    raw = FIXTURE.read_text(encoding="utf-8")
    block = json.dumps(entries, indent=2, ensure_ascii=False)
    block = "\n".join(("  " + line) if line else line for line in block.splitlines())
    marker = ',\n  "transcripts": '
    if marker in raw:
        head = raw[: raw.index(marker)]
    else:
        head = raw.rstrip()[:-1].rstrip()  # drop the closing brace
    FIXTURE.write_text(f"{head}{marker}{block.lstrip()}\n}}\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write-fixture", action="store_true", help="also write the fixture's transcripts entries")
    parser.add_argument("--self-test", action="store_true", help="check the alignment and caption code; no network")
    args = parser.parse_args(argv)
    if args.self_test:
        self_test()
        return 0
    generate(args.write_fixture)
    return 0


if __name__ == "__main__":
    sys.exit(main())
