"""The CloudBridge Summit fixture matches CLOUDBRIDGE-SKETCH.md, and the loader maps it to the right row values.
No database needed: these tests read the fixture packaged with the code."""

import contextlib
import json
import re
import uuid
from collections import Counter, defaultdict
from datetime import UTC, date, datetime, time, timedelta
from importlib import resources
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from conferenceops import cloudbridge
from conferenceops.cloudbridge import (
    Fixture,
    fixture_profiles,
    profile_id,
    read_fixture,
    session_data,
    speaker_profile_id,
    to_utc,
)
from conferenceops.models import ClockMode, EventKind, Level, Role, TranscriptStatus
from conferenceops.settings import Settings

VENUE = ZoneInfo("America/Denver")
FIXTURE = read_fixture()
SESSIONS = {s.key: s for s in FIXTURE.sessions}
PERSONAS = {p.key: p for p in FIXTURE.personas}
ROOM_IDS = {r.key: uuid.uuid4() for r in FIXTURE.rooms}

# CLOUDBRIDGE-SKETCH.md, copied by hand. Session number -> (title, level, topics, speakers).
SKETCH_SESSIONS = {
    1: (
        "Bridges, Not Silos: Running Clusters With AI Teammates (opening keynote)",
        "all",
        ["ai-sre", "platform"],
        ["S1"],
    ),
    2: ("AI SRE in Practice: What Our Agent Got Wrong", "advanced", ["ai-sre", "incident-response"], ["S2"]),
    3: ("OpenTelemetry From Zero to Traces in One Afternoon", "beginner", ["observability", "opentelemetry"], ["S3"]),
    4: ("Golden Paths That People Actually Walk", "intermediate", ["platform-engineering"], ["S4"]),
    5: ("Kernel Eyes: Watching Every Syscall Without Slowing Down", "advanced", ["security", "ebpf"], ["S5"]),
    6: ("GitOps Rollbacks Under Pressure", "intermediate", ["gitops", "delivery"], ["S6"]),
    7: ("Cost Signals for Kubernetes Teams", "intermediate", ["finops", "kubernetes"], ["S7"]),
    8: ("Accessible Dashboards for On-Call Humans", "beginner", ["accessibility", "observability"], ["S8"]),
    9: ("Operating Kubernetes Agents Safely", "advanced", ["ai-sre", "security"], ["S2"]),
    10: (
        "Search That Understands Your Docs: Hybrid Retrieval for Internal Portals",
        "intermediate",
        ["search", "elasticsearch"],
        ["S9"],
    ),
    11: ("Sampling Without Regret", "advanced", ["observability", "opentelemetry"], ["S3"]),
    12: ("Fleet Upgrades Without Weekends", "advanced", ["kubernetes", "operations"], ["S10"]),
    13: ("Policy as Code for Humans", "intermediate", ["security", "policy"], ["S11"]),
    14: ("Your First Operator", "beginner", ["kubernetes", "operators"], ["S12"]),
    15: ("Load Shedding: Saying No Gracefully", "intermediate", ["reliability", "capacity"], ["S1"]),
    16: ("Inclusive Incident Reviews", "all", ["culture", "accessibility"], ["S8"]),
    17: ("Autonomous Cluster Security: Promises and Limits", "advanced", ["security", "ai"], ["S5"]),
    18: ("Tracing LLM Calls in Production", "intermediate", ["observability", "genai"], ["S9"]),
    19: ("Platform APIs Your Developers Will Love", "intermediate", ["platform-engineering", "api"], ["S4"]),
    20: ("AI Foundations for Platform Teams", "beginner", ["ai"], ["S6"]),
    21: ("Chaos Days Without Chaos", "intermediate", ["reliability", "testing"], ["S10"]),
    22: ("Secrets Management at Scale", "intermediate", ["security"], ["S11"]),
    23: ("From Alert to Pull Request: Automating the Last Mile", "advanced", ["ai-sre", "gitops"], ["S7"]),
    24: ("What We Learned Building With Agents (closing panel)", "all", ["community", "ai-sre"], ["S2", "S5", "S12"]),
}
SKETCH_DAYS = {date(2026, 10, 6): range(1, 9), date(2026, 10, 7): range(9, 17), date(2026, 10, 8): range(17, 25)}
SKETCH_ROOMS = ["Summit Hall", "Cascade", "Granite", "Aspen"]
SKETCH_SPEAKERS = {
    "S1": ("Maya Okafor", "Staff SRE", "Larkspur Logistics"),
    "S2": ("Daniel Kowalczyk", "Principal engineer", "Quillon Labs"),
    "S3": ("Rosa Delgado", "Observability lead", "Tidewater Health"),
    "S4": ("Kenji Moriyama", "Platform engineer", "Brightpath Cloud"),
    "S5": ("Hannah Ostrowski", "Security researcher", "Quillon Labs"),
    "S6": ("Marcus Bell", "Developer advocate", "Ferncrest Data"),
    "S7": ("Leila Haddad", "FinOps engineer", "Larkspur Logistics"),
    "S8": ("Owen Brightwater", "Accessibility engineer", "Tidewater Health"),
    "S9": ("Ananya Iyer", "Search engineer", "Orbital Weave"),
    "S10": ("Tomasz Nowicki", "SRE manager", "Larkspur Logistics"),
    "S11": ("Fatima Al-Sayed", "Security engineer", "Brightpath Cloud"),
    "S12": ("Chris Adeyemi", "Independent Kubernetes contributor", None),
}
SKETCH_COMPANIES = {  # name -> partner
    "Larkspur Logistics": False,
    "Quillon Labs": False,
    "Tidewater Health": False,
    "Brightpath Cloud": True,
    "Ferncrest Data": True,
    "Orbital Weave": True,
}
NO_PREFERENCES = (False, False, False)
# Persona -> (name, role, company, public interests, (step-free, captions, reduced motion), attended, bookmarked).
SKETCH_PERSONAS = {
    "P1": (
        "Aisha Rahman",
        Role.ATTENDEE,
        None,
        ["AI SRE", "Kubernetes agents", "observability"],
        (True, True, False),
        [2, 9, 15, 23],
        [17, 18],
    ),
    "P2": (
        "Tomás Vega",
        Role.ATTENDEE,
        None,
        ["platform engineering", "GitOps"],
        (False, True, True),
        [4, 6, 19, 21],
        [],
    ),
    "P3": ("Lin Zhao", Role.ATTENDEE, None, ["security", "eBPF"], NO_PREFERENCES, [5, 13, 17, 22], []),
    "P4": (
        "Grace Mensah",
        Role.ATTENDEE,
        None,
        ["OpenTelemetry", "Kubernetes basics"],
        NO_PREFERENCES,
        [3, 8, 14, 20],
        [],
    ),
    "P5": ("Daniel Kowalczyk", Role.SPEAKER, "Quillon Labs", ["AI SRE"], NO_PREFERENCES, [], []),
    "P6": ("Priyanka Desai", Role.PARTNER, "Ferncrest Data", ["developer experience"], NO_PREFERENCES, [6, 10], []),
    "P7": ("Sam Rivera", Role.ORGANIZER, None, ["operations"], NO_PREFERENCES, [], []),
    "P8": ("Jordan Lee", Role.HOST, None, ["community"], NO_PREFERENCES, [], []),
}
PRIVATE_WORDS = ("step-free", "step free", "caption", "reduced motion", "wheelchair")


def _row(spec):
    return session_data(spec, uuid.uuid4(), ROOM_IDS[spec.room], VENUE)


def test_the_conference_is_cloudbridge_summit_in_denver_oct_6_to_8():
    conf = FIXTURE.conference
    assert (conf.slug, conf.name, conf.timezone) == (
        "cloudbridge-summit-2026",
        "CloudBridge Summit 2026",
        "America/Denver",
    )
    assert (conf.starts_on, conf.ends_on) == (date(2026, 10, 6), date(2026, 10, 8))


def test_the_conference_is_an_authored_fictional_archive_with_a_fixed_clock():
    values = cloudbridge.conference_values(FIXTURE.conference)
    assert (values["kind"], values["clock_mode"], values["source"]) == (EventKind.AUTHORED, ClockMode.FIXED, "fixture")
    assert values["is_fictional"] is True
    assert values["has_transcripts"] is True


def test_the_24_sessions_are_the_sketch_program():
    assert len(FIXTURE.sessions) == 24
    actual = {int(s.key): (s.title, s.level.value, s.topics, s.speakers) for s in FIXTURE.sessions}
    assert actual == SKETCH_SESSIONS


def test_every_session_has_an_abstract_of_two_to_four_sentences():
    for spec in FIXTURE.sessions:
        sentences = re.split(r"(?<=[.?!])\s+(?=[A-Z])", spec.abstract.strip())
        assert 2 <= len(sentences) <= 4, spec.key


def test_8_sessions_a_day_from_oct_6_to_8_in_venue_time():
    # The stored instants are UTC; read back in the venue's zone, each talk lands on its sketch day.
    by_day = defaultdict(list)
    for spec in FIXTURE.sessions:
        by_day[_row(spec).starts_at.astimezone(VENUE).date()].append(int(spec.key))
    assert {day: sorted(keys) for day, keys in by_day.items()} == {day: list(r) for day, r in SKETCH_DAYS.items()}


def test_each_day_has_two_45_minute_slots_that_fill_the_four_rooms():
    rooms_by_slot = defaultdict(list)
    for spec in FIXTURE.sessions:
        assert spec.ends_local - spec.starts_local == timedelta(minutes=45), spec.key
        assert spec.starts_local.time() in (time(10, 0), time(14, 0)), spec.key
        rooms_by_slot[spec.starts_local].append(spec.room)
    assert len(rooms_by_slot) == 6  # 3 days x 2 slots
    for slot, rooms in rooms_by_slot.items():
        assert sorted(rooms) == sorted(r.key for r in FIXTURE.rooms), slot  # one talk per room, no double booking


def test_the_keynote_and_the_closing_panel_are_in_summit_hall():
    assert SESSIONS["1"].room == SESSIONS["24"].room == "summit-hall"
    assert SESSIONS["1"].starts_local == datetime(2026, 10, 6, 10, 0)  # opens the event
    assert SESSIONS["24"].starts_local == max(s.starts_local for s in FIXTURE.sessions)  # closes it


def test_venue_times_become_utc_instants_through_the_iana_zone():
    # TIME-1: Denver is UTC-6 in October (daylight time) and UTC-7 in KubeCon week. One fixed offset gets one wrong.
    assert to_utc(datetime(2026, 10, 6, 10, 0), VENUE) == datetime(2026, 10, 6, 16, 0, tzinfo=UTC)
    assert to_utc(datetime(2026, 11, 10, 10, 0), VENUE) == datetime(2026, 11, 10, 17, 0, tzinfo=UTC)
    keynote = _row(SESSIONS["1"])
    assert (keynote.starts_at, keynote.ends_at) == (
        datetime(2026, 10, 6, 16, 0, tzinfo=UTC),
        datetime(2026, 10, 6, 16, 45, tzinfo=UTC),
    )
    assert keynote.starts_at.utcoffset() == timedelta(0)


def test_a_session_time_with_a_utc_offset_is_rejected():
    raw = FIXTURE.model_dump(mode="json")
    raw["sessions"][0]["starts_local"] = "2026-10-06T10:00:00-06:00"
    with pytest.raises(ValidationError, match="TIME-1"):
        Fixture.model_validate(raw)


def test_unknown_fields_are_rejected():
    # extra="forbid": a typo or a field the loader doesn't know fails the load instead of being dropped silently.
    raw = FIXTURE.model_dump(mode="json")
    raw["sessions"][0]["track"] = "AI SRE"
    with pytest.raises(ValidationError, match=r"sessions\.0\.track\s+Extra inputs are not permitted"):
        Fixture.model_validate(raw)


def test_an_empty_program_is_rejected():
    # A reset deletes the sessions, rooms and speakers the fixture doesn't list; an empty list must not wipe them all.
    for part in ("sessions", "rooms", "speakers"):
        raw = FIXTURE.model_dump(mode="json")
        raw[part] = []
        with pytest.raises(ValidationError, match=f"{part}\n  List should have at least 1 item"):
            Fixture.model_validate(raw)


def test_two_people_with_one_profile_key_are_rejected():
    # Profile IDs are uuid5 of the key, so an attendee keyed like a speaker would share that speaker's profile.
    raw = FIXTURE.model_dump(mode="json")
    raw["attendees"][0]["key"] = "S9"
    with pytest.raises(ValidationError, match=r"duplicate profile: \['S9'\]"):
        Fixture.model_validate(raw)


def test_broken_references_are_rejected():
    raw = FIXTURE.model_dump(mode="json")
    raw["sessions"][4]["speakers"] = ["S99"]
    raw["personas"][0]["attended"].append("99")
    with pytest.raises(ValidationError) as failure:
        Fixture.model_validate(raw)
    assert "session 5: unknown or missing speakers ['S99']" in str(failure.value)
    assert "persona P1: unknown session '99'" in str(failure.value)


def test_the_12_speakers_are_the_sketch_speakers_and_all_of_them_speak():
    assert {s.key: (s.name, s.title, s.company) for s in FIXTURE.speakers} == SKETCH_SPEAKERS
    assert {key for s in FIXTURE.sessions for key in s.speakers} == set(SKETCH_SPEAKERS)


def test_the_companies_are_the_sketch_companies_with_three_partners():
    assert {c.name: c.partner for c in FIXTURE.companies} == SKETCH_COMPANIES


def test_levels_are_the_database_levels():
    raw = json.loads(resources.files("conferenceops").joinpath("fixtures", "cloudbridge.json").read_text("utf-8"))
    allowed = {level.value for level in Level}
    assert {s["level"] for s in raw["sessions"]} <= allowed
    assert {p["experience"] for p in raw["personas"] if "experience" in p} <= allowed
    assert Counter(s.level for s in FIXTURE.sessions) == {
        Level.BEGINNER: 4,
        Level.INTERMEDIATE: 10,
        Level.ADVANCED: 7,
        Level.ALL: 3,
    }


def test_session_5_is_about_ebpf_and_runtime_security_without_saying_so_in_its_title():
    # The golden query "something about eBPF and security" has to find it through the abstract (PLAN §8.4).
    five = SESSIONS["5"]
    assert "eBPF" in five.abstract
    assert "runtime security" in five.abstract
    assert "ebpf" not in five.title.lower()
    assert "security" not in five.title.lower()


@pytest.mark.parametrize(
    ("key", "off_topic"),
    [
        ("20", ("ebpf", "security", "agent", "production")),  # forbidden for the eBPF and the AI-agent queries
        ("11", ("beginner", "new to")),  # forbidden for "observability for beginners"
    ],
)
def test_forbidden_golden_query_results_stay_off_topic(key, off_topic):
    text = f"{SESSIONS[key].title} {SESSIONS[key].abstract}".lower()
    assert [word for word in off_topic if word in text] == []


def test_the_8_personas_are_the_sketch_personas():
    actual = {
        p.key: (
            p.display_name,
            p.role,
            p.company,
            p.interests,
            (p.preferences.needs_step_free, p.preferences.wants_captions, p.preferences.reduced_motion),
            [int(key) for key in p.attended],
            [int(key) for key in p.bookmarked],
        )
        for p in FIXTURE.personas
    }
    assert actual == SKETCH_PERSONAS
    assert {k: p.experience for k, p in PERSONAS.items() if p.experience} == {
        "P1": Level.ADVANCED,
        "P2": Level.INTERMEDIATE,
        "P3": Level.ADVANCED,
        "P4": Level.BEGINNER,
    }
    assert PERSONAS["P8"].hosts_room == "summit-hall"


def test_aisha_rahman_the_hero_attended_four_talks_and_bookmarked_two():
    aisha = PERSONAS["P1"]
    assert (aisha.display_name, aisha.role, aisha.experience) == ("Aisha Rahman", Role.ATTENDEE, Level.ADVANCED)
    assert [SESSIONS[key].title for key in aisha.attended] == [
        "AI SRE in Practice: What Our Agent Got Wrong",
        "Operating Kubernetes Agents Safely",
        "Load Shedding: Saying No Gracefully",
        "From Alert to Pull Request: Automating the Last Mile",
    ]
    assert [SESSIONS[key].title for key in aisha.bookmarked] == [
        "Autonomous Cluster Security: Promises and Limits",
        "Tracing LLM Calls in Production",
    ]
    assert aisha.interests == ["AI SRE", "Kubernetes agents", "observability"]
    assert aisha.preferences.needs_step_free and aisha.preferences.wants_captions


def test_daniel_kowalczyk_is_persona_p5_and_speaker_s2_with_his_three_talks():
    daniel = PERSONAS["P5"]
    speaker = next(s for s in FIXTURE.speakers if s.key == daniel.speaker)
    assert (speaker.key, speaker.name, speaker.company) == ("S2", daniel.display_name, daniel.company)
    assert sorted(int(s.key) for s in FIXTURE.sessions if "S2" in s.speakers) == [2, 9, 24]


def test_nobody_is_in_two_sessions_at_once():
    for persona in FIXTURE.personas:
        starts = [SESSIONS[key].starts_local for key in persona.attended]
        assert len(starts) == len(set(starts)), persona.key
    by_speaker = defaultdict(list)
    for spec in FIXTURE.sessions:
        for key in spec.speakers:
            by_speaker[key].append(spec.starts_local)
    for key, starts in by_speaker.items():
        assert len(starts) == len(set(starts)), key


def test_every_name_is_fictional_per_the_sketch():
    # SPEAKER-1: no real people. Only the sketch's invented people, companies and rooms, plus new invented attendees.
    assert [r.name for r in FIXTURE.rooms] == SKETCH_ROOMS
    assert {s.name for s in FIXTURE.speakers} == {name for name, _, _ in SKETCH_SPEAKERS.values()}
    assert {p.display_name for p in FIXTURE.personas} == {persona[0] for persona in SKETCH_PERSONAS.values()}
    attendees = [a.display_name for a in FIXTURE.attendees]
    assert 18 <= len(attendees) <= 22
    assert len(set(attendees)) == len(attendees)
    named_in_sketch = {s.name for s in FIXTURE.speakers} | {p.display_name for p in FIXTURE.personas}
    assert not set(attendees) & named_in_sketch
    people = [*FIXTURE.speakers, *FIXTURE.personas, *FIXTURE.attendees]
    assert {p.company for p in people} - {None} <= set(SKETCH_COMPANIES)


def test_about_40_profiles_one_for_every_speaker_and_daniel_is_his_persona():
    # PLAN §6.1 and the sketch: about 40 profiles. 8 personas, 20 attendees and 11 speakers, because speaker S2 is
    # persona P5 and has no second profile. Follows (Aisha follows S2 and S9) need the speakers' profiles.
    profiles = fixture_profiles(FIXTURE)
    assert len(profiles) == 39
    speakers = {key: values for key, values in profiles.items() if values["role"] == Role.SPEAKER}
    assert set(speakers) == {"P5", "S1", *(f"S{n}" for n in range(3, 13))}
    assert speakers["S9"]["display_name"] == "Ananya Iyer"
    assert speakers["P5"]["display_name"] == SKETCH_SPEAKERS["S2"][0]
    assert speaker_profile_id(FIXTURE, "S2") == profile_id("P5")
    assert speaker_profile_id(FIXTURE, "S9") == profile_id("S9")
    for key, (name, _, company) in SKETCH_SPEAKERS.items():
        profile = profiles["P5" if key == "S2" else key]
        assert (profile["display_name"], profile["company"]) == (name, company)


def test_only_the_8_personas_are_selectable_and_every_profile_is_flagged_fictional():
    profiles = list(fixture_profiles(FIXTURE).values())
    assert [p["display_name"] for p in profiles if p["demo_selectable"]] == [p.display_name for p in FIXTURE.personas]
    assert all(p["is_fictional"] for p in profiles)
    assert {p["role"] for p in profiles if not p["demo_selectable"]} == {Role.ATTENDEE, Role.SPEAKER}


def test_private_preferences_never_reach_public_profile_fields():
    # PRIV-1: `profiles` feeds public DTOs and search; preferences go to `profile_preferences` only.
    for key, public in fixture_profiles(FIXTURE).items():
        assert not set(public) & {"needs_step_free", "wants_captions", "reduced_motion", "preferences"}
        text = json.dumps(public, default=str).lower()
        assert [word for word in PRIVATE_WORDS if word in text] == [], key


def test_profile_ids_are_deterministic_and_distinct():
    # Pinned: a new namespace would give every profile a new ID, and the next load would duplicate them all.
    assert profile_id("P1") == uuid.UUID("9e93bdf5-9c94-57eb-a683-02c6d8aff44f")
    keys = list(fixture_profiles(FIXTURE))
    assert len({profile_id(key) for key in keys}) == len(keys) == 39


def test_the_fixture_ships_inside_the_package():
    # The image and the wheel carry the package directory, so loading never depends on a repository checkout.
    assert resources.files("conferenceops").joinpath("fixtures", "cloudbridge.json").is_file()


def test_the_cli_refuses_to_run_without_database_url(monkeypatch):
    monkeypatch.setattr(cloudbridge, "settings", lambda: Settings(database_url=None))
    monkeypatch.setattr(cloudbridge, "setup_telemetry", lambda *_: None)

    def no_database():
        raise AssertionError("the CLI opened a transaction without DATABASE_URL")

    monkeypatch.setattr(cloudbridge, "transaction", no_database)
    assert cloudbridge.main([]) == 2


def test_the_cli_resets_only_when_asked(monkeypatch):
    # A deploy runs the plain command, which only adds what is missing; the full reseed is a deliberate --reset.
    monkeypatch.setattr(cloudbridge, "settings", lambda: Settings(database_url="postgresql+psycopg://db/test"))
    monkeypatch.setattr(cloudbridge, "setup_telemetry", lambda *_: None)
    monkeypatch.setattr(cloudbridge, "transaction", contextlib.nullcontext)
    calls = []

    def load(db, *, reset):
        calls.append(reset)
        return cloudbridge.LoadResult(conference_id=uuid.uuid4())

    monkeypatch.setattr(cloudbridge, "load_cloudbridge", load)
    assert cloudbridge.main([]) == 0
    assert cloudbridge.main(["--reset"]) == 0
    assert calls == [False, True]


# --- The three captioned clips (PLAN §6.1, §9; SPEAKER-1) ------------------------------------------------------------

MEDIA_DIR = Path(__file__).resolve().parents[2] / "apps" / "web" / "public" / "media" / "cloudbridge"
# CLOUDBRIDGE-SKETCH.md: clip -> (session, speaker, state at demo start).
SKETCH_CLIPS = [
    ("2", "S2", TranscriptStatus.PUBLISHED),
    ("10", "S9", TranscriptStatus.PUBLISHED),
    ("18", "S9", TranscriptStatus.PROCESSING),
]


def test_the_three_clips_are_the_sketch_clips_and_clip_3_starts_processing():
    assert [(t.session, t.speaker, t.status) for t in FIXTURE.transcripts] == SKETCH_CLIPS
    for number, spec in enumerate(FIXTURE.transcripts, start=1):
        assert 60 <= spec.duration_seconds <= 120, spec.session  # short excerpts, never a whole talk
        assert (spec.media, spec.captions) == (
            f"/media/cloudbridge/clip-{number}.mp4",
            f"/media/cloudbridge/clip-{number}.vtt",
        )
        assert spec.cues[0].start >= 1.0  # the label cue and the title slide come first
        assert spec.cues[-1].end <= spec.duration_seconds
        assert 100 <= len(" ".join(c.text for c in spec.cues).split()) <= 260  # about 60-90 s of speech


def test_the_committed_captions_carry_the_label_the_speaker_and_the_fixture_s_cue_text():
    # The WebVTT file the player shows and the transcript the API publishes must say the same words.
    for number, spec in enumerate(FIXTURE.transcripts, start=1):
        captions = (MEDIA_DIR / f"clip-{number}.vtt").read_text(encoding="utf-8")
        speaker = next(s.name for s in FIXTURE.speakers if s.key == spec.speaker)
        assert captions.startswith("WEBVTT\nKind: captions\nLanguage: en\n")
        assert "[Simulated excerpt: synthesized voice, fictional speaker]" in captions
        voiced = re.findall(r"^<v ([^>]+)>(.*)$", captions, flags=re.M)
        assert {name for name, _ in voiced} == {speaker}
        assert [text for _, text in voiced] == [c.text for c in spec.cues]
        assert (MEDIA_DIR / f"clip-{number}.mp4").stat().st_size < 1_500_000  # a few MB in all


def test_transcript_values_derive_the_text_from_the_cues_and_label_them_simulated():
    spec = FIXTURE.transcripts[0]
    values = cloudbridge.transcript_values(spec, "Daniel Kowalczyk")
    assert values["is_simulated"] is True
    assert values["text"] == " ".join(c.text for c in spec.cues)
    assert values["cues"][0] == {"start": spec.cues[0].start, "end": spec.cues[0].end, "text": spec.cues[0].text}
    assert set(values) == {
        "speaker_name",
        "is_simulated",
        "media_url",
        "captions_url",
        "duration_seconds",
        "text",
        "cues",
    }


def test_a_transcript_for_a_speaker_who_isn_t_at_that_session_or_with_cues_out_of_order_is_rejected():
    raw = FIXTURE.model_dump(mode="json")
    raw["transcripts"][0]["speaker"] = "S9"
    with pytest.raises(ValidationError, match="transcript 2: 'S9' doesn't speak at that session"):
        Fixture.model_validate(raw)
    raw = FIXTURE.model_dump(mode="json")
    raw["transcripts"][0]["cues"][1]["start"] = 0.0
    with pytest.raises(ValidationError, match="cues must be in order"):
        Fixture.model_validate(raw)
    raw = FIXTURE.model_dump(mode="json")
    raw["transcripts"][0]["media"] = "https://example.com/clip.mp4"
    with pytest.raises(ValidationError, match="served by the web container"):
        Fixture.model_validate(raw)
