# Codebase documentation: correct, traceable, enforced

**Date:** 2026-10-26
**Mode:** New Feature (Standard)
**Status:** Plan, not started

## Problem

The repo is already densely documented. Every source file has a PURPOSE/INPUT/OUTPUT header with worked examples, 95 % of
functions have docstrings, and there are 15 Markdown docs with no broken internal links. The gaps are elsewhere:

- **Facts that went stale.** [`main.c`](../../firmware/ehg_node/src/main.c) says 115200 baud (it is 460800).
  [`codebase-guide.md`](../codebase-guide.md) step 2 says the firmware "never contains a recording" (built-in mode exists since 2026-10-22).
  [`design/codebase-design.md`](../design/codebase-design.md) gives wire `version = 1` (it is 2). The README layout
  misses `fhir_export.py`, `watch_db.py`, `demo.sh` and `tests/`.
- **Claims an examiner cannot trace.** The thesis says "open, reproducible", but there is no LICENSE (so no one may legally
  reuse the code) and no single path from raw data to each H1/H2/H4 number with the expected output.
- **The "why" is scattered** across the design spec, three plans and the progress log.
- **Nothing stops drift.** The wire format lives in three places (`ehg_packet.h`, `packet.py`, `interface-spec.md`)
  and only the C header size is checked. There is no CI, although the repo has a GitHub remote.

Primary reader: **the examiners and supervisor** (BIOM4951). A wrong number in the docs makes them doubt the measured ones.

## Industry practice this follows (research 2026-10-26)

| Practice | Source | Used here as |
|---|---|---|
| Docs-as-code, "Current", "Unique" (one place per fact) | Write the Docs, Google documentation guide | drift sweep + doc tests |
| Minimum viable docs, delete dead docs | Google documentation guide | no new site, no per-function rewrite; superseded docs marked |
| Architecture Decision Records (Context, Decision, Status, Consequences) | Nygard 2011, MADR | `docs/decisions/` |
| Licence + citation file for research software | FAIR4RS R1.1, SSI minimal deposit, Lee 2018 rule 10 | `LICENSE`, `CITATION.cff` |
| Script docstring doubles as its `--help` | PEP 257 | `description=__doc__` on every CLI |
| Quick-start + reproduce-the-results path, errors that say the fix | Lee 2018 rules 3–4, 9 | README section + `docs/troubleshooting.md` |

Rejected as overkill for a single-author thesis repo: CONTRIBUTING, CODEOWNERS, SECURITY.md, a Sphinx/MkDocs site,
SemVer + per-release CHANGELOG (`progress.md` is the change log), arc42, docstring-coverage gates, and rewriting the
house header style into Google/Doxygen sections.

## Decisions (2026-10-26, with Byron)

| Decision | Chosen | Rejected |
|---|---|---|
| Primary reader | examiners + supervisor | future student, portfolio, open-source reuse |
| In-code style | **keep the house headers**, fix stale facts, fill missing `--help`, plain `shortcut:` notes | add Google `Args:`/Doxygen `@param` on top; replace headers outright |
| Drift prevention | **pytest doc checks + GitHub Actions** running `uv run pytest` | local tests only; no checks |

## Requirements

| ID | Requirement | Priority |
|----|-------------|----------|
| R1 | Every fact in tracked docs and code comments matches the code at HEAD (baud, wire version, modes, file lists, defaults); stale statements in historical docs are marked superseded, not silently rewritten | Must |
| R2 | A test fails if `ehg_packet.h` and `gateway/packet.py` disagree on version, header field layout/size, flag bits, or if the firmware and gateway disagree on the GATT UUIDs or control op codes | Must |
| R3 | A test fails if a relative Markdown link in a tracked doc points at a missing file, if any `*.py` / `*.sh` under `tools/ eval/ gateway/ viewer/ backend/` is missing from the codebase guide's file index, or if a baud rate in current docs/code differs from the board overlay | Must |
| R4 | A GitHub Actions workflow runs `uv sync` + `uv run pytest -q` on push and pull request, with no hardware, data download or Docker | Must |
| R5 | `LICENSE` (SPDX-identified) and `CITATION.cff` exist; README states licence and how to cite | Must |
| R6 | README has a "Reproducing the results" section: for H1, H2, H4 the command, the input, and the expected output (from progress run 3), and H3's status | Must |
| R7 | `docs/decisions/` holds short ADRs (Context / Decision / Status / Consequences) for the decisions an examiner will ask "why" about, back-dated from git history and linked from the docs | Should |
| R8 | `docs/troubleshooting.md` lists known failures as symptom / cause / fix, linked from README | Should |
| R9 | Every CLI's `--help` prints its module docstring; `# ponytail:` tags become `shortcut:` notes; the tracked `xiao_blinky` absolute symlink is removed from the repo | Should |
| R10 | `docs/codebase-guide.md` "Other docs" table marks each doc as current reference or historical record | Should |

## Success criteria

- `uv run pytest -q` passes locally and in GitHub Actions; deliberately changing `EHG_PKT_VERSION`, a doc link, or the
  overlay baud makes a named test fail.
- A reader can go from a fresh clone to each H1/H2/H4 number using only the README, and knows what output to expect.
- `git grep 115200` finds nothing outside historical research/plan docs.

## Scope boundaries

**In scope:** tracked docs, code comments/docstrings where a fact is wrong, CLI help, new LICENSE / CITATION.cff /
ADRs / troubleshooting / CI / doc tests, the local `CLAUDE.md` baud line (gitignored, but read every session).

**Out of scope:** behaviour changes to firmware, gateway, backend or eval; new docstrings on trivial functions
(`main`, `__init__`); test-function docstrings; rewriting plans and session logs (they are dated records);
generating one wire-format copy from the other (a test catches drift for far less code).

## Outstanding questions

| # | Question | Impact if wrong | Owner |
|---|---|---|---|
| Q1 | Which licence (MIT / Apache-2.0 / other), and does UNSW or the supervisor need a say? | a licence Byron cannot grant | Byron (Phase 4) |
| Q2 | Do the tests that need `data/raw` skip cleanly on a CI runner, and does `bleak` install on Linux? | CI red on day one | verify in Unit 4 |

Q1 answered 2026-10-26: **ask the supervisor first.** Unit 7 (LICENSE) waits; until then README says "Licence: pending".

## Implementation units

Order: tests first (red), then the fixes that turn them green, then CI, then the new docs.
`1 → 2 → 3 → 4`; `5` and `6` need only `1` (the link test covers them); `7` waits on Q1.

### Unit 1: Doc and wire-format consistency tests

**Goal:** One test file that fails on every kind of drift found on 2026-10-26.
**Requirements trace:** R2, R3
**Dependencies:** none
**Files:** `tests/test_docs.py` (new)
**Approach:** Plain pytest, standard library only, regex over the files (no C parser).
- `test_wire_version_and_flags`: `#define EHG_PKT_VERSION`, `EHG_FLAG_RETRANS BIT(0)`, `EHG_FLAG_24BIT BIT(1)`,
  `EHG_CRC_BYTES` in `ehg_packet.h` equal `packet.VERSION`, `FLAG_RETRANS`, `FLAG_24BIT`, `packet.CRC.size`.
- `test_wire_header_layout`: the `uint8_t/uint16_t/uint32_t` field types inside `struct ehg_pkt_hdr`, in order, map to
  `B/H/I` and equal `packet.HEADER.format` without the `<`; their summed size equals `packet.HEADER.size` and the
  `BUILD_ASSERT(... == 20` literal.
- `test_gatt_ids`: `EHG_UUID_BASE(n)` in `ehg_service.h` rebuilt as text equals `gateway.SERVICE_UUID/DATA_UUID/CTRL_UUID`
  for n = 1, 2, 3; `EHG_OP_START/STOP/RESEND` equal the gateway's `OP_*` constants.
- `test_relative_links_resolve`: every `](path)` in `git ls-files '*.md'` that is not `http(s)`/`mailto`/`#` exists on disk.
- `test_doc_paths_in_code_exist`: every `docs/<path>.md` written as plain text in a tracked source or config file
  (e.g. "Full table: docs/interface-spec.md 3." in `ehg_packet.h`) exists. Code comments cannot hold links, so this is their link check.
- `test_file_index_complete`: every tracked `*.py`/`*.sh` under `tools/ eval/ gateway/ viewer/ backend/ firmware/`
  appears (by file name) in `docs/codebase-guide.md`.
- `test_one_baud_rate`: the overlay's `current-speed = <N>` equals `tools.feed.BAUD`, and every `\d{5,7} baud` in
  README, `docs/*.md` (top level only: plans, sessions and research are dated records) and `firmware/ehg_node/src/*`
  equals N.
- `test_cli_help_is_module_docstring`: for each tracked module that builds an `ArgumentParser`, the call passes
  `description=__doc__` (text check, so nothing is imported or run).
**Patterns:** `tests/test_packet.py` (imports `gateway.packet` directly, `pythonpath = ["."]`); `ROOT = Path(__file__).parents[1]`.
**Test scenarios:**
- [ ] Today: wire, GATT and link tests pass; baud fails on `main.c:10`; file index and CLI help fail on the gaps listed in Problem
- [ ] Changing `EHG_PKT_VERSION` to 3 (or swapping two header fields) fails the wire tests with both values in the message
- [ ] A link to a renamed file fails naming the doc and the target
- [ ] Empty match (e.g. regex finds no `struct ehg_pkt_hdr`) fails loudly, never passes vacuously
**Verification:** `uv run pytest tests/test_docs.py -q` shows exactly the expected failures before Unit 2, all green after.
**Planning-time unknowns:** Deferred to implementation: whether `firmware/ehg_node/src/replay_data.h` (gitignored,
generated) needs excluding from the baud scan (it is not tracked, so `git ls-files` already skips it).

### Unit 2: Drift sweep

**Goal:** Every stale fact found on 2026-10-26 is correct or marked historical.
**Requirements trace:** R1, R9 (`ponytail` tags), R10
**Dependencies:** Unit 1
**Files:**
- `firmware/ehg_node/src/main.c`: header line 10, 115200 → 460800
- `docs/codebase-guide.md`: step 2 names both modes (serial feed default, built-in via `build.sh --record`);
  file index adds `tools/watch_db.py`, `tools/demo.sh`, `docs/troubleshooting.md`, `docs/decisions/`;
  "Other docs" table gets a Status column (current reference / historical record)
- `README.md`: layout table adds `fhir_export.py`, `watch_db.py`, `demo.sh`, `tests/`, `docs/decisions/`
- `docs/design/codebase-design.md`: banner under the title: "Historical design (2026-09-29). Current facts:
  interface-spec.md, firmware.md; decisions: decisions/". §3 version row gets "(now 2: CRC-16 trailer, see interface-spec §3)"
- `docs/research/nrf-ble-environment.md`: one-line note at the 115200 mentions: the project now uses 460800 (overlay)
- `gateway/tracking.py`: two `# ponytail:` → `# shortcut:` (wording kept)
- `CLAUDE.md` (gitignored, local only): baud 115200 → 460800; design pointer also names `docs/design/` and `docs/decisions/`
**Approach:** Edit only the stale sentence; do not rewrite surrounding text. Historical docs get a banner or note, never a silent rewrite.
**Patterns:** the existing "*(Superseded 2026-10-20: ...)*" note in `codebase-design.md` A5.
**Test scenarios:**
- [ ] Unit 1 baud, file-index and link tests go green
- [ ] `git grep -n 115200` hits only the research note's historical context
- [ ] `git grep -n ponytail` returns nothing
**Verification:** `uv run pytest -q` all green except the CLI-help test (Unit 3).
**Planning-time unknowns:** none.

### Unit 3: CLI help and repo hygiene

**Goal:** `--help` on every script prints its header; nothing tracked points outside the repo.
**Requirements trace:** R9
**Dependencies:** Unit 1
**Files:** `eval/integrity.py`, `eval/latency.py`, `tools/convert.py`, `tools/fhir_export.py`, `tools/make_replay.py`
(`argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)`);
`xiao_blinky` (`git rm --cached`; the symlink stays on disk) and `.gitignore` (add `xiao_blinky`).
**Approach:** Copy the exact call already used in `gateway/gateway.py:253`. No new help text: the module headers already are the usage text.
**Patterns:** `gateway/gateway.py`, `tools/feed.py`, `tools/watch_db.py`.
**Test scenarios:**
- [ ] Unit 1 CLI-help test goes green
- [ ] `uv run python eval/latency.py --help` prints the PURPOSE block, then the options
- [ ] `git ls-files xiao_blinky` is empty; `ls xiao_blinky` still resolves locally
**Verification:** full `uv run pytest -q` green.
**Planning-time unknowns:** none.

### Unit 4: GitHub Actions

**Goal:** Every push and pull request runs the test suite without hardware.
**Requirements trace:** R4
**Dependencies:** Unit 3 (suite must be green first)
**Files:** `.github/workflows/tests.yml` (new), `README.md` (one line under "tests": CI runs the same command)
**Approach:** `ubuntu-latest`, `actions/checkout@v7`, `astral-sh/setup-uv@v10` (latest majors on 2026-10-26), `uv sync --frozen`,
`uv run pytest -q`. Triggers: `push` and `pull_request`. No caching or matrix (YAGNI).
**Test scenarios:**
- [ ] Local stand-in for CI: a fresh `git worktree` (no `.venv`, no `data/raw`) runs `uv sync --frozen && uv run pytest -q` green, TPEHG test skipped
- [ ] `bleak` and `pyserial` resolve on Linux from `uv.lock` (`uv sync --frozen` succeeds in the worktree; Linux check happens on first push)
- [ ] A test that needs `data/raw` skips with its reason, not errors
**Verification:** worktree run green. First real run happens when Byron pushes; pushing stays his call.
**Planning-time unknowns:** none. Checked 2026-10-26: `uv.lock` is a universal lock with 409 manylinux wheel entries.

### Unit 5: Reproducing the results, troubleshooting, citation

**Goal:** An examiner can rerun each claim and recover from the known failures; the work is citable.
**Requirements trace:** R5 (citation, licence-pending line), R6, R8
**Dependencies:** Unit 1 (link test covers the new links)
**Files:**
- `README.md`: new "Reproducing the results" section after Quickstart: a table H1 / H2 / H4 / H3 → command, input
  (DK EHG-93C0, TPEHG tpehg1007, faults `drop=0.05,burst=3,corrupt=0.01,disc=120`, seed 3, 7 min), expected output
  (run 3, 2026-10-20: delivery 100.0000 % (8410/8410), 0 mismatches, H1 median/p95 259/478 ms), and
  H3 "not measured: needs 8 boards; `--simulate N` exercises the code path". A **no-hardware row** first, because most
  examiners have no DK: `gateway --simulate 1 --sim-loss 0.01` with the backend up → 100 % delivery, 0 mismatches,
  median/p95 252/459 ms (progress.md, labelled "tooling check, not H1/H2"). Each row names its prerequisites (backend up,
  data converted). Plus "Licence: pending" and "Citing" lines, and the README's top doc-map line reordered as a
  reading path: codebase guide → reproducing → decisions → troubleshooting.
- `docs/troubleshooting.md` (new): symptom / cause / fix rows: serial port busy (one process per port, nRF Terminal);
  NCS build fails on spaces (`~/thesisb` symlink); Bleak scan hangs (macOS Bluetooth permission for the terminal app);
  Docker takes 5–15 min to start containers (wait; don't restart: Supabase); Grafana not on 3000 (it is 3300);
  log lines with bad CRC are ignored by design; `starved` > 0 (feeder fell behind); built-in board answers `@ERR built-in record mode`.
- `tests/test_docs.py`: `test_citation_has_required_keys` (`cff-version`, `title`, `authors`, `message`)
- `CITATION.cff` (new): `cff-version: 1.2.0`, title, author Byron Petselis, UNSW, `type: software`, repository URL, no licence field until Q1.
**Approach:** Expected outputs are copied from `docs/progress.md` run 3, with the exact printed format taken from running
`eval/latency.py` / `eval/integrity.py` source (`print` lines), so the README shows what the reader will see.
**Patterns:** README's existing commented command blocks; the `progress.md` results tables.
**Test scenarios:**
- [ ] Link test passes with the new README/troubleshooting links
- [ ] Every command in the reproduce table appears verbatim in the Quickstart or `--help` (no invented flags)
- [ ] `CITATION.cff` parses as YAML and has `cff-version`, `title`, `authors`, `message`
**Verification:** pytest green; `python -c "import yaml"` not needed: a test reads `CITATION.cff` with a 5-line key check.
**Planning-time unknowns:** Deferred: the exact output lines of `eval/*.py`, read from the source at implementation time.

### Unit 6: Architecture decision records

**Goal:** The six decisions an examiner will ask "why" about each have one short record.
**Requirements trace:** R7
**Dependencies:** Unit 1
**Files:** `docs/decisions/0001-raw-int16-end-to-end.md`, `0002-digital-injection-over-serial.md`,
`0003-built-in-recording-build-time-option.md`, `0004-end-to-end-loss-recovery.md`,
`0005-h1-clock-offset-from-serial-sync.md`, `0006-local-mosquitto-influx27-grafana.md`;
links from `docs/codebase-guide.md` and the `codebase-design.md` banner (Unit 2 edits);
`tests/test_docs.py`: `test_adrs_have_sections` (Status, Context, Decision, Consequences in every record).
**Approach:** Nygard format, at most one page each: Title, Date (from the git commit that made the decision),
Status (Accepted / Amended by 000N), Context, Decision, Consequences (including the thesis hypothesis it serves),
and a Sources line pointing at the plan/progress section it was taken from. No new reasoning: each record restates
what the design spec, plans and progress log already decided, with dates:
0001 2026-09-29 (`cd4d06b`), 0002 2026-10-20 (`ca54a5c`, 460800 + RTS/CTS), 0003 2026-10-22 (`3eb063f`, amends 0002),
0004 2026-09-29 → v2 CRC 2026-10-20, 0005 2026-10-20, 0006 2026-09-29.
**Patterns:** Plain language as in `codebase-guide.md`; links relative.
**Test scenarios:**
- [ ] Link test passes for all records and their Sources links
- [ ] Every record has the five headings (a test greps `docs/decisions/*.md` for them)
- [ ] 0002 says "Amended by 0003"; 0003 says "Amends 0002"
**Verification:** pytest green; each record fits on one screen.
**Planning-time unknowns:** none.

### Unit 7: LICENSE (blocked on Q1)

**Goal:** The "open" claim holds legally.
**Requirements trace:** R5
**Dependencies:** Byron's answer after asking the supervisor
**Files:** `LICENSE`, `CITATION.cff` (`license:` SPDX id), `README.md` (replace "pending"), `pyproject.toml` (`license = "<SPDX>"`)
**Approach:** Standard licence text, copyright "2026 Byron Petselis".
**Test scenarios:**
- [ ] `CITATION.cff` `license` equals the `pyproject.toml` licence
- [ ] README no longer says "pending"
- [ ] Link test passes for `LICENSE`
**Verification:** pytest green; GitHub shows the licence in the repo sidebar.
**Planning-time unknowns:** **Resolve before implementing:** Q1.

## Validation (2026-10-26, scope mode: hold)

**CEO review.** No P0. P1: the reproduce section only had hardware runs, but examiners will mostly not have a DK.
Fixed: a no-hardware row first (Unit 5). Silent-failure check: every Unit 1 test fails loudly on an empty match.

**Design review (docs as a reader's interface):**

| Dimension | Score | Change to reach 10 |
|---|---|---|
| Clarity | 8 | – |
| Hierarchy | 6 | README's top doc line becomes a reading path (guide → reproducing → decisions → troubleshooting) (Unit 5) |
| Consistency | 8 | – |
| Feedback | 8 | test failure messages name the file and both values (Unit 1) |
| Error recovery | 5 | `docs/troubleshooting.md` with symptom / cause / fix (Unit 5) |
| First use (empty state) | 5 | no-hardware reproduce row with prerequisites (Unit 5) |
| Accessibility | 8 | – (Mermaid diagrams already have a numbered text walkthrough) |

**Engineering review.**
- Scope: complements, not duplicates, `tests/test_packet.py` (Python self-consistency) and `BUILD_ASSERT` (C size).
- P1: doc paths written in code comments were unchecked (Markdown links only). Fixed: `test_doc_paths_in_code_exist` (Unit 1).
- P1: Units 5 and 6 added tests without listing `tests/test_docs.py`. Fixed.
- Regex over C is brittle by design: a reformatted struct fails loudly, never passes silently. Accepted.

Coverage:

| Change | Checked by |
|---|---|
| wire constants, header layout, UUIDs, op codes | Unit 1 wire/GATT tests |
| links in docs, doc paths in code | Unit 1 link tests |
| file index, baud, CLI help | Unit 1 tests |
| CI workflow | fresh-worktree run (Unit 4), first push |
| citation, ADR structure | Unit 5/6 key and heading tests |
| ADR and README *content* is true | **not automated**: Byron reads each ADR against its Sources line |
| `#anchor` part of links | **not checked** (accepted: anchors break rarely and loudly to a reader) |
