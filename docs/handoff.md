# Handoff pin (written 2026-11-03 20:50 AEDT)

## Resume from
- Unit: Unit 4, step "verify the Oracle Always Free numbers and every image's linux/arm64 manifest, then write deploy/ (Terraform, cloud-init, update.sh, deploy/compose/docker-compose.cloud.yml, tests/test_deploy_assets.py)"
- Phase / checkpoint status: Phase 0 done except the no-hardware baseline run. Phase 1 done (Checkpoint 1 approved). Phase 2 done (Units 2 and 3; Checkpoint 2 answered: Byron switched the outbox ON by default). Phase 3 not started
- Branch / last commit: feat/enterprise-topology, see `git log -1` (feat(gateway): switch the outbox on by default ...)

## State of the repo
- Tests: `uv run pytest -q` = 145 passed, 8 skipped; `EHG_INTEGRATION=1 uv run pytest -q` = 153 passed (needs `mosquitto`, Homebrew 2.1.2 installed here)
- Uncommitted changes: none once the commit is made
- Docker / compose: Docker daemon not running; nothing started. `docker compose config` works without the daemon
- Background processes or runs: none
- Database / migration state: none

## Decisions made this session (not yet in an ADR or progress.md)
- None outstanding. Recorded: outbox on by default (ADR 0009, progress 2026-11-03), no backoff jitter (paho), TLS broker forces client id = CN

## Open questions waiting for Byron
- none. Coming up at Unit 5: Oracle sign-up needs a phone number and a credit card for verification (ask first, never upgrade to Pay-As-You-Go); hostname route (default DuckDNS + Caddy); whether the repo may be public (Unit 14)

## Byron's pending manual steps
- Optional: start Docker Desktop yourself so I can run the no-hardware baseline, the cloud-profile containers, `eval/integrity.py` after an outage (expect `missing ranges: []`), and the arm64 manifest checks with `docker buildx imagetools inspect`
- Optional: `brew install shellcheck terraform tflint` (Unit 4 tests skip those checks when the binaries are missing)

## Next three actions
1. Unit 4 groundwork: fetch Oracle's current "Always Free Resources" page and report drift against section 5 of the prompt (2 OCPU / 12 GB A1, 200 GB block, 10 TB egress, 20 GB object storage); verify `linux/arm64` for each planned image (hapi, keycloak, timescaledb-ha, eclipse-mosquitto, nats, caddy, prometheus, loki, grafana, step-ca) via the registry API if Docker is not running, and say which method was used
2. Add `deploy/**` secret patterns to `.gitignore` (`deploy/**/.env*`, `*.tfstate*`, `terraform.tfvars`, `secrets/`), extend `tests/test_docs.py` file-index directories to `deploy/`, then write `deploy/terraform` (modules network, compute, storage, monitoring), cloud-init template, `update.sh`, the cloud compose file with memory limits summing to at most 9.5 GB, `deploy/README.md`, `tests/test_deploy_assets.py`
3. SOPS + age: commit `.sops.yaml` only, never keys; never create or query cloud resources, never ask for credentials; stop after Unit 4 for Unit 5 (Byron executes the runbook)

## Warnings / traps
- Never push, never commit on `main`, no AI trailer in commit messages (CI fails on it)
- With the outbox on by default the validated H1 numbers (259 / 478 ms) were measured WITHOUT it: gateway receive-to-publish rose from about 124 us to about 630 us median (p95 about 2 ms). Thesis H1 runs should use `--no-outbox` or be reported as a separate row
- Open security item from Unit 2: a gateway can publish for any node id (`ehg/+/pkt`); close with the registry plus a triage/ingest check (Units 6 to 8). One process per certificate (client id = CN) and one gateway per outbox file
- paho runs the TLS handshake in its network thread: a bad certificate raises nothing at `connect()`; piped subprocess tests need `PYTHONUNBUFFERED=1`; a background job in non-interactive bash ignores SIGINT, so drive subprocesses from Python
- Gateway tests that run main()/make_publisher must chdir to a temp folder (the default outbox path is relative) or pass `--no-outbox`
- The 6 h soak and first H3 were skipped by Byron's choice
