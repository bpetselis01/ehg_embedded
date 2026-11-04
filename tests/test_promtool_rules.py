"""Runs the promtool unit tests of the SLO and alert rules (deploy/observability/tests/rules_test.yml) in the pinned Prometheus image.

Only with EHG_INTEGRATION=1 and a running Docker daemon (it never starts one). The file also has a plain check that every alert has a test, so a new alert cannot be added untested."""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parents[1]
OBS = ROOT / "deploy/observability"
IMAGE = re.search(r"prom/prometheus:\S+", (ROOT / "deploy/compose/docker-compose.cloud.yml").read_text()).group(0)


def docker_up() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=15).returncode == 0
    except subprocess.TimeoutExpired:
        return False


def test_every_alert_has_a_promtool_test():
    alerts = {r["alert"] for g in yaml.safe_load((OBS / "rules/alerts.yml").read_text())["groups"] for r in g["rules"]}
    tested = {a["alertname"] for t in yaml.safe_load((OBS / "tests/rules_test.yml").read_text())["tests"] for a in t.get("alert_rule_test", [])}
    assert alerts <= tested, f"alerts without a unit test: {sorted(alerts - tested)}"


def test_every_alert_is_tested_both_ways():
    """At least one case where it fires and one where it stays quiet, so an expression that is always true or never true cannot pass."""
    cases = [a for t in yaml.safe_load((OBS / "tests/rules_test.yml").read_text())["tests"] for a in t.get("alert_rule_test", [])]
    for name in {c["alertname"] for c in cases}:
        mine = [c for c in cases if c["alertname"] == name]
        assert any(c["exp_alerts"] for c in mine), f"{name}: no case where it fires"
        assert any(not c["exp_alerts"] for c in mine), f"{name}: no case where it stays quiet"


@pytest.mark.skipif(os.environ.get("EHG_INTEGRATION") != "1" or not docker_up(), reason="needs EHG_INTEGRATION=1 and a running Docker daemon")
def test_promtool_accepts_the_rule_unit_tests():
    r = subprocess.run(["docker", "run", "--rm", "--entrypoint", "promtool", "-v", f"{OBS}:/o:ro", IMAGE, "test", "rules", "/o/tests/rules_test.yml"], capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stdout + r.stderr
