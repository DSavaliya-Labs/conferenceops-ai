"""core-api: personas, agenda, sessions, social features and the operations API."""

import uvicorn

from conferenceops.dictation import router as dictation_router
from conferenceops.settings import settings
from conferenceops.web import create_app

app = create_app("core-api")
app.include_router(dictation_router)  # POST /api/v1/dictation: the spoken half of the Ask bar


def run() -> None:
    cfg = settings()
    # No access log: request lines could carry query strings, and our own logs carry trace IDs (PRIV-1).
    uvicorn.run(app, host=cfg.host, port=cfg.port, log_config=None, access_log=False)
