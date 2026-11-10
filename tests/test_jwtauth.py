"""Checks token validation (decision 0013): a good token is accepted with the right role; expired, wrong audience, wrong issuer, no expiry, tampered, signed by another key, `alg=none`
and HS256-signed-with-the-public-key tokens are all refused; roles map to the highest permission; dev mode exists only on loopback; there is no default auth mode."""

import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from backend.jwtauth import AuthError, Authenticator, JwtVerifier, Principal, from_env, highest_role

ISS, AUD = "https://id.example.org/realms/ehg", "triage-api"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER = rsa.generate_private_key(public_exponent=65537, key_size=2048)
EC = ec.generate_private_key(ec.SECP256R1())


def mint(key=KEY, alg="RS256", **over):
    claims = {"iss": ISS, "aud": AUD, "sub": "u-1", "preferred_username": "alice", "exp": int(time.time()) + 300, "iat": int(time.time()),
              "realm_access": {"roles": ["operator", "offline_access"]}}
    claims.update(over)
    return jwt.encode({k: v for k, v in claims.items() if v is not None}, key, algorithm=alg)


@pytest.fixture
def verifier():
    return JwtVerifier(ISS, AUD, lambda token: KEY.public_key())


def test_a_good_token_gives_the_user_and_the_highest_known_role(verifier):
    p = verifier.verify(mint())
    assert (p.user, p.role) == ("alice", "operator") and p.at_least("viewer") and p.at_least("operator") and not p.at_least("admin")


def test_es256_tokens_work_too():
    v = JwtVerifier(ISS, AUD, lambda t: EC.public_key())
    assert v.verify(mint(EC, "ES256")).user == "alice"


@pytest.mark.parametrize("roles, expect", [(["viewer"], "viewer"), (["viewer", "admin", "operator"], "admin"), (["offline_access"], None), ([], None)])
def test_role_mapping_takes_the_most_powerful(verifier, roles, expect):
    assert verifier.verify(mint(realm_access={"roles": roles})).role == expect


def test_a_flat_roles_claim_is_understood_and_a_token_without_a_known_role_has_no_access(verifier):
    assert verifier.verify(mint(realm_access=None, roles=["admin"])).role == "admin"
    p = verifier.verify(mint(realm_access={"roles": ["offline_access"]}))
    assert p.role is None and not p.at_least("viewer")


BAD = {
    "expired": lambda: mint(exp=int(time.time()) - 10),
    "wrong audience": lambda: mint(aud="some-other-api"),
    "wrong issuer": lambda: mint(iss="https://evil.example.org/realms/ehg"),
    "no expiry": lambda: mint(exp=None),
    "no subject": lambda: mint(sub=None),
    "no audience": lambda: mint(aud=None),
    "signed by another key": lambda: mint(OTHER),
    "garbage": lambda: "not.a.jwt",
    "empty": lambda: "",
}


@pytest.mark.parametrize("name", list(BAD))
def test_bad_tokens_are_refused(verifier, name):
    with pytest.raises(AuthError):
        verifier.verify(BAD[name]())


def test_a_tampered_payload_is_refused(verifier):
    head, payload, sig = mint(realm_access={"roles": ["viewer"]}).split(".")
    forged_payload = jwt.utils.base64url_encode(b'{"iss":"%s","aud":"%s","sub":"u-1","exp":%d,"realm_access":{"roles":["admin"]}}' % (ISS.encode(), AUD.encode(), int(time.time()) + 300)).decode()
    with pytest.raises(AuthError):
        verifier.verify(f"{head}.{forged_payload}.{sig}")


def test_alg_none_is_refused(verifier):
    token = jwt.encode({"iss": ISS, "aud": AUD, "sub": "x", "exp": int(time.time()) + 300, "realm_access": {"roles": ["admin"]}}, key=None, algorithm="none")
    with pytest.raises(AuthError, match="not accepted"):
        verifier.verify(token)


def test_hs256_signed_with_the_public_key_is_refused(verifier):
    """The classic algorithm-confusion attack: use the PUBLIC key as an HMAC secret. PyJWT refuses to build such a token, so the attacker's token is assembled by hand here."""
    import base64
    import hashlib
    import hmac
    import json

    pem = KEY.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    b64 = lambda raw: base64.urlsafe_b64encode(raw).rstrip(b"=").decode()  # noqa: E731
    head = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body = b64(json.dumps({"iss": ISS, "aud": AUD, "sub": "x", "exp": int(time.time()) + 300, "realm_access": {"roles": ["admin"]}}).encode())
    sig = b64(hmac.new(pem, f"{head}.{body}".encode(), hashlib.sha256).digest())
    with pytest.raises(AuthError, match="not accepted"):
        verifier.verify(f"{head}.{body}.{sig}")


def test_key_lookup_failure_is_an_auth_error_not_a_crash():
    def broken(token):
        raise ConnectionError("JWKS endpoint unreachable")
    with pytest.raises(AuthError):
        JwtVerifier(ISS, AUD, broken).verify(mint())


# ---------------------------------------------------------------- the Authenticator

def test_header_parsing(verifier):
    a = Authenticator("oidc", verifier)
    assert a.authenticate("Bearer " + mint()).user == "alice" and a.authenticate("bearer " + mint()).user == "alice"
    for bad in (None, "", "Bearer", "Bearer ", "Basic abc", mint(), "Token " + mint()):
        with pytest.raises(AuthError):
            a.authenticate(bad)


def test_dev_mode_has_fixed_users_only_and_a_loud_banner():
    a = Authenticator("dev")
    assert a.authenticate("Bearer dev-admin").role == "admin" and a.authenticate("Bearer dev-viewer").role == "viewer" and "DEV MODE" in a.banner
    with pytest.raises(AuthError):
        a.authenticate("Bearer dev-root")
    assert Authenticator("oidc", JwtVerifier(ISS, AUD, lambda t: KEY.public_key())).banner is None


def test_oidc_mode_does_not_accept_dev_tokens(verifier):
    with pytest.raises(AuthError):
        Authenticator("oidc", verifier).authenticate("Bearer dev-admin")


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.5", "example.org", ""])
def test_dev_mode_refuses_non_loopback_addresses(host):
    with pytest.raises(SystemExit, match="refuses to run"):
        Authenticator("dev", bind_host=host)


@pytest.mark.parametrize("env", [{}, {"AUTH_MODE": ""}, {"AUTH_MODE": "none"}, {"AUTH_MODE": "DEV"}, {"AUTH_MODE": "oidc"}, {"AUTH_MODE": "oidc", "OIDC_ISSUER": "x"},
                                 {"AUTH_MODE": "dev", "BIND_HOST": "0.0.0.0"}])
def test_from_env_has_no_default_and_refuses_incomplete_settings(env):
    with pytest.raises(SystemExit):
        from_env(env)


def test_from_env_builds_dev_on_loopback():
    assert from_env({"AUTH_MODE": "dev"}).mode == "dev"


def test_principal_helpers():
    assert Principal("x", None).at_least("viewer") is False
    assert highest_role(["admin", "viewer"]) == "admin" and highest_role(["nope"]) is None
