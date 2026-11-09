"""Where should I go next? The hard rules (ACCESS-1, TIME-1), checkable reasons, deterministic ranking, AUTHZ-1 and
PRIV-1. No PostgreSQL, Elasticsearch or OpenAI: the data, the index and the model are fakes."""

import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from conferenceops.discovery_app.main import app
from conferenceops.kubecon import ACCESS_FACTS, ACCESS_PAGE
from conferenceops.where_next import (
    INTEREST_WEIGHT,
    RELEVANCE_WEIGHT,
    SIMILARITY_MIN,
    RecommendationRequest,
    WhereNext,
    get_where_next,
    in_session_room,
    open_to_attendees,
    window_bounds,
)
from conferenceops.where_next_data import Event, PastSession, Persona, PlannedSession, PublishedFact, SessionRow

DENVER = ZoneInfo("America/Denver")
NOW = datetime(2026, 11, 10, 0, 30, tzinfo=UTC)  # Mon Nov 9, 5:30 PM in Salt Lake City
KUBECON_ID = uuid.UUID("00000000-0000-0000-0000-00000000c0de")
CLOUDBRIDGE_ID = uuid.UUID("00000000-0000-0000-0000-0000000000cb")
AISHA_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
PRIVATE_INTEREST = "zebrafish-telemetry"  # a stated interest that must never reach a log line (PRIV-1)


def venue(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=DENVER).astimezone(UTC)


def facts(*, captions: bool = True) -> tuple[PublishedFact, ...]:
    """The facts the KubeCon import stores, with their real wording and source link."""
    return tuple(
        PublishedFact(id=f"fact-{i}", kind=kind.value, text=text, location=location, source_url=ACCESS_PAGE)
        for i, (kind, text, location) in enumerate(ACCESS_FACTS)
        if captions or kind.value != "captions"
    )


def kubecon(*, captions: bool = True) -> Event:
    return Event(
        id=KUBECON_ID,
        slug="kubecon-na-2026",
        name="KubeCon + CloudNativeCon North America 2026",
        timezone="America/Denver",
        is_fictional=False,
        facts=facts(captions=captions),
    )


def row(session_id, title, starts_at, minutes=30, abstract=None, level=None, room="Salt Palace | Level 1 | 155"):
    return SessionRow(
        session_id=session_id,
        title=title,
        abstract=abstract,
        topics=(),
        level=level,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(minutes=minutes),
        room=room,
        revision=3,
    )


ROWS = {
    "k-started": row("k-started", "Observability Live Now", venue(9, 16)),
    "k-tonight": row("k-tonight", "Observability for AI SRE Teams", venue(9, 18)),
    "k-agents": row(
        "k-agents", "Agents Under Pressure", venue(10, 9), abstract="Running Kubernetes agents safely", level="advanced"
    ),
    "k-similar": row(
        "k-similar", "Postmortems That Teach", venue(10, 10), level="all", room="Salt Palace | Level 2 | 254"
    ),
    "k-unrelated": row("k-unrelated", "Database Sharding Patterns", venue(10, 11)),
    "k-beginner": row("k-beginner", "Observability 101", venue(10, 9), level="beginner"),
    "k-planned": row("k-planned", "Already On My Agenda", venue(10, 14)),
    "k-conflict": row("k-conflict", "Observability at Scale", venue(10, 14, 15)),
    "k-later": row("k-later", "Observability Deep Dive", venue(11, 15)),
}


def aisha(**changes) -> Persona:
    values = dict(
        profile_id=AISHA_ID,
        display_name="Aisha Rahman",
        interests=("AI SRE", "Kubernetes agents", "observability"),
        needs_step_free=True,
        wants_captions=True,
        attended=(PastSession("cb-1", "AI SRE in Practice", "CloudBridge Summit 2026", "attended"),),
        planned=(
            PlannedSession(
                "cb-2",
                CLOUDBRIDGE_ID,
                "Tracing LLM Calls in Production",
                "CloudBridge Summit 2026",
                datetime(2026, 10, 7, 16, tzinfo=UTC),
                datetime(2026, 10, 7, 17, tzinfo=UTC),
            ),
            PlannedSession(
                "k-planned",
                KUBECON_ID,
                "Already On My Agenda",
                "KubeCon + CloudNativeCon North America 2026",
                venue(10, 14),
                venue(10, 14, 30),
            ),
        ),
    )
    values.update(changes)
    return Persona(**values)


class FakeData:
    def __init__(self, persona: Persona, event: Event, rows=None):
        self._persona = persona
        self._event = event
        self.rows = ROWS if rows is None else rows

    def persona(self, profile_id):
        return self._persona if profile_id == self._persona.profile_id else None

    def event(self, slug):
        return self._event if slug == self._event.slug else None

    def sessions(self, ids, event_id):
        return {i: self.rows[i] for i in ids if i in self.rows and event_id == self._event.id}


HISTORY_VECTORS = {"cb-1": [1.0, 0.0], "cb-2": [0.0, 1.0]}
# Cosine similarity of each retrieved talk to the past talk (Elasticsearch scores it as (1 + cosine) / 2).
NEIGHBORS = {
    "cb-1": [("k-similar", 0.62), ("k-tonight", 0.58), ("k-unrelated", 0.42), ("k-started", 0.61)],
    "cb-2": [("k-later", 0.55), ("k-unrelated", 0.30)],
}
ALL_IDS = sorted(ROWS)


class FakeES:
    """Answers like the co-sessions alias: BM25, kNN with a query vector, and the stored vectors by ID."""

    def __init__(self, bm25=None, knn=None, fail_with: Exception | None = None):
        self.bm25 = ALL_IDS if bm25 is None else bm25
        self.knn = ALL_IDS if knn is None else knn
        self.fail_with = fail_with
        self.calls: list[dict] = []

    def search(self, index, size, source, query=None, knn=None):
        assert index == "co-sessions"
        self.calls.append({"query": query, "knn": knn, "source": source})
        if self.fail_with:
            raise self.fail_with
        if query and "ids" in query:
            assert source == {"includes": ["embedding"], "exclude_vectors": False}
            hits = [{"_id": i, "_source": {"embedding": HISTORY_VECTORS[i]}} for i in query["ids"]["values"]]
        elif knn:
            past = next((key for key, vector in HISTORY_VECTORS.items() if vector == knn["query_vector"]), None)
            pairs = NEIGHBORS[past] if past else [(i, 0.3) for i in self.knn]
            hits = [{"_id": i, "_score": (1 + cosine) / 2} for i, cosine in pairs]
        else:
            hits = [{"_id": i, "_score": 1.0} for i in self.bm25]
        return {"hits": {"hits": hits}}

    def filters(self) -> list[list[dict]]:
        """The filters of every BM25 and kNN request."""
        out = []
        for call in self.calls:
            if call["knn"]:
                out.append(call["knn"]["filter"])
            elif call["query"] and "bool" in call["query"]:
                out.append(call["query"]["bool"]["filter"])
        return out


class FakeEmbedder:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.texts: list[str] = []

    def embed(self, texts):
        self.texts += list(texts)
        if self.fail:
            raise TimeoutError("model too slow")
        return [[0.5, 0.5] for _ in texts]


def recommend(persona=None, event=None, es=None, embedder=None, rows=None, **request):
    service = WhereNext(
        FakeData(persona or aisha(), event or kubecon(), rows),
        es or FakeES(),
        embedder or FakeEmbedder(),
        clock=lambda: NOW,
    )
    return service.recommend(AISHA_ID, RecommendationRequest(**request))


def ids(response) -> list[str]:
    return [c.session_id for c in response.candidates]


# --- Hard rules ---------------------------------------------------------------------------------------------------


def test_talks_that_already_started_are_never_offered_even_when_the_index_returns_them():
    es = FakeES()
    response = recommend(es=es, limit=5)
    assert "k-started" not in ids(response)
    assert response.excluded["STARTED"] == 1
    # The index is asked for talks after now, on the real clock, and never for service sessions (TIME-1).
    assert es.filters()
    for filters in es.filters():
        assert {"term": {"is_service": False}} in filters
        assert {"term": {"conference": "kubecon-na-2026"}} in filters
        assert {"range": {"starts_at": {"gt": NOW.isoformat()}}} in filters


def test_the_windows_follow_the_event_day_in_its_time_zone():
    today = window_bounds("today", NOW, DENVER)
    assert today == (NOW, venue(10, 0))
    assert window_bounds("tomorrow_morning", NOW, DENVER) == (venue(10, 0), venue(10, 12))
    assert window_bounds("any", NOW, DENVER) == (NOW, None)
    assert ids(recommend(window="today")) == ["k-tonight"]
    assert set(ids(recommend(window="tomorrow_morning", limit=5))) == {"k-agents", "k-beginner", "k-similar"}


def test_windows_cross_a_daylight_saving_change_correctly():
    # Sat Oct 31, 8 PM MDT. Clocks go back at 2 AM on Sunday Nov 1, so Sunday noon is 19:00 UTC, not 18:00 (TIME-1).
    evening = datetime(2026, 11, 1, 2, 0, tzinfo=UTC)
    assert window_bounds("tomorrow_morning", evening, DENVER) == (
        datetime(2026, 11, 1, 6, 0, tzinfo=UTC),
        datetime(2026, 11, 1, 19, 0, tzinfo=UTC),
    )


def test_a_talk_on_the_agenda_and_a_talk_that_overlaps_it_are_not_offered():
    response = recommend(limit=5)
    assert "k-planned" not in ids(response)
    assert "k-conflict" not in ids(response)
    assert response.excluded["ON_AGENDA"] == 1
    assert response.excluded["AGENDA_CONFLICT"] == 1


def test_excluded_levels_add_up_and_an_unknown_level_stays_unknown():
    persona = aisha(excluded_levels=frozenset({"beginner"}))
    response = recommend(persona=persona, limit=5, filters={"excludedLevels": ["advanced"]})
    assert "k-beginner" not in ids(response)  # saved exclusion
    assert "k-agents" not in ids(response)  # requested exclusion
    assert response.excluded["LEVEL_EXCLUDED"] == 2
    assert "k-tonight" in ids(response)  # no level published: not treated as an excluded one


def test_a_caption_requirement_without_a_published_captions_fact_gives_no_fit_never_a_softer_list():
    response = recommend(event=kubecon(captions=False), limit=5)
    assert response.status == "no_fit"
    assert response.candidates == []
    assert response.excluded["CAPTIONS_NOT_PUBLISHED"] > 0
    # Without the requirement, the same talks qualify.
    assert recommend(persona=aisha(wants_captions=False), event=kubecon(captions=False)).status == "matches"


def test_captions_and_seating_are_stated_from_the_published_facts_with_their_source_link():
    response = recommend(limit=5)
    assert response.candidates
    for candidate in response.candidates:
        published = [a for a in candidate.access if a.status == "published"]
        assert [a.kind for a in published] == ["captions", "reserved_seating"]
        stored = {f.text for f in facts()}
        for statement in published:
            assert statement.text in stored  # the event's own wording, nothing added (ACCESS-1)
            assert statement.source_url == ACCESS_PAGE
        assert "CAPTIONS_PUBLISHED" in [r.code for r in candidate.reasons]


def test_nothing_is_ever_labeled_step_free_and_every_answer_points_to_staff():
    staff = next(f for f in facts() if f.kind == "staff_help")
    for response in (recommend(limit=5), recommend(event=kubecon(captions=False))):  # a match and a no-fit
        assert [w.code for w in response.warnings] == ["STEP_FREE_NOT_PUBLISHED"]
        assert staff.text in response.warnings[0].text
        assert response.warnings[0].source_url == ACCESS_PAGE
        for candidate in response.candidates:
            assert candidate.eligibility == "unconfirmed"
            step_free = [a for a in candidate.access if a.kind == "step_free_route"]
            assert len(step_free) == 1
            assert step_free[0].status == "not_published"
            assert step_free[0].source_url == ACCESS_PAGE
        # Every mention of step-free anywhere in the answer is the statement that routes aren't published.
        text = json.dumps(response.model_dump(by_alias=True), default=str).casefold()
        assert text.count("step-free") == text.count("step-free routes aren't published")


def test_a_persona_without_access_needs_gets_no_step_free_statement():
    response = recommend(persona=aisha(needs_step_free=False, wants_captions=False), limit=5)
    assert response.warnings == []
    for candidate in response.candidates:
        assert candidate.eligibility == "confirmed"
        assert "step_free_route" not in [a.kind for a in candidate.access]
        assert "CAPTIONS_PUBLISHED" not in [r.code for r in candidate.reasons]


# --- Who can walk in, and where the published facts apply (ACCESS-1) ----------------------------------------------

OFF_SITE = "Off-Site Location"
EXPO_FLOOR = "Salt Palace | Level 1 | Halls A-C, Halls 1-5"
# The 36 room names KubeCon sessions used on Nov 9, 2026: the event's session rooms, then everywhere else.
SESSION_ROOMS = [
    "Hyatt Regency | Level 2 | Salt Lake Ballroom C",
    "Hyatt Regency | Level 2 | Salt Lake Ballroom DE",
    "Hyatt Regency | Level 3 | Park City",
    "Hyatt Regency | Level 4 | Regency Ballroom A",
    "Hyatt Regency | Level 4 | Regency Ballroom B",
    "Hyatt Regency | Level 4 | Regency Ballroom B-D",
    "Hyatt Regency | Level 4 | Regency Ballroom CD",
    "Hyatt Regency | Level 5 | Capitol Reef",
    "Salt Palace | Level 1 | 151",
    "Salt Palace | Level 1 | Grand Ballroom ACE",
    "Salt Palace | Level 1 | Grand Ballroom BDF",
    "Salt Palace | Level 1 | Grand Ballroom GI",
    "Salt Palace | Level 1 | Grand Ballroom H",
    "Salt Palace | Level 1 | Grand Ballroom J",
    "Salt Palace | Level 1 | Hall DE",
    "Salt Palace | Level 2 | 250 A-C",
    "Salt Palace | Level 2 | 250 D-F",
    "Salt Palace | Level 2 | 251 A-C",
    "Salt Palace | Level 2 | 251 D-F",
    "Salt Palace | Level 2 | 254",
    "Salt Palace | Level 2 | 255 A",
    "Salt Palace | Level 2 | 255 BC",
    "Salt Palace | Level 2 | 255 D",
    "Salt Palace | Level 2 | 255 EF",
]
NOT_SESSION_ROOMS = [
    "Off-Site Location",
    "Salt Palace | Level 1 | Halls A-C, Halls 1-5",
    "Salt Palace | Level 1 | Solutions Showcase | Hall 1 | Alpine Overlook Zone | Demo Theater",
    "Salt Palace | Level 1 | Solutions Showcase | Hall 1 | Alpine Overlook Zone | Network Nook",
    "Salt Palace | Level 1 | Solutions Showcase | Hall 5 | Cosmic Canyon Zone | Project Pavilion",
    "Salt Palace | Level 1 | Solutions Showcase | Hall B | Starlight Summit Zone",
    "Salt Palace | Level 1 | Solutions Showcase | Halls 5 | Cosmic Canyon Zone | Cloud Native Theater",
    "Salt Palace | 200 South Entrance (South)",
    "Salt Palace | Level 2 | East Registration",
    "Salt Palace | Level 1 | North Grand Ballroom Foyer",
    "Salt Palace | Level 2 | Upper Mezzanine",
    "Hyatt Regency Lobby",
]


def test_a_talk_an_attendee_cant_simply_walk_into_is_never_offered():
    # On Nov 9 the live answer offered off-site co-located events that need a separate registration, like this one,
    # with the captions and seating facts attached.
    tigera = "Discovering and Securing AI Agents in Kubernetes Hosted by Tigera | SEPARATE REGISTRATION REQUIRED"
    rows = {
        "k-tigera": row("k-tigera", tigera, venue(9, 18), room=OFF_SITE),
        "k-sold-out": row("k-sold-out", "Kubernetes Agents Workshop [Sold Out]", venue(9, 18, 30)),  # a session room
        "k-off-site": row("k-off-site", "Observability Mixer", venue(9, 19), room=OFF_SITE),  # off-site, no marker
        "k-tonight": ROWS["k-tonight"],
    }
    es = FakeES(bm25=sorted(rows), knn=sorted(rows))
    personas = (aisha(), aisha(needs_step_free=False), aisha(needs_step_free=False, wants_captions=False))
    for persona in personas:
        response = recommend(persona=persona, rows=rows, es=es, limit=5)
        assert ids(response) == ["k-tonight"]  # every one of them matches an interest; only this one is open
        assert response.excluded["NOT_OPEN"] == 3
        assert all(c.room != OFF_SITE for c in response.candidates)
    assert [open_to_attendees(r) for r in rows.values()] == [False, False, False, True]


def test_the_facts_apply_in_the_feeds_session_rooms_and_nowhere_else():
    def in_room(room):
        return in_session_room(row("k-1", "A talk", venue(10, 9), room=room))

    assert [room for room in SESSION_ROOMS if not in_room(room)] == []
    # Off-site, the expo floor and public areas, and a room that isn't listed: the statements say nothing there.
    assert [room for room in [*NOT_SESSION_ROOMS, None] if in_room(room)] == []


def test_captions_and_seating_are_stated_only_for_the_events_session_rooms():
    rows = {
        "k-room": row("k-room", "Observability for AI SRE Teams", venue(10, 9)),
        "k-booth": row(
            "k-booth",
            "Gold In-Booth Demos: ControlTheory, New Relic, OpenObserve",
            venue(10, 10),
            abstract="Observability demos at the sponsors' booths",
            room=EXPO_FLOOR,
        ),
        "k-unlisted": row("k-unlisted", "Observability Office Hours", venue(10, 11), room=None),
    }
    es = FakeES(bm25=sorted(rows), knn=sorted(rows))
    stored = {f.kind: f for f in facts()}

    # Without a captions need, all three are offered, and only the session room carries the published facts.
    response = recommend(persona=aisha(needs_step_free=False, wants_captions=False), rows=rows, es=es, limit=5)
    by_id = {c.session_id: c for c in response.candidates}
    assert set(by_id) == {"k-room", "k-booth", "k-unlisted"}
    room = by_id["k-room"]
    assert [(a.kind, a.status, a.text) for a in room.access] == [
        ("captions", "published", stored["captions"].text),
        ("reserved_seating", "published", stored["reserved_seating"].text),
    ]
    assert {"access_fact:fact-0", "access_fact:fact-1"} <= set(room.source_ids)
    for elsewhere in (by_id["k-booth"], by_id["k-unlisted"]):
        assert [(a.kind, a.status) for a in elsewhere.access] == [
            ("captions", "not_published"),
            ("reserved_seating", "not_published"),
        ]
        assert all(a.source_url == ACCESS_PAGE for a in elsewhere.access)
        assert all(a.text not in {f.text for f in facts()} for a in elsewhere.access)  # the facts aren't borrowed
        assert elsewhere.source_ids == [f"session:{elsewhere.session_id}:3"]

    # A caption requirement is met only where captioning is published: never softened to fill the list.
    for persona in (aisha(needs_step_free=False), aisha()):
        response = recommend(persona=persona, rows=rows, es=es, limit=5)
        assert ids(response) == ["k-room"]
        assert response.excluded["CAPTIONS_NOT_PUBLISHED"] == 2
        assert "CAPTIONS_PUBLISHED" in [r.code for r in response.candidates[0].reasons]
    assert recommend(persona=aisha(needs_step_free=False), rows=rows, es=es).candidates[0].eligibility == "confirmed"
    assert recommend(persona=aisha(), rows=rows, es=es).candidates[0].eligibility == "unconfirmed"  # step-free


# --- Reasons and ranking ------------------------------------------------------------------------------------------


def test_every_reason_can_be_checked_against_the_talk_or_the_history():
    response = recommend(limit=5)
    by_id = {c.session_id: c for c in response.candidates}
    tonight = {r.code: r for r in by_id["k-tonight"].reasons}
    assert tonight["INTEREST_MATCH"].interests == ["AI SRE", "observability"]  # both words of "AI SRE" in the title
    assert tonight["HISTORY_MATCH"].related_session_id == "cb-1"
    assert tonight["HISTORY_MATCH"].similarity == 0.58
    assert "AI SRE in Practice" in tonight["HISTORY_MATCH"].text
    # No interest words, but close to a talk she attended.
    similar = by_id["k-similar"].reasons
    assert [r.code for r in similar] == ["HISTORY_MATCH", "CAPTIONS_PUBLISHED"]
    assert similar[0].similarity >= SIMILARITY_MIN
    # Close to a talk she bookmarked at CloudBridge, and it mentions an interest.
    later = {r.code: r for r in by_id["k-later"].reasons}
    assert later["BOOKMARK_MATCH"].related_session_id == "cb-2"
    assert "bookmarked at CloudBridge Summit 2026" in later["BOOKMARK_MATCH"].text


def test_a_talk_with_no_interest_or_history_reason_is_not_offered():
    response = recommend(limit=5)
    assert "k-unrelated" not in ids(response)  # 0.42 to her history is below the bar, and no interest words
    assert response.excluded["NO_MATCH"] == 1


def test_the_ranking_is_deterministic_and_follows_the_configured_weights():
    first = recommend(limit=5)
    assert ids(first) == ["k-tonight", "k-later", "k-similar", "k-agents", "k-beginner"]
    items = sorted(ROWS.items())
    for order in (items[::-1], items[3:] + items[:3], items[5:] + items[:5]):  # the same data in any order
        again = recommend(limit=5, rows=dict(order))
        assert [(c.session_id, c.score, c.reasons) for c in again.candidates] == [
            (c.session_id, c.score, c.reasons) for c in first.candidates
        ]
    scores = {c.session_id: c.score for c in first.candidates}
    # k-later has the best fused relevance (normalized to 1.0) and mentions one of her three interests.
    assert scores["k-later"] == pytest.approx(RELEVANCE_WEIGHT * 1.0 + INTEREST_WEIGHT * 1 / 3, abs=1e-4)
    assert list(scores.values()) == sorted(scores.values(), reverse=True)
    assert len(recommend().candidates) == 3  # PLAN §8.3: up to three choices by default


def test_cards_carry_title_venue_time_room_and_a_real_event_label():
    candidate = next(c for c in recommend(limit=5).candidates if c.session_id == "k-agents")
    assert candidate.title == "Agents Under Pressure"
    assert candidate.starts_at_venue == "Tue Nov 10, 9:00 AM MST"
    assert candidate.room == "Salt Palace | Level 1 | 155"
    assert candidate.event == "KubeCon + CloudNativeCon North America 2026"
    assert candidate.is_fictional is False
    assert candidate.source_ids[0] == "session:k-agents:3"
    assert set(candidate.source_ids[1:]) == {"access_fact:fact-0", "access_fact:fact-1", "access_fact:fact-5"}


def test_when_the_model_is_down_interests_fall_back_to_keywords_and_history_still_works():
    embedder = FakeEmbedder(fail=True)
    response = recommend(embedder=embedder, limit=5)
    assert response.retrieval_mode == "lexical"
    assert "k-similar" in ids(response)  # history matching uses stored vectors: no model call


def test_with_no_interests_and_no_history_the_answer_says_so():
    persona = aisha(interests=(), attended=(), planned=(), needs_step_free=False)
    es = FakeES()
    response = recommend(persona=persona, es=es)
    assert response.status == "no_fit"
    assert [w.code for w in response.warnings] == ["NO_SIGNALS"]
    assert es.calls == []


# --- AUTHZ-1 and PRIV-1 through the HTTP endpoint ---------------------------------------------------------------


@pytest.fixture
def client():
    state = {"es": FakeES(), "persona": aisha(interests=("AI SRE", PRIVATE_INTEREST, "observability"))}
    app.dependency_overrides[get_where_next] = lambda: WhereNext(
        FakeData(state["persona"], kubecon()), state["es"], FakeEmbedder(), clock=lambda: NOW
    )
    try:
        yield TestClient(app), state
    finally:
        app.dependency_overrides.clear()


def test_the_persona_comes_from_the_header_and_the_answer_is_camel_case(client):
    http, _ = client
    response = http.post("/api/v1/recommendations/next", json={}, headers={"X-Demo-Profile-ID": str(AISHA_ID)})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "matches"
    assert body["candidates"][0]["startsAtVenue"].endswith("MST")
    assert body["candidates"][0]["access"][0]["sourceUrl"] == ACCESS_PAGE


@pytest.mark.parametrize(
    ("headers", "status"),
    [
        ({}, 400),
        ({"X-Demo-Profile-ID": ""}, 400),
        ({"X-Demo-Profile-ID": "not-a-uuid"}, 404),
        ({"X-Demo-Profile-ID": str(uuid.uuid4())}, 404),
    ],
    ids=["missing", "empty", "malformed", "unknown"],
)
def test_a_missing_or_unknown_persona_gets_no_answer_and_no_search(client, headers, status):
    http, state = client
    response = http.post("/api/v1/recommendations/next", json={}, headers=headers)
    assert response.status_code == status
    assert state["es"].calls == []  # refused before any retrieval


def test_a_role_in_the_body_is_not_a_thing_the_api_accepts(client):
    http, _ = client
    body = {"role": "organizer", "profileId": str(uuid.uuid4())}
    response = http.post("/api/v1/recommendations/next", json=body, headers={"X-Demo-Profile-ID": str(AISHA_ID)})
    assert response.status_code == 200  # extra fields are ignored; the header alone says who is asking


def test_interests_queries_and_preferences_never_reach_the_logs(client, caplog):
    http, state = client
    caplog.set_level(logging.DEBUG)
    query = "quiet-room-near-the-keynote"
    headers = {"X-Demo-Profile-ID": str(AISHA_ID)}
    assert http.post("/api/v1/recommendations/next", json={"query": query}, headers=headers).status_code == 200
    assert "where-next" in caplog.text  # the request was logged, as counts
    # A failure whose message quotes the request is logged by type only.
    state["es"].fail_with = RuntimeError(f"search failed for {query} {PRIVATE_INTEREST}")
    assert http.post("/api/v1/recommendations/next", json={"query": query}, headers=headers).status_code == 503
    assert "RuntimeError" in caplog.text
    for private in (query, PRIVATE_INTEREST, "AI SRE", "observability", "step", "caption"):
        assert private.casefold() not in caplog.text.casefold()
