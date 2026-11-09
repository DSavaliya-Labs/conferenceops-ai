"""core-api: personas, agenda, sessions, social features and the operations API."""

import uvicorn

from conferenceops.settings import settings
from conferenceops.transcripts import router as transcripts_router
from conferenceops.web import create_app

app = create_app("core-api")
app.include_router(transcripts_router)  # GET /api/v1/sessions/{id}/replay, POST /api/v1/ops/transcripts/{id}/publish


def run() -> None:
    cfg = settings()
    # No access log: request lines could carry query strings, and our own logs carry trace IDs (PRIV-1).
    uvicorn.run(app, host=cfg.host, port=cfg.port, log_config=None, access_log=False)
