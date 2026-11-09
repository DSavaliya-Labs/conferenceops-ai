"""The community feed: posts and one level of replies, by the persona in X-Demo-Profile-ID (AUTHZ-1), with the text
kept out of logs (PRIV-1). No PostgreSQL: the data layer is a fake."""

import logging
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from conferenceops import export_openapi
from conferenceops.community import (
    MAX_BODY,
    SEED_POSTS,
    PostRow,
    Thread,
    get_community_data,
    seed_post_id,
)
from conferenceops.core_app.main import app

AISHA = uuid.UUID("11111111-1111-1111-1111-111111111111")
AS_AISHA = {"X-Demo-Profile-ID": str(AISHA)}
NOW = datetime(2026, 11, 9, 18, 0, tzinfo=UTC)


class FakeData:
    def __init__(self, fail: Exception | None = None):
        self.fail = fail
        self.posts: list[PostRow] = []
        self.limits: list[int] = []

    def feed(self, limit):
        if self.fail:
            raise self.fail
        self.limits.append(limit)
        top = sorted((p for p in self.posts if p.parent_id is None), key=lambda p: p.created_at, reverse=True)
        return [
            Thread(p, tuple(sorted((r for r in self.posts if r.parent_id == p.id), key=lambda r: r.created_at)))
            for p in top[:limit]
        ]

    def create(self, author_id, body, parent_id):
        if self.fail:
            raise self.fail
        if author_id != AISHA:
            return "no_profile"
        if parent_id is not None:
            parent = next((p for p in self.posts if p.id == parent_id), None)
            if parent is None or parent.parent_id is not None:
                return "no_parent"
        row = PostRow(
            id=uuid.uuid4(),
            author="Aisha Rahman",
            author_role="attendee",
            body=body,
            created_at=NOW + timedelta(minutes=len(self.posts)),
            parent_id=parent_id,
        )
        self.posts.append(row)
        return row


@pytest.fixture
def client():
    state = {"data": FakeData()}
    app.dependency_overrides[get_community_data] = lambda: state["data"]
    try:
        yield TestClient(app), state
    finally:
        app.dependency_overrides.clear()


def test_a_post_and_a_reply_appear_in_the_feed_newest_first_with_replies_under_their_post(client):
    http, state = client
    first = http.post("/api/v1/community/posts", json={"body": "  Who is going to the eBPF talks?  "}, headers=AS_AISHA)
    assert first.status_code == 201
    created = first.json()
    assert created["body"] == "Who is going to the eBPF talks?"  # trimmed
    assert created["author"] == "Aisha Rahman"
    second = http.post("/api/v1/community/posts", json={"body": "Second post"}, headers=AS_AISHA)
    reply = http.post(
        "/api/v1/community/posts", json={"body": "Me!", "parentId": created["id"]}, headers=AS_AISHA
    ).json()
    assert reply["parentId"] == created["id"]

    feed = http.get("/api/v1/community/posts").json()
    assert feed["isCommunityContent"] is True
    assert [p["body"] for p in feed["posts"]] == ["Second post", "Who is going to the eBPF talks?"]
    assert [r["body"] for r in feed["posts"][1]["replies"]] == ["Me!"]
    assert feed["posts"][0]["id"] == second.json()["id"]
    assert state["data"].limits == [20]


def test_the_author_is_always_the_persona_in_the_header(client):
    http, _ = client
    # A client can't name an author; an unknown persona is 404 and a missing header 400 (AUTHZ-1).
    sneaky = http.post("/api/v1/community/posts", json={"body": "hi", "author": "Sam Rivera"}, headers=AS_AISHA)
    assert sneaky.json()["author"] == "Aisha Rahman"
    other = http.post("/api/v1/community/posts", json={"body": "hi"}, headers={"X-Demo-Profile-ID": str(uuid.uuid4())})
    assert (other.status_code, other.json()) == (404, {"detail": "Not found"})
    assert http.post("/api/v1/community/posts", json={"body": "hi"}).status_code == 400


def test_a_reply_needs_a_top_level_post_and_both_misses_answer_the_same_404(client):
    http, _ = client
    post = http.post("/api/v1/community/posts", json={"body": "top"}, headers=AS_AISHA).json()
    reply = http.post("/api/v1/community/posts", json={"body": "r", "parentId": post["id"]}, headers=AS_AISHA).json()
    nested = http.post("/api/v1/community/posts", json={"body": "rr", "parentId": reply["id"]}, headers=AS_AISHA)
    unknown = http.post("/api/v1/community/posts", json={"body": "x", "parentId": str(uuid.uuid4())}, headers=AS_AISHA)
    assert (nested.status_code, nested.json()) == (404, {"detail": "Not found"})
    assert (unknown.status_code, unknown.json()) == (404, {"detail": "Not found"})


@pytest.mark.parametrize("body", ["", "   ", "x" * (MAX_BODY + 1)])
def test_an_empty_blank_or_too_long_post_is_rejected(client, body):
    http, state = client
    assert http.post("/api/v1/community/posts", json={"body": body}, headers=AS_AISHA).status_code == 422
    assert state["data"].posts == []


def test_the_feed_limit_is_bounded(client):
    http, state = client
    assert http.get("/api/v1/community/posts?limit=51").status_code == 422
    assert http.get("/api/v1/community/posts?limit=0").status_code == 422
    assert http.get("/api/v1/community/posts?limit=5").status_code == 200
    assert state["data"].limits == [5]


def test_a_failing_store_answers_503_and_logs_no_text(client, caplog):
    http, state = client
    state["data"] = FakeData(fail=RuntimeError("could not insert 'my secret plan'"))
    with caplog.at_level(logging.DEBUG):
        posted = http.post("/api/v1/community/posts", json={"body": "my secret plan"}, headers=AS_AISHA)
        listed = http.get("/api/v1/community/posts")
    assert posted.status_code == listed.status_code == 503
    assert "secret plan" not in caplog.text  # PRIV-1: the error's type only
    assert "RuntimeError" in caplog.text


def test_the_seeded_conversation_is_stable_fictional_and_one_level_deep():
    keys = [key for key, *_ in SEED_POSTS]
    assert len(keys) == len(set(keys))
    assert seed_post_id("post-1") == seed_post_id("post-1")  # the same ID in every database
    by_key = {key: parent for key, _, parent, _, _ in SEED_POSTS}
    for key, persona, parent, _, text in SEED_POSTS:
        assert persona.startswith("P")  # a fictional demo persona, never a real speaker (SPEAKER-1)
        assert 1 <= len(text) <= MAX_BODY
        if parent is not None:
            assert by_key[parent] is None, f"{key} replies to a reply"


def test_the_contract_lists_the_feed_and_the_write_with_the_header_required():
    paths = export_openapi.schema()["paths"]
    feed = paths["/api/v1/community/posts"]
    assert {"200", "422", "503"} == set(feed["get"]["responses"])
    write = feed["post"]
    headers = [p for p in write.get("parameters", []) if p["in"] == "header"]
    assert [(h["name"], h["required"]) for h in headers] == [("X-Demo-Profile-ID", True)]
    assert {"201", "400", "404", "422", "503"} == set(write["responses"])
