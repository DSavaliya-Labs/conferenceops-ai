"""worker: outbox indexing, imports and transcript processing, each in its own bounded async loop, so a slow model
or vendor call can't block the others. For now it runs a heartbeat until it is told to stop."""

import asyncio
import contextlib
import logging
import signal

from conferenceops.settings import settings
from conferenceops.telemetry import setup_telemetry

log = logging.getLogger("conferenceops.worker")

HEARTBEAT_SECONDS = 30.0


async def heartbeat(stop: asyncio.Event, interval: float = HEARTBEAT_SECONDS) -> int:
    """Log a heartbeat every `interval` seconds until `stop` is set. Returns the number of beats."""
    beats = 0
    while not stop.is_set():
        beats += 1
        log.info("worker heartbeat", extra={"beat": beats})
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval)
    return beats


async def main_async() -> None:
    setup_telemetry("worker", settings())
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    log.info("worker started")
    await heartbeat(stop)
    log.info("worker stopped")


def run() -> None:
    asyncio.run(main_async())
