"""discovery-api: search, "where should I go next?" and cited answers. A separate process so it can scale out on
its own during an overload while the core API keeps working."""

import uvicorn

from conferenceops.settings import settings
from conferenceops.web import create_app

app = create_app("discovery-api")


def run() -> None:
    cfg = settings()
    # No access log: request lines could carry query strings, and our own logs carry trace IDs (PRIV-1).
    uvicorn.run(app, host=cfg.host, port=cfg.port, log_config=None, access_log=False)
