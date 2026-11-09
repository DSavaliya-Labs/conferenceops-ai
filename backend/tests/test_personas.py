"""The persona switcher's listing (public fields only, PRIV-1) and the one API contract both apps export (API-1)."""

import pytest
from fastapi.testclient import TestClient

from conferenceops import export_openapi
from conferenceops.core_app.main import app
from conferenceops.personas import PersonaCard, get_persona_directory

PRIVATE = {"needsStepFree", "wantsCaptions", "reducedMotion", "excludedLevels", "preferences"}


class StubDirectory:
    def __init__(self, fail: bool = False):
        self.fail = fail

    def selectable(self):
        if self.fail:
            raise RuntimeError("database down")
        return [
            PersonaCard(
                id="5f0d7a8e-0000-4000-8000-000000000001",
                display_name="Aisha Rahman",
                role="attendee",
                interests=["AI SRE", "observability"],
                is_fictional=True,
            )
        ]


@pytest.fixture
def client():
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_the_switcher_lists_selectable_personas_with_public_fields_only(client):
    app.dependency_overrides[get_persona_directory] = StubDirectory
    response = client.get("/api/v1/demo/profiles")
    assert response.status_code == 200
    persona = response.json()["personas"][0]
    assert set(persona) == {"id", "displayName", "role", "company", "interests", "isFictional"}
    assert PRIVATE.isdisjoint(persona)
    assert persona["isFictional"] is True


def test_the_listing_says_when_personas_are_unavailable(client):
    app.dependency_overrides[get_persona_directory] = lambda: StubDirectory(fail=True)
    assert client.get("/api/v1/demo/profiles").status_code == 503


def test_the_public_card_model_has_no_private_fields():
    assert PRIVATE.isdisjoint(PersonaCard.model_json_schema(by_alias=True)["properties"])


def test_the_contract_holds_both_apps_routes_behind_the_web_proxy():
    paths = export_openapi.schema()["paths"]
    assert {"/api/v1/demo/profiles", "/api/v1/search", "/api/v1/recommendations/next"} <= set(paths)
    header = paths["/api/v1/recommendations/next"]["post"]["parameters"][0]
    assert (header["in"], header["name"]) == ("header", "X-Demo-Profile-ID")


def test_the_contract_refuses_two_different_definitions_of_one_path():
    merged: dict = {"/health/live": {"get": {"summary": "Live"}}}
    with pytest.raises(ValueError, match="differently"):
        export_openapi._merge_into(merged, {"/health/live": {"get": {"summary": "Other"}}}, "the path")
