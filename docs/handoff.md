# Handoff pin (written 2026-11-03 20:25 AEDT)

## Resume from
- Unit: Unit 3, step "CHECKPOINT 2: show Byron the outbox overhead and the outage-recovery result, and ask whether to enable the outbox by default"
- Phase / checkpoint status: Phase 0 done except the no-hardware baseline run. Phase 1 done (Checkpoint 1 approved). Phase 2: Unit 2 and Unit 3 done and committed. Checkpoint 2 NOT yet answered. After it comes Phase 3 (Unit 4, infrastructure as code), which needs the Oracle decisions at Unit 5
- Branch / last commit: feat/enterprise-topology, see `git log -1` (feat(gateway): durable SQLite outbox ... and the follow-up docs commit)

## State of the repo
- Tests: `uv run pytest -q` = 140 passed, 8 skipped; `EHG_INTEGRATION=1 uv run pytest -q` = 148 passed (needs `mosquitto` from Homebrew, installed here: 2.1.2)
- Uncommitted changes: none once the Unit 3 docs commit is made
- Files created in Unit 3: `gateway/outbox.py`, `tests/test_outbox.py`; edited `gateway/gateway.py` (PahoTransport, make_publisher, --outbox/--outbox-max-mb/--gateway-id), `backend/ingest/ingest.py` (replayed), `eval/latency.py` (--exclude-replayed), `tests/test_mqtt_tls_config.py` (spawn_broker), interface-spec, ADR 0009, security.md, README, troubleshooting, progress, `.gitignore`, codebase guide
- Docker / compose: Docker daemon not running; nothing started
- Background processes or runs: none
- Database / migration state: none

## Decisions made this session (not yet in an ADR or progress.md)
- None outstanding. Deviation recorded in ADR 0009: no backoff jitter (paho 2.1 has none)

## Open questions waiting for Byron
- Checkpoint 2: enable the outbox by default? Options: keep it off (recommended: the default local experience stays identical, and the soak/H3 baseline was skipped), or on by default

## Byron's pending manual steps
- Optional: start Docker Desktop yourself so I can run the no-hardware baseline, then `gateway --simulate 1 --sim-loss 0.01 --outbox ...` with the broker stopped for 60 s and `eval/integrity.py` (expect `missing ranges: []`), which is the still-open Unit 3 integration step
- Optional: `brew install shellcheck`

## Next three actions
1. Apply Byron's Checkpoint 2 answer (if "on by default", change the `--outbox` default carefully and update README, ADR 0009, tests, and say what changes for H1 runs)
2. Start Unit 4 (Phase 3): fetch Oracle's current "Always Free Resources" page and confirm the section 5 numbers, verify every image has a `linux/arm64` manifest (`docker buildx imagetools inspect` needs Docker; otherwise use the registry API and say so), then write `deploy/terraform`, cloud-init, `deploy/update.sh`, `deploy/compose/docker-compose.cloud.yml`, `tests/test_deploy_assets.py`
3. Before Unit 4 creates `deploy/`: extend `tests/test_docs.py` file-index directory list to `deploy/`, add the `deploy/**` secret patterns to `.gitignore`, and keep every secret out of git (SOPS + age)

## Warnings / traps
- Never push, never commit on `main`, no AI trailer in commit messages (CI fails on it)
- Open security item from Unit 2: a gateway can publish for any node id (`ehg/+/pkt`); close with the registry plus a triage/ingest check (Units 6 to 8). The TLS broker forces client id = certificate CN: one process per certificate
- paho runs the TLS handshake in its network thread: a bad certificate raises nothing at `connect()`; piped subprocess tests need `PYTHONUNBUFFERED=1`; probe with the raw CONNECT/CONNACK helper
- Unit 4 onward touches the cloud: you never create or query cloud resources or ask for credentials; Byron runs `terraform apply`. Ask before the Oracle sign-up and the card (Unit 5)
- The 6 h soak and first H3 were skipped by Byron's choice
- 5-hour window: usage was 89% at 20:25 AEDT and the window was about to reset; check usage before starting Unit 4
