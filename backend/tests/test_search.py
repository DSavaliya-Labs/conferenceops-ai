from datetime import UTC, datetime

from fastapi.testclient import TestClient

from conferenceops.discovery_app.main import app, get_search
from conferenceops.search import HybridSearch, SearchHit, SearchRequest, SearchResponse, rrf_fuse, venue_time


def test_rrf_uses_one_over_sixty_plus_rank_and_breaks_ties_by_id():
    # "b" is 2nd and 1st: 1/62 + 1/61 beats "a" (1st only, 1/61) and "c" (2nd only, 1/62).
    assert rrf_fuse([["a", "b"], ["b", "c"]]) == ["b", "a", "c"]
    # Identical scores are ordered by ID, so results never shuffle between requests.
    assert rrf_fuse([["y"], ["x"]]) == ["x", "y"]


def test_venue_time_uses_the_event_time_zone():
    assert venue_time(datetime(2026, 11, 10, 17, 30, tzinfo=UTC)) == "Tue Nov 10, 10:30 AM MST"


class FakeES:
    def __init__(self):
        self.calls = []
        self.src = {
            "s1": {
                "title": "Tracing Everything",
                "event_name": "KubeCon",
                "conference": "kubecon-na-2026",
                "is_fictional": False,
                "starts_at": "2026-11-10T17:30:00+00:00",
                "room": "Hall DE",
                "level": "beginner",
                "topics": ["Observability"],
            },
            "s2": {
                "title": "Policy as Code",
                "event_name": "KubeCon",
                "conference": "kubecon-na-2026",
                "is_fictional": False,
                "starts_at": "2026-11-11T16:00:00+00:00",
            },
        }

    def search(self, index, size, source, query=None, knn=None):
        self.calls.append("knn" if knn else "bm25")
        assert {"term": {"is_service": False}} in ((knn or {}).get("filter") or query["bool"]["filter"])
        return {"hits": {"hits": [{"_id": "s2"}, {"_id": "s1"}] if knn else [{"_id": "s1"}]}}

    def mget(self, index, ids):
        return {"docs": [{"_id": i, "found": True, "_source": self.src[i]} for i in ids]}


class FakeEmbedder:
    def __init__(self, fail=False):
        self.fail = fail

    def embed(self, texts):
        if self.fail:
            raise TimeoutError("model too slow")
        return [[0.1] * 1536 for _ in texts]


def test_hybrid_search_fuses_both_channels_with_the_same_filters():
    es = FakeES()
    response = HybridSearch(es, FakeEmbedder()).search(SearchRequest(query="tracing"))
    assert es.calls == ["bm25", "knn"]
    assert response.retrieval_mode == "hybrid"
    assert [r.id for r in response.results] == ["s1", "s2"]  # s1 is in both channels
    assert response.results[0].starts_at_venue == "Tue Nov 10, 10:30 AM MST"


def test_search_falls_back_to_keywords_when_the_model_fails(caplog):
    es = FakeES()
    response = HybridSearch(es, FakeEmbedder(fail=True)).search(SearchRequest(query="secret-words"))
    assert es.calls == ["bm25"]
    assert response.retrieval_mode == "lexical"
    assert [r.id for r in response.results] == ["s1"]
    assert "secret-words" not in caplog.text  # PRIV-1: never log the query


class StubBackend:
    def search(self, request):
        assert request.query == "observability"
        hit = SearchHit(
            id="s1",
            title="T",
            event="KubeCon",
            conference="kubecon-na-2026",
            is_fictional=False,
            starts_at=datetime(2026, 11, 10, 17, 30, tzinfo=UTC),
            starts_at_venue="Tue Nov 10, 10:30 AM MST",
        )
        return SearchResponse(results=[hit], retrieval_mode="hybrid")


def test_the_endpoint_takes_the_query_in_the_body_and_answers_in_camel_case():
    app.dependency_overrides[get_search] = StubBackend
    try:
        client = TestClient(app)
        body = client.post("/api/v1/search", json={"query": "observability"}).json()
        assert body["retrievalMode"] == "hybrid"
        assert body["results"][0]["startsAtVenue"] == "Tue Nov 10, 10:30 AM MST"
        assert client.post("/api/v1/search", json={"query": ""}).status_code == 422
    finally:
        app.dependency_overrides.clear()
