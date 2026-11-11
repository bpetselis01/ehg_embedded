# Security

**Intended purpose.** Research prototype for measuring wireless telemetry performance. Not for clinical decision-making. Not supplied to any person for patient care.
Makes no diagnostic or predictive claims. The replayed data are public, de-identified PhysioNet records.

**Status of this document (2026-11-03, after Unit 2).** It starts the threat model and the controls table of the enterprise-style evolution
([production-topology.md](production-topology.md)). Only the **gateway to broker** boundary is analysed so far, because it is the only one with new code.
Each later unit adds its boundary and updates the Status column. Nothing here says "secure" or "compliant": a control is *Implemented*, an *Optional flag*, *Planned* or a *Documented gap*.

## Controls

| Control | Where | Status |
|---|---|---|
| Wire CRC-16 + sequence number + RESEND | BLE link ([interface-spec.md](interface-spec.md) §3) | Implemented (existing) |
| MQTT over TLS 1.3 with client certificates, one identity per component, topic ACLs | `backend/mosquitto/mosquitto.tls.conf`, `acl`, `docker-compose.cloud.yml` | **Implemented as an optional cloud profile, tested against a real Mosquitto 2.1.2** (Unit 2). The default local stack stays anonymous on 1883 |
| Client side of the above | `gateway --mqtt-tls`; `MQTT_*` variables for ingest and the viewer | Implemented (Unit 2); half-set TLS configuration refuses to start instead of falling back to plain MQTT |
| Private CA bootstrap (root, intermediate, broker and client certificates, CRL) | `tools/pki.sh` | Implemented (Unit 2). Client certificates last 30 days by default |
| Short-lived (7-day) gateway certificates, CSR enrolment, revocation by an admin | Enrolment API and registry | **Written** (Unit 7): CSR vetting, rate limit, revocation of every live certificate; tested with a fake CA; never run against step-ca |
| LESC passkey pairing, `_AUTHEN` GATT permissions | Firmware | **Implemented, optional** (`build.sh --secure`; bench-tested with the Mac gateway 2026-11-04, see progress.md) |
| MCUboot with our own ECDSA P-256 key | Firmware | **Implemented, optional** (`build.sh --secure`); key outside the repo, backup is the owner's job |
| OIDC sign-in, TOTP multi-factor, roles, token validation | Keycloak realm as code and the services' token checks | **Written** (Units 7, 8): token validation tested against forged, expired, wrong-audience and `alg=none` tokens; the realm never imported into a real Keycloak |
| Hash-chained audit log, FHIR `AuditEvent` | Triage service | **Written** and tested on a real PostgreSQL: tamper-evident, **not** write-once; no FHIR `AuditEvent` is written |
| Secrets kept out of git | `.gitignore` (`backend/pki/`, `*.key`, `*.pem`, `*.p12`) | Implemented for keys and certificates. SOPS with age for deployment secrets: **written** in Unit 4 (`deploy/.sops.yaml` holds a placeholder key; nothing is encrypted yet) |
| Gateway outbox on disk | Python: SQLite file mode 0600 (WAL files inherit it) and FileVault; iOS: Data Protection class | Python **implemented, on by default (`--no-outbox` turns it off)** in Unit 3, plain SQLite: a **documented gap** against SQLCipher. iOS Planned (Unit 15) |
| Encryption at rest on the cloud VM | Provider disk encryption; encrypted backups | Planned; partial |
| Container hardening, image scan, SBOM, signing | Compose hardening: **written** (Unit 4, tested structurally); scanners, SBOM and signing: **not wired** ([supply-chain.md](supply-chain.md)) |
| Readback protection (APPROTECT) and secure element on the board | nRF52840 DK | **Documented gap** (replay rig, not a product) |
| Regulatory framing | [regulatory.md](regulatory.md) | **Written** (Unit 17); "designed with reference to" wording only, no compliance claim |

## Final controls summary (2026-11-04) and the OWASP API Security Top 10 (2023)

Mapping of the HTTP APIs (triage, enrolment, FHIR facade) to the 2023 list, from my knowledge of that list (not re-read from owasp.org this session) and only for items a test in this repository exercises. **It is not an ASVS assessment and not a penetration test.**

**Since 11 Nov 2026** the triage API, the enrolment API and the FHIR facade were removed, and their tests with them. The rows below record what those tests covered; the Tests column now lists only tests that still exist.

| Item | What was done | Tests |
|---|---|---|
| API1 Broken object level authorisation | Every route that touches a node or alert checks active consent for that node first, whatever the caller's role; the facade re-checks every returned resource against the requested node | removed 11 Nov 2026 |
| API2 Broken authentication | Signature, algorithm list, `iss`, `aud`, `exp` required; `alg=none`, HS256-with-public-key and expired tokens refused; dev mode only on loopback | removed 11 Nov 2026 |
| API3 Broken object property level authorisation | Request bodies reject unknown fields; responses are built from named fields, not whole rows; configuration values are range-checked | removed 11 Nov 2026 |
| API4 Unrestricted resource consumption | Body size limits at the front door, a rate limit on enrolment, page sizes and `_count` capped, monitor memory bounded | `test_caddy_config` (others removed 11 Nov 2026) |
| API5 Broken function level authorisation | Role matrix tested for every route against every role | removed 11 Nov 2026 |
| API6 Unrestricted access to sensitive business flows | Escalate and resolve need a note; revocation is admin-only and audited; enrolment is limited per login | removed 11 Nov 2026 |
| API7 Server-side request forgery | The only outbound calls are to configured servers (the CA, FHIR, the JWKS); no URL comes from a request | removed 11 Nov 2026 |
| API8 Security misconfiguration | Strict CSP, nosniff, `no-store` on `/api` and `/fhir`, no stack traces, `/metrics` refused when proxied, admin consoles not routed | `test_caddy_config` (others removed 11 Nov 2026) |
| API9 Improper inventory management | OpenAPI documents are generated from the code and diffed in a test | removed 11 Nov 2026 |
| API10 Unsafe consumption of APIs | Upstream answers are validated and failures become a 502 without details; the CA's answer is re-checked before a certificate is returned | removed 11 Nov 2026 |

**Still not done, in one place:** Keycloak, step-ca, Caddy, HAPI, NATS and TimescaleDB have each been started and exercised ONCE on a laptop (2026-11-04, docs/progress.md) but nothing has run on the VM, and no production-like traffic or browser sign-in has been tried; revocation reaches the broker only through the cron job that has never run; a gateway can publish for any node id with the default ACL (opt-in fix: tools/gen_acl.py); service passwords are container environment variables; the audit log is not write-once; the scanners were run ONCE by hand on 2026-11-04 (shellcheck, hadolint, gitleaks, trivy: docs/supply-chain.md) but are not in CI; no SBOM exists; the DEFAULT firmware build still uses Nordic's development signing key and an open link (the secure build, `build.sh --secure`, does not).

## Threat model: gateway to broker (MQTT QoS 1, JSON on `ehg/<node>/pkt`)

| STRIDE | Threat | Control (Unit 2) | Residual risk |
|---|---|---|---|
| **S**poofing | Someone publishes as a gateway, or reads the stream, without being one | TLS 1.3 with `require_certificate`; the certificate CN becomes the MQTT user name **and the client id** (`use_username_as_clientid`); no anonymous access in the cloud profile | A stolen client key works until the certificate expires or is revoked and the broker reloads its CRL |
| **S**poofing | A fake broker captures gateway data | The client trusts only the private CA and checks the broker name against the certificate | A wrong `--host` at certificate creation fails closed (name mismatch) |
| **T**ampering | A gateway publishes packets for nodes it does not serve | ACL limits what each identity can publish | the hand-written ACL's `ehg/+/pkt` lets a gateway publish for **any** node id; `tools/gen_acl.py` pins each gateway to its nodes (tested on a real broker) but is opt-in (`EHG_ACL_FILE`) |
| **T**ampering | Data altered in transit | TLS integrity; the application CRC-16 detects corruption before the gateway | Samples are not signed end to end; a compromised gateway can publish altered data |
| **R**epudiation | A publisher denies sending | One identity per component, visible in the broker log | The broker log is not tamper-evident (audit log planned, Unit 8) |
| **I**nformation disclosure | Packets read in transit or by an unauthorised reader | TLS 1.3; readers are listed in the ACL; no `#` grants | Public de-identified data only; real patient data must not be used (see below) |
| **D**enial of service | Flooding, an expired revocation list, or one identity taking over another's client id and session | Client id forced to the CN, so a takeover is impossible (reproduced and fixed, below); `max_queued_messages 100000` bounds the backlog | An **expired CRL refuses every client** (tested, below); no rate limiting at the broker; two processes sharing one certificate kick each other off, so each process needs its own certificate |
| **E**levation of privilege | A reader publishes, or a gateway writes another gateway's status | ACL; both cases tested against the real broker | The CA signing key controls every identity (see "Keys") |

## What was checked, and how

- **Mosquitto 2.1.2 (Homebrew, 2026-11-03) manual:** `tls_version` sets the *minimum* version, so `tlsv1.3` refuses TLS 1.2; `use_identity_as_username` takes the certificate CN; `crlfile` is a PEM revocation list.
  The manual also warns that any CA in `cafile` can issue certificates the broker will accept, so keep only this project's root and intermediate in `ca-chain.crt`.
- **Real broker tests** (`EHG_INTEGRATION=1 uv run pytest tests/test_mqtt_tls_config.py`, needs `brew install mosquitto`): the repo's real config and ACL, only paths and the port rewritten.
  A reader cannot publish; a gateway cannot write another gateway's status but can write its own; no client certificate, TLS 1.2 only and a certificate from another CA are all refused;
  `gateway --simulate 1 --mqtt-tls` publishes packets that the viewer identity reads; a refused certificate is reported by the gateway instead of failing silently.
- **Revocation:** after `tools/pki.sh revoke`, a **SIGHUP** makes the running broker refuse the revoked identity while others still connect (test). Done by hand once, before the signal, the revoked certificate still connected,
  so the CRL is picked up on reload or restart, not on its own. How an already-open session is treated at reload was **not** tested.
- **Expired CRL:** with the CRL checked, a list past its next-update date refuses every client until `tools/pki.sh crl` rewrites it (tested with Python's OpenSSL server context, which uses the same mechanism as the broker; not tested against Mosquitto itself).
  `tools/pki.sh` writes CRLs valid for 365 days; put a refresh in the calendar.
- **Client-id takeover (found by an automated security review, 2026-11-03):** without `use_username_as_clientid`, the viewer identity connected with the ingest service's client id and the broker dropped ingest and gave the viewer its session. Reproduced with a failing test, then fixed in `mosquitto.tls.conf`; the test now passes.
- **CA keys stay out of containers (same review):** the override mounts single certificate files, never `backend/pki`, which also holds `intermediate/ca.key` (and `root/root.key` until it is moved off). A test checks the merged compose file.
- **No Docker daemon needed:** all of the above ran without Docker running. `docker compose config` (which only reads files) confirms the merged cloud profile publishes only `127.0.0.1:8883`,
  mounts the TLS config, ACL and certificates read-only, and still has the same 4 services; the default stack still publishes 1883. The containers themselves were **not started** (Docker was not running).

## Cloud VM and containers (Unit 4): findings of the automated review, 2026-11-03

- **Host mounts (HIGH, fixed):** the first compose draft let cAdvisor mount `/`, `/var/run` and Docker's data directory, and Alloy mount the container log directory. Any of those reaches the Docker socket (a read-only mount of a socket file still lets a process connect to it)
  or every container's `config.v2.json`, which holds each container's environment, including the database passwords. The first test only looked for the text `docker.sock`, so it missed all of it.
  Now: no container mounts the host root, `/run`, `/var/run` or the data directory; Docker logs to the **journal**, Alloy reads only the journal, node-exporter gets `/proc`, `/sys` and a metrics file written by cron (it no longer monitors host filesystems directly), cAdvisor gets `/sys`.
  A test allows only five named host paths, read-only. Cost: cAdvisor shows containers by cgroup id, not name, and disk-space metrics come from the cron file (`ehg_disk_*_bytes`).
- **Networks (fixed):** two networks. `internal` has `internal: true` (no route in or out of the host); only Caddy and Mosquitto also join `edge`. Anything that later needs the internet (Alertmanager's webhook) must be added to `edge` on purpose. A test enforces this.
- **Metadata service (MEDIUM, fixed):** the VM now disables the legacy v1 metadata endpoint (`are_legacy_imds_endpoints_disabled = true`), and a systemd unit adds an iptables rule in `DOCKER-USER` so containers cannot reach `169.254.169.254` at all. UNVERIFIED on a real VM: if Oracle's agents need the v1 endpoint, the instance setting will show up as a failure at first boot.
- **Host logs in Loki (MEDIUM, fixed):** Alloy mounts the whole journal, so the first config would have shipped sshd, sudo and login lines to Loki, readable by anyone who can read Grafana. It now keeps only entries that carry a Docker container name (a relabel `keep` rule; a test checks it) and runs with `cap_drop: ALL` and a read-only root filesystem.
  Residual, accepted: the Alloy container can still *read* the whole journal, so a compromised Alloy would see host logs; filtering happens inside Alloy.
- **Still true, and not fixed:** the services' passwords are environment variables of those services (visible in `docker inspect` to anyone in the `docker` group, and in the container's `/proc/<pid>/environ`); Docker secrets or `_FILE` variables would be better where the image supports them. The `ehg` service user is in the `docker` group, which is root-equivalent on the VM.
  Hardening flags (`cap_drop`, `read_only`) have never been run.

## Data platform (Unit 6): findings of the automated review, 2026-11-03

All four were real, all fixed with failing tests first (the writer and bridge tests, removed 11 Nov 2026, and `test_platform_compose.py`):
- **Poison messages in the writer (HIGH):** only three exception types were caught per message, so `fs_hz: 0` (a ZeroDivisionError), an absurd timestamp (OverflowError) or similar could escape and stall the loop for everyone. Now ANY failure on one message terminates that message only, and `rows_for` validates every field it uses first (safe node id, integers in range, finite times between 2000 and 2100, 1 to 10,000 frames of 1 to 64 equal-length integer channels in the 32-bit range).
- **One bad row stalling a whole batch (MEDIUM):** a database DATA error used to be treated like an outage (nak the batch, retry forever). Now it is told apart (`BadData`): the batch is retried one message at a time and only the refused message is terminated; a real connection problem is still "degraded".
- **Subject injection in the bridge (MEDIUM):** `node_id` was put straight into a NATS subject, so a value containing `.`, `>` or `*` could publish onto subjects it should not. Node and gateway ids must now be one safe token (`[A-Za-z0-9_-]{1,64}`), the payload node must equal the node in the MQTT topic, `seq` and `t_inject` must be real integers in range; anything else is kept under `EHG.BAD.<reason>` and acknowledged.
- **The Influx writer's loop (MEDIUM):** one failing message stopped the loop. Now each message is isolated: unusable ones are terminated, a transient InfluxDB error is retried after 5 s.
- Still open: a malformed MQTT message to the ORIGINAL `ingest` (MQTT path) is swallowed by paho's callback handling as before; the bridge's `EHG.BAD.*` messages are kept but nothing alerts on them yet (Unit 8's dead-letter surfacing).

## Triage API (Unit 8): findings of the automated review, 2026-11-03

- **Consent bypass through other routes (MEDIUM, 2 findings, fixed):** the consent rule was applied to node detail and trends only. The worklist (with each node's health sparkline), the alert list, alert detail and the workflow actions exposed or changed data about a node whose subject has no active consent.
  Now a single gate covers every route that touches a node, whatever the caller's role: such nodes and their alerts are left out of the worklist and the list (the worklist reports `hidden_no_consent`), alert detail and every workflow action return 403, and each refusal is an audit row (`consent.denied`). Tests: the triage API tests, removed 11 Nov 2026 with the service.
- Open: aggregate numbers on `/metrics` (alert counts by state) include alerts on non-consented nodes; they identify no node or subject. The consent rule depends on the registry linking nodes to subjects; a node that is not registered is not gated (documented in `backend/triage/registry.py`).

## Stream monitor (Unit 8): second review round, 2026-11-03

- **Unvalidated packets in the monitor (HIGH, fixed):** `stream.py` trusted message contents that the writer already validates. A hostile `fs_hz`, a huge packet or a changing channel count could crash the evaluation loop or exhaust memory. It now runs the SAME `validate()` as the writer before any state changes, resets its frame window when the channel count changes, keeps at most 4,000 frames per node whatever `fs_hz` claims (trimmed on receipt, not only when a snapshot is taken),
  caps the number of monitored nodes at 256, and isolates each node's evaluation (a broken node is logged and skipped; the evaluation loop survives a failed round).
- **A related attack the review did not name, found while fixing it (fixed):** a sequence number jumping forward by billions made `GapTracker` build one entry per missing number. A forward jump over 10,000 is now treated as a restart (and shown as SEQ_RESTART) before the tracker sees it. My test for this ran against the unfixed code first and was killed, which is how I knew it was real.
- **TOTP did not cover admins (HIGH, fixed):** the Keycloak OTP condition matches the single role `operator`, so a user holding only `admin` would have skipped TOTP. `admin` is now a composite role containing `operator` (and `operator` contains `viewer`); a test requires every staff role to be covered. Whether the real Keycloak follows composites in that condition is UNVERIFIED.
- **A node could become invisible (MEDIUM, fixed):** a sampling rate near zero made the signal window size 0, the trim emptied the window, and the snapshot then crashed every round, so that node's alerts (even NODE_OFFLINE) never fired. The window is now at least one frame and never drops the newest packet, `fs_hz` must be at least 1, and a node whose full evaluation fails falls back to a liveness-only view (and is logged) instead of being skipped.
- Still open: `GapTracker` remembers every sequence number seen in a stream (its own comment says so); a long soak from a hostile gateway can still grow that set slowly.

## Enrolment API (Unit 7): findings of the automated review, 2026-11-03

- **Revocation reached only the newest certificate (HIGH, fixed):** re-enrolling overwrote the stored serial, so revoking later revoked one certificate while earlier unexpired ones (up to 7 days old) stayed valid. Every issued serial is now kept (`registry.gateway_cert`, migration 0002), revocation revokes ALL live ones at the CA and names any that failed, and a failed revocation of one does not stop the others.
- **Reserved names were a fixed list (MEDIUM, fixed):** four names were denied by a tuple. The whole `ehg-` namespace (case-insensitive) is now reserved: every provisioned identity (`tools/pki.sh`) lives there, so a login can never enrol into it. A name outside it also gets no broker permissions unless it is added to the ACL by hand.
- Honesty note: the tests for these two were written together with the fix in one step, so I did not see them fail first.
- **A failed CA revocation was never retried (MEDIUM, fixed, third finding on this API):** the registry marked certificates revoked before the CA confirmed, so after a CA failure a repeated `/revoke` found nothing to do. A certificate now leaves the retry list only when the CA confirms (`ca_revoked_at`); repeating the request retries exactly the failures and sends nothing twice.
- **Migration edited in place (MEDIUM, acknowledged):** `ca_revoked_at` was added to revision 0002 instead of a new revision. No database has ever applied 0002 (nothing is deployed, the branch is unpushed, test databases are throwaway), so no deployment can drift; the policy is now written in `backend/db/migrate.py`: from the first deployment an applied revision is frozen and every change is a new one. The review's second suggestion does not apply: `revoke_gateway` already commits the registry revocation BEFORE any CA call, so CA bookkeeping can never block it.
- Closed in code (Unit 11b follow-up), never run on the VM: `deploy/crl-sync.sh` (cron every 5 minutes from cloud-init) fetches `GET /enroll-api/crl`, refuses a list whose signature does not verify against the CA chain, that has expired, that is malformed, or whose CRL number is LOWER than the installed one (a replayed old list would un-revoke certificates),
  writes it INTO the existing file (the compose file bind-mounts that single file, so a rename would leave the container on the old list), and signals Mosquitto only after a verified install. A revoked gateway is refused when it next connects; an open connection is not dropped by a reload.
  **A test found a real hole while this was written:** `openssl crl -verify` prints "verify failure" for a list from another CA but still exits 0, so checking the exit status would have installed a forged list; the script checks the printed result (`tests/test_crl_sync.py`).

## Front door (Unit 11): findings of the automated review, 2026-11-03

- **The 404 for /metrics and probes never ran (MEDIUM, fixed):** Caddy runs `handle` blocks before a bare `respond`, and a catch-all `handle` forwarded everything, so `/metrics` and `/healthz` would have been proxied. It is now the FIRST `handle` block and also matches prefixed paths (`/grafana/metrics`, `/enroll-api/healthz`). The services also refuse `/metrics` when a proxy header is present (second line of defence).
- **Keycloak's admin interface was reachable (MEDIUM, fixed):** `/auth/*` was proxied whole. Now only `/auth/realms/ehg/*` and `/auth/resources/*` are; the admin console, the master realm, health and metrics answer 404. Administer Keycloak through an SSH tunnel to the container. Test: `tests/test_caddy_config.py` (path-matching of allowed and blocked examples; `caddy adapt` ordering is checked only where Caddy is installed).
- **Ordering of the deny rule (MEDIUM, second look, fixed):** relying on the source order of separate `handle` blocks is not guaranteed (Caddy sorts them by matcher specificity). All routing is now inside ONE `route` block, which runs exactly as written, with the deny rule first and the catch-all last (tested); Grafana's own `/metrics` is also switched off (`GF_METRICS_ENABLED=false`). Still UNVERIFIED by `caddy adapt` (Caddy is not installed here).

## Triage page (Unit 10)

- **Controls, each with a static test** (`tests/test_triage_page.py`): no inline script or style and no external host (the front door's CSP allows only same-origin files); server text is only inserted as text (no `innerHTML`, `eval` or string timers); no `localStorage`, cookie or IndexedDB, the access token lives in one JavaScript variable (a reload signs in again),
  `sessionStorage` holds only the one-off PKCE verifier and state; the code flow with S256 PKCE and a checked `state`, no implicit or password flow; no clinical or diagnostic wording; the routes the script calls exist in the API; role-gated buttons (a viewer sees History only; the API enforces the real rule).
- **Dev mode:** `/ui-config.json` lists the fixed dev users WITH their tokens, but only when `AUTH_MODE=dev`, which refuses to start on a non-loopback address; in `oidc` mode it carries no user list and no secret (tested).
- **Limits:** no refresh token (the page asks to sign in again when the five-minute token ends); the page was exercised in the built-in browser against a stub API (roles, escalate with a required note, history, EHR panel, consent refusal) but never against Keycloak, a real database or the Caddy CSP, so a CSP violation that only a real header would show is not ruled out. No admin screens (configuration, audit) yet: those are API-only.

## FHIR sync and facade (Unit 9)

- **Boundary:** browser or page -> triage API `/fhir/*` -> HAPI (private network, not routed by Caddy). The sync worker writes to HAPI directly; nothing else does.
- **Controls, each with a test** (the facade tests, removed 11 Nov 2026 with the facade, and `tests/test_fhir_sync.py`): `viewer` role needed; GET only (405 for everything else); `node` mandatory and a plain id, so there is no unscoped search; ACTIVE consent required and read on every request (a node with no subject is refused too); the refusal is audited; the server is not contacted for a refused node;
  each result is re-checked against the requested node, so a wrong answer from the server cannot leak another node's data; upstream errors become a 502 with no server text; the worker exports only when consent is True, checks it every round, and sends nothing for False or None.
- **Automated review, 1 finding, fixed:** `/fhir/*` responses (waveform data, consent refusals) did not get `Cache-Control: no-store` like `/api/*`; they do now, errors included (its test was removed with the facade on 11 Nov 2026). Also found while writing the page: the Keycloak realm had no audience mapper, so no `triage-ui` token would have carried `aud: triage-api` and every call would have been refused; mappers added for `triage-ui` and `ios-gateway` (test in `tests/test_realm_export.py`, still unverified on a real Keycloak).
- **Documented gap:** withdrawing consent stops further exports and all reads through the facade, but does NOT delete what was already sent to HAPI. A deletion route would need a deliberate, audited design (and HAPI delete/expunge is switched off in `deploy/hapi/application.yaml`).
- **Documented gap:** the worker holds its "already sent" memory in the process; after a restart it resends, and the conditional requests keep the server at one copy (tested). HAPI authentication on the private network is not configured (network isolation only).
- **Not verified:** that a real HAPI R5 server accepts the transaction (tests/test_hapi_integration.py, needs Docker), HAPI's memory use on the 12 GB VM, and the HAPI configuration keys beyond having been read from the starter's own file.

## Pairing the board without a serial log: the Button 2 window (2026-11-06)
The passkey build (`build.sh --secure`) shows its code only on the UART log, which nobody outside a lab can read. The alternative build (`build.sh --pairing-button`, overlay `security-button.conf`, `CONFIG_EHG_PAIRING_BUTTON`) needs no code: the CCC, the command slot and the OTA service need an **encrypted** link (LE Secure Connections, Just Works), and the board accepts a NEW pairing only for 60 s after **Button 2** (LED3 lit). A central that is already bonded reconnects without the button. While the window is open the board also asks a connected central to pair (SMP security request).
- **What it protects:** against passive listening (the link is encrypted) and against anyone who is not at the board. **What it does not:** an active man-in-the-middle during the 60 s window (Just Works has no code to compare). The passkey build remains the stronger setting.
- **Found on the first phone run (checked on the board's log):** iOS starts pairing by itself 2 s after connecting, so a closed window makes that attempt fail (`pairing refused: press Button 2 first`) and iOS does not retry on its own; the board's request after the button press is what makes it pair (`security level 2`, `pairing complete (bonded: yes)`). `CONFIG_BT_SMP_SC_ONLY` must NOT be set in this build: it means "security level 4 only" (authenticated), which rejects Just Works (`pairing failed, reason 4`, `bt_conn_set_security` returning -EINVAL). `CONFIG_BT_SMP_SC_PAIR_ONLY` is the setting that switches legacy pairing off.
- **Also:** `CONFIG_BT_MAX_PAIRED=4` (the default of 1 would let a leftover Mac bond block the phone).

## Known limit: a gateway can publish for any node id (fixable with tools/gen_acl.py, opt-in)

**Update 2026-11-04:** `tools/gen_acl.py` now writes an ACL that grants each gateway `ehg/<node>/pkt` only for the nodes listed under it (file `deploy/acl/gateways.json` or the registry). A real Mosquitto drops a gateway's publish for another gateway's node and for an unassigned node (`tests/test_mqtt_tls_config.py`). It is NOT the default: nothing assigns nodes to gateways yet, so the JSON file has to be edited when a node is added, and the topic layout and the recorded runs are unchanged. The original discussion follows.

`topic write ehg/+/pkt` lets an authenticated gateway publish packets labelled with another node's id. Pinning a gateway to its nodes needs either a gateway id in the topic or message (a change to the documented `ehg/<node>/pkt` interface that `eval/` and the recorded runs rely on) or a check against a registry of which gateway serves which node.
It is **not fixed in Unit 2** and is accepted for now: the planned registry (`node.assigned_gateway`, Units 6 and 7) and the triage worker's checks (Unit 8) are where it gets closed, and the pin in `docs/handoff.md` carries it forward. Until then, a compromised gateway certificate can forge data for any node.

## Not verified (carried forward)

- The `eclipse-mosquitto:2` image's exact version (the tests ran 2.1.2 from Homebrew), and whether the container's `mosquitto` user can read `backend/pki/broker.key` (mode 600). On Linux this may need a group-readable key or running the container with the host user. Check when Docker is available (Unit 5 on the VM).
- The WebSocket-over-TLS path on port 443, whether a carrier blocks 8883, and client certificates through a SNI-routing front door.
- Whether the single-file bind mount of `ca.crl` follows a rewrite by `tools/pki.sh crl` (it should, since OpenSSL rewrites the file in place, but a replaced file would leave the container with a stale list). Check with Docker.
- OpenSSL 3 is required by `tools/pki.sh`; macOS's own `/usr/bin/openssl` is LibreSSL and is refused with a message.

## Keys

- `backend/pki/` is gitignored, as are `*.key`, `*.pem` and `*.p12`. Private keys are created mode 600 and never printed.
- `tools/pki.sh init` tells you to move the **root key off this computer**; it is only needed to replace the intermediate. Losing it means re-issuing every certificate; copying it casually defeats the purpose.
- If a key is ever committed or pasted somewhere, treat its identity as compromised: revoke it, rewrite the CRL, reload the broker, and rotate.

## Data

Only the public, de-identified PhysioNet recordings used by this project belong on the cloud VM. Recording real people with the nRF52840 would make the data identifiable health information and
needs ethics approval and privacy obligations before it touches this chain; that is the line the prototype must not cross without approval ([decision 0010](decisions/0010-oracle-always-free-cloud-target.md)).
