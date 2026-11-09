import asyncio
import logging

import pytest

from conferenceops.settings import Settings
from conferenceops.worker import main as worker
from conferenceops.worker.main import IMPORT_RETRY_SECONDS, Loop, every, loops

DATABASE_URL = "postgresql+psycopg://co@db:5432/conferenceops"


def _work(*outcomes):
    """Work that returns or raises each outcome in turn, and records each call."""
    calls = []

    def work():
        outcome = outcomes[len(calls)]
        calls.append(outcome)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    return work, calls


def _pauses(monkeypatch, loop: Loop, count: int) -> list[float]:
    """Run `every` with the waits recorded instead of slept; stop after `count` waits."""
    pauses: list[float] = []

    async def scenario():
        stop = asyncio.Event()

        async def record(_stop, seconds):
            pauses.append(seconds)
            if len(pauses) == count:
                stop.set()

        monkeypatch.setattr(worker, "_pause", record)
        await asyncio.wait_for(every(stop, loop), timeout=5)

    asyncio.run(scenario())
    return pauses


def test_a_failure_is_retried_soon_with_a_doubling_delay_that_resets_after_a_success(monkeypatch):
    # On a fresh install the first imports fail (PostgreSQL or the schema isn't there yet): the schedule must not wait
    # a whole 30-minute interval for the next try.
    down = RuntimeError("database not ready")
    work, calls = _work(down, down, down, down, False, down, False)
    loop = Loop("import", work, interval=1800, retry=15, max_retry=100)

    pauses = _pauses(monkeypatch, loop, count=7)

    assert pauses == [15, 30, 60, 100, 1800, 15, 1800]
    assert len(calls) == 7  # no failure ended the loop


def test_busy_work_runs_again_without_waiting(monkeypatch):
    work, calls = _work(True, True, False)
    loop = Loop("indexing", work, interval=2, retry=2, max_retry=60)

    pauses = _pauses(monkeypatch, loop, count=1)

    assert len(calls) == 3
    assert pauses == [2]


@pytest.mark.parametrize("outcome", [False, RuntimeError("elasticsearch not ready")], ids=["idle", "retrying"])
def test_stop_ends_the_loop_promptly_even_during_a_long_wait(outcome):
    async def scenario():
        stop = asyncio.Event()
        task = asyncio.create_task(every(stop, Loop("import", lambda: _raise_or_return(outcome), 3600, 3600, 3600)))
        await asyncio.sleep(0.05)
        stop.set()
        await asyncio.wait_for(task, timeout=1)

    asyncio.run(scenario())


def _raise_or_return(outcome):
    if isinstance(outcome, Exception):
        raise outcome
    return outcome


def test_the_import_runs_without_elasticsearch_or_openai(caplog):
    # core-api needs the KubeCon schedule even when the OpenAI key is missing; only indexing needs it.
    cfg = Settings(database_url=DATABASE_URL, elasticsearch_url=None, openai_api_key=None)

    with caplog.at_level(logging.WARNING, logger="conferenceops.worker"):
        found = loops(cfg)

    assert [loop.name for loop in found] == ["import"]
    assert (found[0].interval, found[0].retry, found[0].max_retry) == (1800.0, IMPORT_RETRY_SECONDS, 1800.0)
    assert "not indexing (missing ELASTICSEARCH_URL, OPENAI_API_KEY)" in caplog.text


def test_the_warning_names_only_the_missing_setting(caplog):
    cfg = Settings(database_url=DATABASE_URL, elasticsearch_url="http://es:9200", openai_api_key=None)

    with caplog.at_level(logging.WARNING, logger="conferenceops.worker"):
        found = loops(cfg)

    assert [loop.name for loop in found] == ["import"]
    assert "not indexing (missing OPENAI_API_KEY)" in caplog.text


def test_indexing_runs_when_elasticsearch_and_openai_are_configured():
    cfg = Settings(database_url=DATABASE_URL, elasticsearch_url="http://es:9200", openai_api_key="test-key")

    assert [loop.name for loop in loops(cfg)] == ["import", "indexing"]


def test_without_a_database_the_worker_only_beats(caplog):
    cfg = Settings(database_url=None, elasticsearch_url="http://es:9200", openai_api_key="test-key")

    with caplog.at_level(logging.WARNING, logger="conferenceops.worker"):
        assert loops(cfg) == []
    assert "DATABASE_URL is missing" in caplog.text
