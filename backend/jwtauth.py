"""Token validation and roles for the triage API and the enrolment API (decision 0013): who is calling, and are they allowed?

PURPOSE: One strict place that turns an `Authorization: Bearer ...` header into a Principal (user, role) or an error. OIDC mode validates Keycloak-issued JWTs; dev mode (loopback only, loud banner)
  lets everything run and be tested without Keycloak.
INPUT: the Authorization header; settings from the environment (from_env): AUTH_MODE = oidc | dev (no default: unset is an error), and for oidc OIDC_ISSUER, OIDC_AUDIENCE, OIDC_JWKS_URL;
  for dev BIND_HOST (must be a loopback address).
  e.g. Authorization: Bearer eyJhbGciOiJSUzI1NiIs...  (oidc)   or   Authorization: Bearer dev-operator  (dev)
OUTPUT: Principal(user, role, roles) with role = the most powerful of viewer < operator < admin found in the token (Keycloak puts them in realm_access.roles; a flat `roles` claim also works),
  plus the separate `gateway` role used by enrolled phones. Errors: AuthError (HTTP 401) for a missing/bad/expired token, Forbidden (HTTP 403) for a valid token without enough role.
HOW: The signature is checked against the issuer's key set (JWKS) with an explicit algorithm allowlist (RS256, ES256), so `alg=none` and the "HS256 signed with the public key" trick are refused.
  `exp`, `iss`, `aud`, `sub` are required and checked. Nothing but the Authorization header is trusted: headers like X-User or X-Role are ignored. Dev tokens (dev-viewer, dev-operator, dev-admin) exist
  only when AUTH_MODE=dev, which refuses to start on a non-loopback address.
ROLE IN THESIS: The authentication and role-based access control of the enterprise chain (docs/security.md); the permission matrix is enforced in backend/triage/app.py.
BEFORE -> AFTER: Before: no authentication anywhere. After: every API route needs a role.
"""

import os
from dataclasses import dataclass, field

import jwt

ROLES = ("viewer", "operator", "admin")  # increasing power
ALGORITHMS = ("RS256", "ES256")
LOOPBACK = ("127.0.0.1", "::1", "localhost")
DEV_USERS = {"dev-viewer": ("dev-viewer", "viewer"), "dev-operator": ("dev-operator", "operator"), "dev-admin": ("dev-admin", "admin")}


class AuthError(Exception):
    """No valid credentials (HTTP 401)."""


class Forbidden(Exception):
    """Valid credentials, not enough role (HTTP 403)."""


@dataclass
class Principal:
    """The authenticated caller."""

    user: str
    role: str | None
    roles: set = field(default_factory=set)

    def at_least(self, needed: str) -> bool:
        """True if the caller's role is `needed` or more powerful."""
        return self.role is not None and ROLES.index(self.role) >= ROLES.index(needed)


def highest_role(roles) -> str | None:
    """The most powerful of viewer/operator/admin in `roles`, or None."""
    found = [r for r in ROLES if r in set(roles)]
    return found[-1] if found else None


class JwtVerifier:
    """`key_for(token)` returns the public key for the token's `kid` (e.g. from the issuer's JWKS)."""

    def __init__(self, issuer: str, audience: str, key_for):
        self.issuer, self.audience, self.key_for = issuer, audience, key_for

    def verify(self, token: str) -> Principal:
        """Validate signature, algorithm, expiry, issuer and audience; return the caller. Raises AuthError for anything wrong."""
        try:
            header = jwt.get_unverified_header(token)
            if header.get("alg") not in ALGORITHMS:
                raise AuthError(f"algorithm {header.get('alg')!r} is not accepted")
            claims = jwt.decode(token, self.key_for(token), algorithms=list(ALGORITHMS), audience=self.audience, issuer=self.issuer,
                                options={"require": ["exp", "iss", "aud", "sub"]})
        except AuthError:
            raise
        except Exception as e:  # noqa: BLE001  (every parse/verify/key-lookup failure means "not authenticated"; the reason is for the log, not the caller)
            raise AuthError(f"{type(e).__name__}: {e}") from e
        roles = set(claims.get("realm_access", {}).get("roles", [])) | set(claims.get("roles", []) if isinstance(claims.get("roles"), list) else [])
        return Principal(str(claims.get("preferred_username") or claims["sub"]), highest_role(roles), roles)


def jwks_key_resolver(url: str):
    """A key_for function that fetches and caches the issuer's JWKS (needs the network; used only in oidc mode)."""
    client = jwt.PyJWKClient(url, cache_keys=True, timeout=5)
    return lambda token: client.get_signing_key_from_jwt(token).key


class Authenticator:
    """The one object the API asks: `authenticate(header)`."""

    def __init__(self, mode: str, verifier: JwtVerifier | None = None, bind_host: str = "127.0.0.1"):
        if mode == "dev":
            if bind_host not in LOOPBACK:
                raise SystemExit(f"AUTH_MODE=dev refuses to run on {bind_host!r}: it has fixed passwordless users. Bind to 127.0.0.1 or use AUTH_MODE=oidc")
        elif mode == "oidc":
            if verifier is None:
                raise SystemExit("AUTH_MODE=oidc needs a verifier (OIDC_ISSUER, OIDC_AUDIENCE, OIDC_JWKS_URL)")
        else:
            raise SystemExit(f"AUTH_MODE must be 'oidc' or 'dev', got {mode!r}")
        self.mode, self.verifier = mode, verifier

    @property
    def banner(self) -> str | None:
        """Text the UI must show loudly in dev mode."""
        return "DEV MODE: fixed users, no real authentication. Never expose this." if self.mode == "dev" else None

    def authenticate(self, authorization: str | None) -> Principal:
        """Header value -> Principal, or AuthError."""
        scheme, _, token = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            raise AuthError("missing bearer token")
        token = token.strip()
        if self.mode == "dev":
            if token not in DEV_USERS:
                raise AuthError("unknown dev token")
            user, role = DEV_USERS[token]
            return Principal(user, role, {role})
        return self.verifier.verify(token)


def from_env(env=os.environ) -> Authenticator:
    """Build the authenticator from environment variables; an unset or unknown AUTH_MODE is an error, never a silent default."""
    mode = env.get("AUTH_MODE")
    if mode == "dev":
        return Authenticator("dev", bind_host=env.get("BIND_HOST", "127.0.0.1"))
    if mode == "oidc":
        missing = [k for k in ("OIDC_ISSUER", "OIDC_AUDIENCE", "OIDC_JWKS_URL") if not env.get(k)]
        if missing:
            raise SystemExit(f"AUTH_MODE=oidc needs {', '.join(missing)}")
        return Authenticator("oidc", JwtVerifier(env["OIDC_ISSUER"], env["OIDC_AUDIENCE"], jwks_key_resolver(env["OIDC_JWKS_URL"])))
    raise SystemExit(f"set AUTH_MODE to 'oidc' or 'dev' (got {mode!r}); there is no default on purpose")
