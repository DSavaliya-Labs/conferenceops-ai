"""core-api: personas, agenda, sessions, social features and the operations API."""

import uvicorn

from conferenceops.personas import router as personas_router
from conferenceops.portal import router as portal_router
from conferenceops.settings import settings
from conferenceops.web import create_app

app = create_app("core-api")
app.include_router(personas_router)  # GET /api/v1/demo/profiles: the personas the switcher offers
app.include_router(portal_router)  # GET /api/v1/conferences, .../{slug}/access-facts, /api/v1/me/agenda: the portal


def run() -> None:
    cfg = settings()
    # No access log: request lines could carry query strings, and our own logs carry trace IDs (PRIV-1).
    uvicorn.run(app, host=cfg.host, port=cfg.port, log_config=None, access_log=False)
