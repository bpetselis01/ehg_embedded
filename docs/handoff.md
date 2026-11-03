# Handoff pin (written 2026-11-03 19:55 AEDT)

## Resume from
- Unit: Unit 3, step "gateway/outbox.py: SQLite WAL outbox, write-first publish, delete on PUBACK (start with tests/test_outbox.py using a fake publisher)"
- Phase / checkpoint status: Phase 0 done except the no-hardware baseline run. Phase 1 done, Checkpoint 1 passed (Byron approved 2026-11-03). Phase 2: Unit 2 done and committed; Unit 3 NOT started. Checkpoint 2 comes after Unit 3 (outbox overhead and outage-recovery result, then ask whether to enable the outbox by default)
- Branch / last commit: feat/enterprise-topology, see `git log -1` (feat(transport): MQTT TLS 1.3 with client certificates as an optional cloud profile)

## State of the repo
- Tests: `uv run pytest -q` = 122 passed, 6 skipped; `EHG_INTEGRATION=1 uv run pytest -q` = 128 passed (needs `brew install mosquitto`, which is installed here: Mosquitto 2.1.2)
- Uncommitted changes: none
- Files created in Unit 2: `tools/pki.sh`, `backend/mosquitto/mosquitto.tls.conf`, `backend/mosquitto/acl`, `backend/docker-compose.cloud.yml`, `docs/security.md`, `tests/test_mqtt_tls_config.py`; edited `gateway/gateway.py` (parse_args, tls_context, TLS flags), `backend/ingest/ingest.py` and `viewer/server.py` (mqtt_settings), README, codebase guide, troubleshooting, ADR 0008, licences, progress, `.gitignore`, `pyproject.toml`/`uv.lock` (+PyYAML dev)
- Docker / compose: Docker daemon not running; nothing started. `docker compose config` works without the daemon and was used
- Background processes or runs: none (a test broker was started and stopped by the tests; check `pgrep mosquitto` if in doubt)
- Database / migration state: none

## Decisions made this session (not yet in an ADR or progress.md)
- None outstanding: Unit 2 findings are in ADR 0008, `docs/security.md` and `docs/progress.md`

## Open questions waiting for Byron
- none for now. Checkpoint 2 (after Unit 3): enable the outbox by default?

## Byron's pending manual steps
- Optional: start Docker Desktop yourself, then I can (a) run the no-hardware baseline row from README "Reproducing the results" (still not recorded), (b) start the cloud-profile containers and run `gateway --simulate 1 --sim-loss 0.01 --mqtt-tls …` then `eval/integrity.py` for the open "100 % over TLS" step, (c) check that the container can read `backend/pki/broker.key`
- Optional: `brew install shellcheck` so `sh` scripts can be linted (it was not run on `tools/pki.sh`)

## Next three actions
1. Read `gateway/gateway.py` (`make_publisher`, `Node.on_bytes`), `gateway/tracking.py`, `eval/latency.py`, `tests/test_gateway.py`; write `tests/test_outbox.py` first (broker down, rows accumulate, delivered in order exactly once; restart replays; cap policy; backoff with a fake clock; lost PUBACK duplicate harmless)
2. Implement `gateway/outbox.py` per ADR 0009 (SQLite WAL, mode 0600, `--outbox PATH` default off, `--outbox-max-mb` 512, `replayed: true`, retained `ehg/_gw/<id>/status` with Last Will) and wire it into `make_publisher` without changing the default path; ingest stores `replayed`; `eval/latency.py --exclude-replayed`
3. Update ACL/docs for the status topic (already allowed for `ehg-gw-01`), README, troubleshooting; add `gateway/outbox*.db` to `.gitignore`; record the outbox overhead benchmark in `docs/progress.md`; then stop at CHECKPOINT 2

## Warnings / traps
- Never push, never commit on `main`, no AI trailer in commit messages (CI fails on it)
- New `.py`/`.sh` under tools/eval/gateway/viewer/backend/firmware need their file name in the file index of `docs/codebase-guide.md`; every CLI uses `ArgumentParser(description=__doc__, ...)`
- paho runs the TLS handshake in its network thread: a bad certificate raises nothing at `connect()`. Tests must probe with the raw CONNECT/CONNACK helper, and piped subprocess tests need `PYTHONUNBUFFERED=1`
- A QoS 1 publish that the ACL denies still gets a PUBACK on Mosquitto 2.1.2, so the outbox cannot treat PUBACK as proof of delivery to readers
- The 6 h soak and first H3 were skipped by Byron's choice; the outbox changes the gateway hot path, so keep `Node.on_bytes` timing unchanged when the outbox is off
- 5-hour window was 81% used at 19:55 AEDT and resets about 20:30 AEDT; check usage before starting Unit 3
