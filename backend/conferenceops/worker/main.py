"""worker: imports the KubeCon feed and turns outbox jobs into Elasticsearch documents, each in its own bounded async
loop, so a slow feed, model or Elasticsearch call can't block the others."""

import asyncio
import contextlib
import logging
import signal

from elasticsearch import Elasticsearch
from openai import OpenAI

from conferenceops.db import transaction
from conferenceops.indexing import OpenAIEmbedder, ensure_index, process_outbox
from conferenceops.kubecon import fetch_feed, import_kubecon
from conferenceops.settings import Settings, settings
from conferenceops.telemetry import setup_telemetry

log = logging.getLogger("conferenceops.worker")

HEARTBEAT_SECONDS = 30.0
OUTBOX_IDLE_SECONDS = 2.0


async def heartbeat(stop: asyncio.Event, interval: float = HEARTBEAT_SECONDS) -> int:
    """Log a heartbeat every `interval` seconds until `stop` is set. Returns the number of beats."""
    beats = 0
    while not stop.is_set():
        beats += 1
        log.info("worker heartbeat", extra={"beat": beats})
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval)
    return beats


async def every(stop: asyncio.Event, interval: float, work, name: str) -> None:
    """Run `work` in a thread, then wait `interval` (or less, when it reports more work); never let one failure end
    the loop."""
    while not stop.is_set():
        busy = False
        try:
            busy = bool(await asyncio.to_thread(work))
        except Exception:
            log.exception("%s failed", name)
        if not busy:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=interval)


def _elasticsearch(cfg: Settings) -> Elasticsearch:
    auth = (cfg.elasticsearch_username, cfg.elasticsearch_password) if cfg.elasticsearch_username else None
    return Elasticsearch(cfg.elasticsearch_url, basic_auth=auth, request_timeout=30)


async def main_async() -> None:
    cfg = settings()
    setup_telemetry("worker", cfg)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    log.info("worker started")
    loops = [heartbeat(stop)]
    if cfg.database_url and cfg.elasticsearch_url and cfg.openai_api_key:
        es = _elasticsearch(cfg)
        embedder = OpenAIEmbedder(
            OpenAI(api_key=cfg.openai_api_key, timeout=30, max_retries=2), cfg.openai_embedding_model
        )

        def import_once() -> bool:
            feed = fetch_feed()
            with transaction() as db:
                counts = import_kubecon(db, feed)
            log.info("kubecon import done", extra=counts)
            return False

        def index_batch() -> int:
            ensure_index(es)
            with transaction() as db:
                return process_outbox(db, es, embedder)

        loops += [
            every(stop, cfg.import_interval_seconds, import_once, "import"),
            every(stop, OUTBOX_IDLE_SECONDS, index_batch, "indexing"),
        ]
    else:
        log.warning("worker idle: DATABASE_URL, ELASTICSEARCH_URL or OPENAI_API_KEY is missing")
    await asyncio.gather(*loops)
    log.info("worker stopped")


def run() -> None:
    asyncio.run(main_async())
