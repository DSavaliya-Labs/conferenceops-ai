import asyncio

import pytest
from fastapi.testclient import TestClient

from conferenceops.core_app.main import app as core_app
from conferenceops.discovery_app.main import app as discovery_app
from conferenceops.worker.main import heartbeat


@pytest.mark.parametrize("app", [core_app, discovery_app], ids=["core-api", "discovery-api"])
@pytest.mark.parametrize("path", ["/health/live", "/health/ready"])
def test_health_endpoints_answer_without_any_downstream_service(app, path):
    # No PostgreSQL, Elasticsearch or OpenAI exists in this test: the probes must still answer (K8S-1).
    response = TestClient(app).get(path)
    assert response.status_code == 200


def test_unknown_paths_are_404_not_500():
    assert TestClient(core_app).get("/nope").status_code == 404


def test_worker_heartbeat_stops_promptly_when_told_to():
    async def scenario() -> int:
        stop = asyncio.Event()
        task = asyncio.create_task(heartbeat(stop, interval=0.01))
        await asyncio.sleep(0.05)
        stop.set()
        return await asyncio.wait_for(task, timeout=1)

    beats = asyncio.run(scenario())
    assert beats >= 2
