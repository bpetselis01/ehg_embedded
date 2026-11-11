"""Keeps docs/regulatory.md honest: every test file it names exists, the intended-purpose statement is exact, and no forbidden claim wording (compliant, certified, approved) appears outside the sentences that deny it."""

import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
DOC = (ROOT / "docs/regulatory.md").read_text()
PURPOSE = "Research prototype for measuring wireless telemetry performance. Not for clinical decision-making. Not supplied to any person for patient care. Makes no diagnostic or predictive claims."


def test_every_test_file_named_exists():
    names = set(re.findall(r"`(test_[a-z0-9_]+)`", DOC))
    assert len(names) >= 20
    for n in names:
        assert (ROOT / "tests" / f"{n}.py").exists(), f"docs/regulatory.md names {n}, which does not exist"


def test_intended_purpose_is_exact_and_first():
    assert PURPOSE in DOC and DOC.index(PURPOSE) < 400


def test_no_compliance_claims():
    for line in DOC.splitlines():
        if re.search(r"\b(compliant with|certified|approved by|conforms to|conformance)\b", line, re.I):
            assert re.search(r"\b(not|no|never|cannot|without|nothing|neither)\b|AU Core", line, re.I), f"possible claim: {line[:100]}"
    assert "designed with reference to" in DOC.lower()


def test_every_test_named_in_security_md_exists_as_a_file_or_a_function():
    text = (ROOT / "docs/security.md").read_text()
    functions = {m for p in (ROOT / "tests").glob("test_*.py") for m in re.findall(r"^def (test_\w+)\(", p.read_text(), re.M)}
    for n in set(re.findall(r"`(test_[a-z0-9_]+)(?:\.py)?`", text)):
        assert (ROOT / "tests" / f"{n}.py").exists() or n in functions, f"docs/security.md names {n}, which does not exist"
