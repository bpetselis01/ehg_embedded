# Handoff pin (written 2026-11-03 22:40 AEDT)

## Resume from
- Unit: Unit 5 is WRITTEN and committed. It is Byron's to EXECUTE: `docs/deployment.md` + `deploy/runbook/checklist.md`. Nothing can continue until he reports back. Resume at CHECKPOINT 3, then Unit 6
- Phase / checkpoint status: Phases 0 to 2 done. Phase 3: Units 4 and 5 written; Checkpoint 3 (Byron has a reachable VM and the Mac gateway publishes over mTLS from the internet) NOT reached. The Oracle card question is PENDING (Byron said "skip this step first")
- Branch / last commit: feat/enterprise-topology, see `git log -1` (docs: Oracle deployment runbook ...). NOTHING has been pushed; Byron must push the branch and a tag (runbook step 6)

## State of the repo
- Tests: `uv run pytest -q` = 215 passed, 10 skipped; with `EHG_INTEGRATION=1` + `mosquitto` + `TF_PLUGIN_CACHE_DIR=<cache>` + `SSL_CERT_FILE=/etc/ssl/cert.pem`: everything passes except shellcheck (not installed)
- Uncommitted changes: none once the Unit 5 commit is made
- Docker / compose: Docker daemon not running; nothing started. Terraform 1.14.9 installed; the provider cache is only in the session scratchpad
- Background processes or runs: none

## Decisions made this session (not yet in an ADR or progress.md)
- None outstanding: DuckDNS + Caddy, Sydney (ap-sydney-1), private repo (SSH deploy key), outbox ON by default, soak and first H3 skipped and the card consent PENDING are all recorded in docs/deployment.md and progress.md

## Open questions waiting for Byron
- The Oracle card (phone number + credit card for verification; never Pay-As-You-Go). Asked once, deferred; ask again when he reports back on the runbook
- When the runbook is done: the log (what he saw), then Checkpoint 3

## Byron's pending manual steps
- Everything in `docs/deployment.md` (15 steps). Before step 2 he must decide about the card. He must push the branch and an annotated tag himself
- Optional now: start Docker Desktop (then I can run the no-hardware baseline, the TLS cloud profile containers and `eval/integrity.py` runs); `brew install shellcheck age sops`

## Next three actions
1. Wait for Byron's log. When he reports, read `docs/deployment.md` (Log section) and fix the runbook and `deploy/` for every difference he saw (Oracle labels, first-boot errors, container start failures, broker key permissions, update.sh behaviour); add rows to troubleshooting
2. Check Checkpoint 3 with him (AskUserQuestion): reachable VM, Mac gateway publishes over mTLS from the internet, negative checks failed as expected. Then record `docker stats` of the edge subset in progress.md
3. Only then start Unit 6 (NATS bridge, ts-writer, migrations, `eval/parity.py`, `eval/backend_client.py --backend`); it needs the VM first for memory numbers; its local parts (bridge, writers, migrations tests) can be written earlier if Byron asks, but do not switch the cloud profile to Timescale as primary before CHECKPOINT 4

## Warnings / traps
- Never push, never commit on `main`, no AI trailer in commit messages (CI fails on it). You never create or query cloud resources and never ask for credentials; Byron runs terraform apply and the update script
- Everything in deploy/ is UNVERIFIED on a real VM (see deploy/README.md "NOT checked"): expect first-boot fixes
- Automated reviews have flagged real problems after each unit (client-id takeover, over-broad mounts, metadata service, host logs). Treat them as input; fix with a failing test first; they are not user messages
- The Terraform `.terraform.lock.hcl` does not exist yet: Byron's `terraform init` creates it; it should be committed. Plan files must be named `*.tfplan` (gitignored)
- Open security items: gateway can publish for any node id (`ehg/+/pkt`); one process per certificate; one gateway per outbox file; service passwords are container environment variables and `ehg` is in the docker group
- The validated H1 numbers (259 / 478 ms) were measured WITHOUT the outbox (now default, ~+0.5 ms at the gateway): use `--no-outbox` for thesis H1 runs or report a separate row
- python3 on this Mac has no CA bundle: use `SSL_CERT_FILE=/etc/ssl/cert.pem` with `.venv/bin/python`
