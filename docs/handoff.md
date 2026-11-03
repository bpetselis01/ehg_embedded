# Handoff pin (written 2026-11-03 21:45 AEDT)

## Resume from
- Unit: Unit 5, step "write docs/deployment.md and deploy/runbook/ checklists (Byron executes them); ask Byron the Oracle/card/hostname questions first"
- Phase / checkpoint status: Phase 0 done except the no-hardware baseline run. Phase 1 done. Phase 2 done (Checkpoint 2: outbox ON by default). Phase 3: Unit 4 done and committed (written and validated, never applied). Unit 5 not started. CHECKPOINT 3 comes after Byron has a reachable VM
- Branch / last commit: feat/enterprise-topology, see `git log -1` (feat(deploy): Terraform, cloud-init, compose and update.sh for the Oracle Always Free VM)

## State of the repo
- Tests: `uv run pytest -q` = 202 passed, 10 skipped; with `EHG_INTEGRATION=1` + `mosquitto` + `TF_PLUGIN_CACHE_DIR=<cache>` = 211 passed, 1 skipped (shellcheck missing)
- Uncommitted changes: none once the Unit 4 commit is made
- Files created in Unit 4: everything under `deploy/` (terraform + 4 modules, cloud-init template, compose, update.sh, caddy/nats/postgres/observability configs, `.sops.yaml`, secrets example + README, README), `tests/test_deploy_assets.py`; edited `.gitignore`, `tests/test_docs.py` (deploy/ in the file-index test), README, codebase guide, ADR 0010, licences, security, production-topology, troubleshooting, progress, pyproject/uv.lock (+python-hcl2)
- Docker / compose: Docker daemon not running; nothing started. `terraform` 1.14.9 is installed and was used; the provider download is cached in the session scratchpad only
- Background processes or runs: none
- Database / migration state: none

## Decisions made this session (not yet in an ADR or progress.md)
- None outstanding. In progress.md: partial compose on purpose; secrets as env from a memory-only file; sops installed by hand; recipient key placeholder; references to later docs are by unit name, not path

## Open questions waiting for Byron
- Unit 5 gates, ask ONE AT A TIME with AskUserQuestion: (1) consent to the Oracle sign-up needing a phone number and a credit card for verification (never upgrade to Pay-As-You-Go); (2) hostname route: default DuckDNS + Caddy HTTP-01 (check duckdns.org is up first), alternatives in the prompt section 5; (3) Australian home region: Sydney or Melbourne; (4) may the repo be public (matters for Unit 14, and the VM's git clone: a private repo needs a deploy key)

## Byron's pending manual steps
- Nothing is required before Unit 5. Optional now: start Docker Desktop so I can run the no-hardware baseline, `docker compose` for the local cloud profile, and the TLS `eval/integrity.py` runs; `brew install shellcheck`
- Later (Unit 5 runbook, YOU run it): create the Oracle account; `age-keygen` and put the public key in `deploy/.sops.yaml`; `terraform init/plan/apply` after reading the plan; point DNS at the output IP; copy the age PRIVATE key to the VM; install sops by hand and verify its checksum

## Next three actions
1. Ask the four Unit 5 questions above (one per message); record the answers in the runbook
2. Write `docs/deployment.md` (click-by-click, with a log section for what Byron actually saw: capacity, region, timings, docker stats of the edge subset) and `deploy/runbook/` checklists; include: Oracle sign-up, API key, DNS name, `tools/pki.sh init --host <name>`, copying gateway cert/key/CA to the Mac, bringing up the edge subset (Caddy + Mosquitto mTLS) and proving a `gateway --simulate 1` run from the Mac to the VM
3. Then stop at CHECKPOINT 3 (Byron has a reachable VM and the Mac gateway publishes over mTLS from the internet) before Unit 6; the data platform (NATS bridge, TimescaleDB writers, parity proof) needs the VM first

## Warnings / traps
- Never push, never commit on `main`, no AI trailer in commit messages (CI fails on it). You never create or query cloud resources and never ask for credentials; Byron runs terraform apply and the update script
- Everything in deploy/ is UNVERIFIED on a real VM: block-device path, Docker from Ubuntu's docker.io package, cap_drop/read_only hardening, TimescaleDB-HA init script and data path, Loki/Alloy configs, healthchecks, `update.sh` rollback. The first real boot will likely need fixes: record each in docs/deployment.md and troubleshooting
- The tfvars example, `.sops.yaml` and secrets are placeholders. The Terraform `.terraform.lock.hcl` does not exist yet: `terraform init` by Byron creates it, and it should be committed
- Open security items: a gateway can publish for any node id (`ehg/+/pkt`; close with the registry plus a check, Units 6 to 8); one process per certificate (client id = CN); one gateway per outbox file
- The validated H1 numbers (259 / 478 ms) were measured WITHOUT the outbox, which is now on by default (+~0.5 ms median at the gateway): use `--no-outbox` for thesis H1 runs or report a separate row
- The 6 h soak and first H3 were skipped by Byron's choice
- python3 on this Mac has no CA bundle: use `SSL_CERT_FILE=/etc/ssl/cert.pem` with `.venv/bin/python` for HTTPS from scripts
