"""Export the OpenAPI documents of the triage API and the enrolment API from the FastAPI apps themselves.

Usage: uv run python tools/export_api_docs.py          # rewrite docs/api/openapi-triage.json and openapi-enroll.json
       uv run python tools/export_api_docs.py --check  # exit 1 if the files differ from what the apps produce now

PURPOSE: The API documents cannot drift from the code because they ARE the code's own description; tests/test_api_docs.py regenerates them and fails on a difference.
INPUT: the apps built with placeholder collaborators (nothing is called while a document is built, so no database, CA or token is needed). The triage app is built with the FHIR facade switched on so its routes are listed.
OUTPUT: docs/api/openapi-triage.json and docs/api/openapi-enroll.json (OpenAPI 3.1, sorted keys, 2-space indent).
HOW: app.openapi() from FastAPI. The page files and /ui-config.json are not listed on purpose for the first (include_in_schema=False for the page files); /ui-config.json is listed.
ROLE IN THESIS: The interface documentation of the cloud core (docs/production-topology.md); the MQTT and bus side is docs/api/asyncapi.yaml.
BEFORE -> AFTER: Before: routes documented only in module docstrings. After: machine-readable documents checked on every test run.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))  # lets `python tools/export_api_docs.py` find backend/
from backend.enroll.app import create_app as enroll_app  # noqa: E402
from backend.jwtauth import Authenticator  # noqa: E402
from backend.triage.app import create_app as triage_app  # noqa: E402

OUT = Path(__file__).parent.parent / "docs/api"


def documents() -> dict[str, str]:
    """File name -> text."""
    triage = triage_app(store=None, audit=None, auth=Authenticator("dev"), registry=None, fhir_upstream=object())
    enroll = enroll_app(registry=None, audit=None, auth=Authenticator("dev"), ca=None)
    return {f"openapi-{n}.json": json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n" for n, app in (("triage", triage), ("enroll", enroll))}


def main(argv: list[str]) -> int:
    """Write the files, or with --check compare."""
    docs = documents()
    if "--check" in argv:
        stale = [n for n, t in docs.items() if not (OUT / n).exists() or (OUT / n).read_text() != t]
        print("stale: " + ", ".join(stale) + " (run uv run python tools/export_api_docs.py)" if stale else "API documents are current")
        return 1 if stale else 0
    OUT.mkdir(parents=True, exist_ok=True)
    for n, t in docs.items():
        (OUT / n).write_text(t)
        print(f"wrote {OUT / n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
