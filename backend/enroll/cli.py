"""Command line for the enrolment API: enrol this Mac's gateway, list gateways, revoke one.

  ENROLL_TOKEN=<access token> uv run python -m backend.enroll.cli enrol-mac --url https://NAME/enroll-api --id ehg-mac-01 --out ~/ehg-gw
  ENROLL_TOKEN=<admin token>  uv run python -m backend.enroll.cli list   --url https://NAME/triage
  ENROLL_TOKEN=<admin token>  uv run python -m backend.enroll.cli revoke ehg-mac-01 --url https://NAME/enroll-api

PURPOSE: The Mac (Python) gateway's way to get a short-lived client certificate without copying keys, and the administrator's way to revoke one (decision 0013).
INPUT: a Keycloak access token in the environment variable ENROLL_TOKEN (never a command-line argument: those show up in process lists and shell history), the API URL, and for enrol-mac the output directory.
  The token comes from a Keycloak login you do by hand (device or password-less flow); this tool does not log in for you.
OUTPUT: enrol-mac writes <out>/<id>.key (the PRIVATE key, mode 600, created on this computer and never sent anywhere), <out>/<id>.crt and <out>/ca-chain.crt, and prints the expiry. list prints the registry's gateways; revoke prints the API's answer.
  e.g. wrote ~/ehg-gw/ehg-mac-01.crt (expires 2026-11-10T09:12:00+00:00); use it with: gateway --mqtt-tls --cert ~/ehg-gw/ehg-mac-01.crt --key ~/ehg-gw/ehg-mac-01.key --ca ~/ehg-gw/ca-chain.crt
HOW: A fresh EC P-256 key is generated locally and only the certificate REQUEST is sent. The certificate lasts 7 days, so run enrol-mac again before it expires.
ROLE IN THESIS: Enrolment of the reference (Python) gateway; the iPhone does the same from the app (Unit 15).
BEFORE -> AFTER: Before: tools/pki.sh issued gateway certificates by hand. After: the same result through the API.
"""

import argparse
import os
from pathlib import Path

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID


def make_key_and_csr(gateway_id: str) -> tuple[bytes, str]:
    """(private key PEM, CSR PEM) for a new EC P-256 key. The subject is only a label: the server ignores it."""
    key = ec.generate_private_key(ec.SECP256R1())
    csr = x509.CertificateSigningRequestBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, gateway_id)])).sign(key, hashes.SHA256())
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    return pem, csr.public_bytes(serialization.Encoding.PEM).decode()


def write_private(path: Path, data: bytes) -> None:
    """Write a secret file that is never readable by others, not even briefly."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)


def main(argv=None, http: httpx.Client | None = None) -> int:
    """Run the command; returns the exit status."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["enrol-mac", "list", "revoke"])
    ap.add_argument("target", nargs="?", help="revoke: the gateway id")
    ap.add_argument("--url", required=True, help="base URL of the API")
    ap.add_argument("--id", help="enrol-mac: only a label; the server uses your login name")
    ap.add_argument("--out", default="ehg-gw", help="enrol-mac: output directory")
    a = ap.parse_args(argv)
    token = os.environ.get("ENROLL_TOKEN")
    if not token:
        ap.error("set ENROLL_TOKEN to a Keycloak access token (it is not accepted as an argument)")
    client = http or httpx.Client(timeout=15)
    headers = {"Authorization": f"Bearer {token}"}
    if a.command == "enrol-mac":
        out = Path(a.out).expanduser()
        out.mkdir(parents=True, exist_ok=True, mode=0o700)
        key_pem, csr_pem = make_key_and_csr(a.id or "gateway")
        r = client.post(a.url.rstrip("/") + "/enroll", json={"csr": csr_pem}, headers=headers)
        if r.status_code != 200:
            print(f"enrolment refused: HTTP {r.status_code} {r.text[:200]}")
            return 1
        j = r.json()
        write_private(out / f"{j['gateway_id']}.key", key_pem)
        (out / f"{j['gateway_id']}.crt").write_text(j["certificate"])
        (out / "ca-chain.crt").write_text("".join(c if c.endswith("\n") else c + "\n" for c in j["chain"]))
        print(f"wrote {out / (j['gateway_id'] + '.crt')} (expires {j['not_after']}); use it with: gateway --mqtt-tls --cert {out / (j['gateway_id'] + '.crt')} --key {out / (j['gateway_id'] + '.key')} --ca {out / 'ca-chain.crt'}")
        return 0
    if a.command == "list":
        r = client.get(a.url.rstrip("/") + "/api/registry/gateways", headers=headers)
        print(r.text if r.status_code != 200 else "\n".join(f"{g['id']:24s} {g['kind']:7s} serial {g['cert_serial']} revoked {g['revoked_at']}" for g in r.json()["gateways"]))
        return 0 if r.status_code == 200 else 1
    if not a.target:
        ap.error("revoke needs the gateway id")
    r = client.post(a.url.rstrip("/") + f"/revoke/{a.target}", headers=headers)
    print(r.text)
    return 0 if r.status_code == 200 else 1


if __name__ == "__main__":
    raise SystemExit(main())
