"""Checks the session-limit resume pin, docs/handoff.md, stays usable: when the file exists it has every heading
the resume protocol reads and its 'Resume from' names a real unit (Unit 0..17) or 'Plan complete'."""

import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
HANDOFF = ROOT / "docs/handoff.md"
HEADINGS = (
    "Resume from",
    "State of the repo",
    "Decisions made this session (not yet in an ADR or progress.md)",
    "Open questions waiting for Byron",
    "Byron's pending manual steps",
    "Next three actions",
    "Warnings / traps",
)
LAST_UNIT = 17


def section(text: str, heading: str) -> str:
    """Body of the '## heading' section, up to the next '## ' heading."""
    m = re.search(rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    assert m, f"docs/handoff.md has no '## {heading}' section"
    return m.group(1)


def test_handoff_has_required_headings():
    if not HANDOFF.exists():
        return
    text = HANDOFF.read_text()
    assert re.match(r"# Handoff pin \(written \d{4}-\d{2}-\d{2}", text), "first line must be '# Handoff pin (written <date>…'"
    for heading in HEADINGS:
        section(text, heading)


def test_handoff_resume_unit_is_valid():
    if not HANDOFF.exists():
        return
    resume = section(HANDOFF.read_text(), "Resume from")
    m = re.search(r"^- Unit: (Plan complete|Unit (\d+)\b)", resume, re.M)
    assert m, "'Resume from' needs a line '- Unit: Unit <n>, step …' or '- Unit: Plan complete'"
    if m.group(2) is not None:
        assert 0 <= int(m.group(2)) <= LAST_UNIT, f"Unit {m.group(2)} is not in 0..{LAST_UNIT}"
    assert "<" not in resume, "'Resume from' still holds template placeholders"
