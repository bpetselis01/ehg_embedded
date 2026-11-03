# Progress log

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
