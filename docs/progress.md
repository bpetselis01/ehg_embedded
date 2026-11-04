# Progress log

## 2026-11-04: a Mosquitto exporter (the last metric gap)

- `backend/mqtt_exporter.py` (own small exporter: the off-the-shelf one cannot present a client certificate to an mTLS-only broker): reads nine allowlisted `$SYS` topics as `ehg_mosquitto_*` gauges plus `ehg_mosquitto_up`; own identity `ehg-exporter` (new in `tools/pki.sh`, the ACL and `tools/gen_acl.py`) that may READ exactly those nine topics, no wildcard (the repository's ACL test forbids `#`; a test keeps the topic list and the ACL equal). Service `mosquitto-exporter` in the cloud compose file, Prometheus job `mosquitto`, alert `EhgMosquittoDown` (page; promtool-tested fire and quiet). **Run against a real Mosquitto in a test** (`tests/test_mqtt_exporter.py`): it reads clients connected and goes up. Two more certificate files now travel to the VM (nine, not seven: deployment.md, checklist and `fix-key-permissions.sh` updated). Not run in the full stack yet.

## 2026-11-04: load and memory measurement, revocation end to end, CI scanners, SBOM

- **Load (10 simulated nodes, 20 Hz, mTLS, six minutes, the full 21-service stack on this Mac):** every node delivered 660 packets with 0 missing; the sum of the PEAK memory of all containers was **3.7 GB** against the 9.5 GB budget (HAPI 1.7 GB of 2.3, Keycloak 0.57 GB, everything else small; Postgres 0.18 GB). Ten containers peaked within 2 to 24 % of their limit and risked out-of-memory kills (Alertmanager 98 %, node-exporter 96 %, Caddy 89 %, nats-exporter 87 %, step-ca 86 %, postgres-exporter 83 %, Alloy 81 %, cAdvisor 76 %, fhir-sync 71 %, Grafana 73 %): their limits were raised (+about 0.6 GB in total; the budget test still passes). This is a Mac with Docker's Linux VM, not the arm64 VM: re-measure there.
- **Revocation end to end** (`deploy/step-ca/crl_e2e.py`, real enrolment API, real step-ca, real Mosquitto container with the CRL as a single-file bind mount, `deploy/crl-sync.sh` unchanged): the certificate connects; after `POST /revoke` it STILL connects until the sync runs; the script reports "installed CRL number 02 and reloaded", the CRL file keeps its inode, and the revoked certificate is then refused. My first attempts failed for my own reasons (a CA bundle made before the template fix; a broker `cafile` without the root because the API returns `[leaf, intermediate]`): both are noted in docs/ios-gateway.md and the step-ca README.
- **CI:** gitleaks over the full history (digest-pinned), hadolint over every Dockerfile (digest-pinned; all clean), and a pyflakes-class ruff lint (pinned) were added to `security.yml`; the last two lint leftovers (fixture re-imports) got `noqa`.
- **SBOM:** `deploy/make-sbom.sh` wrote CycloneDX files for the platform image (119 components), the rebuilt Caddy (183) and the Python lock (78) into `docs/sbom/`; not signed.

## 2026-11-04: backup and restore drill run on the REAL TimescaleDB image

- `deploy/backup/backup.sh` against the running `timescale/timescaledb-ha:pg16.15-ts2.31.0` container (`--pg-prefix "docker exec -i -u postgres <container>"`), then `deploy/backup/restore_drill.sh` into a SECOND, clean container of the same image (the `timescaledb_pre_restore()` / `post_restore()` branch had never run). Result: checksums verified, roles and the database restored, "RESTORE DRILL OK in 1 s" (a tiny database: the RTO of a real one is not measured). Checked by hand in the target afterwards: all 300 seeded `ehg_health` rows, the three hypertables (`ehg_raw`, `ehg_pkt`, `ehg_health`), both continuous aggregates, the registry row and the 10 Timescale background jobs. `pg_dump` prints a warning about a circular foreign key inside TimescaleDB's own catalog (`continuous_agg`); it did not matter here.
- Not run: the restic copy (needs a repository), a restore on the VM, and the audit-chain verification on restored data (this database had no audit rows, so the drill printed `audit head: none`).

## 2026-11-04: review of the iPhone gateway: three findings, all valid, fixed

- **Zero sampling rate (MEDIUM):** a packet with a valid CRC (the CRC proves integrity, not sanity) and fs_hz = 0 divided by zero in the clock rule: a hard TRAP in Swift (the app would crash on one packet from a buggy or hostile board), a ZeroDivisionError in Python. Both parsers now refuse it as a bad packet (a new golden invalid case `zero_sampling_rate_with_a_valid_crc`, kind `zero_rate`); tests in Python and Swift.
- **Node name as a topic (MEDIUM):** the advertised name became `ehg/<name>/pkt`. A name with `+` or `#` makes the broker drop the connection on PUBLISH, and the stored row would be replayed after every reconnect, blocking the whole queue. Fixed in three places: names must be plain node ids (Python scan and Swift), the outbox refuses a non-publishable topic up front (`rejected` counter, never stored), tests for both.
- **Impersonating a node (MEDIUM):** any device advertising `EHG-*` was adopted. The app now needs an explicit allowlist of node ids (it will not start without one), refuses a second device claiming a name that is already claimed, and logs each refusal once (`NodeAdmission`). Stated limit: identification, not authentication; the real protection is the secure board build with passkey pairing. The Python gateway got the name rule but no allowlist (it takes the first N boards it finds, on a trusted bench).

## 2026-11-04: the iPhone gateway built as far as a Mac can take it

- **`EHGGatewayCore`** (Swift): the published JSON and clock rule (golden cases generated from the Python functions), the outbox (a port of `gateway/outbox.py` on SQLite: disk first, delete on PUBACK, replay marked, cap with loud loss), the per-node session (decode, clock, gaps, RESEND scheduling), a DER `notAfter` reader (iOS has no public call). 15 Swift tests.
- **MQTT client written on Network.framework** (no third-party library: the plan feared popular ones cannot present an EC client identity): TLS 1.3 only, our CA only, client certificate from a `SecIdentity`, QoS 1, persistent session, last will, keep-alive, backoff. **Proven against a real Mosquitto 2.1.2** with the repository's TLS config and ACL (`tests/test_ios_mqtt.py`): five messages arrive (two written to the outbox before the link existed are replayed and marked), another gateway's status topic is still dropped, the will fires after an unclean drop. A deadlock the real run found (`queue.sync` from inside the client's own callback) is fixed; macOS also needs SHA-1/3DES PKCS#12, not OpenSSL 3's default.
- **Two bugs found while building the golden cases, fixed in Python too:** a RESEND as the first packet ever seen left the clock without an offset and the packet was lost (`to_host()` raised); Python's floor division and Swift's truncation differ for a 0-frame packet.
- **App `ios/EHGGateway`** (SwiftUI, CoreBluetooth link with state restoration, Keychain identity import, status screen with certificate expiry and signature days left, log export): project generated by XcodeGen (not committed), **builds for the iOS simulator, runs there, screenshot in `docs/img/ios-gateway-simulator.png`**. Not done and not possible here: any run on a phone, Bluetooth (none in the simulator), background behaviour, in-app enrolment, renewal, OTA. `docs/ios-gateway.md` has the build steps and the six-point feasibility spike.

## 2026-11-04: review finding on the image scanner (valid, fixed)

- `deploy/scan-images.sh` mounted `/var/run/docker.sock` into `aquasec/trivy:latest`: root-equivalent access for an unpinned image. Now trivy is pinned by version and digest (0.76.1), and each image is saved with `docker save` to a temporary tarball that trivy reads read-only (`--input`); no socket. Guard test added. The results are the same as with the socket.

## 2026-11-04: third-party images scanned; the edge rebuilt; Terraform modules declare their provider

- **All 16 pinned images scanned** (`deploy/scan-images.sh`, table in docs/supply-chain.md). Every pinned release is already the newest, so for most images no fixed build exists: the findings are inside vendor-bundled libraries. Two things were possible and done: **Caddy** (the only service on the internet; 1 CRITICAL, 8 HIGH) is rebuilt from the same v2.11.7 with Go 1.27.3 and the two patched modules (`deploy/caddy/Dockerfile`, builds in under a minute on this Mac; 0/0 after; config validates; all routes and headers unchanged on the 22-service stack), and the **NATS exporter** moved 0.17.3 -> 0.20.3 (clean; the three metrics the alerts use are still exported). The rest are internal-only and recorded as accepted with their exposure; HAPI (28 CRITICAL, Java libraries) is not routed by Caddy.
- **Terraform:** tflint found that no module declared its provider or required Terraform version; each module has a `versions.tf` now (tflint 0 warnings, real-provider `validate` still passes). `terraform plan/apply` need the Oracle account and API key and cannot be run without them.

## 2026-11-04: review finding on the secret-scan allowlist (valid, fixed)

- `.gitleaks.toml` listed `paths` and `regexes` in one allowlist, which gitleaks combines with OR: every finding in `tests/test_deploy_assets.py` and the fake key in any file were skipped (shown: with the old config a scan of a tree holding the fake key elsewhere reported nothing). Adding `condition = "AND"` was not enough in gitleaks 8.30.1: with `regexTarget` `secret` or `match` a DIFFERENT key in the allowed file was still skipped. The allowlist now uses `condition = "AND"`, `regexTarget = "line"`, one anchored line, and `targetRules = ["aws-access-token"]`. Proved on throwaway trees: the exact line is allowed; a different key in the file, the fake and a real key on one line, and the fake line in another file are all reported. Real repository (history and tests): no leaks. Guard: `tests/test_gitleaks_config.py`.

## 2026-11-04: "other non-setup issues?": scanners run for the first time, key permissions, lint

- **Scanners (shellcheck, hadolint, gitleaks, trivy) run by hand for the first time** (table in docs/supply-chain.md): 3 shell-quoting warnings fixed (one was a real nested-quote bug in `init-offline.sh` that worked only by accident; `build.sh` now builds its options as real arguments, and both firmware variants were rebuilt), `backend/ingest/Dockerfile` no longer runs as root, `pip` is removed from the platform image (its vendored copies carried 5 HIGH CVEs; imports still work), aiohttp/certifi/pillow upgraded in the lock (861 tests pass), and `.gitleaks.toml` allowlists exactly the two deliberate look-alikes.
- **A real deployment bug found by thinking about Linux, not by running anything: file ownership.** On the VM a bind-mounted key keeps its host owner and mode. The containers run as fixed non-root users (Mosquitto 1883, the Python services 10001, step-ca 1000), so a 600 key owned by `ehg` is unreadable to all of them; it never showed on the Mac, where mounts look permissive. My own instruction `chown -R 1000:1000` on the step-ca bundle would also have taken the enrolment key away from the enrolment API (uid 10001). New `deploy/fix-key-permissions.sh` (sudo, one line per file, required keys checked, enrolment key set AFTER the recursive chown) with 5 tests using recorders; docs/deployment.md and the runbook checklist now point at it. Untested on a real VM.
- **Lint:** `ruff --select F,E9` flagged 7 leftovers (unused variable and imports, one in my own earlier edit); all cleaned except one intended fixture re-import.

## 2026-11-04: second review of the sync worker: two more valid findings, fixed

- **Key collision across subjects (MEDIUM):** the Observation key was `<node>/<record>/<chunk>`. A node handed to another subject (or the same record played again) produced the same key, and the conditional PUT would have REPLACED the first subject's window with the second's. The key is now `<node>/<subject>/<record>/<chunk>` (docs/interface-spec.md updated; the facade's per-node check is unchanged). Test: after a subject change the first subject's windows are byte-identical and the new ones have their own keys; the Device stays one.
- **Stale snapshot (MEDIUM):** the service read consent and `subject_since` once per round, so a withdrawal could be ignored for up to a round (a minute) and a reassignment mid-round used the old subject. The worker now asks consent, `subject_since` and the subject again right before EVERY transaction (`node_state()`, one small query, tested on a real PostgreSQL) and stops the round with `skipped: changed` if any differs. Tests: a withdrawal after the first send stops the second; a subject change between the start and the first send stops it.
- Remaining window, stated: the few milliseconds between that last read and HAPI accepting the request. The consent check cannot be atomic with a call to another server; withdrawal never recalls what was already sent (docs/security.md).
- Existing windows written under the old key form (none outside the local test stacks) would not be replaced, only duplicated under the new key.

## 2026-11-04: review finding on node reassignment (valid): a node moved to another subject must not carry the first subject's data

- **Finding (MEDIUM):** `assign_node` could point a node at a different subject at any time. The FHIR worker then exports the node's last 24 hours (and the facade shows what HAPI holds for the node) under the NEW subject's consent, although those frames were recorded for the old one: a consent bypass by misattribution.
- **Fix, four parts:** (1) `assign_node` refuses a node that already has a DIFFERENT subject (`SubjectChange`) unless `reassign=True` (CLI: `--reassign`), and the audit row records `previous_subject`; the same subject again changes nothing. (2) New column `registry.node.subject_since` (migration 0002; set whenever the subject changes; NULL for rows written by hand = no limit). (3) The worker drops frames recorded before `subject_since` (chunk times keep the right base) and forgets what it had sent for the node when `subject_since` changes. (4) The facade hides Observations whose start is before `subject_since` (an unreadable start is hidden, not guessed).
- **Tests:** refusal, idempotence, reassignment with the audit trail (real PostgreSQL); only post-assignment frames sent, correct chunk times, nothing sent when all frames are older, state reset (worker); old windows hidden, NULL hides nothing (facade).
- **Limit, stated:** what HAPI already holds from the first period stays there and stays readable under the FIRST subject's record only through direct HAPI access (HAPI is not exposed); no deletion is done. A database created before this change needs the column (`ALTER TABLE registry.node ADD COLUMN subject_since timestamptz`); no database anywhere is older than this.

## 2026-11-04: registry command line for subjects, consent and node assignment

- `backend/triage/registry_cli.py` (`subject add`, `consent <subject> <none|active|withdrawn>`, `node <id> --subject/--gateway`, `list`) over new audited `Registry` methods (`add_subject`, `set_consent`, `assign_node`). Every change appends to the hash-chained audit log with actor `cli:<user>`, role `admin`, and (for consent) the old and new value. Ids must be plain ids; unknown subject or gateway, a duplicate id and a non-state like "maybe" are refused with nothing changed. 5 tests on a real PostgreSQL, plus the real run: inside the triage container of the 19-service stack the three commands changed the registry and left three audit rows. This closes the "SQL is the only way" gap noted for `fhir-sync`.
- A subject is a CODE (the pseudonym), never a name; the tool cannot enforce that, and the docstring says so. It is a data-model demonstration, not a legal consent process.

## 2026-11-04: FHIR sync worker as a compose service (`fhir-sync`), run end to end

- The worker no longer needs `data/converted`: `tools/export_record_meta.py` writes each record's fs, gain, baseline and channel names (the public PhysioNet headers, 56 KB, no samples) into `deploy/fhir/records.json`, which the service mounts (`--records-file` / `FHIR_RECORDS_FILE`; `--check` and a test keep it equal to the data when the data is present). Settings come from the environment (`FHIR_SYNC_INTERVAL`, `FHIR_SYNC_SINCE`); an unknown record skips that node instead of stopping the loop.
- Its own database login **`ehg_fhir`** (secret `FHIR_DB_PASSWORD`, migration 0002): SELECT only on `registry.node`, `registry.subject`, `ehg_pkt`, `ehg_raw`; a test shows it cannot read gateways, the audit log or alerts, nor write anything. The service is in the default stack (19 services); with no registered subject it exports nothing.
- **Run on the clean stack:** a subject with ACTIVE consent and a node were inserted by SQL (nothing creates them yet: no API for subjects/consent exists), `gateway --simulate 1` ran over mTLS for 140 s, and the worker sent 4 complete 30-second windows to HAPI; the consent-checked facade returned the Observations and the Device to a Keycloak viewer. **Withdrawing consent** stopped both: the worker logged `skipped: consent`, the facade answered 403.
- Subjects and consent are now managed with `backend/triage/registry_cli.py` (next entry). Not done: the worker has no metrics endpoint (one log line per node per round).

## 2026-11-04: NATS exporter and two alerts; a flaky alert test fixed

- **`nats-exporter`** (natsio/prometheus-nats-exporter 0.17.3, pinned by digest, arm64) in the cloud compose file and a Prometheus job `nats`; run on the local stack, target up. Two new alerts with promtool tests: `EhgNatsStorageHigh` (JetStream file storage above 80 % of `max_file_store`, ticket) and `EhgNatsSlowConsumers` (a client dropped as slow, ticket). `EhgTargetDown` now also covers NATS itself. 14 alerts, 17 promtool cases. A Mosquitto exporter was not added (its metrics come from `$SYS` topics over an authenticated TLS session: more moving parts than the value).
- **Flaky test found and fixed by running promtool 12 times:** the backup-age case used one sample an hour; the alert reads a recording rule of another rule group and promtool evaluates groups concurrently, so it failed about 1 run in 12. It now uses a continuous series (20 of 20 runs pass). In production Prometheus the alert simply sees the previous evaluation; only the test needed the change.

## 2026-11-04: two review findings on the CA work (both valid, fixed) and the last hop to the broker proven

- **Review: one password for the root and the intermediate key (MEDIUM, valid).** `deploy/step-ca/init-offline.sh` reused the CA's password, so the file on the VM (`vm/keys/password`) also unlocked the offline root key. Now the intermediate key is re-encrypted under a SECOND random password (`step crypto change-pass`), the only one on the VM; the root password stays in `offline/`. Checked: the two files differ and step-ca starts healthy from the bundle. Also fixed the quoted glob that never expanded (`rm -f "$OUT"/vm/config/*.bak`).
- **Review: the enrolment API shared the triage service's database login (MEDIUM, valid).** New role `ehg_enroll` with its own secret `ENROLL_DB_PASSWORD` (init script, secrets example, both compose files). Migration 0002 grants it only: INSERT/UPDATE/SELECT on `registry.gateway` and `registry.gateway_cert`, SELECT on `registry.node` and `registry.subject`, SELECT/INSERT on the audit log and its sequence. Test (real PostgreSQL): it can enrol, revoke and append to the audit log; it cannot read alerts or telemetry, write nodes or subjects, or touch the audit history. Verified on a clean 20-service start from empty volumes: three audit rows written by the enrolment API (issue, enrol, revoke).
- **Defect found by the new broker step of `deploy/step-ca/local-check.sh`: with the CRL enabled, EVERY certificate the CA issued was refused by the broker.** step-ca's CRL carries an Issuing Distribution Point, and OpenSSL then requires the certificate to name the same point; our leaf template (which replaced step-ca's default) had no `crlDistributionPoints`. The template now sets it, and the CRL config sets the matching `idpURL`. After the fix: a certificate issued by step-ca connects to a real Mosquitto 2.1.2 whose cafile is the CA chain; after `POST /revoke`, the CA's own CRL is installed in place and the broker gets SIGHUP, and the same certificate is refused. (The broker's own server certificate still comes from `tools/pki.sh`, because the template issues client certificates only; the gateway trusts that chain, the broker trusts the CA chain: two files, not one.)

## 2026-11-04: private CA and enrolment API added to the cloud compose file (profile `enrol`), run end to end

- **`deploy/step-ca/init-offline.sh OUTDIR`** makes the CA on the owner's computer: random passwords, the JWK provisioner `ehg-enroll` written INTO `ca.json` (no admin database), the init provisioner removed, renewal and the SSH CA switched off, CRL with a 24 h cache. Output: `vm/` (config, certificates, the INTERMEDIATE key, the enrolment key) for the VM, `offline/` (the ROOT key and the CA password) to keep offline, and `env.txt` (root fingerprint, `COMPOSE_PROFILES=enrol`).
- **Compose services `step-ca` and `enroll`** behind the profile `enrol` (not started by default; `COMPOSE_PROFILES` in `site.env` turns it on, which `update.sh` honours). Tests: the service set is exactly the 18 plus these two, both profile-only; the memory budget now reserves only the FHIR sync worker.
- **Run locally with a CA made by the script, on the 18-service stack:** Keycloak login (password grant on a throwaway user) -> Caddy `/enroll-api/enroll` -> enrolment API -> real step-ca: certificate CN `ios-phone-1`, client-auth only, 7 days; `/enroll-api/crl` serves the CRL; the admin's `POST /revoke/ios-phone-1` revokes at the CA, the serial appears in the CRL, and a second enrolment is refused (403).
- **Four more defects found by running it, all fixed:** (1) the phone client's token had no `preferred_username` (it was given only `basic` + `roles`), so the gateway id became the user's UUID and the CA refused the CSR: the realm now maps just that claim for `ios-gateway` (test added); (2) step-ca's binary carries a file capability, so `cap_drop: ALL` made it fail with "Operation not permitted": `cap_add: NET_BIND_SERVICE`; (3) a named volume for the CA database is root-owned and the CA (uid 1000) cannot open it: a host folder `db/` in the bundle instead; (4) a volume path ending `secrets:` tripped the repository's own secret scan, so the host folder is called `keys`.
- **Also confirmed on the real Keycloak:** password-only sign-in for viewers, a second factor demanded for `operator` and `admin` (the earlier security fix holds); the page's PKCE flow (S256, state, wrong verifier refused) ends in a token that the API accepts through Caddy, a forged token is refused. The JavaScript itself was not run in a browser: the built-in browser refuses Caddy's internal CA (a trust decision that is the owner's).
- **Not done:** the broker trusting the step-ca chain (it still uses the `tools/pki.sh` CA locally); gateway renewal; step-ca on the VM.

## 2026-11-04: promtool unit tests for every alert: the delivery SLO never produced a value (fixed)

- `deploy/observability/tests/rules_test.yml` (`promtool test rules`, run in the pinned Prometheus image; wrapped by `tests/test_promtool_rules.py` with `EHG_INTEGRATION=1`): 14 cases, every one of the 12 alerts fires after its `for` and is shown quiet when healthy (a plain test fails if an alert has no test or lacks one of the two directions).
- **Defect found (HIGH for the SLO):** the recording rules `ehg:delivery_ratio:5m` and `:1h` were written `rx / (rx + missing)` with two selectors that carry different `kind` labels. Prometheus matches vectors on all labels, so the denominator was empty and **the delivery ratio never produced a sample**; `EhgDeliveryBelowObjective` could never fire, and the earlier real Prometheus run only showed rule health "ok", not data. Now `sum(rx) / sum(rx or missing)`. The same unit test also covers "no packets": the ratio is NaN, which is never a breach and never reads as 100 %.

## 2026-11-04: the WHOLE cloud compose file started for the first time (locally, 18 services), two defects fixed

Run on this Mac with a throwaway PKI (`tools/pki.sh init --host localhost`), random secrets and `EHG_DOMAIN=localhost` (Caddy then issues from its internal CA); not a substitute for the VM (arm64 Linux, journald, 12 GB), but the services, images, mounts and configs are the real ones.

- **All 18 services came up; 14 healthy by their own health checks, the rest by logs/targets.** Prometheus: 10 targets up (alertmanager, alloy, bridge, cadvisor, loki, node, postgres, prometheus, triage, ts-writer); both rule groups load with health `ok` (6 + 12 rules); Grafana (sub-path `/grafana`) has the provisioned dashboard "EHG pipeline overview" and both data sources report OK; Keycloak 26.8.1 imports the realm into PostgreSQL and serves `/auth/realms/ehg`; HAPI starts on PostgreSQL in 30 s; `ehg_consumer_pending` is exported for both consumers.
- **Caddy routes behave as designed over HTTPS:** `/` and `/triage.js` 200, `/ui-config.json` 200, `/api/worklist` and `/fhir/metadata` 401 without a token, `/hapi/` and `/metrics` not routed, security headers present (CSP, HSTS, X-Frame-Options DENY, no-referrer).
- **End to end over mTLS TLS 1.3:** `gateway --simulate 1 --mqtt-tls` with the `ehg-gw-01` certificate, through the cloud Mosquitto (ACL), the bridge, NATS JetStream and ts-writer: 59 packets in TimescaleDB, 59 published by the bridge.
- **Defect 1 (HIGH; ts-writer, triage, and the influx writer's loop):** on an idle stream both crashed with `TimeoutError` and were restarted by Docker. nats-py's `fetch()` raises the BUILT-IN `TimeoutError`; the code caught `nats.errors.TimeoutError`, which is a SUBCLASS and so does not catch its parent. Tests raised the subclass themselves and could not see it; it only showed with no traffic. Fixed with `backend/bus.py:fetch_batch` (ts-writer, triage) and the built-in class in `ingest.py`, with `tests/test_bus_fetch.py` (including a guard that no consumer catches only the subclass).
- **Defect 2: Alloy crash-looped** (`mkdir /var/lib/alloy/data: permission denied`): the image runs as root, its data directory belongs to uid 473 (mode 770), and with `cap_drop: ALL` root cannot enter it, nor read the journal files of the host group. Fixed with `cap_add: [DAC_OVERRIDE]` (nothing else is reachable from that container); journal reading itself could not be tested on this Mac (no journald), so it stays UNVERIFIED until the VM.
- **Not run:** Alloy to Loki with a real journal; cAdvisor naming; restic; `update.sh` on a VM; Keycloak sign-in through the browser (only tokens were checked); step-ca/enrolment/FHIR-sync as services in this file (they are not in it).

## 2026-11-04: Docker is up: the first real runs of NATS, TimescaleDB, Keycloak, HAPI, Caddy and Prometheus

Byron started Docker Desktop. `docker pull` first hung for over an hour (`error getting credentials`, the Desktop credential helper blocks); pulls of public images work with `DOCKER_CONFIG=/tmp/dockercfg-nocreds` (a config holding `{}`, with `cli-plugins` linked to `~/.docker/cli-plugins` so `docker compose` is found). Nothing of Byron's Docker configuration was changed. All images were pulled by the pinned digests.

- **Data platform, local profile (`docker compose --profile platform ...`), first start ever:** NATS 2.15.2, TimescaleDB (HA image, pg16, ts 2.31.0), `migrate` (exit 0, revision 0003), bridge, ts-writer, influx-writer all started on the first try. Hypertables `ehg_raw`, `ehg_pkt`, `ehg_health`, continuous aggregates `ehg_health_hourly/daily` exist, `timescaledb.license` = `timescale`. `gateway --simulate 2` for 40 s: 79 packets per node stored.
- **Parity proof (Checkpoint 4) on real servers: `eval/parity.py --node SIM-0000`: PARITY: PASS** (790 of 790 frames identical in InfluxDB and TimescaleDB, same packet set, no missing ranges, H1 median 0.465 vs 0.463 s, p95 0.468 vs 0.466 s). 
- **HAPI FHIR R5 (`tests/test_hapi_integration.py`, `EHG_INTEGRATION=1`): 1 passed** (the sync transaction is accepted and idempotent, SampledData string as expected).
- **Prometheus 3.15.1 `promtool`: `check rules` (6 + 12 rules) and `check config` pass; Alertmanager 0.34.2 `amtool check-config` passes.** Caddy 2.11.7 `caddy validate` on `deploy/caddy/Caddyfile`: Valid configuration.
- **Keycloak 26.8.1: a real defect found and fixed.** The realm import produced NO built-in client scopes, so tokens had no `sub`, `preferred_username` or `realm_access` and the APIs would have refused every login. Fixed in `realm-ehg.json` (see `deploy/keycloak/README.md`), proved with a re-import and with a real signed token accepted by `backend/jwtauth.py` (and refused for the wrong audience). `tests/test_realm_export.py` now guards the scopes and mappers. This settles the "Keycloak built-in scopes/flows" open question.
- **step-ca 0.30.2, real (`deploy/step-ca/local-check.sh`): three defects in my enrolment client and template, all invisible to the fake CA, fixed with tests:** the token had no `kid` header (401), the revoke token's subject must be the serial (403), and the leaf template produced a certificate with NO subject name (`.Insecure.User.commonName` is empty unless template data is sent; now `{{ toJson .Subject }}`). The enrolment API's CRL fetch asked for PEM by header and got DER: now `GET /crl?pem=true`. After the fixes: signed certificate has the right CN, client-auth only, 7 days; revoke works; the CRL lists the serial and `openssl crl -verify` says OK. Recommendation recorded in deploy/step-ca/README.md: a long CRL `cacheDuration` (24 h), because an expired CRL locks every client out.
- **Still not run:** the cloud compose file with step-ca in it; Loki, Alloy, Grafana provisioning of the new dashboard, cAdvisor and node-exporter; the full cloud compose file as one stack; restic; Caddy serving traffic; the triage page against a real Keycloak login (only the token side was checked).

## 2026-11-04: two more non-cloud items: per-gateway ACL generator, RESEND scheduling in Swift

- **`tools/gen_acl.py`** (commit 15bd2c7): writes a Mosquitto ACL where each gateway may write `ehg/<node>/pkt` only for ITS nodes (`deploy/acl/gateways.json`, or the registry with `--dsn`), plus its own status topic; service identities are the same as the hand-written ACL (tested equal). Ids must be plain ids (no `+ # / space`), a gateway named like a service is refused, a gateway with no node can write only its status, the file is rewritten in place and only on change (bind mount follows the inode). **Proved on a real Mosquitto 2.1.2:** gateway 01 publishes for its node, its publishes for gateway 02's node and for an unassigned node are dropped. Opt-in: `EHG_ACL_FILE` in the cloud compose file; NOT the default because nothing assigns nodes to gateways yet (the JSON file must be edited when a node is added). 16 tests.
- **RESEND scheduling** (commit cfa1763): the "ask again after 1 s, at most 5 requests" logic moved out of `gateway.Node` into `gateway/tracking.py` (`ResendScheduler`, `ranges`, `RETRY_S`, `MAX_TRIES`; `gateway.gateway` re-exports the names, so triage and the tests are unchanged) and was ported to `ios/EHGWire/Sources/EHGWire/Tracking.swift`. Seven golden cases (new gap at once, retry each second, give up after five, merged runs, new stream, no retry before the interval) are generated from the Python class into `tests/fixtures/packet_vectors.json` and replayed by the Swift test (`swift test`: 10 tests, 0 failures) and by Python. The app target, CoreBluetooth and the timer that calls it are still not written (needs the iPhone).
- **Found while starting the local platform stack:** `docker pull` hung for over an hour with `error getting credentials - signal: terminated`: Docker Desktop's credential helper blocks (not our code). Workaround that touches nothing of Byron's: `DOCKER_CONFIG=/tmp/dockercfg-nocreds` (a config with `{}`) for pulls of public images.

## 2026-11-04: Unit 13 on the DK: LESC passkey pairing, own MCUboot key, and a latent bootloader defect found

Byron attached the DK and chose "Full Unit 13" (build, new key, flash). All of it ran on the board EHG-93C0 with the Mac gateway.

- **Built (opt-in; the default build is unchanged in behaviour):** `CONFIG_EHG_REQUIRE_PAIRING` (default n) and `firmware/ehg_node/security.conf`; `firmware/build.sh --secure` builds into `build-secure/` with `$EHG_MCUBOOT_KEY`; `firmware/gen_signing_key.sh` makes the ECDSA P-256 key OUTSIDE the repo (`~/.ehg-keys/mcuboot-p256.pem`, mode 600, **Byron must back it up**). The CCC, the command slot AND the MCUmgr (OTA) service need an authenticated link (`_AUTHEN`); DisplayOnly passkey (random, printed as `PASSKEY nnnnnn` on the UART log); `BT_SMP_SC_ONLY`, `BT_SMP_ENFORCE_MITM`, bonds in flash (NVS). The board never requests security itself. Every symbol was grepped in `~/ncs/v3.4.1` first.
- **Gateway:** `protected()` wraps the first `start_notify` (60 s, one retry, passkey hint after 3 s) and `stale_bond()` explains a bond mismatch; the gateway never calls `pair()`.
- **Defects found by running it (none would have shown in unit tests):**
  1. **`sysbuild/mcuboot.overlay` replaced MCUboot's own `app.overlay`, so MCUboot was linked into the app slot (0xC000) instead of the boot partition.** Both the default and the secure build produced such a bootloader; a flash left the board's OLD bootloader running (it only looked fine). Fixed by choosing `boot_partition` in the overlay (bootloader now 32 KB of 48 KB at 0x0). Found because the new-key image was rejected ("Image in the primary slot is not valid") by the old dev-key bootloader.
  2. NCS's OTA helper sets the MCUmgr Bluetooth service to open read/write; `security.conf` now forces `_AUTHEN`, otherwise firmware upload would be unauthenticated in the secure build.
  3. `CONFIG_SETTINGS` without a backend silently became `SETTINGS_NONE` (bonds never stored, `Unable to store name`); `CONFIG_NVS=y` added; `settings_load()` moved to right after `bt_enable`.
  4. macOS reports a board without a bond as `CBErrorDomain 14 "Peer removed pairing information"`, which `stale_bond()` did not recognise: fixed, and the hint now also prints when the connect itself fails.
- **Pairing on the bench:** the system dialog appears on the first protected access; two attempts failed (30 s timeout, then a wrong code), the third succeeded: `security level 4`, bonded. The bond survived four forced reconnects and a board reset (no new dialog, same identity address).
- **Run 3 repeated at LINK level, 7 min, tpehg1007, `drop=0.05,burst=3,corrupt=0.01,disc=120,seed=3`, no backend** (Docker was not running and was not started, so there is NO H1 latency and NO integrity.py number for these runs):

| Build | Packets in gateway CSV | Missing after recovery | Streams | Injected (drop / corrupt / disc) | Gateway `bad` | Board `resent` | `notify_err` | `max_inject_late` |
|---|---|---|---|---|---|---|---|---|
| secure (pairing + own key) | 834 (seq 0..833) | 0 | 1 | 69 / 8 / 3 | 8 | 136 | 0 | 1435 us |
| default (open, dev key) | 834 (seq 0..833) | 0 | 1 | 69 / 8 / 3 | 8 | 134 | 7 | 428 us |

  Same faults, same recovery, no difference in delivery at the link. The two small differences (`resent` 136 vs 134; `max_inject_late` 1435 vs 428 us, still far under the 50 ms frame period) are single runs and are not explained. Run 3's published row (100 %, 259 / 478 ms) is untouched. (End-to-end numbers were measured later the same day, see below.)
- **Follow-up the same day, END TO END (Docker backend up, gateway `--no-outbox` as in run 3, same faults and seed, 7 min each, `eval/integrity.py` and `eval/latency.py`):**

| Build | Delivery | Bit-exact | H1 median / p95 / p99 | Notes |
|---|---|---|---|---|
| default (open, dev key) | **100.0000 %** (8330/8330 frames) | 0 | 265 / 483 / 494 ms | sync offset method serial, +-1.8 ms |
| secure (passkey pairing, bonded, own key) | **100.0000 %** (8340/8340 frames) | 0 | 266 / 481 / 492 ms | same |
| run 3 (published, 2026-10-20) | 100.0000 % (8410/8410) | 0 | 259 / 478 ms | untouched |

  Pairing adds no measurable delivery or latency cost in this setup (differences of 1-2 ms are inside run-to-run noise; single runs, no repeats). Stage medians are unchanged (inject to gateway 472 ms of it is the 10-frame batching at 20 Hz). The board was left on the SECURE build and the Mac holds its bond; moving back to the default build needs "Forget This Device" first.

- **State left on the board (superseded by the end-to-end follow-up above):** see that paragraph. Secure build: `EHG_MCUBOOT_KEY=... firmware/build.sh --secure flash`, then type the passkey from the UART log once. Default build: `firmware/build.sh flash` after forgetting the device on the Mac.
- **Not done / not verified:** iPhone pairing (no app); OTA with the new key (not tried); the "Forget This Device" dependence is a usability cost, documented in troubleshooting.md; `BT_SMP_SC_ONLY` forcing level 4 on macOS is now observed (level 4 reached).

## 2026-11-04: automated review of the review fixes: poison messages (2 findings), both valid, fixed

- **Bridge (HIGH):** my new restart-on-failure would have turned one poison message into a crash loop: `json.loads` on absurd nesting raises `RecursionError` (not a `ValueError`), which escaped the router and was treated as a publish failure; an oversized payload would have been refused by the stream on every redelivery. Now only a failure of the publish itself raises (and restarts); anything that cannot be routed goes to `EHG.BAD.unroutable`, an oversized payload is replaced by a short marker under `EHG.BAD.too_large`, both are acknowledged, and the broker refuses packets above 256 KiB (`max_packet_size`, checked with the real Mosquitto 2.1.2).
  A deterministic PUBLISH failure (for example the stream missing) would still restart the bridge in a loop; that is a configuration fault and the loop is the visible signal (EhgTargetDown), so no per-message restart counter was added.
- **Writer (MEDIUM):** unlimited deliveries are kept on purpose (a finite limit loses the stream's messages to the consumer after a short outage), but the review is right that a wrongly classified failure would then stall everything: a `record_id` with a lone surrogate would have raised `UnicodeEncodeError`, which was treated as a database outage and naked for ever. Validation now rejects a record id that is not valid text or holds a NUL, and a text-encoding failure in the store counts as bad data (terminated), not as an outage. A schema or permission error is still treated as an outage on purpose (terminating would lose messages when a migration has not run).
- **Tests:** deep nesting, oversize, a surprise inside the router, a publish failure still raising, the broker limit; record-id validation and the store's classification.

## 2026-11-04: independent first-start review (a fresh agent, never shown my conclusions): 12 findings, 11 acted on

- **Confirmed by me against the code or the installed library, fixed, tested:** (1) the triage service would have crashed at start-up with `UnboundLocalError` (my own mistake: the SLI gauges were used before they were created); the wiring is now in a function, `build()`, that a test can run. (2) A lambda as NATS subscription callback: nats-py refuses it ("must use coroutine"), so triage would have failed there too (test scans the repository for it).
  (3) `update.sh` could not create `/run/ehg` as the unprivileged user (a tmpfiles rule is now in cloud-init, and the failure has its own message). (4) `update.sh` ignored failures: in `deploy_current || rc=$?` POSIX shells skip `set -e`, so a failed `build` left no containers and the script still printed "all services are up"; now every step returns its status and an empty container list is not healthy (run for real with stub `docker` and `sops`, and mutation-checked).
  (5) With `max_deliver=5` a database outage of about 30 s ended the writers' chance to see the stream's messages (they stayed in the stream, unseen); the store writers now use unlimited deliveries, and messages a writer `term()`s are counted too (second advisory subscription). (6) The documented backup command used `docker compose exec`, which interpolates the whole compose file and needs the secrets; now plain `docker exec -i -u postgres ehg-postgres-1`.
  (7) Grafana: healthcheck path under the sub-path, and a scrape target that could never be up (it would have paged for ever): the job is gone. (8) A failed hand-over in the bridge left MQTT messages unacknowledged inside a live connection, where Mosquitto does not resend; the bridge now restarts itself (tested). (10) Postgres healthcheck over TCP so the init-time server does not count. (11) Mosquitto healthcheck falls back for a busybox `nc` without `-z`. (12) The runbook checklist now lists all seven certificate files.
- **Not settled (needs a real Keycloak):** (9) whether importing a realm with its own `clientScopes` and `authenticationFlows` drops the built-in ones; a check procedure is in `deploy/keycloak/README.md`.
- **Said to be fine by the review:** every nats-py and paho-mqtt call against the installed signatures, migrations 0001 and 0002, and the TimescaleDB calls of 0003 (not run on TimescaleDB), Caddyfile, nats.conf, Loki, Alloy, Alertmanager, the rules and the dashboard. That is a reading, not a run.

## 2026-11-04: integration run with everything that can run here

- `EHG_INTEGRATION=1` (real Mosquitto 2.1.2, Terraform validate against the real Oracle provider schema, `swift test`, real OpenSSL, throwaway PostgreSQL 14): **777 passed, 3 skipped** (Caddy not installed; no TimescaleDB; shellcheck not installed). The one failure was my own HAPI test, which treated "the docker command exists" as "Docker is running"; it now asks `docker info` and skips without a daemon (it never starts Docker).
- Never run anywhere: anything that needs a Docker daemon, NATS, TimescaleDB, Keycloak, step-ca, HAPI, Caddy, Prometheus, Grafana, Loki, Alloy, restic, shellcheck.

## 2026-11-04: `.dockerignore` for the repository-root build context

- **Added:** `.dockerignore` (keys, certificates, `.env`, `backend/pki`, `deploy/secrets`, data, the git history and local tooling are not sent to the Docker builder), and a test that it lists those and does not exclude anything the Dockerfile copies. `CLAUDE.md` is listed in `.gitignore` in this repository, so my edit to it (three wire-format copies) is local and not in any commit.

## 2026-11-04: front-door review by reading (CSP vs Keycloak and Grafana; Grafana sub-path), fixed

- **Found (never run, from knowledge of how the products behave, so not verified):** (1) the Caddyfile put one strict Content-Security-Policy on EVERY response; Caddy's `header` replaces what the upstream sent, so Keycloak's sign-in pages and Grafana (which need inline scripts and styles and send their own policy) would very probably have broken. The policy is now a snippet applied only to what the triage service serves (the page, `/api`, `/fhir`).
  (2) Grafana was routed at `/grafana` but not told it lives on a sub-path (`GF_SERVER_ROOT_URL`, `GF_SERVER_SERVE_FROM_SUB_PATH`), which usually gives redirect loops or 404s; both are now set. (3) The Caddyfile comment said Grafana signs in through Keycloak: it does not (no OAuth settings exist); the comment now says it uses its own admin login.
- **Reviewed, no change needed:** Loki (schema v13, tsdb, retention with the compactor and a delete-request store) and Alloy (journal relabel keeps only entries with a container name) match the documented option names; both still unrun.
- **Tests:** the CSP placement is checked block by block (only the three triage-bound handles import the snippet); Grafana's sub-path settings and the honest sign-in wording are checked.

## 2026-11-04: another first-start failure found by reading (TimescaleDB extension permission), fixed

- **Found:** migration 0003 runs as the NON-superuser role `ehg` and starts with `CREATE EXTENSION timescaledb`, which needs a superuser (the extension is not marked trusted, to my knowledge of TimescaleDB; not verified on a server). The first real start would have stopped there unless the image happened to pre-create it.
- **Fixed:** `deploy/postgres/10-init-databases.sh` (runs as the superuser) now creates the extension inside database `ehg`; on a server without TimescaleDB it only prints a notice and migration 0003 still fails loudly. Run for real against a throwaway PostgreSQL 14 in `tests/test_backup_scripts.py` (five roles, three databases, owner `ehg`, a password with a quote in it, the notice path), plus a structural test.
- **Reviewed and found consistent with TimescaleDB's rules (from memory, not run):** every primary key of the three hypertables contains `time`; continuous-aggregate windows span at least two buckets; the aggregates are created `WITH NO DATA` inside `autocommit_block`; `remove_*_policy(..., if_exists => TRUE)` signatures.

## 2026-11-04: the service image checked for completeness (clean environment and a static guard)

- **Done:** installed exactly the Dockerfile's packages into a fresh Python 3.13 environment, laid the repository files out as the image copies them, and imported every service module (bridge, ts-writer, triage, migrate, enrolment service, FHIR sync, facade): all import. `eval`'s InfluxDB store is not in this image by design (the sync worker asks for the timescale store only).
  The paho-mqtt 2.x calls in the bridge were compared with the installed signatures: they match.
- **Guard:** `tests/test_platform_compose.py` now follows the imports of every service entry point and fails if a module is not copied by the Dockerfile or a third-party package is not installed (mutation-checked: removing the `tools/fhir_export.py` copy or `httpx` makes it fail).
- **Still not checked:** the container actually building (no Docker), and any run against NATS, TimescaleDB, Keycloak or step-ca.

## 2026-11-04: a start-up bug found by reading the never-run NATS code (fixed, with a guard test)

- **Found:** the triage service's consumer filtered on `EHG.>` but the stream listed only `EHG.PKT.>`, `EHG.GW.>` and `EHG.BAD.>`. JetStream refuses a consumer whose filter is not inside the stream's subjects (to my recollection of the server's rule: **not verified against a real server**, none is installed), so triage would have failed at start-up on the real stack. Nothing could catch it before because no NATS server has run.
- **Fixed:** the stream has one subject, `EHG.>` (harmless if my recollection is wrong); `tests/test_bridge.py` now implements the subset rule and checks every consumer filter found in the code (it failed before the fix with the exact message). All other nats-py calls in the services were compared with the installed library's real signatures (`add_stream`, `pull_subscribe`, `fetch`, `consumer_info`, `ack`, `nak(delay=)`, `term`, `publish(headers=, timeout=)`, `connect(name=, max_reconnect_attempts=)`): they match.
- **Lesson recorded:** the `run()` functions of the services are the least tested code in the repository; the first real start will find more.

## 2026-11-04: Swift GapTracker and ClockMap (phone-free part of Unit 15, continued)

- **Added:** `ios/EHGWire/Sources/EHGWire/Tracking.swift` (line-for-line ports of `gateway/tracking.py`), a `tracking` section in the golden vectors (10 gap cases and 4 clock cases evaluated by the Python classes, including a stream restart, uint32 wrap-around of sequence numbers and of the tick counter), 3 more Swift tests and 3 Python guards.
- **Tested:** `swift test` 9 of 9 pass (command line only): every `observe()` return value and final state, and every host time, equal Python's (to 1e-9 s).
- **Shared limit, on purpose:** a huge forward jump in the sequence returns a huge list of missing numbers in both languages (capping it would break "every loss is identified" after a long outage); the app must cap what it asks the board to re-send (the board keeps 256 packets). Noted in the Swift header.
- **NOT done:** RESEND scheduling (1 s retry, at most 5 tries) lives in `gateway/gateway.py`'s Node class and is not ported; the app, CoreBluetooth and MQTT client are not written.

## 2026-11-04: enrolment service entry point and CRL delivery to the broker (written, tested; never run against step-ca, Keycloak or a broker container)

- **Added:** `backend/enroll/service.py` (starts the enrolment API from environment variables; names every missing variable, refuses a malformed root fingerprint or a key file others can read; `/crl` is a 502 when the CA fails, never an empty list), `deploy/crl-sync.sh` plus a cron entry in cloud-init (every 5 minutes), the script added to the CI shellcheck job.
- **Tested:** 10 + 8 tests, the CRL ones with real OpenSSL lists from `tools/pki.sh`: install in place (same inode), `.prev` copy, signal only after a verified install, forged list from another CA refused, older list refused, expired list refused, malformed or unreachable answer changes nothing, failing signal reported.
- **Finding:** `openssl crl -verify` prints "verify failure" for a foreign list but exits 0; the first version of the script would have installed it. Fixed by checking the printed result (test `test_a_list_signed_by_another_ca_is_refused`).
- **NOT done:** the enrolment API and step-ca are still not in the cloud compose file (step-ca is initialised by hand; its CRL endpoint path and format are assumptions); `crl-sync.sh` has never run against a live Mosquitto container or the real endpoint.

## 2026-11-04: Unit 11b, the cloud compose file completed as far as it can be without the cloud (never started)

- **Added to `deploy/compose/docker-compose.cloud.yml`:** `migrate` (one-shot), `bridge`, `ts-writer`, `triage` (one image built on the VM from `backend/platform.Dockerfile`, whose base is now pinned by digest), `keycloak` 26.8.1 and `hapi` v7.6.0 (digests read from the registry API, arm64 present). 18 services; `docker compose config` resolves them.
  The triage service now serves the FHIR facade when `FHIR_BASE_URL` is set (it was not wired before), and `update.sh` builds the local image and accepts an exited one-shot job (`ExitCode` 0) while still failing a crashed service (tested with the real jq filter). Four new secrets are in the example (`KEYCLOAK_ADMIN_PASSWORD` and three client secrets); passwords must now be hex.
- **Tested:** 10 new structural tests (shared built image, no privileges, migrations before writers, one role per service, real sign-in only, the bridge mounts only its own certificate, every realm placeholder is supplied to Keycloak, every required secret is in the example, the update order, the jq filter).
- **Honest gaps:** Keycloak and HAPI have NO health check on purpose (the right probes are unverified and a wrong one would roll back every release); step-ca, the enrolment API (it has no service entry point) and the FHIR sync worker are still not in the file; memory: about 3.7 GB for Keycloak + HAPI + the Python services counted against the 9.5 GB ceiling (limits, not measurements);
  the Python services' read-only root and dropped capabilities are untested; nothing has been started.

## 2026-11-04: Unit 15 (the part that needs no phone): Swift wire-format package and golden vectors

- **Added:** `tools/gen_packet_vectors.py` and `tests/fixtures/packet_vectors.json` (8 valid and 7 invalid packets from `gateway/packet.py`: 16- and 24-bit, one channel, no frames, the retransmission flag, extreme sample values and header fields, every refusal reason), the Swift package `ios/EHGWire` (decoder, encoder, CRC-16),
  `tests/test_ios_wire_format.py` (vectors are current; Python parses them; Swift constants, offsets and check order match Python; opt-in run of `swift test`), and the wire-format rule in CLAUDE.md now names three copies.
- **Tested:** `swift test` (command-line SwiftPM, Swift 6.4, 6 tests, all pass): every vector decodes to identical values and re-encodes to identical bytes; every bad vector is refused for the same reason; every truncation and every single-bit flip of a valid packet is refused; CRC check value 0x29B1.
  No Xcode project was opened, no simulator or phone was used.
- **NOT done (needs Byron, an Apple ID and the iPhone):** the app target `ios/EHGGateway` (CoreBluetooth, MQTT client, background behaviour), gap tracking and RESEND in Swift, the feasibility spike and `docs/ios-gateway-feasibility.md`, free-signing limits, any cellular measurement. (Superseded: `GapTracker` and `ClockMap` were ported later the same day, see the entry above; RESEND scheduling is still not.)

## 2026-11-04: Unit 12, observability, SLOs, backups and the restore drill (written and unit-tested; never evaluated by Prometheus, Grafana or on the VM)

- **Added:** `backend/triage/slis.py` (fleet gauges and consumer backlog), `deploy/observability/rules/slo.yml` + `alerts.yml` (6 recording rules, 12 alerts), `prometheus.yml` (rule files, Alertmanager, triage/bridge/ts-writer jobs), a read-only provisioned dashboard `pipeline-overview.json`,
  `alertmanager.ntfy.example.yml` (not loaded), `deploy/backup/backup.sh` + `restore_drill.sh`, `docs/slo.md`, `docs/dr.md`, an amendment to decision 0017.
- **Tested** (23 tests): every metric a rule or panel uses is defined in this repository (a rename cannot silently blind an alert); alerts hold no node ids or secrets; a real dump and restore on a second clean PostgreSQL with the audit chain verified;
  checksum guard; never-overwrite; failed backup keeps the old freshness timestamp; rotation only touches its own directories; NaN (unknown) is never reported as zero.
- **Defect found and fixed:** `ehg_triage_bad_messages` and `ehg_triage_dead_letters` were incremented but never exposed (the API served a different registry); they and the new gauges are now served from the service's registry. The writer also reports its backlog.
- **Honest changes of plan:** pgBackRest (point-in-time recovery) was NOT built; a nightly `pg_dump` meets the 24 h RPO (decision 0017 amended). No Mosquitto or NATS exporter. The NATS stream is not backed up (stated in dr.md).
- **NOT done / not verified:** promtool, a real Prometheus, Grafana and Alertmanager; restic; TimescaleDB restore calls; any failure drill (all "observed" cells in dr.md are empty); the nightly schedule; the bridge, ts-writer and triage services are still not in the cloud compose file, so their scrape targets are down until that is added.

## 2026-11-04: Unit 10, the triage page (tried in a browser against a stub API; never against Keycloak or a real database)

- **Added:** `viewer/triage.html|css|js` (vanilla, no CDN, CSP-clean), served by the triage API at `/`, `/triage.js`, `/triage.css`, plus a public `/ui-config.json` (mode, banner, purpose, the sign-in server or the dev users); the platform image copies the three files; `tests/test_triage_page.py` (10 static and serving tests).
- **Tried by hand** (built-in browser, stub store): dev sign-in, worklist order and bands, sparklines, trend charts with the "not fully covered" notice, alerts tab with role-dependent buttons, escalate dialog with a required note, history, EHR panel, the consent refusal shown as a message. Fixed on the way: the native required-field bubble hid our own message (`novalidate` on the form).
- **Bug found by this work, fixed:** the Keycloak realm (Unit 7) had no audience mapper, so no `triage-ui` token could have passed the API's audience check. Mappers added and tested (not verified on a real Keycloak).
- **Automated review of Unit 9, 1 finding, fixed:** `/fhir/*` now gets `Cache-Control: no-store`.
- **NOT done:** admin screens; refresh tokens; any run against a real Keycloak; a Lighthouse or screen-reader pass. (Phone width, 375 px, was checked later the same day in the built-in browser: the first layout overflowed, so the worklist now hides the open-alerts, rules and waiting-time columns below 720 px and fits without sideways scrolling.)

## 2026-11-04: Unit 9, FHIR sync worker and read-only facade (tested against a FAKE FHIR server; HAPI never started)

- **Added:** `backend/fhir_sync/worker.py` (reuses `tools.fhir_export.bundle` unchanged; one conditional transaction per COMPLETE 600-frame chunk: Device POST with If-None-Exist, Observation conditional PUT by identifier), `backend/triage/fhir_api.py` (`/fhir/metadata|Observation|Device`, wired in by `create_app(fhir_upstream=...)`),
  `deploy/hapi/application.yaml` (key names read from the starter's file at tag image/v7.6.0), platform image gains httpx and the two files the worker imports, `fhir.resources==8.3.0` (dev) and `tests/test_hapi_integration.py` (opt-in, needs Docker).
- **Tested (12 + 9 tests):** repeat/restart/two-worker idempotence, late recovered frames replace rather than duplicate, raw counts and chunk times, consent False/None/withdrawn sends nothing, server failure is retried not dropped, an entry-level failure inside a 200 is an error, R5 model validation of our resources; facade roles, strict consent, mandatory node scoping, GET-only, audit, a lying upstream cannot leak another node, 502 without details.
- **NOT done / not verified:** any real HAPI server; HAPI and the sync worker are NOT yet in `deploy/compose/docker-compose.cloud.yml` (nor are the bridge, ts-writer, triage, Keycloak and step-ca: completing that file is still to do); the worker needs the converted `.npz` folder (gain, baseline) mounted, which is gitignored data; the `Consent` and `AuditEvent` FHIR resources of the plan are not written (consent is enforced from the registry).

## 2026-11-03: Unit 14, CI and supply chain (minimal, honest)

- **Added:** `.github/workflows/security.yml` (shellcheck; the deployment guard tests; Linux only, read-only token), `.github/dependabot.yml` (uv, actions, Dockerfile bases, Terraform), `docs/supply-chain.md`, `tests/test_ci_config.py`. Only the two actions already vetted in `tests.yml` are used.
- **NOT wired, on purpose:** Trivy, Syft, cosign, gitleaks (versions unverifiable from here) and CodeQL (private repository); `docs/supply-chain.md` has the local commands. The workflows have never run on GitHub. Dockerfile base images are tag-pinned only, not digest-pinned.

## 2026-11-03: Unit 11, the Caddyfile (front door); never run

- **Added:** the real `deploy/caddy/Caddyfile` (routes `/`, `/api`, `/fhir` to the triage service; `/auth` to Keycloak; `/grafana` to Grafana; `/enroll-api` to the enrolment API; `/metrics` and probe paths answered 404; HSTS, nosniff, frame denial, strict CSP, no Server header; 1 MB / 16 KB body limits) and `tests/test_caddy_config.py` (28 structural tests; `caddy validate` runs only where Caddy is installed).
- **Automated review, 2 findings, fixed:** the 404 for `/metrics` and probes was a bare `respond` that Caddy runs AFTER `handle` blocks, so it never executed: it is now the first `handle` block and also covers prefixed paths; Keycloak is reachable only at `/auth/realms/ehg/*` and `/auth/resources/*` (admin console and master realm answer 404; use an SSH tunnel for the console).
- **Honest gap:** stock Caddy has no rate-limit directive; rate limiting lives in the services (enrolment 5 per hour per login; Keycloak brute-force protection). The upstream services (triage, Keycloak, enrolment, FHIR) are not in the cloud compose file yet, so those routes would answer 502 until they are added.

## 2026-11-03: Unit 7, identity and certificate enrolment (code; not run against Keycloak or step-ca) (decision [0013](decisions/0013-keycloak-and-certificate-lifecycle.md))

- **Added:** `deploy/keycloak/realm-ehg.json` (+README), `backend/enroll/` (`app.py`, `csr.py`, `stepca_client.py`, `cli.py`), `deploy/step-ca/templates/ehg-gateway.tpl` (+README).
- **Tested** (50 + 11 tests): CSR vetting (EC P-256 only; RSA, P-384, garbage, oversize, wildcard names, CA requests, a corrupted key all refused with a reason); the CN on the certificate comes from the login, never from the request; the provisioned/service identities (`ehg-gw-01`, `ehg-bridge`, ...) cannot be claimed by a login; revoked gateways cannot enrol and the CA is not even asked;
  seven ways a CA can go wrong (error, garbage, wrong name, server-auth usage, 90-day life, someone else's key, CA flag) each become a 502 with nothing recorded; 5-per-hour rate limit; admin-only revocation that reaches the CA and reports a CA failure instead of hiding it; the one-time-token claims; the CLI writes a private key mode 600 and never takes the token as an argument.
- **Bug found by a test:** a CSR with a corrupted key parsed but raised `ValueError` when its signature was checked, which would have been a 500 instead of a refusal.
- **Automated reviews (two rounds on `stream.py` / the realm):** TOTP did not cover admins (admin now contains operator as a composite role); a near-zero sampling rate could make a node invisible (window, floor and fallback fixed); both in [security.md](security.md).
- **Automated review of the enrolment API, 2 findings, fixed:** revocation now covers every live certificate (a `registry.gateway_cert` table), and the `ehg-` name prefix is reserved ([security.md](security.md)).
- **NOT done / not verified:** everything against a real step-ca (template variables, provisioner claims, revocation/CRL support of the pinned version, the OIDC-provisioner shortcut); the realm import and the conditional-OTP flow on a real Keycloak (including whether the role condition follows composite roles); the host-side job that copies `GET /crl` to the broker and signals Mosquitto (not built); the registry-to-broker ACL sync (a new gateway still needs its two ACL lines by hand).

## 2026-11-03: Unit 8, triage service (rules, audit chain, alert state, JWT auth, API, stream monitor) (decision [0011](decisions/0011-triage-service-pipeline-health-not-clinical.md)); not run against NATS or HAPI

- **Added:** `backend/triage/rules.py` (rules, hysteresis, score, worklist: pure), `audit.py` (hash chain, `--verify`), `state.py` (lifecycle), `registry.py`, `app.py` (FastAPI), `stream.py` (NodeMonitor, FleetMonitor, Engine, service entry point), `backend/jwtauth.py`; migration 0002 (triage + registry schemas, restricted `ehg_triage` role, append-only triggers).
- **Tested on a real PostgreSQL 14 (local, no Docker):** the audit chain detects edited, deleted and reordered rows and says plainly that deleting the newest rows is caught only by the off-VM head hash; concurrent writers form one gap-free chain; the application role cannot UPDATE, DELETE, TRUNCATE or alter the log; the alert lifecycle, required notes, auto-resolve (never of an escalated alert) and one-active-alert-per-rule; every API route against every role; JWT attacks (expired, wrong audience/issuer, `alg=none`, HS256-with-the-public-key built by hand, tampered, other key); degraded database as a clean 503; no secrets or traces in responses.
- **Bugs found by the tests:** the first packet of any node looked like a "sequence restart" (GapTracker counts it as stream 1), which would have raised SEQ_RESTART on every new node; a `while` condition with the wrong precedence would have emptied the latency window on every snapshot (caught on re-reading, before it ran); several of my own test feeders produced gaps.
- **Second review round on `stream.py`** (unvalidated packets, unbounded frame window, node-count cap, one bad node stopping the loop; plus a 2-billion-entry sequence-jump attack): fixed with tests ([security.md](security.md)).
- **Automated security review of the Unit 8 commit:** consent bypass through the worklist, alert list, alert detail and workflow actions: fixed and tested (see [security.md](security.md)).
- **NOT done / not verified:** the stream service against a real NATS server; `/api/audit` FHIR mirroring (Unit 9); OIDC against a real Keycloak (JWKS fetch is untested); the triage service in the cloud compose (Unit 11, needs `AUTH_MODE=oidc` because dev mode refuses a non-loopback bind); dead-letter surfacing is a metric only (no alert rule for it).
- Run locally (dev mode, loopback only): `AUTH_MODE=dev EHG_TRIAGE_DSN="host=localhost port=5433 dbname=ehg user=ehg_triage password=..." NATS_URL=nats://localhost:4222 uv run python -m backend.triage.stream`, then `curl -H "Authorization: Bearer dev-admin" localhost:8780/api/worklist`.

## 2026-11-03: Unit 6, data platform code (bridge, writer, migrations, parity) (decision [0012](decisions/0012-timescaledb-nats-data-platform.md)); parity proof NOT run

- **Scope decision:** Byron asked for every unit that does not need the cloud; this is the first. The checkpoint ordering (Checkpoint 3 before Unit 6) is superseded by that instruction for the CODE; putting it on the VM still waits for the VM.
- **Added:** `backend/bus.py`, `backend/mqtt_env.py`, `backend/bridge/bridge.py`, `backend/ts_writer/writer.py`, `backend/db/` (Alembic: 0001 plain tables, 0002 TimescaleDB), `eval/backend_client.py` rewritten for two stores (imports without `.env`), `--backend` on integrity/latency/fhir_export, `eval/parity.py`,
  `backend/ingest/ingest.py` gains `INGEST_SOURCE=jetstream` (the influx-writer), `backend/platform.Dockerfile` and `backend/docker-compose.platform.yml` (profile `platform`; default stack unchanged, checked with `docker compose config`), `tests/conftest.py` (throwaway local PostgreSQL).
- **Tests added:** bridge ack ordering and crash-redelivery with a fake JetStream, writer rows/idempotency/atomicity on a real PostgreSQL 14, outage handling, loud failure of the TimescaleDB revision without the extension, backend_client shapes, parity checks for every kind of difference.
- **Mistake caught:** I wrote a full-length image digest for NATS in the platform override from a 12-character prefix, i.e. an invented value; replaced with the real registry digest before committing. A neighbouring test claimed more than it checked and was rewritten with exact expectations.
- **Automated security review of the Unit 6 commit, 4 findings (poison messages in the writer, per-message data errors, subject injection in the bridge, the Influx writer loop), all fixed with tests first** (details in [security.md](security.md)). Migrations are now 0001 plain tables, 0002 triage and registry (needs the `ehg_triage` role), 0003 TimescaleDB.
- **NOT done / not verified:** anything against a real NATS server or TimescaleDB; the parity proof and the data-platform memory on the VM (Checkpoint 4); the `timescaledb-ha` init script; the platform images were never built.

## 2026-11-03: Unit 5, Oracle runbook (written; NOT run: it is yours to execute) ([deployment.md](deployment.md))

- **Decisions recorded** (asked one at a time): hostname **DuckDNS + Caddy**; home region **Sydney `ap-sydney-1`**; repository **stays private** (so the VM clones over SSH with a read-only deploy key). **The Oracle card question was deferred by Byron ("skip this step first") and is still PENDING:** the runbook says not to start sign-up until it is decided, and nothing assumes consent.
- **Added:** [`docs/deployment.md`](deployment.md) (15 steps, the expected `terraform plan` of 14 resources, negative checks, a list of what could not be checked, an empty log), [`deploy/runbook/checklist.md`](../deploy/runbook/checklist.md) and [`log-template.md`](../deploy/runbook/log-template.md).
  Changes the private repo needed: cloud-init writes `/opt/ehg/.gitconfig` (deploy key, strict host keys); `update.sh --services "caddy mosquitto"` starts only the edge subset (tested; the jq health filter was exercised on sample output); the tfvars example shows the SSH repo URL.
  Checked on the way: GitHub's `api.github.com/meta` publishes SSH host keys and SHA256 fingerprints (used for `known_hosts`, with the fingerprint to be compared against GitHub's documentation).
- **Mistakes caught here:** `sops --encrypt secrets.yaml` would not match the creation rule in `.sops.yaml` (its regex expects `*.sops.yaml`), so the instructions now pass the recipient with `--age` (untested: sops is not installed); a plan file called `tfplan` would not have been gitignored, so plans are named `ehg.tfplan` (`*.tfplan` is ignored and tested);
  a test now fails if the runbook's resource count, its step 10 or its recorded decisions drift from the code.
- **Two more automated security-review findings on the earlier Unit 4 commits were fixed** (host mounts and the metadata service; host logs reaching Loki): see [security.md](security.md).
- **NOT done / not verified:** every step of the runbook; the Oracle console labels; `sops` commands; the broker reading `broker.key` in its container (the runbook gives a likely fix, untested). Nothing was pushed to GitHub; the tag and branch must be pushed by Byron.
- Tests: 215 pass, 10 skipped by default.

## 2026-11-03: Unit 4, infrastructure as code (written and validated, NOT applied; [deploy/README.md](../deploy/README.md))

- **Verified first:** Oracle's Always Free page matches the prompt's numbers (2 OCPU / 12 GB A1, 200 GB volumes, 10 TB egress, 20 GB object storage, idle rule); see [decision 0010](decisions/0010-oracle-always-free-cloud-target.md) for the two differences (47 vs 50 GB minimum boot volume; the June 2026 halving is not on the page).
  All 16 candidate images have `linux/arm64` (registry API, Docker not running), **including the non-HA `timescale/timescaledb`, which resolves that unverified item**. `eclipse-mosquitto:2` is 2.1.2 (image label), so the Homebrew tests match the image.
- **Added:** `deploy/terraform` (root + 4 modules, provider `oracle/oci` pinned to 9.11.0), cloud-init template, `deploy/compose/docker-compose.cloud.yml` (12 services, every image pinned by version and digest), `deploy/update.sh`, Caddy/NATS/Postgres/observability config files, SOPS + age layout, `tests/test_deploy_assets.py`.
  Secret and state patterns added to `.gitignore`; the file-index test now covers `deploy/`. New dev dependency: python-hcl2.
- **Checked:** `terraform fmt` clean and `terraform validate` passes against the real provider schema (it caught one real bug: a literal dollar-brace in a template comment); `docker compose config` resolves, and a missing secret stops it naming the variable; only 80/443/8883 are published;
  limits plus reserved later services total under the 9.5 GB ceiling; `update.sh` refuses unsafe input and its dry run prints every step; the secret scanner has its own self-test (which found a flag bug in the scanner itself).
- **Deviations from the prompt:** the compose file is partial on purpose (Keycloak, HAPI, step-ca and the workers have no config until Units 6 to 9; their memory is reserved in a test); secrets reach containers as environment variables from a memory-only file, not Docker secrets;
  node_exporter runs as a container, not a host package; Docker comes from Ubuntu's `docker.io` package; `sops` is installed by hand (no distribution package) and the recipient key in `deploy/.sops.yaml` is a placeholder until you supply yours.
- **Automated security review of the Unit 4 commit (2026-11-03), 2 findings, both fixed after the commit:** (1) HIGH, cAdvisor (and Alloy) host mounts reached the Docker socket and every container's environment: removed all host-root/`/run`/data-root mounts, logs now go through journald, disk metrics via a cron-written textfile, `internal: true` network for everything but Caddy and Mosquitto, and a test with an explicit allowlist of host paths;
  (2) MEDIUM, legacy metadata endpoint: disabled in Terraform and blocked from containers by an iptables unit. Details and what stays open: [security.md](security.md).
- **Third review finding (MEDIUM, fixed):** Alloy would have shipped host auth/ssh/sudo journal lines to Loki; now only entries with a Docker container name are kept (relabel `keep`), Alloy drops all capabilities and is read-only. Residual: Alloy can still read the whole mounted journal.
- **NOT done / not verified:** `terraform plan/apply`; the data-volume device path; the VM-down alarm (needs Oracle's monitoring plugin); any container starting (hardening flags, TimescaleDB-HA init, Loki/Alloy configs, healthchecks); `update.sh` on a real checkout and its rollback path; `shellcheck` (not installed); real memory use.
  Terraform CLI licence text not read (BSL per the prompt). A newer HAPI (v8.0.0) exists; Unit 9 decides.
- Tests: 202 pass and 10 skipped by default; with `EHG_INTEGRATION=1`, `mosquitto` and a cached provider (`TF_PLUGIN_CACHE_DIR`), 211 pass and 1 is skipped (`shellcheck` not installed).
- Code under `deploy/` names later docs by unit ("the deployment runbook (Unit 5)") rather than by path, because `test_doc_paths_in_code_exist` rightly refuses paths to files that do not exist yet.

## 2026-11-03: outbox switched ON by default (Byron's decision at Checkpoint 2)

- **Change:** `gateway --outbox` now defaults to `gateway/outbox.db`; `--no-outbox` restores the old in-memory path (the two flags exclude each other). Changing the default exposed two traps, both fixed with tests:
  (1) `connect_async` never raises, so a broker that is not there would have been retried in silence: now `[mqtt] cannot reach the broker at host:port; retrying`;
  (2) two gateways started from the same folder would have shared one file and sent each other's rows: now a lock file refuses the second process with a clear message.
- **Cost, measured** (throwaway driver, plain local Mosquitto 2.1.2, 400 packets at 20 per second, two runs each): gateway receive to publish, median 124 us with `--no-outbox` vs 649 / 615 us with the outbox; p95 152 us vs 2.0 / 2.3 ms; worst 0.6 to 1.3 ms vs 4.2 / 5.0 ms.
  That is larger than the isolated `add()` benchmark (36 us) because the PUBACK delete in paho's thread contends with the insert. It is under 1 % of the validated H1 median (259 ms) and p95 (478 ms), but it is **in the H1 chain and not zero**. H1 with the outbox on has NOT been re-measured (needs the board and Docker).
- **Default behaviour check** (plain broker, no flags except the port): packets arrive without a `replayed` key, `t_mqtt_pub - t_gw_rx` about 0.8 ms, 0 rows left after a clean stop, file mode 0600, a second gateway from the same folder exits with code 1 and the message.
- **Consequences to remember:** the earlier invariant "default `python -m gateway.gateway` behaves exactly as today" no longer holds for the outbox (decided by Byron); H1 runs for the thesis should either use `--no-outbox` to stay comparable with the recorded numbers (259 / 478 ms) or be reported as a separate row; left-over rows replay at the next start, marked `replayed`.
- Tests: 145 pass and 8 skipped without a broker; with `EHG_INTEGRATION=1` and `mosquitto` installed, 153 pass (about 49 s).

## 2026-11-03: Unit 3, durable gateway outbox ([decision 0009](decisions/0009-gateway-durable-outbox.md)); Checkpoint 2 pending

- **Added:** [`gateway/outbox.py`](../gateway/outbox.py) (SQLite WAL, mode 0600, write first, delete on PUBACK, oldest first, at most 100 in flight, disk cap, durable-write fallback),
  `gateway --outbox PATH --outbox-max-mb --gateway-id` (retained `online`/`offline` status with a last will), `replayed` stored by ingest, `eval/latency.py --exclude-replayed`. Off by default; without the flags the publish path is the same code as before.
- **Tested:** 19 tests in [`tests/test_outbox.py`](../tests/test_outbox.py): broker down then up (in order, once), restart replay, backlog before new traffic, in-flight limit, disconnect with unacknowledged messages, stale PUBACKs, lost PUBACK duplicate (ingest writes once), disk cap, database error fallback, file mode and WAL, defaults unchanged.
  **Against a real Mosquitto 2.1.2 over TLS** (`EHG_INTEGRATION=1`): `gateway --simulate 1 --outbox`, broker stopped for 5 s mid-run and restarted: at least 5 packets piled up on disk, a persistent reader received **every seq from 0 with no gap**, the late ones marked `replayed` with a fresh `t_mqtt_pub` (the longest wait at least 4 s).
- **Overhead (throwaway benchmark, not a CI assertion; this Mac, Python 3.13):** per packet, JSON stamp alone vs `add()` (insert + send; the PUBACK delete runs in paho's thread): 3 ch x 10 frames median 5.6 us vs 36 us (p99 85 us, worst 1.2 ms);
  16 ch x 100 frames median 84 us vs 133 us (p99 304 us, worst 2.6 ms). Packets arrive every 0.5 s or slower, so this is under 0.03 % of the interval; the rare ms-scale outliers are SQLite WAL checkpoints.
- **Deviation from the prompt:** no backoff jitter (paho 2.1 has no option); documented in the ADR.
- **NOT done / not verified:** `eval/integrity.py` reporting `missing ranges: []` after the outage (needs Influx, i.e. Docker; the test checks gap-free delivery at the broker instead); the outbox on a real BLE node; behaviour with a very long outage on the real disk cap beyond the unit test; "H1 unchanged with the outbox on" is not measured (no Docker, no board).
  Tests now: 140 pass and 8 are skipped without a broker; with `EHG_INTEGRATION=1` and `mosquitto` installed, 148 pass (about 50 s).

## 2026-11-03: Unit 2, MQTT over TLS 1.3 with client certificates (optional cloud profile; [decision 0008](decisions/0008-mqtt-mtls-and-topic-acls.md))

- **Added:** [`tools/pki.sh`](../tools/pki.sh) (offline root, intermediate, broker and client certificates, revoke, CRL; ECDSA P-256), [`backend/mosquitto/mosquitto.tls.conf`](../backend/mosquitto/mosquitto.tls.conf) + [`acl`](../backend/mosquitto/acl),
  [`backend/docker-compose.cloud.yml`](../backend/docker-compose.cloud.yml) (override), `gateway --mqtt-port/--mqtt-tls/--ca/--cert/--key`, `MQTT_*` environment variables for ingest and the viewer, [`docs/security.md`](security.md), a troubleshooting section, README "cloud profile locally".
  `.gitignore` now covers `backend/pki/`, `*.key`, `*.pem`, `*.p12`. Dev dependency added: PyYAML (`uv.lock` updated).
- **Default behaviour unchanged:** `docker compose config` shows the default stack still publishes 1883 and has the same 4 services; gateway, ingest and viewer defaults are plain 1883 (tests). A half-set TLS setup exits with an error instead of falling back to plain MQTT.
- **Tested against a real Mosquitto 2.1.2** (Homebrew, `EHG_INTEGRATION=1`; the repo's config and ACL with only paths and port rewritten): readers cannot publish; a gateway cannot write another gateway's status but can write its own; no client certificate, TLS 1.2 only and a certificate from another CA are refused;
  `gateway --simulate 1 --mqtt-tls` publishes packets the viewer identity reads; a refused certificate is reported on the gateway console. Revoked certificate: refused after SIGHUP (CRL not re-read by itself). Expired CRL: refuses all clients (Python OpenSSL, not the broker).
- **Numbers:** 83 tests before, 45 new. `uv run pytest -q`: 122 passed, 6 skipped (the real-broker group). With `EHG_INTEGRATION=1` and `mosquitto` installed: 128 passed in about 29 s.
- **Automated security review of the commit (2026-11-03), 3 findings:** (1) the whole `backend/pki` directory was mounted into the broker and ingest containers, exposing the intermediate CA key: **fixed**, single files only, tested. (2) Client ids were not tied to the certificate: **reproduced** (the viewer identity kicked ingest off), **fixed** with `use_username_as_clientid true`, tested. (3) `ehg/+/pkt` lets a gateway publish for any node: **acknowledged, not fixed**; closing it needs a registry or an interface change ([security.md](security.md), Units 6 to 8). Tests are now 46 new (129 with the broker).
- **NOT done / not verified:** the compose containers were not started (Docker not running); `eval/integrity.py` over TLS (needs Influx, i.e. Docker) so the "100 % over TLS" integration step is still open; key-file readability inside the container; WebSocket on 443; carrier blocking of 8883;
  TLS overhead (plain versus TLS timing) not measured yet; `shellcheck` not run (not installed). The "default stack still the same 4 containers" claim is from `docker compose config`, not from running them.

## 2026-11-03: Unit 1, decisions and topology documents (docs only; [prompt](claude-code-prompt-thesis-b-hardening.md))

- **Added:** [decisions 0007 to 0017](decisions/0007-enterprise-evolution-keep-influx-as-reference.md) (*Accepted* by Byron at Checkpoint 1, 2026-11-03),
  [`production-topology.md`](production-topology.md) (architecture, four tiers, boundaries, six-hop chain, capability checklist) and [`licences.md`](licences.md).
  No code, firmware, compose or cloud change. The default local stack is untouched.
- **Verified locally:** Zephyr and MCUboot are Apache-2.0; Python dependency licences read from installed metadata. **Prompt discrepancy:** it lists the
  nRF Connect SDK as Apache-2.0, but `~/ncs/v3.4.1/nrf/LICENSE` is `LicenseRef-Nordic-5-Clause`; the repository wins and `licences.md` says so.
- **Not verified (carried into the units that use them):** Oracle limits and A1 capacity, component licences other than the above, image arm64 support,
  Mosquitto `crlfile` and WebSocket behaviour, JetStream duplicate window, HAPI R5 SampledData, step-ca and Keycloak behaviour, iOS free-team capabilities, carrier port blocking.
- `uv run pytest -q`: 83 passed (link check and ADR-section check cover the new files).

## 2026-11-03: baseline before enterprise work ([claude-code-prompt-thesis-b-hardening.md](claude-code-prompt-thesis-b-hardening.md), Unit 0)

- **Frozen:** tag `baseline-pre-enterprise` on `9ba4ad7`; work happens on `feat/enterprise-topology` (never `main`, not pushed).
  `uv run pytest -q`: 81 passed on the baseline, 83 after adding [`tests/test_handoff.py`](../tests/test_handoff.py).
- **Resume pin:** [`handoff.md`](handoff.md) is the session-limit pin (overwritten each session, never a history).
- **No-hardware baseline run (`--simulate 1 --sim-loss 0.01`, `eval/integrity.py`, `eval/latency.py`) NOT done:** the Docker daemon
  was not running and Docker Desktop is not started or restarted from here (it hosts another project). Run it before Unit 2 merges.
- **6 h TPEHG soak (H2) and first H3 (≥ 8 boards): not done, skipped for now** (Byron's decision, 2026-11-03). Consequence: there is
  no soak on the untouched baseline commit, so later units could contaminate a soak run on this branch. Unit 16 repeats both on the cloud chain.

## 2026-10-26: documentation made correct, traceable and checked ([plans/codebase-docs-plan.md](plans/codebase-docs-plan.md))

- **Stale facts fixed:** `main.c` header gave the old serial speed (it is 460800), the codebase guide said the firmware never holds
  a recording, the design spec gave wire v1; README/guide file lists completed. Historical docs now carry a banner.
- **Drift is now a test failure:** [`tests/test_docs.py`](../tests/test_docs.py) checks the two wire-format copies (version,
  header layout, flags), GATT UUIDs and commands, every Markdown link, `docs/…` paths in code comments, the file index,
  one baud rate everywhere, and that every CLI's `--help` prints its header. CI: [`.github/workflows/tests.yml`](../.github/workflows/tests.yml) (not yet run on GitHub).
- **New:** README "Reproducing the results" (command + expected output per hypothesis, incl. a no-hardware row),
  [troubleshooting.md](troubleshooting.md), [decisions/](decisions/) (6 records), `CITATION.cff`.
- **Open:** licence (asking the supervisor); `xiao_blinky` symlink no longer tracked (still on disk).

## 2026-10-22: choose built-in or streamed recording at build time ([plans/baked-or-streamed-plan.md](plans/baked-or-streamed-plan.md))

- `firmware/build.sh flash --record <npz> [--seconds S]` compiles the recording in (`tools/make_replay.py` restored,
  `CONFIG_EHG_BAKED_RECORD`): the board runs **stand-alone**. A plain `build.sh flash` is the serial feed as before.
- `tools/feed.py --sync-only --node EHG-93C0`: clock pings only, so built-in runs keep the exact H1 offset.
- Built-in mode refuses CONFIG/DATA/FAULT (`@ERR built-in record mode`), so no fault injection in that mode.

| Run (DK EHG-93C0, TPEHG tpehg1007) | Delivery | Bit-exact | H1 median / p95 | Notes |
|---|---|---|---|---|
| built-in, 4 min, gateway + `--sync-only` | 100 % (4780/4780) | 0 | 268 / 485 ms | sync ±1.8 ms; BLE envelope would read 11.3 ms low |
| serial feed, 3 min (after switching back) | 100 % (3620/3620) | 0 | – | `starved=0`, `rx_bad=0` |

Flash: built-in app 377 KB (79 % of the 464 KB slot) vs 167 KB streamed.

## 2026-10-20: realistic injection, continuous acquisition, fault injection (plan: [plans/realistic-injection-plan.md](plans/realistic-injection-plan.md))

What changed (details: [firmware.md](firmware.md), [interface-spec.md](interface-spec.md) §3/§3b, [fault-injection.md](fault-injection.md)):
- **The recording is streamed, not built in.** [`tools/feed.py`](../tools/feed.py) sends it over the J-Link serial port
  (460800 baud, RTS/CTS) about 2 s ahead; the board's timer still decides each sampling instant (H1 start unchanged).
  `make_replay.py` / `replay_data.h` are gone. Any record, any length, 16 ch × 200 Hz possible.
- **Wire v2:** CRC-16 trailer on every BLE packet. **Acquisition continues through disconnects**; the gateway keeps one
  gap tracker per board and re-asks for lost re-sends (1 s, up to 5 times).
- **H1 clock offset from serial pings** (`eval/results/sync_<node>.csv`, ±1.8 ms) instead of the BLE lower envelope.
- **Fault injection** (`feed.py --faults`): Gilbert burst loss, bit corruption, forced disconnects; seeded, logged.
- **Button 1** starts/stops; LED1 connected, LED2 sampling. **MCUboot + MCUmgr/SMP** for updates over Bluetooth.

Hardware results (DK EHG-93C0, macOS gateway, Docker backend, TPEHG tpehg1007, 7 min each):

| Run | Faults | Delivery | Bit-exact | H1 median / p95 | Notes |
|---|---|---|---|---|---|
| Unit 3 (no backend) | none | 474/474 packets first frame checked | 0 mismatches | – | `starved=0`, `rx_bad=0`, max_inject_late 397 µs |
| run 2 | drop 5 % (burst 3), corrupt 1 %, disc 120 s | 98.76 % | 0 | 257 / 474 ms | exposed: serial line errors (fixed: RTS/CTS) and single-shot resend requests (fixed: retries) |
| run 3 | same, seed 3 | **100.0000 %** (8410/8410) | **0** | **259 / 478 ms** | 69 drops, 8 corruptions, 4 disconnects all recovered; `streams=1`; sync ±1.8 ms; envelope bias measured 0.1–3 ms |

Engineering notes from this session:
- **1 Mbaud loses board→laptop bytes on the J-Link VCOM** (≈0.1 % of log lines); 460800 is clean without Bluetooth.
  With Bluetooth busy a few log lines still lose bytes, so machine lines (`@…`) carry a CRC.
- **Zephyr deferred logging batches lines for up to 1 s** (`LOG_PROCESS_THREAD_SLEEP_MS`); `LOG_PROCESS_TRIGGER_THRESHOLD=1`
  makes each line go out at once. Without it the feeder's queue reports arrived in late pairs and the board ran dry.
- **Without RTS/CTS the UART dropped ~1 serial message a minute** while Bluetooth was busy (UART line errors).
- **pyserial `read(4096)` with a timeout waits the full timeout**: it put 50 ms into every clock ping; `read(in_waiting)` fixed it (RTT 3.5 ms).
- `nrfutil mcu-manager ble` needs a second Nordic board as its radio; the phone app is the one-board update route.
- A reset makes the serial line emit a burst of junk bytes; harmless (only CRC-checked `@` lines are acted on).

Not verified on hardware: pressing Button 1 / the LEDs (no one at the board); an actual over-the-air image upload
(the update service answered an image-list request: slot 0, confirmed, active).

## 2026-09-29: autonomous setup session (while Byron was away)

### Gantt status

| Phase | Task | Status |
|---|---|---|
| 0a | Order DK | ✅ (done by Byron) |
| 0b | NCS install, sample app | ✅ NCS v3.4.1. The `ehg_node` firmware builds and flashes, and the DK advertises as `EHG-93C0` |
| 0c | Dataset conversions | ✅ TPEHG (300), TPEHGT (31), ICEHG (126; was 252 with duplicates, fixed 2026-10-16), Icelandic EHGDB (123 segments) → [`data/converted/`](../data/converted), bit-exact vs WFDB (test) |
| 0d | Interface spec with Zicheng | 🟡 **Draft** in [`docs/interface-spec.md`](interface-spec.md), with questions for Zicheng marked |
| 1a | Digital sample injection | ✅ Absolute-tick scheduler, record loops for soak tests |
| 1b | Ring buffer | ✅ `k_msgq`; overflow counted and logged |
| 1c | GATT notification service | ✅ Built and flashed. ⛔ **Not yet tested over the air** (macOS Bluetooth permission, see below) |
| 1d | Seq + timestamps + injection jitter | ✅ In the packet header. `max_inject_late` is logged on UART |
| 2a | Bleak central | ✅ Written. Resend requests on gaps, per-notification CSV log. ⛔ Waits on the same permission |
| 2b | MQTT (Mosquitto QoS 1) | ✅ Docker |
| 2c | InfluxDB schema + ingest | ✅ `ehg_raw` / `ehg_pkt`, dedup on (node, seq) |
| 3a | Grafana | ✅ Local, provisioned dashboard (raw, latency, pkt rate, retransmissions). Cloud deployment ❌ not done (needs your account/cost decision) |
| 3b | WebSocket viewer | ✅ [`viewer/`](../viewer) (screenshot verified) |
| 4a/4b | H1/H2 tooling | ✅ [`eval/latency.py`](../eval/latency.py) (drift-corrected), [`eval/integrity.py`](../eval/integrity.py) |

**Pipeline check with simulated nodes** (no BLE hop; full Docker stack; 1 % injected loss):
- 100 % delivery after resends, 0 bit-exact mismatches.
- Per-sample latency: median 252 ms, p95 459 ms. About 450 ms of that is batching (10 frames at 20 Hz). The software stack adds about 10 ms.

These are **not H1/H2 results**, because the BLE hop is missing. They only show the tooling works.

### Needs you (blockers)

1. **Bluetooth permission for the terminal app.** Bleak scans hang silently because macOS never granted Bluetooth access to the app that runs your terminal.
   - Fix: System Settings → Privacy & Security → Bluetooth, and enable it for your terminal (or VS Code). Or run `uv run python -m gateway.gateway --no-mqtt` yourself once and click Allow.
   - Then: `uv run python -m gateway.gateway` with the DK powered.
   - The first BLE run will show on the DK's UART what macOS actually granted (MTU, PHY, DLE, connection interval). Record those numbers in the thesis.
2. **Docker Desktop is very slow to start containers on this machine** (5–15 minutes). It's not broken, just slow. Once up, the stack stays up (`restart: unless-stopped`). I did not restart Docker Desktop because your Supabase stack is running in it.
3. Grafana is on **port 3300**: VS Code is holding 3000–3003.
4. **Interface spec questions for Zicheng**: raw vs pre-filtered TPEHG channels; Icelandic channel choice / bipolar derivation ([`docs/interface-spec.md`](interface-spec.md) §1, §6).
5. **Classifier hook**: Zicheng's code isn't in this repo, so the H4 label-agreement half isn't wired. The proposed hook contract is in the interface spec §5.
6. **Cloud deployment (3a)**: not provisioned. The compose file runs unchanged on any small VM. Before exposing it: set `backend/.env` secrets, and turn on Mosquitto password + TLS (commented in [`mosquitto.conf`](../backend/mosquitto/mosquitto.conf)).

### Findings worth putting in the thesis (the reference audit found these; details in [`papers/INDEX.md`](../papers/INDEX.md))

- **BioGAP firmware *is* public.** `pulp-bio/BioGAP` has had Apache-2.0 NCS/Zephyr nRF5340 firmware in `Firmware/src_NRF` since July 2025. The interim report (§8.3) says no code was found. A local clone is at `~/thesisb-refs/BioGAP` (outside the repo); its [`prj.conf`](../firmware/ehg_node/prj.conf) is a direct reference for PHY/DLE/connection-parameter setup. Also, ref [72]'s URL points to `pulp-bio/VowelNet`, not BioGAP.
- **[55]:** the TimescaleDB 3.5× / 3.4–71× figures are a Timescale vendor benchmark that the paper quotes, not the paper's own result.
- **[18]:** no "≥20k connections" figure is in the paper (3 publishers / 15 subscribers were tested).
- **[20]:** chest/wrist nodes at 1 kHz; the ring + nRF52840 detail comes from [19], not [20].
- **[47]:** S140 is an nRF5-SDK SoftDevice. NCS uses the SoftDevice Controller; its link limit (20) still holds (`BT_MAX_CONN` range 1–20).
- **[51]:** latencies are in *minutes*, so it's a weak comparator for a < 2 s target.
- **[17]:** 99.72 % is decrypt-and-store over 1 h; InfluxDB is only mentioned as optional there.
- **Refs [33], [39], [71], [73]** are missing from the numbered list.

### Engineering notes discovered in this session

- **NCS sysbuild fails on paths containing spaces.** Build through the symlink `~/thesisb` ([`firmware/build.sh`](../firmware/build.sh) does this).
- **Timer rounding:** `k_timer` periods round to 32 768 Hz ticks, so 50 ms runs as 20.005 Hz. The replay therefore schedules on absolute ticks.
- **Clock alignment for H1 must use the *last* frame of a batch.** Aligning on frame 0 folds the batching delay into the offset and makes latency read ~0 (this bug was caught by a test).
- **Crystal drift** of 20–50 ppm is 72–180 ms/h. [`eval/latency.py`](../eval/latency.py) fits the lower envelope of the offset (Moon et al. 1999) from the raw device ticks stored in `ehg_pkt.t_inject_dev`.
- **Absolute latency excludes the minimum BLE link delay** (about one connection interval). State this with the H1 results. A round-trip ping characteristic would bound it, if a marker asks.
- **Ingest runs in Docker** and stamps `t_db_ack` with the Docker VM clock. Docker Desktop keeps it synced with macOS, but check the offset before the final H1 runs (or run ingest on the host).
- **8 nodes (H3):** macOS's connection limit is undocumented. The fallback is a DK/dongle as a multi-link central (NCS `central_multilink`). `--simulate 8` load-tests everything after BLE today.

### Next steps (in order)

1. Grant Bluetooth permission → first over-the-air run → record the negotiated link parameters.
2. Run a 10 min run, then the 6 h TPEHG soak (H2 MVP): [`eval/integrity.py`](../eval/integrity.py) + [`eval/latency.py`](../eval/latency.py).
3. Agree the interface spec with Zicheng; wire the classifier hook (H4).
4. Cloud deployment decision (3a).
