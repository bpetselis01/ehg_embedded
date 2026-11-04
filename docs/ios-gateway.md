# The iPhone gateway (Unit 15)

**Status in one line:** the gateway logic and the MQTT client are built and tested against a real broker; the app compiles for the simulator and its screen renders; **nothing has run on a phone, and Bluetooth has never been exercised** (the simulator has none). The feasibility spike (15a) is yours to run.

## What exists
| Part | Where | Verified how |
|---|---|---|
| Wire decoder/encoder, gap tracker, clock map, RESEND scheduler | `ios/EHGWire/Sources/EHGWire` | golden vectors generated from the Python code (`swift test`) |
| Published JSON, outbox (SQLite, replay, cap), per-node session, DER `notAfter` reader | `ios/EHGWire/Sources/EHGGatewayCore` | golden messages from the Python functions; recording-transport tests |
| MQTT 3.1.1 over TLS 1.3 with a client certificate, QoS 1, last will, keep-alive, backoff | `EHGGatewayCore/MQTT.swift` | **real Mosquitto 2.1.2** with the repository's TLS config and ACL (`tests/test_ios_mqtt.py`): messages arrive, the ones written before the link existed are replayed and marked, the ACL still drops another gateway's topic, the will fires on an unclean drop |
| App: SwiftUI status screen, CoreBluetooth link, settings, Keychain identity | `ios/EHGGateway` | builds for the iOS simulator; screenshot `docs/img/ios-gateway-simulator.png`; **Bluetooth code never run** |

Found on the way (and fixed in the Python gateway too): a RESEND as the very first packet seen left the clock map without an offset, so `to_host()` raised and the packet was lost; and Python's floor division differs from Swift's truncation for a 0-frame packet (golden case `no_frames`).

## Build and install (you, Xcode, free Apple account)
1. `brew install xcodegen`, then `cd ios/EHGGateway && xcodegen generate` (writes `EHGGateway.xcodeproj`, not committed).
2. Copy the CA chain next to the sources so it is bundled (public file, never the keys): `cp backend/pki/ca-chain.crt ios/EHGGateway/Sources/` (for the cloud: the chain of the CA that signed the BROKER certificate).
3. Open the project in Xcode, Signing & Capabilities: pick your Personal Team and change the bundle identifier (`net.thesisb.ehggateway` is taken by nobody, but a free account needs a unique one). Plug in the phone and Run. **The signature lasts 7 days**: after that the app will not open until you install it again from Xcode; the screen shows the days left.
4. A client certificate as `.p12`: `tools/pki.sh client ehg-ios-01 --days 7`, then `openssl pkcs12 -export -inkey ehg-ios-01.key -in ehg-ios-01.crt -certfile ca-chain.crt -keypbe PBE-SHA1-3DES -certpbe PBE-SHA1-3DES -macalg sha1 -out ehg-ios-01.p12` (iOS and macOS do not read OpenSSL 3's default AES PKCS#12). AirDrop or save it to Files, then "Import certificate" in the app with its password. Set the broker host, the gateway id (= the certificate's CN) and the **allowed nodes** (e.g. `EHG-93C0`; the app ignores every other Bluetooth device and refuses to start with an empty list), then tap Start.

**Note on the CA chain:** the enrolment API's response `chain` is `[leaf, intermediate]`, NOT a trust chain (no root). The root certificate has to be shipped separately as the trust anchor on whatever verifies the gateway certificate (the broker's `cafile` is intermediate + root, as in deploy/step-ca/README.md).

## Who may be a node
A device is adopted only if its advertised name is a plain node id (`EHG-` plus letters, digits, `_`, `-`: no `/`, `+`, `#`), it is on the allowlist, and no OTHER device already claims that name (`NodeAdmission`, tested). That is identification, not authentication: BLE advertisements can be copied, so while the real board is off an imitator with the same name can still be adopted. The link-level protection is the secure board build (LESC passkey pairing, `firmware/build.sh --secure`); the app cannot force pairing, but a secure board refuses the subscription until the system pairing dialog has been completed. The same name rule now applies to the Python gateway's scan.

## Not implemented (and why)
- **In-app enrolment** (Keycloak device grant to the enrolment API) and **certificate renewal**: the manual `.p12` route above is the stand-in; the server side exists (`backend/enroll`, tested against a real step-ca).
- **Pairing UI**: CoreBluetooth cannot pair itself; a secure board makes iOS show its own passkey dialog on the first protected access (the board prints the passkey on its UART log).
- **OTA firmware update through the app** (15d): needs Nordic's McuMgr library and a phone.

## The feasibility spike (15a): what only the phone can answer
Run these, write the answers into this file, and stop and decide if 1 or 2 fails (the plan's gate):
1. Does a **30-minute locked-screen run** keep receiving about 2 packets/s per node, and do the board's resends fill any gap? Check the "received / missing" numbers and `eval/integrity.py`.
2. Does MQTT keep publishing from the **background** (Network.framework connections are suspended when the app is suspended; Bluetooth wakes the app, so the outbox should drain in those windows)? Watch "Waiting on the phone" and the last PUBACK time.
3. After a **kill** (swipe away) or a Bluetooth restart, does state restoration relaunch the app and reconnect? Is the outbox kept?
4. Does the free Personal Team allow the Bluetooth background mode and Keychain (it should; confirm)?
5. Is port 8883 reachable on your carrier? If not, the fallback is WSS on 443 (not built).
6. Cellular H1: run the same 7-minute scenario over cellular and compare with run 3.
If 2 fails, the honest options are screen-on operation or a different transport; record it as a security/availability deviation, do not hack around iOS.
