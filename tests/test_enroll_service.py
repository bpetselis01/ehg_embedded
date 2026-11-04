"""Checks backend/enroll/service.py: the enrolment API can be started from environment variables, refuses to start on anything missing or unsafe (naming the variables, never their values),
and its /crl never turns a CA failure into an empty list. No database or CA is contacted: the CA is a fake transport."""

import datetime

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient

from backend.enroll.service import REQUIRED, build

KEY = ec.generate_private_key(ec.SECP256R1()).private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption())
CRL = "-----BEGIN X509 CRL-----\nAAAA\n-----END X509 CRL-----\n"


@pytest.fixture
def env(tmp_path):
    key = tmp_path / "provisioner.key"
    key.write_bytes(KEY)
    key.chmod(0o600)
    root = tmp_path / "root.crt"
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test root")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(ec.generate_private_key(ec.SECP256R1()).public_key()).serial_number(1)
            .not_valid_before(now).not_valid_after(now + datetime.timedelta(days=1)).add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(ec.generate_private_key(ec.SECP256R1()), hashes.SHA256()))
    root.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return {"AUTH_MODE": "dev", "EHG_ENROLL_DSN": "host=db password=hunter2-secret", "STEPCA_URL": "https://step-ca:9000", "STEPCA_PROVISIONER": "ehg-enroll",
            "STEPCA_KEY_FILE": str(key), "STEPCA_ROOT_FP": "ab" * 32, "STEPCA_ROOT_CERT": str(root)}


def fake_ca(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_it_starts_from_a_complete_environment_without_touching_the_database_or_the_ca(env):
    def nothing_may_be_called(request):
        raise AssertionError("the CA must not be contacted while starting")
    app = build(env, http=fake_ca(nothing_may_be_called))
    assert TestClient(app).get("/healthz").status_code == 200


def test_every_missing_variable_is_named_at_once_and_values_never_appear(env):
    broken = {k: v for k, v in env.items() if k not in ("STEPCA_URL", "STEPCA_ROOT_FP", "EHG_ENROLL_DSN")}
    with pytest.raises(SystemExit) as e:
        build(broken)
    msg = str(e.value)
    assert all(name in msg for name in ("STEPCA_URL", "STEPCA_ROOT_FP", "EHG_ENROLL_DSN")) and "hunter2" not in msg
    assert set(REQUIRED) == {"EHG_ENROLL_DSN", "STEPCA_URL", "STEPCA_PROVISIONER", "STEPCA_KEY_FILE", "STEPCA_ROOT_FP", "STEPCA_ROOT_CERT"}


@pytest.mark.parametrize("fp", ["", "xyz", "ab" * 31, "AB" * 33, "g" * 64])
def test_a_malformed_fingerprint_stops_the_start(env, fp):
    env["STEPCA_ROOT_FP"] = fp
    with pytest.raises(SystemExit):
        build(env)


def test_a_missing_world_readable_or_wrong_key_file_stops_the_start(env, tmp_path):
    from pathlib import Path
    key = Path(env["STEPCA_KEY_FILE"])
    key.chmod(0o644)
    with pytest.raises(SystemExit, match="accessible to other users"):
        build(env)
    key.chmod(0o640)
    build(env)  # group-readable is allowed (a container user in the key's group)
    key.write_text("not a key")
    with pytest.raises(SystemExit, match="PEM private key"):
        build(env)
    env["STEPCA_KEY_FILE"] = str(tmp_path / "missing.key")
    with pytest.raises(SystemExit, match="existing files"):
        build(env)


def test_no_auth_mode_means_no_start(env):
    del env["AUTH_MODE"]
    with pytest.raises(SystemExit, match="AUTH_MODE"):
        build(env)


def test_crl_is_the_cas_list_and_a_ca_failure_is_a_502_not_an_empty_list(env):
    app = build(env, http=fake_ca(lambda r: httpx.Response(200, text=CRL) if (r.url.path, r.url.query) == ("/crl", b"pem=true") else httpx.Response(404)))
    r = TestClient(app).get("/crl")
    assert r.status_code == 200 and "BEGIN X509 CRL" in r.text
    for broken in (httpx.Response(500), httpx.Response(200, text=""), httpx.Response(200, text="<html>login</html>")):
        bad = build(env, http=fake_ca(lambda r, b=broken: b))
        got = TestClient(bad).get("/crl")
        assert got.status_code == 502 and "BEGIN" not in got.text, "an unusable answer must never be installed as 'no revocations'"
    def unreachable(request):
        raise httpx.ConnectError("boom https://step-ca:9000")
    got = TestClient(build(env, http=fake_ca(unreachable))).get("/crl")
    assert got.status_code == 502 and "step-ca" not in got.text
