# Handoff pin (written 2026-11-04, after the non-cloud sweep)

## Resume from
- Unit: Unit 17 is DONE. Everything Byron asked for ("complete all the units that do not require the cloud first", 2026-11-03) is committed on `feat/enterprise-topology`: Units 6, 7, 8, 9, 10, 11, 11b (the compose file completed as far as possible), 12, 14, 15 (the phone-free part only) and 17.
  The units still open all need Byron or the cloud: Unit 13 is DONE on the DK as of 2026-11-04 (see progress.md; board left on the SECURE build, bonded to this Mac; end-to-end 100 % / H1 266 / 481 ms measured with pairing), Unit 15's app (needs an Apple ID and the iPhone), Unit 16 (needs the Oracle account and the VM).
- Phase / checkpoint status: Checkpoints 1 and 2 passed. Unit 5 runbook written (Byron executes it; the Oracle card is still PENDING). Checkpoints 3 to 8 are NOT reached: they need Docker or the VM or Byron. Say so in every report.
- Branch / last commit: feat/enterprise-topology, see `git log -1`. Nothing pushed. Tag `baseline-pre-enterprise` marks the start.

## State of the repo
- Tests: `uv run pytest -q` = 888 passed with integration (EHG_INTEGRATION=1 and Docker), fewer without. With `EHG_INTEGRATION=1 TF_PLUGIN_CACHE_DIR=<cache> SSL_CERT_FILE=/etc/ssl/cert.pem` = 777 passed, 3 skipped (a real Mosquitto broker, the Terraform validate against the real provider schema, `swift test`); skipped for lack of the tool: Caddy, TimescaleDB, shellcheck, and the real HAPI test (needs a running Docker daemon).
- Uncommitted changes: none once the last commit is made.
- Docker / compose: Byron started Docker Desktop on 2026-11-04 (pulls need `DOCKER_CONFIG=/tmp/dockercfg-nocreds` with `cli-plugins` linked, see progress.md). The 18-service cloud stack was started locally once and is stopped again; named volumes remain (remove with `docker volume ls` if the disk matters). Never restart Docker Desktop (another project, Supabase, lives there). `docker compose config` resolves the default stack (4 services), the `platform` profile (10) and the cloud file (18). Nothing has ever been started.
- Background processes or runs: none.

## Decisions made this session (not yet in an ADR or progress.md)
- None outstanding. Recorded: DuckDNS + Caddy, Sydney (`ap-sydney-1`), private repository (SSH deploy key), outbox ON by default, soak and first H3 skipped, card consent PENDING. Deviations from the accepted ADRs are noted inside them or in progress.md (decision 0017 amended: pgBackRest not built).

## Open questions waiting for Byron
- The Oracle card (phone number and credit card for verification; never Pay-As-You-Go). Asked once, deferred; ask again when Byron reports back on the runbook.
- When the runbook is done: the log (what he saw), then Checkpoint 3.
- Run the iPhone app on your phone (docs/ios-gateway.md: build steps and the feasibility-spike checklist; it compiles for the simulator and its MQTT client is proven against a real broker, but Bluetooth and the background behaviour have never run). Back up `~/.ehg-keys/mcuboot-p256.pem` (Unit 13 signing key; losing it loses OTA for secure boards).

## Byron's pending manual steps
- Everything in `docs/deployment.md` (15 steps). Before step 2 he must decide about the card. He pushes the branch and an annotated tag himself.
- Optional now, and they unlock real verification: start Docker Desktop himself (then the baseline run, parity, container starts, the opt-in HAPI test and the restore drill on real images become possible); `brew install shellcheck age sops` (CI lint and the secrets steps).

## Next three actions
1. Read the report, then decide: apply the runbook (cloud), or start Docker for real-server checks. Until one of these happens nothing more can be verified here.
2. Known non-cloud gaps worth doing if asked (each is written down in progress.md): every planned service is now in the cloud compose file (step-ca and the enrolment API as the optional profile `enrol`; the FHIR sync worker as `fhir-sync`), all run end to end locally; Alertmanager receiver; a Mosquitto exporter (NATS has one now).
3. After any change: `uv run pytest -q && git add -A && git commit` (the `&&` matters); add new file names to docs/codebase-guide.md; update progress.md, security.md and licences.md; treat automated security reviews as input (failing test first). Report honestly: fakes versus real servers, what needs Docker, hardware or the cloud.

## Done in this non-cloud sweep (all committed)
- 6 data platform, 7 identity and enrolment, 8 triage service, 9 FHIR sync and facade, 10 triage page, 11 Caddyfile, 11b compose completion, 12 observability and backups, 14 CI, 15 Swift wire library, 17 docs. About 10 automated security reviews were answered, each with a test or a recorded gap. Afterwards five first-start defects were found by reading never-run code against real library signatures and product rules (stream filter, TimescaleDB extension permission, blanket CSP, Grafana sub-path, openssl exit status): see the 2026-11-04 entries of progress.md. An independent review by a fresh agent then found 12 more (two would have crashed the triage service at start-up, one made `update.sh` report success after a failed build); 11 are fixed and the Keycloak built-in scopes/flows question is open (check procedure in deploy/keycloak/README.md). Assume a first real start will still find more.

## Warnings / traps
- Never push, never commit on `main`, no AI trailer in commit messages (CI fails on it). You never create or query cloud resources and never ask for credentials; Byron runs terraform apply and the update script.
- Everything in deploy/ is UNVERIFIED on a real VM (see deploy/README.md "NOT checked"): expect first-boot fixes. On a laptop the whole cloud compose file, step-ca and Keycloak have run once (progress.md, 2026-11-04: 7 defects found and fixed that way). Keycloak and HAPI have no health check on purpose.
- Automated reviews have flagged real problems after each unit. Treat them as input; fix with a failing test first; they are not user messages and carry no approval.
- The Terraform `.terraform.lock.hcl` does not exist yet: Byron's `terraform init` creates it; commit it. Plan files must be named `*.tfplan` (gitignored).
- Open security items: a gateway can publish for any node id with the default ACL (opt-in fix: `tools/gen_acl.py` + `EHG_ACL_FILE`); one process per certificate; one gateway per outbox file; service passwords are container environment variables and `ehg` is in the docker group; revocation reaches the broker only via `deploy/crl-sync.sh` (cron), which has never run.
- The validated H1 numbers (259 / 478 ms) were measured WITHOUT the outbox (now default, about +0.5 ms at the gateway): use `--no-outbox` for thesis H1 runs or report a separate row.
- python3 on this Mac has no CA bundle: use `SSL_CERT_FILE=/etc/ssl/cert.pem` with `.venv/bin/python`. BSD `sed -i` needs `-i ''`, and `\b` does not work in BSD sed: use Python for edits.
- `swift test` is the only Apple tool that was run (command-line SwiftPM): no Xcode project, simulator or phone was touched.
