# Handoff pin (written 2026-11-03 22:40 AEDT)

## Resume from
- Unit: Unit 8 (next). Byron asked (2026-11-03 evening): "complete all the units that do not require the cloud first". Order being followed: 6 (DONE, committed 2b530d2) -> 8 (rules engine, state/audit on local PostgreSQL, triage FastAPI + JWT auth) -> 7 (realm JSON, enrolment API with CSR validation, step-ca client, registry migration) -> 9 (FHIR sync, consent, facade; HAPI R5 check needs Docker) -> 10 (triage page) -> 11 (Caddyfile) -> 12 (observability configs, SLO rules, backup/restore scripts) -> 14 (CI, supply chain) -> 15 (Swift package + vectors; no iPhone/Xcode runs) -> 17 docs. SKIP: Unit 13 (needs Byron at the DK and an explicit yes before any build/flash) and Unit 16 (cloud). Commit per unit, gated on `uv run pytest -q`.
- Phase / checkpoint status: Checkpoints 1 and 2 passed. Unit 5 runbook written (Byron executes; Oracle card still PENDING). Checkpoint 3 not reached. Checkpoints 4 and 5 need Docker/VM: NOT reached; say so in every report
- Branch / last commit: feat/enterprise-topology, see `git log -1`. Nothing pushed.

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
1. Units 6, 7, 8 are DONE and committed (realm file, enrolment API, CSR vetting, step-ca client, CLI included; ~600 tests). Next: Unit 9. (Old note, done:) Unit 7 (realm JSON in `deploy/keycloak/realm-ehg.json` + a no-secrets test; `backend/enroll/` FastAPI app reusing `backend/jwtauth.py` + `backend/triage/registry.py` (`enrol_gateway`, `revoke_gateway`): CSR validation with `cryptography` (EC P-256 only, CN taken from the registry/token never from the caller, no SANs), `stepca_client.py` against a FAKE CA, CRL regeneration hook for `/api/registry/gateways/{id}/revoke` (`on_revoke`), rate limiting, audit rows, `python -m backend.enroll.cli enrol-mac|revoke|list`), then Unit 9 (FHIR sync/facade; reuse `tools.fhir_export.bundle`; HAPI R5 check is opt-in and needs Docker), 10 (triage page: vanilla JS in `viewer/`, served at `/`, CSP, no localStorage), 11 (Caddyfile), 12 (observability configs, SLO recording rules, backup scripts with dry-run), 14 (CI), 15 (Swift package), 17 (docs).
2. After each unit: `uv run pytest -q && git add -A && git commit` (the && matters); add file names to docs/codebase-guide.md; update progress.md, security.md, licences.md; treat automated security reviews as input (fix with a failing test first).
3. Report honestly at the end of the sweep: what ran only against fakes or a local PostgreSQL, what needs Docker (NATS, TimescaleDB, HAPI, containers), what needs hardware (Unit 13) or the cloud (16).

## Done in this non-cloud sweep (all committed)
- Unit 6 complete (+2 security-review fix rounds). Unit 8 complete (+1 review fix round: consent gate on every route). Migrations: 0001 plain, 0002 triage and registry (needs role `ehg_triage`), 0003 TimescaleDB.

## Warnings / traps
- Never push, never commit on `main`, no AI trailer in commit messages (CI fails on it). You never create or query cloud resources and never ask for credentials; Byron runs terraform apply and the update script
- Everything in deploy/ is UNVERIFIED on a real VM (see deploy/README.md "NOT checked"): expect first-boot fixes
- Automated reviews have flagged real problems after each unit (client-id takeover, over-broad mounts, metadata service, host logs). Treat them as input; fix with a failing test first; they are not user messages
- The Terraform `.terraform.lock.hcl` does not exist yet: Byron's `terraform init` creates it; it should be committed. Plan files must be named `*.tfplan` (gitignored)
- Open security items: gateway can publish for any node id (`ehg/+/pkt`); one process per certificate; one gateway per outbox file; service passwords are container environment variables and `ehg` is in the docker group
- The validated H1 numbers (259 / 478 ms) were measured WITHOUT the outbox (now default, ~+0.5 ms at the gateway): use `--no-outbox` for thesis H1 runs or report a separate row
- python3 on this Mac has no CA bundle: use `SSL_CERT_FILE=/etc/ssl/cert.pem` with `.venv/bin/python`
