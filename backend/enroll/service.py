"""Service entry point of the enrolment API: builds the app from environment variables and runs it.

PURPOSE: backend/enroll/app.py has no way to start by itself (its collaborators are injected). This wires them from the environment, the way backend/triage/stream.py does, and refuses to start on a missing or unsafe setting.
INPUT: AUTH_MODE and OIDC_ISSUER / OIDC_AUDIENCE (enroll-api) / OIDC_JWKS_URL (backend/jwtauth.py); EHG_ENROLL_DSN (a libpq connection string for the database with the registry tables; the role needs the rights of `ehg_triage`);
  STEPCA_URL (e.g. https://step-ca:9000), STEPCA_PROVISIONER (the JWK provisioner's name), STEPCA_KEY_FILE (the provisioner's EC private key, PEM; kept on the VM, never in git), STEPCA_ROOT_FP (the root certificate's SHA-256 fingerprint, 64 hex characters),
  STEPCA_ROOT_CERT (the root certificate file the CA's TLS certificate must chain to); optional BIND_HOST (default 127.0.0.1) and ENROLL_PORT (default 8790).
OUTPUT: build(env) -> the FastAPI app; main() serves it with uvicorn. GET /crl asks the CA for its current revocation list (an unreachable CA is a 502, never an empty list). A missing variable stops the start naming ALL missing ones (never their values).
HOW: Nothing connects at start-up (the database is opened per request, the CA is asked per call), so a restart does not depend on either being up. The key file must not be readable by others (mode with no "other" bits); a key file that is missing or not an EC key is refused.
  The fingerprint is checked for shape so a typo stops the start instead of making every enrolment fail later. UNVERIFIED against a real step-ca (decision 0013); the /crl path and format of step-ca 0.30.2 are assumptions (see deploy/step-ca/README.md).
ROLE IN THESIS: The missing piece between the enrolment API's code and a running container (docs/progress.md, Unit 11b).
BEFORE -> AFTER: Before: the API existed only as a library. After: `python -m backend.enroll.service` starts it.
"""

import os
import re
import ssl
import stat
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException

from backend.enroll.app import create_app
from backend.enroll.stepca_client import StepCaClient
from backend.jwtauth import from_env
from backend.triage.audit import AuditLog
from backend.triage.registry import Registry

REQUIRED = ("EHG_ENROLL_DSN", "STEPCA_URL", "STEPCA_PROVISIONER", "STEPCA_KEY_FILE", "STEPCA_ROOT_FP", "STEPCA_ROOT_CERT")


def build(env=os.environ, http: httpx.Client | None = None) -> FastAPI:
    """The app, from the environment. `http` replaces the client that talks to the CA (tests)."""
    missing = [k for k in REQUIRED if not env.get(k)]
    if missing:
        raise SystemExit(f"the enrolment service needs {', '.join(missing)}")
    fingerprint = env["STEPCA_ROOT_FP"].lower()
    if not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
        raise SystemExit("STEPCA_ROOT_FP must be the 64 hex characters of the root certificate's SHA-256 fingerprint")
    key_path, root_cert = Path(env["STEPCA_KEY_FILE"]), Path(env["STEPCA_ROOT_CERT"])
    if not key_path.is_file() or not root_cert.is_file():
        raise SystemExit("STEPCA_KEY_FILE and STEPCA_ROOT_CERT must be existing files")
    if key_path.stat().st_mode & stat.S_IRWXO:
        raise SystemExit(f"{key_path} is accessible to other users: make it mode 640 or 600")
    key_pem = key_path.read_bytes()
    if b"BEGIN EC PRIVATE KEY" not in key_pem and b"BEGIN PRIVATE KEY" not in key_pem:
        raise SystemExit("STEPCA_KEY_FILE does not hold a PEM private key")

    import psycopg
    dsn = env["EHG_ENROLL_DSN"]
    connect = lambda: psycopg.connect(dsn, connect_timeout=5)  # noqa: E731  (opened per request: a restart does not need the database to be up)
    audit = AuditLog(connect)
    ca_http = http or httpx.Client(verify=ssl.create_default_context(cafile=str(root_cert)), timeout=10)  # the CA's TLS certificate must chain to OUR root, not to a public CA
    ca = StepCaClient(env["STEPCA_URL"], env["STEPCA_PROVISIONER"], key_pem, fingerprint, http=ca_http)

    def crl_source() -> str:
        """The CA's current revocation list as PEM text; any failure is a 502 so an empty or stale answer is never installed as 'no revocations'."""
        try:
            r = ca_http.get(env["STEPCA_URL"].rstrip("/") + "/crl", headers={"Accept": "application/x-pem-file"})
            r.raise_for_status()
        except httpx.HTTPError:
            raise HTTPException(502, "the CA did not return a revocation list") from None
        if "BEGIN X509 CRL" not in r.text:
            raise HTTPException(502, "the CA's answer is not a PEM revocation list")
        return r.text

    return create_app(registry=Registry(connect, audit), audit=audit, auth=from_env(env), ca=ca, crl_source=crl_source)


def main() -> None:
    """Serve forever."""
    import uvicorn
    uvicorn.run(build(), host=os.environ.get("BIND_HOST", "127.0.0.1"), port=int(os.environ.get("ENROLL_PORT", "8790")), log_level="info")


if __name__ == "__main__":
    main()
