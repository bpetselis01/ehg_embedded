"""The secret-scan allowlist must stay narrow. gitleaks combines `paths` and `regexes` with OR unless condition = "AND", which made a first version skip EVERY finding in the allowed file and the
fake key everywhere (a security review caught it; checked with throwaway trees on gitleaks 8.30.1, 2026-11-04). With "secret"/"match" targets even AND did not narrow it; "line" did."""

import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[1]
CFG = tomllib.loads((ROOT / ".gitleaks.toml").read_text())


def test_default_rules_stay_on():
    assert CFG["extend"]["useDefault"] is True


def test_an_allowlist_that_names_a_file_must_AND_it_with_an_exact_line():
    for a in CFG["allowlists"]:
        if a.get("paths"):
            assert a.get("condition") == "AND", a["description"]
            assert a.get("regexTarget") == "line" and a.get("regexes"), "narrow the finding itself, not only the file"
            assert all(r.startswith("^") and r.endswith("$") for r in a["regexes"]), "anchor the whole line"
            assert a.get("targetRules"), "name the one rule it silences"


def test_there_are_exactly_two_allowlists_and_no_blanket_path_or_commit_rules():
    assert len(CFG["allowlists"]) == 2
    for a in CFG["allowlists"]:
        assert not a.get("commits") and not a.get("stopwords"), a["description"]
        assert not (a.get("paths") and not a.get("regexes")), "a path alone would hide every secret in it"
