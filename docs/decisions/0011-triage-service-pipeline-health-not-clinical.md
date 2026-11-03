# 0011. Triage service ranks pipeline and signal health, not clinical state

**Date:** 2026-11-03

## Status

Proposed (awaiting Checkpoint 1 approval). Implemented by Units 8 and 10.

## Context

An enterprise monitoring worklist ranks patients by severity. The replayed signal here is public electrohysterography (EHG) data; it has no heart
rate, oxygen saturation or blood pressure, and the analogue front end and classification belong to another thesis (Zicheng Shang's). Any
diagnostic or predictive claim could also change how the software is regulated (see `docs/regulatory.md`, written in Unit 17).

## Decision

- The triage service ranks **nodes** by a transparent score built from operational rules only: `NODE_OFFLINE`, `GATEWAY_OFFLINE`, `LOSS_UNRECOVERED`,
  `LOSS_RATE`, `LATENCY_P95`, `FLATLINE`, `SATURATION`, `SEQ_RESTART`. Thresholds are configuration with defaults taken from the H1 and H2 bounds.
- No contraction index, no classifier hook, no diagnostic, predictive or clinical wording in code, UI or docs (a test greps for it).
- Rules fire after 3 consecutive bad evaluations and clear after 10 healthy ones (anti-fatigue hysteresis). The scoring and ordering functions are pure and unit-tested.
- Alerts follow `open, acknowledged, escalated, resolved`; escalate and resolve need a note; every read, transition, note, export and relayed
  login writes a hash-chained audit row. The log is **tamper-evident, not write-once**.
- Authoritative H1, H2 and H4 numbers still come from `eval/latency.py` and `eval/integrity.py`. The triage worker's latency is an operational approximation.
- The UI footer states the intended purpose: research prototype for measuring wireless telemetry performance, not for clinical decision-making.

## Consequences

- The dashboard looks like a clinician worklist but reports on the pipeline; the thesis must say so wherever it shows the page.
- Dropping vital-sign rules keeps the work inside this thesis's scope and avoids implying an unvalidated alerting claim.
- A future clinical analysis layer would need its own safety case; this decision gives it a clean seam but builds nothing for it.

**Sources:** [hardening prompt](../claude-code-prompt-thesis-b-hardening.md) §3 (decision 4), §6, §8; [decision 0004](0004-end-to-end-loss-recovery.md) (the loss bound the rules reuse).
