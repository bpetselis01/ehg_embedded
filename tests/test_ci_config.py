"""Checks the CI and supply-chain files (Unit 14): workflows are valid YAML, use only the pinned actions the repository already uses, run on Linux with read-only permissions and no pull_request_target,
Dependabot covers the ecosystems that are pinned, and every Dockerfile base image has an explicit tag. Structural only; the workflows have never run on GitHub."""

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parents[1]
WORKFLOWS = sorted((ROOT / ".github/workflows").glob("*.yml"))
KNOWN_ACTIONS = {"actions/checkout@v7", "astral-sh/setup-uv@v10.3.2"}  # the only third-party actions already vetted in tests.yml


def load(path):
    doc = yaml.safe_load(path.read_text())
    doc["on"] = doc.get("on", doc.get(True))  # YAML turns a bare `on:` key into True
    return doc


@pytest.mark.parametrize("wf", WORKFLOWS, ids=lambda p: p.name)
def test_workflows_use_only_known_pinned_actions_and_linux_runners(wf):
    doc = load(wf)
    for name, job in doc["jobs"].items():
        assert job["runs-on"] == "ubuntu-latest", f"{wf.name}:{name} must run on Linux (macOS minutes cost 10x on a private repository)"
        for step in job["steps"]:
            if "uses" in step:
                assert step["uses"] in KNOWN_ACTIONS, f"{wf.name}:{name} uses {step['uses']}: only vetted, version-pinned actions are allowed"
    assert "pull_request_target" not in str(doc["on"]), "pull_request_target runs with secrets on untrusted code"


def test_the_security_workflow_has_read_only_permissions_and_the_authorship_check_is_kept():
    assert load(ROOT / ".github/workflows/security.yml")["permissions"] == {"contents": "read"}
    assert "co-authored-by" in (ROOT / ".github/workflows/tests.yml").read_text()


def test_shellcheck_covers_every_tracked_script_at_warning_level():
    cmd = next(s["run"] for s in load(ROOT / ".github/workflows/security.yml")["jobs"]["shellcheck"]["steps"] if "run" in s)
    assert "git ls-files '*.sh'" in cmd, "a new script must be checked without editing the workflow"
    assert "-S warning" in cmd


def test_dependabot_covers_every_pinned_ecosystem():
    cfg = yaml.safe_load((ROOT / ".github/dependabot.yml").read_text())
    pairs = {(u["package-ecosystem"], u["directory"]) for u in cfg["updates"]}
    assert {("uv", "/"), ("github-actions", "/"), ("terraform", "/deploy/terraform"), ("docker", "/backend"), ("docker", "/backend/ingest")} <= pairs
    assert all(u["schedule"]["interval"] == "weekly" for u in cfg["updates"]) and cfg["version"] == 2


def test_every_dockerfile_base_image_has_an_explicit_tag():
    found = 0
    for df in list((ROOT / "backend").rglob("*Dockerfile*")):
        for m in re.finditer(r"^FROM\s+(\S+)", df.read_text(), re.M):
            found += 1
            assert ":" in m.group(1) and not m.group(1).endswith(":latest"), f"{df.name}: {m.group(1)} needs a specific tag"
    assert found >= 2


def test_the_supply_chain_doc_says_what_is_not_wired():
    text = (ROOT / "docs/supply-chain.md").read_text()
    for tool in ("Syft", "Trivy", "cosign", "gitleaks", "CodeQL"):
        assert tool in text
    assert "Planned, not wired" in text and "never been pushed" in text
