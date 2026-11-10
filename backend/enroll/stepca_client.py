"""Client for Smallstep step-ca: sign a certificate request, revoke a certificate (decision 0013).

PURPOSE: The enrolment API asks the private CA to issue the gateway certificate; this module speaks step-ca's HTTP API with a short-lived one-time token (OTT) signed by the JWK provisioner key.
INPUT: CA URL, provisioner name, the provisioner's EC private key (kept on the VM, never in git), the root certificate's SHA-256 fingerprint, and (for sign) the PEM CSR and the subject.
  e.g. StepCaClient("https://step-ca:9000", "ehg-enroll", provisioner_key, root_fp).sign(csr_pem, "ehg-ios-01", valid_hours=168)
OUTPUT: sign() -> {"certificate": PEM, "chain": [PEM, ...]}; revoke(serial) -> None. Errors: CaError with the HTTP status and a short reason; secrets and tokens are never put in the message.
HOW: The OTT is an ES256 JWT: iss = provisioner, sub = the gateway id, aud = the CA URL + the endpoint path, sha = the root fingerprint, iat/nbf/exp (5 minutes), a random jti. step-ca checks it against the
  provisioner and signs with the leaf template that allows client-auth only (deploy/step-ca/templates/ehg-gateway.tpl).
STATUS: CHECKED against a real step-ca 0.30.2 on 2026-11-04 (sign, revoke, CRL; see docs/progress.md). Three things the fake CA could not tell: the token needs a `kid` header (the key's RFC 7638 thumbprint),
  the revoke token's subject must be the serial, and the leaf template must take the subject from the CSR. Tests use a fake CA for speed; the real run is a manual procedure in deploy/step-ca/README.md.
  Whether the pinned step-ca version offers CRL/revocation as needed, and whether its OIDC provisioner could consume Keycloak tokens directly (a simpler route), are still to be checked (decision 0013).
ROLE IN THESIS: The CA step of the certificate lifecycle.
BEFORE -> AFTER: Before: tools/pki.sh signed by hand. After: the API signs through the CA.
"""

import base64
import hashlib
import json
import secrets
import time

import httpx
import jwt
from cryptography.hazmat.primitives import serialization


def jwk_thumbprint(key_pem: str | bytes) -> str:
    """The RFC 7638 thumbprint of an EC private key's public half. step-ca names a JWK provisioner's key by this value (`kid`) and finds the provisioner by it."""
    return public_thumbprint(serialization.load_pem_private_key(key_pem.encode() if isinstance(key_pem, str) else key_pem, None).public_key())


def public_thumbprint(public_key) -> str:
    """RFC 7638 thumbprint of an EC public key."""
    jwk = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(public_key))
    canonical = json.dumps({"crv": jwk["crv"], "kty": jwk["kty"], "x": jwk["x"], "y": jwk["y"]}, separators=(",", ":"), sort_keys=True)
    return base64.urlsafe_b64encode(hashlib.sha256(canonical.encode()).digest()).rstrip(b"=").decode()


class CaError(Exception):
    """The CA refused, was unreachable, or answered something unusable."""


class StepCaClient:
    """`http` is an httpx.Client (tests pass one with a mock transport); `verify` is the root CA certificate file the CA's TLS certificate must chain to."""

    def __init__(self, ca_url: str, provisioner: str, key_pem: str | bytes, root_fingerprint: str, http: httpx.Client | None = None, verify=True, clock=time.time):
        self.url, self.provisioner, self._key, self.root_fp, self._clock = ca_url.rstrip("/"), provisioner, key_pem, root_fingerprint, clock
        self._kid = jwk_thumbprint(key_pem)
        self._http = http or httpx.Client(verify=verify, timeout=10)

    def one_time_token(self, subject: str, path: str, lifetime_s: int = 300) -> str:
        """A single-use token for one request to the CA."""
        now = int(self._clock())
        claims = {"iss": self.provisioner, "sub": subject, "aud": self.url + path, "sha": self.root_fp, "iat": now, "nbf": now, "exp": now + lifetime_s, "jti": secrets.token_hex(16)}
        return jwt.encode(claims, self._key, algorithm="ES256", headers={"kid": self._kid})  # without kid a real step-ca answers 401 "provisioner not found"

    def sign(self, csr_pem: str, subject: str, valid_hours: int = 168) -> dict:
        """Ask the CA to sign the request for `subject`; the lifetime is requested, and the caller re-checks what comes back."""
        body = {"csr": csr_pem, "ott": self.one_time_token(subject, "/1.0/sign"), "notAfter": f"{valid_hours}h"}
        data = self._post("/1.0/sign", body)
        cert = data.get("crt")
        if not isinstance(cert, str) or "BEGIN CERTIFICATE" not in cert:
            raise CaError("the CA's answer has no certificate")
        chain = data.get("certChain") or [data.get("ca")]
        return {"certificate": cert, "chain": [c for c in chain if isinstance(c, str)]}

    def revoke(self, serial: str, subject: str | None = None) -> None:
        """Revoke a certificate by serial number (decimal). step-ca requires the token's subject to BE that serial (a real CA answered 403 for the gateway id, 2026-11-04);
        `subject` (the gateway id) is accepted only so callers can keep passing it, and is not used."""
        self._post("/1.0/revoke", {"serial": serial, "ott": self.one_time_token(serial, "/1.0/revoke"), "reasonCode": 1, "reason": "revoked by an administrator", "passive": True})

    def _post(self, path: str, body: dict) -> dict:
        """POST to the CA; any network problem or non-2xx status becomes CaError."""
        try:
            r = self._http.post(self.url + path, json=body)
        except httpx.HTTPError as e:
            raise CaError(f"the CA is unreachable ({type(e).__name__})") from e
        if r.status_code // 100 != 2:
            raise CaError(f"the CA answered {r.status_code}")
        try:
            return r.json()
        except ValueError as e:
            raise CaError("the CA's answer is not JSON") from e
