"""Write the discovery API's OpenAPI schema, sorted, so CI can diff it against contracts/openapi.json (API-1)."""

import json
import sys

from conferenceops.discovery_app.main import app


def main() -> None:
    json.dump(app.openapi(), sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
