"""worker: imports the KubeCon feed and turns outbox jobs into Elasticsearch documents, each in its own bounded async
loop, so a slow feed, model or Elasticsearch call can't block the others."""

import asyncio
import contextlib
import logging
import signal
from collections.abc import Callable
from dataclasses import dataclass

from elasticsearch import Elasticsearch
from openai import OpenAI

from conferenceops.db import transaction
from conferenceops.indexing import OpenAIEmbedder, ensure_index, process_outbox
from conferenceops.kubecon import fetch_feed, import_kubecon
from conferenceops.settings import Settings, settings
from conferenceops.telemetry import setup_telemetry

log = logging.getLogger("conferenceops.worker")

HEARTBEAT_SECONDS = 30.0
IMPORT_RETRY_SECONDS = 15.0  # a failed import is retried after 15 s, doubling up to the import interval
OUTBOX_IDLE_SECONDS = 2.0  # a failed indexing pass is retried after 2 s, doubling up to a minute
OUTBOX_RETRY_MAX_SECONDS = 60.0


async def heartbeat(stop: asyncio.Event, interval: float = HEARTBEAT_SECONDS) -> int:
    """Log a heartbeat every `interval` seconds until `stop` is set. Returns the number of beats."""
    beats = 0
    while not stop.is_set():
        beats += 1
        log.info("worker heartbeat", extra={"beat": beats})
        await _pause(stop, interval)
    return beats


async def _pause(stop: asyncio.Event, seconds: float) -> None:
    """Wait `seconds`, or less when `stop` is set."""
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(stop.wait(), timeout=seconds)


@dataclass(frozen=True)
class Loop:
    """Work the worker repeats: after a success it waits `interval` (not at all when the work reports more to do).
    After a failure it retries after `retry` seconds, doubling with each failure in a row up to `max_retry`."""

    name: str
    work: Callable[[], object]
    interval: float
    retry: float
    max_retry: float


async def every(stop: asyncio.Event, loop: Loop) -> None:
    """Run `loop.work` in a thread until `stop` is set; one failure never ends the loop.

    A failure is retried soon, not after a whole interval: on a fresh install the worker starts before PostgreSQL is up
    and before the migration Job has run, and Elasticsearch takes a minute or two to start, so the first runs fail. The
    retry delay doubles while the failures continue, so an outage doesn't flood the logs, and resets after a success."""
    retry = loop.retry
    while not stop.is_set():
        try:
            busy = bool(await asyncio.to_thread(loop.work))
        except Exception:
            delay = min(retry, loop.max_retry)
            retry = delay * 2
            log.exception("%s failed; retrying in %.0f s", loop.name, delay)
        else:
            retry = loop.retry
            delay = 0.0 if busy else loop.interval
        if delay:
            await _pause(stop, delay)


def _elasticsearch(cfg: Settings) -> Elasticsearch:
    auth = (cfg.elasticsearch_username, cfg.elasticsearch_password) if cfg.elasticsearch_username else None
    return Elasticsearch(cfg.elasticsearch_url, basic_auth=auth, request_timeout=30)


def import_once() -> bool:
    feed = fetch_feed()
    with transaction() as db:
        counts = import_kubecon(db, feed)
    log.info("kubecon import done", extra=counts)
    return False


def loops(cfg: Settings) -> list[Loop]:
    """The loops this configuration can run. The import needs only PostgreSQL, so core-api gets the KubeCon schedule
    even without Elasticsearch or an OpenAI key. Indexing needs both; until they are configured, the import's outbox
    jobs wait in PostgreSQL."""
    if not cfg.database_url:
        log.warning("worker idle: DATABASE_URL is missing, so there is nothing to import or index")
        return []
    found = [
        Loop("import", import_once, cfg.import_interval_seconds, IMPORT_RETRY_SECONDS, cfg.import_interval_seconds)
    ]
    needed = {"ELASTICSEARCH_URL": cfg.elasticsearch_url, "OPENAI_API_KEY": cfg.openai_api_key}
    missing = [name for name, value in needed.items() if not value]
    if missing:
        log.warning("worker not indexing (missing %s); imported sessions wait in the outbox", ", ".join(missing))
        return found
    es = _elasticsearch(cfg)
    embedder = OpenAIEmbedder(OpenAI(api_key=cfg.openai_api_key, timeout=30, max_retries=2), cfg.openai_embedding_model)

    def index_batch() -> int:
        ensure_index(es)
        with transaction() as db:
            return process_outbox(db, es, embedder)

    found.append(Loop("indexing", index_batch, OUTBOX_IDLE_SECONDS, OUTBOX_IDLE_SECONDS, OUTBOX_RETRY_MAX_SECONDS))
    return found


async def main_async() -> None:
    cfg = settings()
    setup_telemetry("worker", cfg)
    stop = asyncio.Event()
    running = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        running.add_signal_handler(sig, stop.set)
    log.info("worker started")
    await asyncio.gather(heartbeat(stop), *(every(stop, loop) for loop in loops(cfg)))
    log.info("worker stopped")


def run() -> None:
    asyncio.run(main_async())
