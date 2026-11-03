# 0010. Cloud target: Oracle Cloud Always Free only

**Date:** 2026-11-03

## Status

Accepted (Checkpoint 1, 2026-11-03; status of the build is stated in each record). Byron chose this target on 2026-11-03; building it starts at Unit 4. No cloud account exists yet.

## Context

The only hard constraint is that every service is free. The chain needs a public address, enough memory for several services, and no time limit
that expires before the thesis is due (30 April 2027). AWS IoT Core's free allowance is time- or credit-limited (one node sends about 172,800
messages a day), and the other hyperscaler free tiers were rejected as too small or time-limited (secondary sources, unverified).

## Decision

- Use one **Oracle Always Free Ampere A1** VM (2 OCPU, 12 GB, arm64 as stated in the prompt, **to be re-verified against Oracle's current page at Unit 4**),
  in an Australian home region, with Terraform and cloud-init ([decision 0017](0017-backup-dr-and-design-only-ha.md) covers recovery).
- Stay Always-Free only: no Pay-As-You-Go upgrade. Sign-up needs a phone number and a credit card for verification; Byron agrees to that explicitly at the Unit 5 gate.
- Everything must be rebuildable from infrastructure as code plus backups, because Oracle gives no SLA and may change or reclaim free resources.
- Do not add artificial load to avoid the idle-reclaim rule; the planned stack's resident memory should keep it from being classed idle (estimate, **unverified**).
- A small AMD micro VM is an optional uptime probe. A restore onto an Azure free VM is **future work**, not built.
- **Exit criteria** (move to a different free host or fall back to the local stack): the VM is reclaimed, the free allowance is cut again, Australian
  capacity cannot be obtained after reasonable retries, or the stack no longer fits in the free memory.

## Consequences

- No availability guarantee; the thesis claims measured behaviour only, never an SLA.
- A1 capacity in Sydney or Melbourne is unverified and "out of host capacity" errors are common, so the runbook (Unit 5) documents retries.
- The home region is permanent. An Australian region is chosen so any later real-data work has fewer cross-border questions (see `docs/regulatory.md`, written in Unit 17).
- I never create or query cloud resources or ask for credentials; Byron runs `terraform apply`.

**Sources:** [hardening prompt](../claude-code-prompt-thesis-b-hardening.md) §3, §5, §5b. Oracle's limits, the June 2026 allowance cut and the idle rule come from that prompt and are unverified until Unit 4.
