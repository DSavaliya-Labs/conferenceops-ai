"""Cited answers (AI-1, TRANSCRIPT-1, PRIV-1): retrieval filters, post-retrieval validation, citation validation,
the deterministic fallback, and what never reaches the logs."""

import logging
import uuid

import pytest
from fastapi.testclient import TestClient

from conferenceops.ask import (
    NO_PASSAGES,
    AskRequest,
    AskResponse,
    AskService,
    GroundedAnswer,
    Passage,
    fallback_answer,
    get_ask,
    passages_prompt,
    validate_citations,
)
from conferenceops.discovery_app.main import app
from conferenceops.transcripts import LABEL

QUESTION = "what did the agent get wrong about the database"
SESSION = uuid.uuid4()
TRANSCRIPT = uuid.uuid4()
MODEL_ANSWER = "The agent blamed a healthy database; a config push had emptied the connection pool."


def _passage(index: int, text: str, revision: int = 1, session_id=SESSION) -> Passage:
    return Passage(
        chunk_id=f"{TRANSCRIPT}:{revision}:{index}",
        transcript_id=TRANSCRIPT,
        session_id=session_id,
        revision=revision,
        index=index,
        text=text,
        start=10.0 * index + 1.5,
        end=10.0 * index + 9.0,
        session_title="AI SRE in Practice: What Our Agent Got Wrong",
        speaker="Daniel Kowalczyk",
        conference="cloudbridge-summit-2026",
        event="CloudBridge Summit 2026",
        is_fictional=True,
        is_simulated=True,
    )


P0 = _passage(0, "The agent blamed the primary database with total confidence.")
P1 = _passage(1, "A config push had set the connection pool to zero on three pods.")
STALE = _passage(2, "This chunk's transcript was unpublished after it was indexed.")


class FakeES:
    """Returns the same hits for both channels; records the filters each one applied."""

    def __init__(self, ids=(P0.chunk_id, STALE.chunk_id, P1.chunk_id)):
        self.ids = list(ids)
        self.calls = []

    def search(self, index, size, source, query=None, knn=None):
        filters = (knn or {}).get("filter") or query["bool"]["filter"]
        self.calls.append(("knn" if knn else "bm25", index, filters))
        order = list(reversed(self.ids)) if knn else self.ids
        return {"hits": {"hits": [{"_id": doc_id} for doc_id in order]}}


class FakeEmbedder:
    def __init__(self, fail=False):
        self.fail = fail

    def embed(self, texts):
        if self.fail:
            raise TimeoutError("model too slow")
        return [[0.1] * 1536 for _ in texts]


class FakePassages:
    """The manifest: only P0 and P1 are published at their revision; STALE isn't, whatever the index says."""

    def __init__(self, published=(P0, P1)):
        self.published = {p.chunk_id: p for p in published}
        self.calls = []

    def load_published(self, chunk_ids, conference):
        self.calls.append((list(chunk_ids), conference))
        return {doc_id: p for doc_id, p in self.published.items() if doc_id in chunk_ids}


class FakeModel:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.seen = []

    def answer(self, question, passages):
        self.seen.append((question, [p.chunk_id for p in passages]))
        if self.error:
            raise self.error
        return self.result


def _service(es=None, embedder=None, passages=None, model=None) -> AskService:
    return AskService(es or FakeES(), embedder or FakeEmbedder(), passages or FakePassages(), model)


def test_both_channels_filter_on_published_text_and_the_conference():
    es = FakeES()
    _service(es=es).ask(AskRequest(query=QUESTION, conference="cloudbridge-summit-2026"))
    assert [call[0] for call in es.calls] == ["bm25", "knn"]
    for _, index, filters in es.calls:
        assert index == "co-transcripts"
        assert filters == [{"term": {"status": "published"}}, {"term": {"conference": "cloudbridge-summit-2026"}}]


def test_hits_are_revalidated_against_postgres_so_a_chunk_that_lost_publication_is_never_cited_or_shown_to_the_model():
    model = FakeModel(GroundedAnswer(answer=MODEL_ANSWER, citations=[P0.chunk_id, STALE.chunk_id]))
    passages = FakePassages()

    response = _service(passages=passages, model=model).ask(AskRequest(query=QUESTION))

    assert passages.calls == [([P0.chunk_id, P1.chunk_id, STALE.chunk_id], None)]  # every fused hit is checked
    assert model.seen == [(QUESTION, [P0.chunk_id, P1.chunk_id])]  # the stale chunk never reaches the model
    assert [c.chunk_id for c in response.citations] == [P0.chunk_id]
    assert response.answer_source == "model" and response.answer == MODEL_ANSWER
    assert response.retrieval_mode == "hybrid"
    assert (response.event, response.is_fictional) == ("CloudBridge Summit 2026", True)


def test_only_citations_from_the_retrieved_set_survive_and_the_server_builds_the_replay_links():
    model = FakeModel(GroundedAnswer(answer=MODEL_ANSWER, citations=["made-up:1:0", P1.chunk_id, P1.chunk_id, " "]))

    response = _service(model=model).ask(AskRequest(query=QUESTION))

    [cited] = response.citations
    assert cited.chunk_id == P1.chunk_id
    assert cited.replay_path == f"/sessions/{SESSION}/replay?t=11.5"  # from the chunk's own timing, not the model
    assert (cited.start_seconds, cited.end_seconds, cited.speaker, cited.label) == (
        11.5,
        19.0,
        "Daniel Kowalczyk",
        LABEL,
    )
    assert cited.excerpt == P1.text
    assert validate_citations(["x", P1.chunk_id, P0.chunk_id, P1.chunk_id], [P0, P1]) == [P0.chunk_id, P1.chunk_id]


@pytest.mark.parametrize(
    "model",
    [
        FakeModel(error=TimeoutError("the model took too long")),
        FakeModel(error=RuntimeError("the model is down")),
        FakeModel(GroundedAnswer(answer="Something confident.", citations=["not-retrieved:1:0"])),
        FakeModel(GroundedAnswer(answer="   ", citations=[P0.chunk_id])),
        None,
    ],
    ids=["slow", "down", "no valid citation", "empty answer", "no key configured"],
)
def test_the_fallback_is_deterministic_and_marked_as_not_model_written(model):
    response = _service(model=model).ask(AskRequest(query=QUESTION))

    assert response.answer_source == "passages"
    assert response.answer == fallback_answer([P0, P1])
    assert response.answer == f"From “{P0.session_title}” ({P0.speaker}): “{P0.text}”"
    assert [c.chunk_id for c in response.citations] == [
        P0.chunk_id,
        P1.chunk_id,
    ]  # every validated passage, with its moment


def test_no_published_passage_means_an_honest_no_answer_without_a_model_call():
    model = FakeModel(GroundedAnswer(answer="should not be used", citations=[]))
    response = _service(es=FakeES(ids=[STALE.chunk_id]), model=model).ask(AskRequest(query=QUESTION))
    assert (response.answer, response.answer_source, response.citations) == (NO_PASSAGES, "passages", [])
    assert model.seen == []
    assert _service(es=FakeES(ids=[]), model=model).ask(AskRequest(query=QUESTION)).citations == []


def test_the_limit_caps_the_passages_the_model_sees_and_the_citations():
    model = FakeModel(GroundedAnswer(answer=MODEL_ANSWER, citations=[P0.chunk_id, P1.chunk_id]))
    response = _service(model=model).ask(AskRequest(query=QUESTION, limit=1))
    assert model.seen[0][1] == [P0.chunk_id]  # fused order: P0 is first in BM25 and second in kNN
    assert [c.chunk_id for c in response.citations] == [P0.chunk_id]


def test_when_the_query_embedding_fails_retrieval_is_lexical_and_says_so():
    es = FakeES()
    response = _service(es=es, embedder=FakeEmbedder(fail=True)).ask(AskRequest(query=QUESTION))
    assert [call[0] for call in es.calls] == ["bm25"]
    assert response.retrieval_mode == "lexical" and response.citations


def test_the_prompt_gives_the_model_ids_titles_speakers_and_text_only():
    prompt = passages_prompt(QUESTION, [P0, P1])
    assert prompt.startswith(f"Question: {QUESTION}\n\nPassages:\n")
    assert f"[{P0.chunk_id}] {P0.session_title} ({P0.speaker}): {P0.text}" in prompt
    assert str(SESSION) not in prompt and "replay" not in prompt  # no links to copy: the server builds them


def test_the_question_the_passages_and_the_model_s_words_never_reach_the_logs(caplog):
    scenarios = [
        FakeModel(GroundedAnswer(answer=MODEL_ANSWER, citations=[P0.chunk_id])),
        FakeModel(error=TimeoutError(f"timed out while answering: {QUESTION}")),
        FakeModel(GroundedAnswer(answer=f"Nothing here answers {QUESTION}", citations=[])),
    ]
    with caplog.at_level(logging.DEBUG, logger="conferenceops.ask"):
        for model in scenarios:
            _service(model=model).ask(AskRequest(query=QUESTION))
        _service(embedder=FakeEmbedder(fail=True)).ask(AskRequest(query=QUESTION))
    assert caplog.records, "the answers produced no log lines at all"
    logged = caplog.text + "".join(str(record.__dict__) for record in caplog.records)
    for private in (QUESTION, P0.text, P1.text, MODEL_ANSWER):
        assert private not in logged, private
    assert "ask model failed (TimeoutError)" in caplog.text  # the type is logged, the message never


class StubBackend:
    def __init__(self):
        self.requests = []

    def ask(self, request):
        self.requests.append(request)
        return AskResponse(answer="A short answer.", answer_source="model", citations=[], retrieval_mode="hybrid")


def test_the_endpoint_takes_the_question_in_the_body_and_answers_in_camel_case():
    backend = StubBackend()
    app.dependency_overrides[get_ask] = lambda: backend
    try:
        client = TestClient(app)
        response = client.post("/api/v1/ask", json={"query": QUESTION, "conference": "cloudbridge-summit-2026"})
        assert response.status_code == 200
        assert response.json()["answerSource"] == "model" and response.json()["retrievalMode"] == "hybrid"
        assert backend.requests[0].query == QUESTION and backend.requests[0].conference == "cloudbridge-summit-2026"
        assert client.post("/api/v1/ask", json={"query": ""}).status_code == 422
        assert client.post("/api/v1/ask", json={"query": "x" * 501}).status_code == 422
    finally:
        app.dependency_overrides.clear()


def test_a_failing_backend_is_a_503_that_logs_the_error_type_and_the_query_length_only(caplog):
    class Broken:
        def ask(self, request):
            raise ConnectionError(f"elasticsearch rejected the query [{request.query}]")

    app.dependency_overrides[get_ask] = Broken
    try:
        with caplog.at_level(logging.DEBUG, logger="conferenceops.ask"):
            response = TestClient(app).post("/api/v1/ask", json={"query": QUESTION})
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 503
    assert "ask failed (ConnectionError)" in caplog.text
    assert QUESTION not in caplog.text
    assert [record.query_length for record in caplog.records if hasattr(record, "query_length")] == [len(QUESTION)]
