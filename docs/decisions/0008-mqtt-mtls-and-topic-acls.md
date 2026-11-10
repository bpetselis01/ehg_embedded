# 0008. MQTT over TLS 1.3 with client certificates and topic ACLs

**Date:** 2026-11-03

## Status

Accepted (Checkpoint 1, 2026-11-03). Implemented by Unit 2 on 2026-11-03 as an optional cloud profile; tested against a local Mosquitto 2.1.2, containers not yet started (Docker was not running).

## Context

[`backend/mosquitto/mosquitto.conf`](../../backend/mosquitto/mosquitto.conf) accepts anonymous plain-text connections on port 1883. That is right
for a laptop on a bench and wrong for a broker on a public address: anyone could publish packets for any node or read them all.

## Decision

- A separate **cloud profile** configuration (`backend/mosquitto/mosquitto.tls.conf`, a compose override) adds an 8883 listener with
  `tls_version tlsv1.3` (a minimum in Mosquitto 2.x), `require_certificate true`, `use_identity_as_username true` and
  `allow_anonymous false`. The local `mosquitto.conf` is not touched.
- The client certificate's CN is the identity. One identity per component (`ehg-gw-01`, `ehg-bridge`, `ehg-viewer`), least-privilege ACLs with
  no `#` grants. A gateway may publish `ehg/+/pkt` and only its own `ehg/_gw/<id>/status`.
- `use_username_as_clientid true`: the client id is forced to the certificate CN, so one identity cannot kick another off or inherit its persistent session (reproduced by a test, then fixed).
- Containers get single certificate files, never the CA directory, so a compromised broker or ingest container does not hold a signing key.
- Certificates come from a private CA: an offline root and an intermediate for issuing, created by `tools/pki.sh`. Short-lived gateway
  certificates and revocation are in [decision 0013](0013-keycloak-and-certificate-lifecycle.md).
- Clients use an `ssl.SSLContext` with `minimum_version = TLSv1_3` (paho-mqtt 2.x `tls_set()` has no TLS-1.3-only switch).
- Port 8883 is exposed directly on the cloud VM. A WebSocket-over-TLS path on 443 is the fallback if a carrier blocks 8883
  (**unverified**; test on the real network). If client certificates do not survive that path, record the finding instead of weakening authentication.
- **Checked against Mosquitto 2.1.2 (Homebrew, 2026-11-03, Unit 2):** `tls_version` is a minimum (manual and test); the CN becomes the user name; `pattern` ACLs with `%u` exist but the ACL uses explicit per-gateway lines so a reader never gets a write grant;
  `crlfile` works and is re-read on **SIGHUP** as well as at start-up (tested; a running broker does not notice a changed CRL by itself); an expired CRL refuses every client (tested with Python's OpenSSL, not the broker). Details and tests: [security.md](../security.md).
- **Still unverified:** the WebSocket-on-443 path and client certificates through it, carrier blocking of 8883, the `eclipse-mosquitto:2` image's version and key-file readability inside the container (Docker was not running).

## Consequences

- The `acl_file` option is discouraged in the newest Mosquitto manual pages but works; revisit if it is removed.
- JWT or OIDC login on Mosquitto would need a third-party plugin (unverified), so certificates are the MQTT credential and Keycloak governs enrolment only.
- TLS adds handshake and per-record overhead; Unit 2 measures the local plain-versus-TLS difference so it is not confused with WAN delay.
- One process per certificate: two processes with the same certificate kick each other off because they share a client id.
- A gateway can still publish for any node id (`ehg/+/pkt`) with the hand-written ACL. **Closed 2026-11-04 as an option:** `tools/gen_acl.py` writes an ACL with one `ehg/<node>/pkt` line per assigned node (proved against a real Mosquitto); the cloud compose file uses it when `EHG_ACL_FILE` points at the result. Not the default, because nothing assigns nodes to gateways yet (a new node needs a line in `deploy/acl/gateways.json`).
- Secrets (private keys) live in `backend/pki/`, which is gitignored in the unit that creates it.

**Sources:** [hardening prompt](../claude-code-prompt-thesis-b-hardening.md) §4, §7 ("Verified technical notes" and reference config); [decision 0004](0004-end-to-end-loss-recovery.md) (QoS 1 and persistent sessions). External Mosquitto and paho facts are from the prompt and are unverified until Unit 2.
