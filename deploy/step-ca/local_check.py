"""Calls a real step-ca the way the enrolment API does (see local-check.sh): sign, vet the answer, revoke, fetch and verify the CRL.

INPUT: argv[1] = folder with prov.pem (the provisioner's EC key), root_ca.crt, intermediate_ca.crt, fp (the root fingerprint). OUTPUT: lines "ok: ..." and exit 0, or "FAIL: ..." and exit 1.
"""

import subprocess
import sys
from pathlib import Path

import httpx
from cryptography import x509

sys.path.insert(0, str(Path(__file__).parents[2]))
from backend.enroll.cli import make_key_and_csr  # noqa: E402
from backend.enroll.csr import check_issued, validate_csr  # noqa: E402
from backend.enroll.stepca_client import StepCaClient  # noqa: E402

work = Path(sys.argv[1])
url = "https://localhost:9000"
ca = StepCaClient(url, "ehg-enroll", (work / "prov.pem").read_text(), (work / "fp").read_text().strip(), verify=str(work / "root_ca.crt"))
_, csr = make_key_and_csr("ehg-gw-check")
signed = ca.sign(csr, "ehg-gw-check")
cert = check_issued(signed["certificate"], validate_csr(csr), "ehg-gw-check")  # raises CsrError on a wrong name, usage or lifetime
print(f"ok: signed, CN=ehg-gw-check, client-auth only, valid until {cert.not_valid_after_utc:%Y-%m-%d}")
ca.revoke(str(cert.serial_number))
print("ok: revoked")
http = httpx.Client(verify=str(work / "root_ca.crt"))
pem = http.get(url + "/crl?pem=true").content
crl = x509.load_pem_x509_crl(pem)
if crl.get_revoked_certificate_by_serial_number(cert.serial_number) is None:
    sys.exit("FAIL: the revoked serial is not in the CRL")
chain = work / "ca-chain.crt"
chain.write_bytes((work / "intermediate_ca.crt").read_bytes() + (work / "root_ca.crt").read_bytes())
(work / "crl.pem").write_bytes(pem)
out = subprocess.run(["openssl", "crl", "-in", str(work / "crl.pem"), "-CAfile", str(chain), "-noout", "-verify"], capture_output=True, text=True)
if "verify OK" not in out.stdout + out.stderr:  # openssl exits 0 even when the check fails
    sys.exit("FAIL: openssl did not verify the CRL: " + out.stderr)
print(f"ok: the CRL lists the serial and verifies; next update {crl.next_update_utc:%Y-%m-%d %H:%M}")
