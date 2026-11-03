# Handoff pin (written 2026-11-03 18:50 AEDT)

## Resume from
- Unit: Unit 1, step "write ADRs 0007 to 0017, docs/production-topology.md and docs/licences.md (docs only)"
- Phase / checkpoint status: Phase 0 done except the no-hardware baseline run. Phase 1 not started. Checkpoint 1 NOT yet asked
- Branch / last commit: feat/enterprise-topology @ 52e99e2 (docs: add session handoff pin and a test that keeps it resumable), plus the commit that updates this pin and progress.md

## State of the repo
- Tests: 83 passed (`uv run pytest -q`)
- Uncommitted changes: none once this pin and the progress.md entry are committed
- Files created in the current unit: none. Files still to create: `docs/decisions/0007` to `0017`, `docs/production-topology.md`, `docs/licences.md`; link all from `docs/codebase-guide.md` and `README.md`
- Docker / compose: Docker daemon not running; nothing started
- Background processes or runs: none
- Database / migration state: none

## Decisions made this session (not yet in an ADR or progress.md)
- Byron chose to skip the 6 h soak and first H3 for now (recorded in progress.md 2026-11-03); Unit 16 repeats them on the cloud chain
- Baseline tag `baseline-pre-enterprise` sits on 9ba4ad7

## Open questions waiting for Byron
- none

## Byron's pending manual steps
- Optional, before Unit 2 merges: start Docker Desktop yourself, then I run the no-hardware baseline row from README "Reproducing the results" and add the numbers to progress.md

## Next three actions
1. Read the latest `docs/progress.md` entry and `docs/decisions/0001` to `0006`, then draft ADRs 0007 to 0017 (four sections plus a Sources line each; UK English)
2. Write `docs/production-topology.md` (diagram, 4-tier map, boundary table, six-hop chain, Appendix A comparison, capability checklist) and `docs/licences.md` (mark anything not confirmable from the repo as UNVERIFIED)
3. Run `uv run pytest -q`, commit, then stop at CHECKPOINT 1 and ask Byron to approve before any code

## Warnings / traps
- 5-hour window was 71% used at 18:50 AEDT and resets about 20:30 AEDT; check usage before starting Unit 1
- Never push, never commit on `main`, no AI trailer in commit messages (CI fails on it)
- `tests/test_docs.py` checks every Markdown link and every `docs/...md` path in tracked code, so create each doc before linking to it
- The prompt's quoted facts (Oracle limits, arm64 images, HAPI R5 behaviour and others) are marked unverified; check and record before relying on them
