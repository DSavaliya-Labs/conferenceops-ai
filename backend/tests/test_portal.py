"""The attendee portal's read endpoints: events with counts, published access facts with their source links
(ACCESS-1), and the persona's agenda behind X-Demo-Profile-ID (AUTHZ-1, PRIV-1), with venue times in each event's
own time zone (TIME-1). No PostgreSQL: the data layer is a fake."""

import json
import logging
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from conferenceops import export_openapi
from conferenceops.core_app.main import app
from conferenceops.kubecon import ACCESS_FACTS, ACCESS_PAGE, ACCESS_READ_ON
from conferenceops.portal import AgendaSession, EventFacts, EventRow, FactRow, PersonaAgenda, get_portal_data

AISHA_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
PRIVATE = {"needsStepFree", "wantsCaptions", "reducedMotion", "excludedLevels", "preferences", "interests"}

KUBECON = EventRow(
    slug="kubecon-na-2026",
    name="KubeCon + CloudNativeCon North America 2026",
    starts_on=date(2026, 11, 9),
    ends_on=date(2026, 11, 12),
    timezone="America/Denver",
    is_fictional=False,
    has_transcripts=False,
    sessions=600,
    speakers=716,
    rooms=37,
    access_facts=7,
)
CLOUDBRIDGE = EventRow(
    slug="cloudbridge-summit-2026",
    name="CloudBridge Summit 2026",
    starts_on=date(2026, 10, 6),
    ends_on=date(2026, 10, 8),
    timezone="America/Denver",
    is_fictional=True,
    has_transcripts=True,
    sessions=24,
    speakers=12,
    rooms=4,
    access_facts=0,
)
FACTS = tuple(
    FactRow(
        id=f"fact-{i}", kind=kind.value, text=text, location=location, source_url=ACCESS_PAGE, read_on=ACCESS_READ_ON
    )
    for i, (kind, text, location) in enumerate(ACCESS_FACTS)
)


def talk(session_id, title, event, zone, starts_at, *, fictional=False, room="Cascade", level="advanced"):
    return AgendaSession(
        session_id=session_id,
        title=title,
        event=event.name,
        conference=event.slug,
        timezone=zone,
        is_fictional=fictional,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(minutes=45),
        room=room,
        level=level,
        topics=("ai-sre", "incident-response"),
    )


# Oct 6, 10:00 in Denver is daylight time (UTC-6); Nov 10, 10:30 is standard time (UTC-7). Same zone, different
# wording, which only the IANA zone gets right (TIME-1).
ATTENDED = talk(
    "cb-2",
    "AI SRE in Practice",
    CLOUDBRIDGE,
    "America/Denver",
    datetime(2026, 10, 6, 16, 0, tzinfo=UTC),
    fictional=True,
)
BOOKMARKED = talk(
    "k-1",
    "Observability for AI SRE Teams",
    KUBECON,
    "America/Denver",
    datetime(2026, 11, 10, 17, 30, tzinfo=UTC),
    room="Salt Palace | Level 1 | Hall DE",
    level=None,
)
# An archive event in another zone: the wording must follow the event, not the Denver default.
ELSEWHERE = EventRow(
    slug="elsewhere-2026",
    name="Elsewhere Days 2026",
    starts_on=date(2026, 9, 1),
    ends_on=date(2026, 9, 1),
    timezone="Europe/Berlin",
    is_fictional=True,
    has_transcripts=False,
    sessions=1,
    speakers=1,
    rooms=1,
    access_facts=0,
)
ABROAD = talk(
    "e-1", "Keynote Abroad", ELSEWHERE, "Europe/Berlin", datetime(2026, 9, 1, 7, 0, tzinfo=UTC), fictional=True
)

AGENDA = PersonaAgenda(
    profile_id=AISHA_ID, display_name="Aisha Rahman", bookmarked=(BOOKMARKED, ABROAD), attended=(ATTENDED,)
)


KNOWN_SESSION = uuid.uuid5(uuid.NAMESPACE_URL, "conferenceops/test/session/known")


class FakeData:
    def __init__(self, fail: Exception | None = None):
        self.fail = fail
        self.writes: list = []

    def _check(self):
        if self.fail:
            raise self.fail

    def events(self):
        self._check()
        return [KUBECON, CLOUDBRIDGE]

    def access_facts(self, slug):
        self._check()
        if slug == KUBECON.slug:
            return EventFacts(slug=KUBECON.slug, name=KUBECON.name, is_fictional=False, facts=FACTS)
        if slug == CLOUDBRIDGE.slug:
            return EventFacts(slug=CLOUDBRIDGE.slug, name=CLOUDBRIDGE.name, is_fictional=True, facts=())
        return None

    def agenda(self, profile_id):
        self._check()
        return AGENDA if profile_id == AISHA_ID else None

    def bookmark(self, profile_id, session_id):
        self._check()
        if profile_id != AISHA_ID:
            return "no_profile"
        if session_id != KNOWN_SESSION:
            return "no_session"
        self.writes.append(("add", profile_id, session_id))
        return "done"

    def unbookmark(self, profile_id, session_id):
        self._check()
        if profile_id != AISHA_ID:
            return "no_profile"
        if session_id != KNOWN_SESSION:
            return "no_session"
        self.writes.append(("remove", profile_id, session_id))
        return "done"


@pytest.fixture
def client():
    state = {"data": FakeData()}
    app.dependency_overrides[get_portal_data] = lambda: state["data"]
    try:
        yield TestClient(app), state
    finally:
        app.dependency_overrides.clear()


AS_AISHA = {"X-Demo-Profile-ID": str(AISHA_ID)}


# --- Events ------------------------------------------------------------------------------------------------------


def test_the_events_come_with_their_zone_counts_and_fictional_flag(client):
    http, _ = client
    response = http.get("/api/v1/conferences")
    assert response.status_code == 200
    events = response.json()["events"]
    assert [e["slug"] for e in events] == ["kubecon-na-2026", "cloudbridge-summit-2026"]  # the current event first
    kubecon, cloudbridge = events
    assert kubecon["isFictional"] is False
    assert cloudbridge["isFictional"] is True
    assert kubecon["timezone"] == "America/Denver"  # an IANA name, never an offset (TIME-1)
    assert (kubecon["startsOn"], kubecon["endsOn"]) == ("2026-11-09", "2026-11-12")
    assert kubecon["counts"] == {"sessions": 600, "speakers": 716, "rooms": 37, "accessFacts": 7}
    assert cloudbridge["counts"]["accessFacts"] == 0
    assert cloudbridge["hasTranscripts"] is True


# --- Access facts ------------------------------------------------------------------------------------------------


def test_access_facts_are_the_stored_ones_each_with_its_source_link_and_read_date(client):
    http, _ = client
    response = http.get("/api/v1/conferences/kubecon-na-2026/access-facts")
    assert response.status_code == 200
    body = response.json()
    assert (body["conference"], body["isFictional"]) == ("kubecon-na-2026", False)
    assert body["event"] == KUBECON.name
    assert len(body["facts"]) == len(ACCESS_FACTS) == 7
    stored = {text for _, text, _ in ACCESS_FACTS}
    for fact in body["facts"]:
        assert fact["text"] in stored  # the event's own wording, nothing added (ACCESS-1)
        assert fact["sourceUrl"] == ACCESS_PAGE
        assert fact["readOn"] == ACCESS_READ_ON.isoformat()
    quiet = [f for f in body["facts"] if f["kind"] == "quiet_room"]
    assert {f["location"] for f in quiet} == {"Salt Palace, Level 2, room 260A", "Salt Palace, Level 2, room 260B"}


def test_nothing_in_the_access_facts_is_ever_labeled_step_free(client):
    http, _ = client
    text = json.dumps(http.get("/api/v1/conferences/kubecon-na-2026/access-facts").json()).casefold()
    assert "step-free" not in text
    assert "step free" not in text


def test_an_event_without_published_facts_gets_an_empty_list_not_invented_ones(client):
    http, _ = client
    response = http.get("/api/v1/conferences/cloudbridge-summit-2026/access-facts")
    assert response.status_code == 200
    assert response.json()["facts"] == []
    assert response.json()["isFictional"] is True


def test_an_unknown_event_is_404(client):
    http, _ = client
    assert http.get("/api/v1/conferences/no-such-event/access-facts").status_code == 404


# --- My agenda ---------------------------------------------------------------------------------------------------


def test_the_agenda_lists_bookmarks_and_attended_talks_with_venue_times_in_each_event_s_zone(client):
    http, _ = client
    response = http.get("/api/v1/me/agenda", headers=AS_AISHA)
    assert response.status_code == 200
    body = response.json()
    assert (body["profileId"], body["displayName"]) == (str(AISHA_ID), "Aisha Rahman")

    kubecon_talk, abroad = body["bookmarked"]
    assert kubecon_talk["title"] == "Observability for AI SRE Teams"
    assert kubecon_talk["startsAt"] == "2026-11-10T17:30:00Z"
    assert kubecon_talk["startsAtVenue"] == "Tue Nov 10, 10:30 AM MST"
    assert kubecon_talk["room"] == "Salt Palace | Level 1 | Hall DE"
    assert kubecon_talk["level"] is None
    assert kubecon_talk["isFictional"] is False
    assert abroad["startsAtVenue"] == "Tue Sep 1, 9:00 AM CEST"  # the event's own zone, not the venue default

    (attended,) = body["attended"]
    assert attended["title"] == "AI SRE in Practice"
    assert attended["startsAtVenue"] == "Tue Oct 6, 10:00 AM MDT"  # daylight time a month earlier (TIME-1)
    assert attended["event"] == "CloudBridge Summit 2026"
    assert attended["isFictional"] is True
    assert attended["topics"] == ["ai-sre", "incident-response"]


def test_the_agenda_carries_public_fields_only(client):
    http, _ = client
    body = http.get("/api/v1/me/agenda", headers=AS_AISHA).json()
    assert set(body) == {"profileId", "displayName", "bookmarked", "attended"}
    assert PRIVATE.isdisjoint(body)
    for entry in body["bookmarked"] + body["attended"]:
        assert PRIVATE.isdisjoint(entry)


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
def test_the_agenda_needs_a_known_persona_in_the_header(client, headers, status):
    http, _ = client
    assert http.get("/api/v1/me/agenda", headers=headers).status_code == status


def test_a_profile_id_in_the_query_string_is_ignored_the_header_alone_says_who_is_asking(client):
    http, _ = client
    other = str(uuid.uuid4())
    response = http.get(f"/api/v1/me/agenda?profileId={other}&role=organizer", headers=AS_AISHA)
    assert response.status_code == 200
    assert response.json()["profileId"] == str(AISHA_ID)


# --- Failures ----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    ["/api/v1/conferences", "/api/v1/conferences/kubecon-na-2026/access-facts", "/api/v1/me/agenda"],
)
def test_a_data_failure_is_503_and_the_log_carries_only_the_error_type(client, caplog, path):
    http, state = client
    caplog.set_level(logging.DEBUG)
    dsn = "postgresql://co:example-credential@db/conferenceops"  # what a connection error could quote
    state["data"] = FakeData(fail=RuntimeError(f"could not connect to {dsn}"))
    response = http.get(path, headers=AS_AISHA)
    assert response.status_code == 503
    assert "aren't available right now" in response.json()["detail"]
    assert dsn not in response.text
    assert "RuntimeError" in caplog.text
    assert dsn not in caplog.text


# --- Contract (API-1) --------------------------------------------------------------------------------------------


def test_the_contract_lists_the_portal_routes_with_the_header_required_and_every_answer():
    paths = export_openapi.schema()["paths"]
    assert {
        "/api/v1/conferences",
        "/api/v1/conferences/{slug}/access-facts",
        "/api/v1/me/agenda",
        "/api/v1/me/agenda/{session_id}",
    } <= set(paths)
    for method in ("put", "delete"):
        write = paths["/api/v1/me/agenda/{session_id}"][method]
        headers = [p for p in write["parameters"] if p["in"] == "header"]
        assert [(h["name"], h["required"]) for h in headers] == [("X-Demo-Profile-ID", True)]
        assert {"204", "400", "404", "422", "503"} == set(write["responses"])
    # A client generated from the contract must send the header, and knows the 400, 404 and 503 it can get (API-1).
    agenda = paths["/api/v1/me/agenda"]["get"]
    [header] = agenda["parameters"]
    assert (header["in"], header["name"], header["required"]) == ("header", "X-Demo-Profile-ID", True)
    assert header["schema"] == {"type": "string", "format": "uuid"}
    assert {"200", "400", "404", "422", "503"} == set(agenda["responses"])
    assert {"200", "503"} == set(paths["/api/v1/conferences"]["get"]["responses"])
    assert {"200", "404", "422", "503"} == set(paths["/api/v1/conferences/{slug}/access-facts"]["get"]["responses"])


# --- Bookmarks ---------------------------------------------------------------------------------------------------


def test_a_bookmark_is_added_and_removed_for_the_persona_in_the_header_and_nothing_else(client):
    http, state = client
    assert http.put(f"/api/v1/me/agenda/{KNOWN_SESSION}", headers=AS_AISHA).status_code == 204
    assert http.delete(f"/api/v1/me/agenda/{KNOWN_SESSION}", headers=AS_AISHA).status_code == 204
    assert state["data"].writes == [("add", AISHA_ID, KNOWN_SESSION), ("remove", AISHA_ID, KNOWN_SESSION)]


def test_an_unknown_session_or_an_unknown_persona_is_404_with_one_body_for_both(client):
    http, state = client
    other = {"X-Demo-Profile-ID": str(uuid.uuid4())}
    unknown_session = http.put(f"/api/v1/me/agenda/{uuid.uuid4()}", headers=AS_AISHA)
    unknown_persona = http.put(f"/api/v1/me/agenda/{KNOWN_SESSION}", headers=other)
    assert unknown_session.status_code == unknown_persona.status_code == 404
    # AUTHZ-1: the body never says which of the two IDs exists.
    assert unknown_session.json() == unknown_persona.json()
    assert http.delete(f"/api/v1/me/agenda/{uuid.uuid4()}", headers=AS_AISHA).status_code == 404
    assert http.delete(f"/api/v1/me/agenda/{KNOWN_SESSION}", headers=other).json() == unknown_session.json()
    assert state["data"].writes == []


@pytest.mark.parametrize("method", ["put", "delete"])
def test_a_bookmark_write_needs_the_persona_header(client, method):
    http, _ = client
    assert getattr(http, method)(f"/api/v1/me/agenda/{KNOWN_SESSION}").status_code == 400
    assert getattr(http, method)("/api/v1/me/agenda/not-a-uuid", headers=AS_AISHA).status_code == 422


def test_a_bookmark_write_failure_is_503_without_details(client, caplog):
    http, state = client
    state["data"] = FakeData(fail=RuntimeError("secret-dsn"))
    assert http.put(f"/api/v1/me/agenda/{KNOWN_SESSION}", headers=AS_AISHA).status_code == 503
    assert "secret-dsn" not in caplog.text
