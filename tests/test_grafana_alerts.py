"""Checks the provisioned Grafana alert rules: fixed thresholds, the local datasource only, no clinical wording, nothing that Grafana would expand or send elsewhere."""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]
FILE = ROOT / "backend/grafana/provisioning/alerting/ehg-health.yml"


def rules():
    return {r["uid"]: r for r in yaml.safe_load(FILE.read_text())["groups"][0]["rules"]}


def threshold(rule):
    return next(d for d in rule["data"] if d["refId"] == "C")["model"]["conditions"][0]["evaluator"]["params"][0]


def test_the_five_rules_exist_and_evaluate_every_10_s():
    group = yaml.safe_load(FILE.read_text())["groups"][0]
    assert group["interval"] == "10s" and group["folder"] == "EHG"
    assert set(rules()) == {"ehg-node-offline", "ehg-loss-rate", "ehg-latency-p95", "ehg-flatline", "ehg-saturation"}


def test_thresholds():
    r = rules()
    assert threshold(r["ehg-node-offline"]) == 5
    assert threshold(r["ehg-loss-rate"]) == 1
    assert threshold(r["ehg-latency-p95"]) == 2
    assert threshold(r["ehg-saturation"]) == 5
    assert threshold(r["ehg-flatline"]) == 0


def test_every_rule_uses_the_influx_datasource_and_then_expressions():
    for uid, rule in rules().items():
        assert rule["condition"] == "C", uid
        assert [d["refId"] for d in rule["data"]] == ["A", "B", "C"], uid
        assert rule["data"][0]["datasourceUid"] == "influx", uid
        assert {d["datasourceUid"] for d in rule["data"][1:]} == {"__expr__"}, uid
        assert "v.defaultBucket" in rule["data"][0]["model"]["query"], uid


def test_every_query_returns_a_time_column():
    for uid, rule in rules().items():
        assert "_time: now()" in rule["data"][0]["model"]["query"], uid


def test_no_contact_point_so_nothing_leaves_the_computer():
    assert not {"contactPoints", "policies"} & set(yaml.safe_load(FILE.read_text()))


def test_no_clinical_wording():
    text = " ".join(f"{r['title']} {r['annotations']['summary']}" for r in rules().values())
    assert not re.search(r"diagnos|prognos|preterm|pre-term|labou?r|contraction|pregnan|gestation|obstetric|patient|predict", text, re.I)
