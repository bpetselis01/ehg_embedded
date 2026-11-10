"""Certificate-request (CSR) validation and the re-check of what the CA issued (decision 0013).

PURPOSE: The enrolment API signs a gateway's CSR only if it is exactly the kind of request we allow, and it re-checks the certificate that comes back, so a misconfigured CA cannot hand out a wrong one.
INPUT: a PEM CSR (text) from a phone or the CLI; later the PEM certificate the CA returned.
  e.g. -----BEGIN CERTIFICATE REQUEST----- MIH... (an EC P-256 request made on the device; its subject name is ignored)
OUTPUT: validate_csr(pem) -> the parsed request or CsrError(reason). check_issued(cert_pem, csr, gateway_id, now) -> the parsed certificate or CsrError.
HOW: A request is accepted only if: at most 4 KiB; the self-signature is valid (proof the requester holds the private key); the key is EC on the curve secp256r1 (nothing else); it asks for no CA certificate;
  none of its names (SANs) contains a wildcard. The requested subject/CN is never used: the CN comes from the registry. The issued certificate must have CN = the gateway id, ONLY the client-authentication
  extended key usage, CA:FALSE, the requester's own public key, and a lifetime of at most MAX_DAYS (7) plus an hour of clock slack.
ROLE IN THESIS: The CSR gate of the certificate lifecycle (docs/security.md); the CA itself (step-ca) is not trusted to be configured correctly.
BEFORE -> AFTER: Before: certificates were made by hand with tools/pki.sh. After: a phone asks for one, and every request and answer is checked.
"""

from datetime import datetime, timedelta, timezone

from cryptography import x509
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

MAX_PEM = 4096
MAX_DAYS = 7


class CsrError(ValueError):
    """The request, or the certificate returned for it, is not acceptable (the message says why, in words safe to show the caller)."""


def validate_csr(pem) -> x509.CertificateSigningRequest:
    """Parse and vet a CSR; raises CsrError for anything outside the allowed shape."""
    data = pem.encode() if isinstance(pem, str) else pem
    if not data or len(data) > MAX_PEM:
        raise CsrError(f"the request must be between 1 and {MAX_PEM} bytes")
    try:
        csr = x509.load_pem_x509_csr(data)
    except ValueError as e:
        raise CsrError("not a PEM certificate request") from e
    try:  # a corrupted key, signature or extension raises ValueError/UnsupportedAlgorithm when it is first used, not when it is parsed
        if not csr.is_signature_valid:
            raise CsrError("the request's signature is invalid (the sender does not hold the private key)")
        key = csr.public_key()
        if not isinstance(key, ec.EllipticCurvePublicKey) or key.curve.name != "secp256r1":
            raise CsrError("the key must be an elliptic-curve key on P-256 (secp256r1)")
        for ext in csr.extensions:
            if isinstance(ext.value, x509.BasicConstraints) and ext.value.ca:
                raise CsrError("a request for a CA certificate is refused")
            if isinstance(ext.value, x509.SubjectAlternativeName) and any("*" in str(n.value) for n in ext.value):
                raise CsrError("wildcard names are refused")
    except (ValueError, UnsupportedAlgorithm) as e:
        if isinstance(e, CsrError):
            raise
        raise CsrError("the request is malformed (unusable key, signature or extension)") from e
    return csr


def check_issued(cert_pem, csr: x509.CertificateSigningRequest, gateway_id: str, now: datetime | None = None) -> x509.Certificate:
    """Verify the CA's answer: right name, client-auth only, not a CA, the requester's key, a short life. Raises CsrError."""
    now = now or datetime.now(timezone.utc)
    try:
        cert = x509.load_pem_x509_certificate(cert_pem.encode() if isinstance(cert_pem, str) else cert_pem)
    except ValueError as e:
        raise CsrError("the CA returned something that is not a PEM certificate") from e
    cns = [a.value for a in cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)]
    if cns != [gateway_id]:
        raise CsrError(f"the certificate's name is {cns}, expected [{gateway_id!r}]")
    try:
        eku = list(cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value)
    except x509.ExtensionNotFound:
        eku = []
    if eku != [ExtendedKeyUsageOID.CLIENT_AUTH]:
        raise CsrError("the certificate must carry the client-authentication usage and nothing else")
    try:
        if cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca:
            raise CsrError("the certificate is a CA certificate")
    except x509.ExtensionNotFound:
        pass
    if cert.public_key().public_numbers() != csr.public_key().public_numbers():
        raise CsrError("the certificate does not contain the requester's public key")
    life = cert.not_valid_after_utc - cert.not_valid_before_utc
    if life > timedelta(days=MAX_DAYS, hours=1) or cert.not_valid_after_utc < now:
        raise CsrError(f"the certificate's lifetime ({life}) exceeds {MAX_DAYS} days or it is already expired")
    return cert
