"""Checks the certificate enrolment flow (decision 0013) with a fake CA that issues real certificates: CSR vetting (EC P-256 only, valid signature, no wildcard names, no CA request), the caller cannot choose the
CN, revoked and system identities are refused, a CA that misbehaves is caught, rate limiting, revocation, the public CRL, the step-ca client's token and the CLI. Registry and audit use a real PostgreSQL."""

import time
from datetime import datetime, timedelta, timezone

import httpx
import jwt
import psycopg
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from fastapi.testclient import TestClient

from backend.db import migrate
from backend.enroll import cli
from backend.enroll.app import create_app
from backend.enroll.csr import CsrError, check_issued, validate_csr
from backend.enroll.stepca_client import CaError, StepCaClient
from backend.jwtauth import Authenticator, JwtVerifier
from backend.triage.audit import AuditLog
from backend.triage.registry import Registry

ISS, AUD = "https://id.example.org/realms/ehg", "enroll-api"
SIGN = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def bearer(user, *roles, exp=300):
    return {"Authorization": "Bearer " + jwt.encode({"iss": ISS, "aud": AUD, "sub": "s-" + user, "preferred_username": user, "exp": int(time.time()) + exp, "realm_access": {"roles": list(roles)}}, SIGN, algorithm="RS256")}


def make_csr(curve=ec.SECP256R1(), cn="whatever", san=None, ca=False, key=None):
    key = key or ec.generate_private_key(curve)
    b = x509.CertificateSigningRequestBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)]))
    if san:
        b = b.add_extension(x509.SubjectAlternativeName([x509.DNSName(san)]), critical=False)
    if ca:
        b = b.add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
    return key, b.sign(key, hashes.SHA256()).public_bytes(serialization.Encoding.PEM).decode()


CA_KEY = ec.generate_private_key(ec.SECP256R1())
CA_NAME = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test intermediate")])


class FakeCA:
    """Signs real certificates; `mode` makes it misbehave in one specific way."""

    def __init__(self, mode=None):
        self.mode, self.signed, self.revoked = mode, [], []

    def sign(self, csr_pem, subject, valid_hours=168):
        self.signed.append(subject)
        if self.mode == "fail":
            raise CaError("the CA answered 500")
        if self.mode == "garbage":
            return {"certificate": "not a certificate", "chain": []}
        csr = x509.load_pem_x509_csr(csr_pem.encode())
        pub = ec.generate_private_key(ec.SECP256R1()).public_key() if self.mode == "other_key" else csr.public_key()
        now = datetime.now(timezone.utc)
        eku = ExtendedKeyUsageOID.SERVER_AUTH if self.mode == "server_eku" else ExtendedKeyUsageOID.CLIENT_AUTH
        b = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "someone-else" if self.mode == "wrong_cn" else subject)]))
             .issuer_name(CA_NAME).public_key(pub).serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=1))
             .not_valid_after(now + (timedelta(days=90) if self.mode == "long" else timedelta(hours=valid_hours)))
             .add_extension(x509.ExtendedKeyUsage([eku]), critical=False).add_extension(x509.BasicConstraints(ca=self.mode == "ca_true", path_length=None), critical=True))
        cert = b.sign(CA_KEY, hashes.SHA256()).public_bytes(serialization.Encoding.PEM).decode()
        return {"certificate": cert, "chain": ["-----BEGIN CERTIFICATE-----\nROOTCHAIN\n-----END CERTIFICATE-----\n"]}

    def revoke(self, serial, subject):
        if self.mode == "revoke_fails":
            raise CaError("the CA is unreachable (ConnectError)")
        self.revoked.append((serial, subject))


@pytest.fixture
def env(pg_url):
    migrate.main(["upgrade", "0002", "--url", pg_url])
    dsn = pg_url.replace("+psycopg", "")
    connect = lambda: psycopg.connect(dsn)  # noqa: E731
    audit = AuditLog(connect, emit=lambda *_: None)
    reg, ca, clock = Registry(connect, audit), FakeCA(), [time.time()]
    env = type("Env", (), {})()
    env.audit, env.registry, env.ca, env.clock = audit, reg, ca, clock
    env.app = lambda **kw: TestClient(create_app(registry=reg, audit=audit, auth=Authenticator("oidc", JwtVerifier(ISS, AUD, lambda t: SIGN.public_key())), ca=ca, clock=lambda: clock[0], **kw))
    env.client = env.app(crl_source=lambda: "-----BEGIN X509 CRL-----\nTEST\n-----END X509 CRL-----\n")
    return env


# ---------------------------------------------------------------- CSR vetting

def test_a_p256_request_is_accepted():
    assert validate_csr(make_csr()[1]).is_signature_valid


@pytest.mark.parametrize("what", ["rsa", "p384", "garbage", "empty", "oversize", "wildcard", "ca", "tampered"])
def test_unacceptable_requests_are_refused_with_a_reason(what):
    if what == "rsa":
        pem = make_csr(key=rsa.generate_private_key(public_exponent=65537, key_size=2048))[1]
    elif what == "p384":
        pem = make_csr(ec.SECP384R1())[1]
    elif what == "garbage":
        pem = "-----BEGIN CERTIFICATE REQUEST-----\nAAAA\n-----END CERTIFICATE REQUEST-----"
    elif what == "empty":
        pem = ""
    elif what == "oversize":
        pem = make_csr()[1] + "A" * 5000
    elif what == "wildcard":
        pem = make_csr(san="*.example.org")[1]
    elif what == "ca":
        pem = make_csr(ca=True)[1]
    else:
        good = make_csr()[1]
        lines = good.splitlines()
        body = list(lines[3])
        body[5] = "A" if body[5] != "A" else "B"
        lines[3] = "".join(body)
        pem = "\n".join(lines)
    with pytest.raises(CsrError):
        validate_csr(pem)


def test_a_request_with_an_ordinary_name_is_fine_its_name_is_just_ignored_later():
    assert validate_csr(make_csr(san="gateway.example.org")[1])


@pytest.mark.parametrize("mode, fragment", [("wrong_cn", "name is"), ("server_eku", "client-authentication"), ("long", "lifetime"), ("other_key", "public key"), ("ca_true", "CA certificate"), ("garbage", "not a PEM")])
def test_check_issued_catches_every_way_a_ca_can_get_it_wrong(mode, fragment):
    key, pem = make_csr()
    csr = validate_csr(pem)
    out = FakeCA(mode).sign(pem, "ios-phone-1")["certificate"]
    with pytest.raises(CsrError, match=fragment):
        check_issued(out, csr, "ios-phone-1")


def test_check_issued_accepts_a_correct_certificate():
    key, pem = make_csr()
    cert = check_issued(FakeCA().sign(pem, "ios-phone-1")["certificate"], validate_csr(pem), "ios-phone-1")
    assert cert.not_valid_after_utc - cert.not_valid_before_utc <= timedelta(days=7, hours=1)


# ---------------------------------------------------------------- the API

def enrol(env, user="ios-phone-1", roles=("gateway",), csr=None, **kw):
    return env.client.post("/enroll", json={"csr": csr or make_csr()[1], **kw}, headers=bearer(user, *roles))


def actions(env):
    return [r["action"] for r in reversed(env.audit.tail(50))]


def test_a_phone_gets_a_certificate_recorded_in_the_registry_and_the_audit_log(env):
    r = enrol(env, app_version="1.0")
    assert r.status_code == 200
    j = r.json()
    cert = x509.load_pem_x509_certificate(j["certificate"].encode())
    assert j["gateway_id"] == "ios-phone-1" and j["chain"]
    g = env.registry.gateway("ios-phone-1")
    assert g["kind"] == "ios" and g["cert_serial"] == str(cert.serial_number) and g["app_version"] == "1.0" and g["revoked_at"] is None
    assert "enroll.issue" in actions(env) and env.audit.verify().ok


def test_the_caller_cannot_choose_the_name_on_the_certificate(env):
    _, pem = make_csr(cn="ehg-gw-01")  # asks to be the provisioned gateway identity
    j = enrol(env, user="ios-phone-1", csr=pem).json()
    cert = x509.load_pem_x509_certificate(j["certificate"].encode())
    assert cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == "ios-phone-1" and env.ca.signed == ["ios-phone-1"]


@pytest.mark.parametrize("user", ["ehg-gw-01", "ehg-bridge", "ehg-ingest", "ehg-viewer", "has space", "x" * 70, ".dotstart"])
def test_system_identities_and_unsafe_names_cannot_be_claimed_by_a_login(env, user):
    r = enrol(env, user=user)
    assert r.status_code == 403 and env.ca.signed == [] and env.registry.gateways() == []


def test_authentication_and_roles(env):
    assert env.client.post("/enroll", json={"csr": make_csr()[1]}).status_code == 401
    assert enrol(env, roles=("viewer",)).status_code == 403 and enrol(env, roles=("operator",)).status_code == 403, "staff roles alone are not gateways"
    r = enrol(env, user="mac-byron", roles=("admin",))
    assert r.status_code == 200 and env.registry.gateway("mac-byron")["kind"] == "python"
    assert enrol(env, roles=("gateway",), user="ios-2").status_code == 200
    assert env.client.post("/enroll", json={"csr": make_csr()[1]}, headers=bearer("x", "gateway", exp=-10)).status_code == 401


def test_a_bad_request_is_a_422_that_says_why_and_is_audited(env):
    r = enrol(env, csr=make_csr(ec.SECP384R1())[1])
    assert r.status_code == 422 and "P-256" in r.json()["error"] and env.ca.signed == [] and "enroll.denied" in actions(env)
    assert env.client.post("/enroll", json={"csr": "x", "extra": 1}, headers=bearer("a", "gateway")).status_code == 422, "unknown fields are refused"


@pytest.mark.parametrize("mode", ["fail", "garbage", "wrong_cn", "server_eku", "long", "other_key", "ca_true"])
def test_a_failing_or_misbehaving_ca_means_502_and_nothing_is_recorded_as_enrolled(env, mode):
    env.ca.mode = mode
    r = enrol(env)
    assert r.status_code == 502 and "certificate" not in r.json() and env.registry.gateways() == []
    assert "enroll.denied" in actions(env) and "enroll.issue" not in actions(env)


def test_a_revoked_gateway_cannot_enrol_again_and_the_ca_is_not_even_asked(env):
    assert enrol(env).status_code == 200
    assert env.client.post("/revoke/ios-phone-1", headers=bearer("root", "admin")).status_code == 200
    env.ca.signed.clear()
    r = enrol(env)
    assert r.status_code == 403 and "revoked" in r.json()["error"] and env.ca.signed == []


def test_rate_limit_is_five_per_hour_per_identity_and_recovers(env):
    codes = [enrol(env).status_code for _ in range(7)]
    assert codes == [200] * 5 + [429] * 2
    assert enrol(env, user="ios-other").status_code == 200, "another identity is not affected"
    env.clock[0] += 3601
    assert enrol(env).status_code == 200


def test_revocation_is_admin_only_reaches_the_ca_and_is_audited(env):
    serial = str(x509.load_pem_x509_certificate(enrol(env).json()["certificate"].encode()).serial_number)
    assert env.client.post("/revoke/ios-phone-1", headers=bearer("op", "operator")).status_code == 403
    r = env.client.post("/revoke/ios-phone-1", headers=bearer("root", "admin"))
    assert r.status_code == 200 and env.ca.revoked == [(serial, "ios-phone-1")] and env.registry.gateway("ios-phone-1")["revoked_at"] is not None and "registry.gateway_revoke" in actions(env)
    assert env.client.post("/revoke/nobody", headers=bearer("root", "admin")).status_code == 404


def test_a_failed_revocation_at_the_ca_is_reported_not_hidden(env):
    enrol(env)
    env.ca.mode = "revoke_fails"
    r = env.client.post("/revoke/ios-phone-1", headers=bearer("root", "admin"))
    assert r.status_code == 200 and "NOT revoked at the CA" in r.json()["ca"] and env.registry.gateway("ios-phone-1")["revoked_at"] is not None


def test_the_revocation_list_is_public_and_health_is_open(env):
    r = env.client.get("/crl")
    assert r.status_code == 200 and "BEGIN X509 CRL" in r.text and env.client.get("/healthz").json() == {"status": "ok"}


# ---------------------------------------------------------------- the step-ca client

CA_PROV = ec.generate_private_key(ec.SECP256R1())
PROV_PEM = CA_PROV.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())


def client_with(handler):
    return StepCaClient("https://step-ca:9000", "ehg-enroll", PROV_PEM, "ab" * 32, http=httpx.Client(transport=httpx.MockTransport(handler)))


def test_the_one_time_token_has_the_claims_step_ca_expects_and_is_single_use():
    ca = client_with(lambda r: httpx.Response(200, json={}))
    t1, t2 = ca.one_time_token("ios-phone-1", "/1.0/sign"), ca.one_time_token("ios-phone-1", "/1.0/sign")
    c = jwt.decode(t1, CA_PROV.public_key(), algorithms=["ES256"], audience="https://step-ca:9000/1.0/sign")
    assert (c["iss"], c["sub"], c["sha"]) == ("ehg-enroll", "ios-phone-1", "ab" * 32) and c["exp"] - c["iat"] == 300
    assert c["jti"] != jwt.decode(t2, CA_PROV.public_key(), algorithms=["ES256"], audience="https://step-ca:9000/1.0/sign")["jti"]


def test_the_token_names_the_provisioner_key_and_revocation_uses_the_serial_as_subject():
    """Both learnt from a real step-ca 0.30.2: no `kid` header means 401 "provisioner not found"; a revoke token whose subject is not the serial means 403."""
    from backend.enroll.stepca_client import jwk_thumbprint
    pem = CA_PROV.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption())
    assert jwt.get_unverified_header(client_with(lambda r: httpx.Response(200, json={})).one_time_token("x", "/1.0/sign"))["kid"] == jwk_thumbprint(pem)
    seen = {}

    def handler(request):
        seen.update(__import__("json").loads(request.content))
        return httpx.Response(200, json={})
    client_with(handler).revoke("12345", "ios-phone-1")
    claims = jwt.decode(seen["ott"], CA_PROV.public_key(), algorithms=["ES256"], audience="https://step-ca:9000/1.0/revoke")
    assert claims["sub"] == "12345" and seen["serial"] == "12345"


def test_thumbprint_matches_the_kid_step_ca_gave_a_key():
    """Known answer: `step crypto jwk create` (step-ca 0.30.2, 2026-11-04) wrote this public key and the kid below; the RFC 7638 value must be identical."""
    from backend.enroll.stepca_client import public_thumbprint

    def n(b64):
        return int.from_bytes(__import__("base64").urlsafe_b64decode(b64 + "=" * (-len(b64) % 4)), "big")
    key = ec.EllipticCurvePublicNumbers(n("NIkHclt9iIdzzOmv66LbfdtVFEM0W-yuOkN6_KNRlp0"), n("87ugjp-YEjN6bzJL82Rwca_qTXMaTJCfaH_G-22JrdQ"), ec.SECP256R1()).public_key()
    assert public_thumbprint(key) == "jxglkFdXRUGlNKBLWwMLo3rz-RwNSFW0R_J_Au07yPE"


def test_sign_posts_the_csr_and_token_and_returns_the_chain():
    seen = {}

    def handler(request):
        seen.update(path=request.url.path, body=__import__("json").loads(request.content))
        return httpx.Response(200, json={"crt": "-----BEGIN CERTIFICATE-----\nX\n-----END CERTIFICATE-----", "ca": "CA", "certChain": ["LEAF", "INT"]})
    out = client_with(handler).sign("CSRPEM", "ios-phone-1", valid_hours=168)
    assert seen["path"] == "/1.0/sign" and seen["body"]["csr"] == "CSRPEM" and seen["body"]["notAfter"] == "168h" and seen["body"]["ott"].count(".") == 2
    assert out["chain"] == ["LEAF", "INT"]


@pytest.mark.parametrize("resp", [httpx.Response(500), httpx.Response(401, json={}), httpx.Response(200, text="<html>"), httpx.Response(200, json={"crt": "nope"})])
def test_ca_failures_become_caerror_without_leaking_the_token(resp):
    with pytest.raises(CaError) as e:
        client_with(lambda r: resp).sign("CSR", "ios-phone-1")
    assert "eyJ" not in str(e.value)


def test_an_unreachable_ca_becomes_caerror():
    def boom(request):
        raise httpx.ConnectError("no route")
    with pytest.raises(CaError, match="unreachable"):
        client_with(boom).sign("CSR", "x")


# ---------------------------------------------------------------- the CLI

def test_cli_makes_a_p256_key_and_a_valid_request():
    key_pem, csr_pem = cli.make_key_and_csr("mac-1")
    assert isinstance(serialization.load_pem_private_key(key_pem, None), ec.EllipticCurvePrivateKey) and validate_csr(csr_pem)


def test_enrol_mac_writes_the_certificate_and_a_private_key_that_only_you_can_read(tmp_path, monkeypatch):
    monkeypatch.setenv("ENROLL_TOKEN", "t")
    ca = FakeCA()

    def handler(request):
        import json
        j = ca.sign(json.loads(request.content)["csr"], "mac-1")
        return httpx.Response(200, json={"gateway_id": "mac-1", "certificate": j["certificate"], "chain": j["chain"], "not_after": "2026-11-10T00:00:00+00:00"})
    rc = cli.main(["enrol-mac", "--url", "https://x/enroll-api", "--out", str(tmp_path / "gw")], http=httpx.Client(transport=httpx.MockTransport(handler)))
    key = tmp_path / "gw/mac-1.key"
    assert rc == 0 and (tmp_path / "gw/mac-1.crt").read_text().startswith("-----BEGIN CERTIFICATE") and (tmp_path / "gw/ca-chain.crt").exists()
    assert key.stat().st_mode & 0o777 == 0o600 and (tmp_path / "gw").stat().st_mode & 0o777 == 0o700


def test_enrol_mac_refuses_politely_and_the_token_is_never_an_argument(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ENROLL_TOKEN", "t")
    rc = cli.main(["enrol-mac", "--url", "https://x", "--out", str(tmp_path)], http=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(403, json={"error": "this gateway has been revoked"}))))
    assert rc == 1 and "revoked" in capsys.readouterr().out and not list(tmp_path.glob("*.key"))
    monkeypatch.delenv("ENROLL_TOKEN")
    with pytest.raises(SystemExit):
        cli.main(["list", "--url", "https://x"])
    with pytest.raises(SystemExit):
        cli.main(["list", "--url", "https://x", "--token", "abc"])


# ---------------------------------------------------------------- automated security review, 2026-11-03

def test_revoking_reaches_every_unexpired_certificate_not_only_the_latest(env):
    serials = [str(x509.load_pem_x509_certificate(enrol(env).json()["certificate"].encode()).serial_number) for _ in range(3)]
    assert len(set(serials)) == 3 and env.registry.gateway("ios-phone-1")["cert_serial"] == serials[-1]
    with env.registry._connect() as c:  # one of the three has already expired: no need to send it to the CA
        c.execute("UPDATE registry.gateway_cert SET not_after = now() - interval '1 hour' WHERE serial = %s", (serials[0],))
    r = env.client.post("/revoke/ios-phone-1", headers=bearer("root", "admin"))
    assert r.status_code == 200 and sorted(s for s, _ in env.ca.revoked) == sorted(serials[1:]), "the two live certificates are revoked at the CA"
    with env.registry._connect() as c:
        assert c.execute("SELECT count(*) FROM registry.gateway_cert WHERE revoked_at IS NULL AND gateway_id = 'ios-phone-1'").fetchone()[0] == 1, "only the already-expired one is left unmarked"
    assert enrol(env).status_code == 403


def test_a_partial_ca_failure_names_which_certificates_were_not_revoked(env):
    [enrol(env) for _ in range(2)]
    original = env.ca.revoke
    calls = []

    def flaky(serial, subject):
        calls.append(serial)
        if len(calls) == 1:
            raise CaError("the CA is unreachable (ConnectError)")
        return original(serial, subject)
    env.ca.revoke = flaky
    j = env.client.post("/revoke/ios-phone-1", headers=bearer("root", "admin")).json()
    assert "NOT revoked" in j["ca"] and calls[0] in j["ca"] and len(calls) == 2, "one failure must not stop the other revocation, and the failed serial is named"


@pytest.mark.parametrize("user", ["ehg-gw-02", "EHG-GW-01", "Ehg-x", "ehg-anything-at-all", "ehg-"])
def test_the_whole_ehg_namespace_is_reserved_for_provisioned_identities(env, user):
    r = enrol(env, user=user)
    assert r.status_code == 403 and "gateway id" in r.json()["error"] and env.ca.signed == []


def test_names_that_merely_contain_ehg_are_fine(env):
    assert enrol(env, user="ios-ehg-1").status_code == 200


def test_a_revocation_that_failed_at_the_ca_is_retried_by_repeating_the_request(env):
    serials = [str(x509.load_pem_x509_certificate(enrol(env).json()["certificate"].encode()).serial_number) for _ in range(2)]
    env.ca.mode = "revoke_fails"
    first = env.client.post("/revoke/ios-phone-1", headers=bearer("root", "admin")).json()
    assert "NOT revoked" in first["ca"] and env.ca.revoked == [] and env.registry.gateway("ios-phone-1")["revoked_at"] is not None
    env.ca.mode = None                                   # the CA is back
    second = env.client.post("/revoke/ios-phone-1", headers=bearer("root", "admin")).json()
    assert sorted(s for s, _ in env.ca.revoked) == sorted(serials) and "revoked 2 certificate(s)" in second["ca"], "the second request retried exactly the failures"
    env.ca.revoked.clear()
    third = env.client.post("/revoke/ios-phone-1", headers=bearer("root", "admin")).json()
    assert env.ca.revoked == [] and "no live certificate waiting" in third["ca"], "nothing left to retry, nothing sent twice"
