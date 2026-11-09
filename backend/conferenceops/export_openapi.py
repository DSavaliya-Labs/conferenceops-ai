"""Write the API's OpenAPI schema, sorted, so CI can diff it against contracts/openapi.json (API-1).

The browser sees one API behind the web proxy: `/api/v1/(search|recommendations|ask)` goes to discovery-api and the
rest of `/api/` to core-api. So the contract is both apps' schemas in one document. A path or a schema that both apps
define must be identical in both (the health probes are), or the export fails instead of picking one."""

import json
import sys
from typing import Any

from conferenceops.core_app.main import app as core_app
from conferenceops.discovery_app.main import app as discovery_app

TITLE = "ConferenceOps AI API"


def _merge_into(target: dict[str, Any], part: dict[str, Any], what: str) -> None:
    for name, value in part.items():
        if name in target and target[name] != value:
            raise ValueError(f"core-api and discovery-api define {what} {name!r} differently")
        target[name] = value


def schema() -> dict[str, Any]:
    core, discovery = core_app.openapi(), discovery_app.openapi()
    merged: dict[str, Any] = {
        "openapi": discovery["openapi"],
        "info": {**discovery["info"], "title": TITLE},
        "paths": {},
        "components": {"schemas": {}},
    }
    for part in (core, discovery):
        _merge_into(merged["paths"], part.get("paths", {}), "the path")
        _merge_into(merged["components"]["schemas"], part.get("components", {}).get("schemas", {}), "the schema")
    return merged


def main() -> None:
    json.dump(schema(), sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
