# Session Q&A Log: Codebase documentation

**Date:** 2026-10-26
**Mode:** New Feature
**Scope:** Standard
**Phases completed:** 0, 1, 2, 3, 4, 5, 6 (two unknowns resolved inline), 7
**Outcome:** Plan produced: `docs/plans/codebase-docs-plan.md` (R1–R10, Units 1–7; Unit 7 blocked on the licence)

---

## Questions & Answers by Phase

### Phase 1: Ground

**Q:** Byron asked to research how industry documents codebases, then bring this codebase to that level.
**AI position:** Research (Google documentation guide, Write the Docs, PEP 257, Zephyr Doxygen rules, Nygard ADRs,
FAIR4RS, Lee 2018) agrees on minimal, current, single-sourced, enforced docs, plus LICENSE/CITATION and a
reproduce-the-results path for research software. The audit found the in-code docs already denser than typical industry
code (headers on every file, 95 % of functions documented, no broken links). The real gaps: stale facts (115200 vs
460800 baud, "never contains a recording", wire v1), no LICENSE, no CI, decisions scattered, no data-to-number path,
no troubleshooting page, an absolute-path symlink tracked, three unchecked copies of the wire format.

### Phase 2: Challenge & Explore

**Q:** Who is the primary reader? **User:** examiners + supervisor (recommended option).
**AI position:** Then correctness first (a wrong baud makes examiners doubt the numbers), then traceability of
H1–H4, then the "why" for the viva. Taken literally, "industry level" (CONTRIBUTING, SECURITY.md, Sphinx, Google
docstrings everywhere) would be cargo cult here.
**Resolved:** premise reframed to "correct, traceable, enforced".

**Q:** In-code style? **User:** keep the house headers, fix drift (recommended).
**AI position:** Rewriting to Google/Doxygen churns every file and loses the worked examples and thesis mapping.

**Q:** How to catch drift? **User:** pytest doc checks + GitHub Actions (recommended).
**Resolved:** doc checks live in `tests/test_docs.py`, CI runs `uv run pytest -q`.

### Phase 4: Structure

**Q:** Which licence? **AI recommended:** MIT. **User:** ask the supervisor first.
**Resolved:** Unit 7 blocked; README says "Licence: pending"; CITATION.cff ships without a licence field.

**Q:** Which decisions get an ADR? **User:** all four offered (raw int16, serial feed + built-in, loss recovery + clock
sync, backend stack). **AI position:** one decision per record, so six ADRs (loss recovery and clock sync split; built-in
amends serial feed).

### Phase 5: Validate

**Q:** CEO review scope mode? **User:** hold scope (recommended).
**Resolved:** P1s fixed in the plan: a no-hardware reproduce row (examiners mostly have no DK), a check for doc paths
written in code comments, test files listed in Units 5/6. Accepted: ADR content and link anchors are not automated.

### Phase 6: Deepen

Resolved inline: `uv.lock` is universal (409 manylinux wheels), so CI on Linux is viable; current action majors
`actions/checkout@v7`, `astral-sh/setup-uv@v10`; the no-hardware expected output is in progress.md (252/459 ms, 100 %).

---

## Key Decisions

| Decision | Chosen | Phase | Rationale |
|---|---|---|---|
| Reader | examiners + supervisor | 2 | the docs support a marked thesis |
| Premise | correct, traceable, enforced; not "more docs" | 2 | stale docs are worse than none (Write the Docs) |
| In-code style | keep house headers | 2 | already consistent; rewrite = churn |
| Enforcement | pytest + GitHub Actions | 2 | drift returns with the next firmware change otherwise |
| Licence | pending supervisor | 4 | Byron's call; may involve UNSW |
| ADRs | six, Nygard format | 4 | one decision per record |
| Review mode | hold scope | 5 | due 10 Dec; scope already pressure-tested |

---

## Positions Where AI Pushed Back

| Topic | AI Position | User Response | Final Resolution |
|---|---|---|---|
| "Industry level" taken literally | CONTRIBUTING, SECURITY.md, Sphinx, per-function Google docstrings are overkill for a single-author thesis | accepted (chose examiners as the reader, kept house style) | listed as rejected in the plan |
| Generate one wire-format copy from the other | a cross-check test gives the same protection for far less code | not raised | test only (Unit 1) |
