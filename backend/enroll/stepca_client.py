"""Client for Smallstep step-ca: sign a certificate request, revoke a certificate (decision 0013).

PURPOSE: The enrolment API asks the private CA to issue the gateway certificate; this module speaks step-ca's HTTP API with a short-lived one-time token (OTT) signed by the JWK provisioner key.
INPUT: CA URL, provisioner name, the provisioner's EC private key (kept on the VM, never in git), the root certificate's SHA-256 fingerprint, and (for sign) the PEM CSR and the subject.
  e.g. StepCaClient("https://step-ca:9000", "ehg-enroll", provisioner_key, root_fp).sign(csr_pem, "ehg-ios-01", valid_hours=168)
OUTPUT: sign() -> {"certificate": PEM, "chain": [PEM, ...]}; revoke(serial) -> None. Errors: CaError with the HTTP status and a short reason; secrets and tokens are never put in the message.
HOW: The OTT is an ES256 JWT: iss = provisioner, sub = the gateway id, aud = the CA URL + the endpoint path, sha = the root fingerprint, iat/nbf/exp (5 minutes), a random jti. step-ca checks it against the
  provisioner and signs with the leaf template that allows client-auth only (deploy/step-ca/templates/ehg-gateway.tpl).
STATUS: UNVERIFIED against a real step-ca (none was available; Docker was not running). The request and token shapes follow step-ca's documented one-time-token flow; tests use a fake CA.
  Whether the pinned step-ca version offers CRL/revocation as needed, and whether its OIDC provisioner could consume Keycloak tokens directly (a simpler route), are still to be checked (decision 0013).
ROLE IN THESIS: The CA step of the certificate lifecycle.
BEFORE -> AFTER: Before: tools/pki.sh signed by hand. After: the API signs through the CA.
"""

import secrets
import time

import httpx
import jwt


class CaError(Exception):
    """The CA refused, was unreachable, or answered something unusable."""


class StepCaClient:
    """`http` is an httpx.Client (tests pass one with a mock transport); `verify` is the root CA certificate file the CA's TLS certificate must chain to."""

    def __init__(self, ca_url: str, provisioner: str, key_pem: str | bytes, root_fingerprint: str, http: httpx.Client | None = None, verify=True, clock=time.time):
        self.url, self.provisioner, self._key, self.root_fp, self._clock = ca_url.rstrip("/"), provisioner, key_pem, root_fingerprint, clock
        self._http = http or httpx.Client(verify=verify, timeout=10)

    def one_time_token(self, subject: str, path: str, lifetime_s: int = 300) -> str:
        """A single-use token for one request to the CA."""
        now = int(self._clock())
        claims = {"iss": self.provisioner, "sub": subject, "aud": self.url + path, "sha": self.root_fp, "iat": now, "nbf": now, "exp": now + lifetime_s, "jti": secrets.token_hex(16)}
        return jwt.encode(claims, self._key, algorithm="ES256")

    def sign(self, csr_pem: str, subject: str, valid_hours: int = 168) -> dict:
        """Ask the CA to sign the request for `subject`; the lifetime is requested, and the caller re-checks what comes back."""
        body = {"csr": csr_pem, "ott": self.one_time_token(subject, "/1.0/sign"), "notAfter": f"{valid_hours}h"}
        data = self._post("/1.0/sign", body)
        cert = data.get("crt")
        if not isinstance(cert, str) or "BEGIN CERTIFICATE" not in cert:
            raise CaError("the CA's answer has no certificate")
        chain = data.get("certChain") or [data.get("ca")]
        return {"certificate": cert, "chain": [c for c in chain if isinstance(c, str)]}

    def revoke(self, serial: str, subject: str) -> None:
        """Revoke a certificate by serial number (decimal)."""
        self._post("/1.0/revoke", {"serial": serial, "ott": self.one_time_token(subject, "/1.0/revoke"), "reasonCode": 1, "reason": "revoked by an administrator", "passive": True})

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
