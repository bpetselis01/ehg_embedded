# Handoff pin (written 2026-11-03 19:10 AEDT)

## Resume from
- Unit: Unit 1, step "CHECKPOINT 1: Byron reviews ADRs 0007 to 0017, the capability checklist and the licence register; on approval set the ADR statuses to Accepted"
- Phase / checkpoint status: Phase 0 done except the no-hardware baseline run. Phase 1 (Unit 1) written and committed. Checkpoint 1 asked, answer NOT yet received. No code may be written before approval
- Branch / last commit: feat/enterprise-topology, see `git log -1` (docs: add ADRs 0007-0017, production topology and licence register)

## State of the repo
- Tests: 83 passed (`uv run pytest -q`)
- Uncommitted changes: none once the Unit 1 commit is made
- Files created in the current unit: `docs/decisions/0007` to `0017`, `docs/production-topology.md`, `docs/licences.md`; links added in `docs/codebase-guide.md` and `README.md`. Files still to create: none for Unit 1
- Docker / compose: Docker daemon not running; nothing started
- Background processes or runs: none
- Database / migration state: none

## Decisions made this session (not yet in an ADR or progress.md)
- Byron chose to skip the 6 h soak and first H3 for now (recorded in progress.md 2026-11-03)
- The ADR status is "Proposed" until Checkpoint 1 approval; the guide and topology doc say so

## Open questions waiting for Byron
- Checkpoint 1: approve the ADRs, capability checklist and licence register so Phase 2 (Unit 2, MQTT TLS) can start? Options offered: approve, approve with changes, hold

## Byron's pending manual steps
- Optional, before Unit 2 merges: start Docker Desktop yourself so the no-hardware baseline row can be run and recorded
- Review `docs/decisions/0007` to `0017`, `docs/production-topology.md`, `docs/licences.md`

## Next three actions
1. Apply Byron's checkpoint answer: if approved, change the ADR statuses from Proposed to Accepted (and the guide row), run the tests, commit
2. Read `backend/mosquitto/mosquitto.conf`, `backend/docker-compose.yml`, `gateway/gateway.py`, `backend/ingest/ingest.py`, `viewer/server.py`, then start Unit 2 (`tools/pki.sh`, TLS config, ACL, compose override, flags, `tests/test_mqtt_tls_config.py`)
3. Before Unit 2 code: verify Mosquitto `crlfile`, WebSocket and `tls_version` behaviour against the pinned image's man page and record it in ADR 0008

## Warnings / traps
- Never push, never commit on `main`, no AI trailer in commit messages (CI fails on it)
- `tests/test_docs.py` checks every Markdown link and every `docs/...md` path in tracked code, so create each doc before linking to it
- Unit 2 adds new `.sh` and `.py` files: each needs its name in the file index of `docs/codebase-guide.md` and a header block; CLIs need `argparse.ArgumentParser(description=__doc__)`
- Add `backend/pki/`, `*.key`, `*.pem`, `*.p12` to `.gitignore` in the same unit that creates them
- Check the usage window before starting Unit 2 (it was 72% at 19:00 AEDT, resets about 20:30 AEDT)
