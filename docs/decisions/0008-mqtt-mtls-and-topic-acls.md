# 0008. MQTT over TLS 1.3 with client certificates and topic ACLs

**Date:** 2026-11-03

## Status

Accepted (Checkpoint 1, 2026-11-03; status of the build is stated in each record). Implemented by Unit 2.

## Context

[`backend/mosquitto/mosquitto.conf`](../../backend/mosquitto/mosquitto.conf) accepts anonymous plain-text connections on port 1883. That is right
for a laptop on a bench and wrong for a broker on a public address: anyone could publish packets for any node or read them all.

## Decision

- A separate **cloud profile** configuration (`backend/mosquitto/mosquitto.tls.conf`, a compose override) adds an 8883 listener with
  `tls_version tlsv1.3` (a minimum in Mosquitto 2.x), `require_certificate true`, `use_identity_as_username true` and
  `allow_anonymous false`. The local `mosquitto.conf` is not touched.
- The client certificate's CN is the identity. One identity per component (`ehg-gw-01`, `ehg-bridge`, `ehg-viewer`), least-privilege ACLs with
  no `#` grants. A gateway may publish `ehg/+/pkt` and only its own `ehg/_gw/<id>/status`.
- Certificates come from a private CA: an offline root and an intermediate for issuing, created by `tools/pki.sh`. Short-lived gateway
  certificates and revocation are in [decision 0013](0013-keycloak-and-certificate-lifecycle.md).
- Clients use an `ssl.SSLContext` with `minimum_version = TLSv1_3` (paho-mqtt 2.x `tls_set()` has no TLS-1.3-only switch).
- Port 8883 is exposed directly on the cloud VM. A WebSocket-over-TLS path on 443 is the fallback if a carrier blocks 8883
  (**unverified**; test on the real network). If client certificates do not survive that path, record the finding instead of weakening authentication.
- Mosquitto's `crlfile` option, its live reload behaviour and the WebSocket listener options are **unverified** on 2.1 and must be checked before use.

## Consequences

- The `acl_file` option is discouraged in the newest Mosquitto manual pages but works; revisit if it is removed.
- JWT or OIDC login on Mosquitto would need a third-party plugin (unverified), so certificates are the MQTT credential and Keycloak governs enrolment only.
- TLS adds handshake and per-record overhead; Unit 2 measures the local plain-versus-TLS difference so it is not confused with WAN delay.
- Secrets (private keys) live in `backend/pki/`, which is gitignored in the unit that creates it.

**Sources:** [hardening prompt](../claude-code-prompt-thesis-b-hardening.md) §4, §7 ("Verified technical notes" and reference config); [decision 0004](0004-end-to-end-loss-recovery.md) (QoS 1 and persistent sessions). External Mosquitto and paho facts are from the prompt and are unverified until Unit 2.
