"""The enrolment API (FastAPI): a gateway proves who it is with a Keycloak token, sends a certificate request, and gets back a short-lived client certificate (decision 0013).

PURPOSE: Turn "this phone has a valid login" into "this phone holds a 7-day MQTT client certificate", without anyone copying keys around, and make revocation possible.
INPUT: POST /enroll {"csr": "<PEM>", "app_version": "1.0"} with `Authorization: Bearer <token>`. The caller needs the `gateway` role (a phone, device grant) or `admin` (the Mac CLI). Other routes:
  POST /revoke/{gateway_id} (admin), GET /crl (no login: a revocation list is public), GET /healthz.
  e.g. POST /enroll {"csr": "-----BEGIN CERTIFICATE REQUEST-----..."} -> 200 {"gateway_id": "ehg-ios-01", "certificate": "-----BEGIN CERTIFICATE-----...", "chain": [...], "not_after": "2026-11-10T..."}
OUTPUT: errors with names: 401 no/invalid token, 403 wrong role or a REVOKED gateway, 422 unacceptable request (reason given), 429 too many requests (5 per hour per identity), 502 the CA failed or returned a wrong certificate.
HOW: The gateway id (the certificate CN) is the token's user name, validated against the id pattern; the CN in the CSR is ignored. Order: rate limit -> role -> registry (revoked?) -> CSR checks -> CA signs ->
  the answer is re-checked (backend/enroll/csr.py) -> only then the registry row is written. If any step fails nothing is recorded as enrolled. Every outcome is an audit row (enroll.issue / enroll.denied).
  Revoking marks the registry, revokes EVERY live certificate of the gateway at the CA (all serials are kept, not only the newest), and reports whether the broker's CRL needs refreshing: the broker reads its CRL on start-up and SIGHUP (docs/security.md), so a host-side job
  that fetches GET /crl into the broker's ca.crl and signals Mosquitto is part of the deployment (not built here).
ROLE IN THESIS: The enrolment API of the certificate lifecycle (docs/security.md, decision 0013). UNVERIFIED against a real step-ca or Keycloak.
BEFORE -> AFTER: Before: certificates by hand. After: an audited, rate-limited, revocable flow.
"""

import re
import time
from collections import defaultdict, deque
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict

from backend.enroll.csr import CsrError, check_issued, validate_csr
from backend.enroll.stepca_client import CaError
from backend.jwtauth import AuthError
from backend.triage.registry import RevokedGateway, UnknownGateway

ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
RESERVED_PREFIX = "ehg-"  # every provisioned identity (tools/pki.sh: ehg-gw-01, ehg-bridge, ehg-ingest, ehg-viewer, ...) lives in this namespace; a login can never enrol into it


class EnrollBody(BaseModel):
    """Request body."""

    model_config = ConfigDict(extra="forbid")
    csr: str
    app_version: str | None = None


def create_app(*, registry, audit, auth, ca, crl_source=lambda: "", limit=(5, 3600), clock=time.time) -> FastAPI:
    """Build the API around its collaborators (all injectable)."""
    app = FastAPI(title="EHG enrolment API", version="0.1.0")
    hits: dict[str, deque] = defaultdict(deque)

    def principal(authorization: str | None = Header(default=None)):
        """Authenticate; role checks happen in the handlers because they depend on what is asked."""
        try:
            return auth.authenticate(authorization)
        except AuthError:
            raise HTTPException(401, "authentication required", headers={"WWW-Authenticate": "Bearer"}) from None

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        """Errors as {"error": ...}."""
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code, headers=getattr(exc, "headers", None))

    def deny(p, reason: str, status: int, target: str = "enroll"):
        """Audit and refuse."""
        audit.append(p.user, p.role or "none", "enroll.denied", target, {"reason": reason})
        raise HTTPException(status, reason)

    @app.post("/enroll")
    def enroll(body: EnrollBody, p=Depends(principal)):
        """Issue a certificate for the caller's own gateway id."""
        window = hits[p.user]
        now = clock()
        while window and window[0] < now - limit[1]:
            window.popleft()
        if len(window) >= limit[0]:
            deny(p, f"too many requests: {limit[0]} per {limit[1] // 3600} hour(s)", 429)
        window.append(now)
        if "gateway" not in p.roles and not p.at_least("admin"):
            deny(p, "this login may not enrol a gateway", 403)
        gid = p.user
        if not ID_RE.fullmatch(gid) or gid.lower().startswith(RESERVED_PREFIX):
            deny(p, "this user name cannot be used as a gateway id", 403)
        try:
            existing = registry.gateway(gid)
            if existing["revoked_at"] is not None:
                deny(p, "this gateway has been revoked", 403, f"gateway:{gid}")
        except UnknownGateway:
            pass
        try:
            csr = validate_csr(body.csr)
        except CsrError as e:
            deny(p, str(e), 422, f"gateway:{gid}")
        try:
            issued = ca.sign(body.csr, gid, valid_hours=168)
            cert = check_issued(issued["certificate"], csr, gid, datetime.fromtimestamp(now, tz=timezone.utc))
        except (CaError, CsrError) as e:
            audit.append(p.user, p.role or "none", "enroll.denied", f"gateway:{gid}", {"reason": "the CA failed or returned an unacceptable certificate", "detail": str(e)})
            raise HTTPException(502, "the certificate authority did not produce an acceptable certificate") from None
        try:
            registry.enrol_gateway(gid, "ios" if "gateway" in p.roles else "python", str(cert.serial_number), p.user, p.role or "none", body.app_version, not_after=cert.not_valid_after_utc)
        except RevokedGateway:
            deny(p, "this gateway has been revoked", 403, f"gateway:{gid}")
        audit.append(p.user, p.role or "none", "enroll.issue", f"gateway:{gid}", {"serial": str(cert.serial_number), "not_after": cert.not_valid_after_utc.isoformat()})
        return {"gateway_id": gid, "certificate": issued["certificate"], "chain": issued["chain"], "not_after": cert.not_valid_after_utc.isoformat()}

    @app.post("/revoke/{gateway_id}")
    def revoke(gateway_id: str, p=Depends(principal)):
        """Admin: revoke a gateway in the registry and at the CA."""
        if not p.at_least("admin"):
            raise HTTPException(403, "needs the admin role")
        serials = registry.active_serials(gateway_id, datetime.fromtimestamp(clock(), tz=timezone.utc))  # every live certificate, not only the newest
        g = registry.revoke_gateway(gateway_id, p.user, p.role)  # UnknownGateway -> 404 below
        failed = []
        for serial in serials:
            try:
                ca.revoke(serial, gateway_id)
                registry.mark_ca_revoked(serial)  # only a CONFIRMED revocation leaves the retry list
            except CaError as e:
                failed.append(f"{serial} ({e})")
        ca_result = ("no live certificate waiting to be revoked at the CA" if not serials else f"revoked {len(serials)} certificate(s) at the CA" if not failed
                     else f"NOT revoked at the CA: {'; '.join(failed)}; the registry will still refuse a new enrolment, but the broker's CRL does not yet list them. Repeat the revocation once the CA is back: it retries exactly these")
        return {"gateway": gateway_id, "ca": ca_result, "crl": "fetch GET /crl and signal the broker (it reads the CRL on start-up and SIGHUP)"}

    @app.exception_handler(UnknownGateway)
    async def unknown(request, exc):
        """404."""
        return JSONResponse({"error": f"unknown gateway: {exc}"}, status_code=404)

    @app.get("/crl")
    def crl():
        """The current revocation list (PEM). Public by design."""
        return PlainTextResponse(crl_source(), media_type="application/x-pem-file")

    @app.get("/healthz")
    def healthz():
        """The process is up."""
        return {"status": "ok"}

    return app
