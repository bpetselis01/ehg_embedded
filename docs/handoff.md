# Handoff pin (written 2026-11-03 18:42 AEDT)

## Resume from
- Unit: Unit 0, step "ask Byron whether the 6 h TPEHG soak and first H3 work are done, then record the no-hardware baseline numbers"
- Phase / checkpoint status: Phase 0. No checkpoint reached. Unit 0 is partly done (branch, tag, handoff pin, pin test). Unit 1 not started.
- Branch / last commit: feat/enterprise-topology @ faa2c4d (docs: add enterprise-topology hardening prompt, ignore local outputs folder), plus the commit that adds this file

## State of the repo
- Tests: 81 passed before this unit's test was added (`uv run pytest -q`, 17.6 s); `tests/test_handoff.py` added since
- Uncommitted changes: none once this file and `tests/test_handoff.py` are committed
- Files created in the current unit: `docs/handoff.md`, `tests/test_handoff.py`. Files still to create: the dated "baseline before enterprise work" entry in `docs/progress.md`
- Docker / compose: Docker daemon is not running (no socket at `~/.docker/run/docker.sock`); nothing was started. Do not start or restart Docker Desktop without asking
- Background processes or runs: none
- Database / migration state: none (no migrations exist yet)

## Decisions made this session (not yet in an ADR or progress.md)
- Baseline tag `baseline-pre-enterprise` sits on 9ba4ad7, the last commit before any enterprise work
- The hardening prompt and a `.gitignore` line for `Claude outputs/` were committed on the feature branch after the tag, so the tag stays pure
- `Claude outputs/` holds two older drafts of the prompt (kept on disk, now gitignored); `docs/claude-code-prompt-thesis-b-hardening.md` is the version being implemented

## Open questions waiting for Byron
- Are the 6 h TPEHG soak (H2) and the first H3 work done? Options offered: done, not done (do the soak on the tagged commit before Unit 3 merges), skip for now. Recommended: soak on `baseline-pre-enterprise` before Unit 3 merges

## Byron's pending manual steps
- none yet. If Docker is wanted for the no-hardware baseline run, Byron starts Docker Desktop himself (it hosts Supabase; I will not restart it)

## Next three actions
1. Ask the soak/H3 question (AskUserQuestion), record the answer here
2. If Docker is up and `data/converted` exists, run the README "Reproducing the results" no-hardware row (`--simulate 1 --sim-loss 0.01`, then `eval/integrity.py`, `eval/latency.py`) and add the dated baseline entry to `docs/progress.md`; otherwise record that it was skipped and why
3. Move the pin to Unit 1 and ask Byron before starting the ADRs (Phase 1 docs-only work ends at Checkpoint 1)

## Warnings / traps
- Never push, never commit on `main`, no AI trailer in commit messages (CI fails on it)
- `tests/test_docs.py` checks every Markdown link and every `docs/...md` path mentioned in tracked code, so new docs must exist before they are referenced
- The prompt's quoted facts (Oracle limits, image arm64 support, HAPI R5 behaviour and others) are marked unverified; check and record before building on them
