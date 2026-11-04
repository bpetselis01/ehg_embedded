"""The certificate request the iPhone app builds in Swift must be accepted by the enrolment API's own validator (backend/enroll/csr.py): valid signature, P-256, the right common name, no CA flag.
Runs `swift test --filter EnrolmentTests/testTheCSRIsWrittenWhereThePythonValidatorCanCheckIt` with EHG_CSR_OUT set. Needs the Swift toolchain (command line); no broker, no network."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from cryptography.x509.oid import NameOID

from backend.enroll.csr import validate_csr

ROOT = Path(__file__).parents[1]


@pytest.mark.skipif(shutil.which("swift") is None or os.environ.get("EHG_INTEGRATION") != "1", reason="needs EHG_INTEGRATION=1 and the Swift toolchain")
def test_the_swift_csr_passes_the_enrolment_validator(tmp_path):
    out = tmp_path / "swift.csr"
    r = subprocess.run(["swift", "test", "--filter", "EnrolmentTests/testTheCSRIsWrittenWhereThePythonValidatorCanCheckIt"], cwd=ROOT / "ios/EHGWire", env={**os.environ, "EHG_CSR_OUT": str(out)},
                       capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, (r.stdout + r.stderr)[-1200:]
    csr = validate_csr(out.read_text())          # raises CsrError on a bad signature, a wrong curve, a CA request or wildcard names
    assert [a.value for a in csr.subject.get_attributes_for_oid(NameOID.COMMON_NAME)] == ["ios-test-1"]
    assert csr.is_signature_valid
