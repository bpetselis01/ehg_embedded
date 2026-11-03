# 0015. Native Swift iPhone gateway built with free Apple signing

**Date:** 2026-11-03

## Status

Accepted (Checkpoint 1, 2026-11-03; status of the build is stated in each record). Implemented by Unit 15; subunit 15a (a feasibility spike) gates the rest.

## Context

The enterprise chain puts the gateway on a phone over cellular. Bleak on macOS cannot pair, so a phone app is also the realistic route to secure BLE.
Byron has an iPhone, a Mac with Xcode and a free Apple account (Personal Team), not the paid programme.

## Decision

- Build a **Swift / CoreBluetooth / SwiftUI** app in `ios/EHGGateway/` that behaves like the Python gateway: wire v2 decode with CRC, gap tracking and RESEND (same constants: retry 1 s, at most 5 tries),
  the same JSON on `ehg/<node>/pkt` at QoS 1, a durable outbox ([decision 0009](0009-gateway-durable-outbox.md)), a Last Will status topic. MQTT uses CocoaMQTT (MIT).
- The Python gateway stays the reference implementation.
- The Swift decoder is the **third copy of the wire format**. It is guarded by golden test vectors generated from `gateway/packet.py` and a static drift test, and
  [interface-spec.md](../interface-spec.md) will say "change all copies together".
- **Gate (15a):** before the app core, a spike must show Background Modes and state restoration work on a free team, an EC or RSA client identity can be presented for TLS, a 30-minute locked-screen run survives,
  and the carrier lets 8883 through (or the 443 route works). If not, stop and ask Byron which compromise to accept; do not hack around iOS limits.
- Certificate enrolment uses the Keycloak device grant and a key pair generated in the Keychain ([decision 0013](0013-keycloak-and-certificate-lifecycle.md)). Over-the-air firmware update through the app (15d) is Tier C.
- I write code, tests and instructions; Byron builds and installs. CI does not build iOS.

## Consequences

- A free-signed build expires after **7 days** and must be re-installed from Xcode. There is no TestFlight, App Store or push. The app is a bench gateway, not a deployable product.
- iOS may terminate the app, and state restoration does not relaunch a force-quit app, so the outbox does not cover a long outage while the app is not running.
- Free-team capabilities, CocoaMQTT client-identity support and locked-screen behaviour are all **unverified** and answered with evidence in `docs/ios-gateway-feasibility.md`.
- H1-cellular measured on the phone is not an LTE-M or NB-IoT measurement and must not be described as one.

**Sources:** [hardening prompt](../claude-code-prompt-thesis-b-hardening.md) §3 (decision 5), §5, §9 Unit 15; [decision 0004](0004-end-to-end-loss-recovery.md). Apple and library facts are from the prompt and unverified until the spike.
