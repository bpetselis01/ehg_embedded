"""Checks the Prometheus rule files, since promtool is not installed here: structure, that every metric a rule uses is defined somewhere in this repository (so a renamed metric cannot silently blind an alert),
that every recording rule an alert uses exists, that alerts hold no sensitive content, and that Prometheus is actually pointed at the rules and at Alertmanager. It does NOT evaluate the expressions."""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]
OBS = ROOT / "deploy/observability"
RULES = {f.name: yaml.safe_load(f.read_text()) for f in (OBS / "rules").glob("*.yml")}
ALL = [r for doc in RULES.values() for g in doc["groups"] for r in g["rules"]]
PROMQL_WORDS = {"sum_over_time", "max_over_time", "avg_over_time", "increase", "absent", "max", "sum", "by", "or", "and", "time", "up", "on", "ignoring", "without", "rate", "for"}
EXTERNAL = {"up"}  # produced by Prometheus itself


def source_text() -> str:
    """Every file where a metric could be defined."""
    parts = [p.read_text() for d in ("backend", "deploy/cloud-init", "deploy/backup") for p in (ROOT / d).rglob("*") if p.is_file() and p.suffix in {".py", ".sh", ".tftpl", ".yml", ".yaml"}]
    return "\n".join(parts)


def metrics_in(expr: str) -> set[str]:
    """Metric names (not recording-rule names, labels or functions) used in an expression."""
    expr = re.sub(r'"[^"]*"', '""', expr)
    names = set(re.findall(r"\b([a-z][a-z0-9_]*)\b(?=\s*(?:\{|\[|\)|\s|$|[<>=!/*+-]))", expr))
    return {n for n in names if n not in PROMQL_WORDS and not n.isdigit() and ":" not in n and (n.startswith("ehg_") or n in EXTERNAL)}


def test_every_rule_file_parses_with_named_groups_and_rules():
    assert set(RULES) == {"slo.yml", "alerts.yml"}
    for doc in RULES.values():
        for g in doc["groups"]:
            assert g["name"] and g["rules"]
            for r in g["rules"]:
                assert r.get("expr") and (("record" in r) != ("alert" in r))


def test_every_metric_used_is_defined_somewhere_in_the_repository():
    text = source_text()
    for r in ALL:
        for m in metrics_in(r["expr"]):
            base = re.sub(r"_total$", "", m)  # prometheus_client adds _total to counter names
            assert m in EXTERNAL or base in text, f"{r.get('alert') or r.get('record')} uses {m}, which nothing in the repository defines"


def test_alerts_only_use_recording_rules_that_exist():
    recorded = {r["record"] for r in ALL if "record" in r}
    for r in ALL:
        if "alert" in r:
            for used in re.findall(r"\behg:[a-z0-9_:]+", r["expr"]):
                assert used in recorded, f"{r['alert']} uses {used}"
    assert all(re.fullmatch(r"ehg:[a-z0-9_]+:[a-z0-9]+", n) for n in recorded)


def test_alerts_are_named_unique_labelled_and_debounced():
    alerts = [r for r in ALL if "alert" in r]
    assert len({a["alert"] for a in alerts}) == len(alerts) and len(alerts) == 4
    for a in alerts:
        assert a["labels"]["severity"] in {"page", "ticket"}, a["alert"]
        assert {"summary", "detail"} <= set(a["annotations"]), a["alert"]
        assert "for" in a or a["alert"] == "EhgBackupStale", f"{a['alert']} fires on one bad evaluation"


def test_alert_text_carries_no_sensitive_content_or_templated_labels():
    for a in (r for r in ALL if "alert" in r):
        text = " ".join(a["annotations"].values())
        assert "{{" not in text, "no label values (node ids, hostnames) in a message that leaves the VM"
        assert not re.search(r"password|secret|token|key|serial|@", text, re.I), a["alert"]


def test_the_documented_backup_target_is_in_the_rules():
    expr = {r["alert"]: r["expr"] for r in ALL if "alert" in r}
    assert "26 * 3600" in expr["EhgBackupStale"]


def test_prometheus_loads_the_rules_and_talks_to_alertmanager_and_compose_mounts_them():
    prom = yaml.safe_load((OBS / "prometheus.yml").read_text())
    assert prom["rule_files"] == ["/etc/prometheus/rules/*.yml"]
    assert prom["alerting"]["alertmanagers"][0]["static_configs"][0]["targets"] == ["alertmanager:9093"]
    jobs = {j["job_name"] for j in prom["scrape_configs"]}
    assert {"node", "postgres"} <= jobs
    compose = yaml.safe_load((ROOT / "deploy/compose/docker-compose.cloud.yml").read_text())
    assert "../observability/rules:/etc/prometheus/rules:ro" in compose["services"]["prometheus"]["volumes"]


# ---------------------------------------------------------------- dashboards as code
import json  # noqa: E402

DASH = json.loads((OBS / "grafana/dashboards/pipeline-overview.json").read_text())
EXTERNAL_PANEL_METRICS = {"container_memory_working_set_bytes", "ALERTS"}


def test_dashboard_panels_have_unique_ids_titles_and_known_datasources():
    ds = {d["uid"] for d in yaml.safe_load((OBS / "grafana/datasources.yml").read_text())["datasources"]}
    assert ds == {"prometheus", "loki"}
    ids = [p["id"] for p in DASH["panels"]]
    assert len(set(ids)) == len(ids) and len({p["title"] for p in DASH["panels"]}) == len(ids)
    for p in DASH["panels"]:
        assert p["datasource"]["uid"] in ds and all(t["datasource"]["uid"] in ds for t in p["targets"]), p["title"]
        assert p["description"], f"{p['title']} must say what it shows and what it does not"
    assert DASH["editable"] is False and DASH["uid"] == "ehg-pipeline"


def test_dashboard_queries_use_only_recording_rules_and_metrics_that_exist():
    recorded = {r["record"] for r in ALL if "record" in r}
    text = source_text()
    for p in DASH["panels"]:
        for t in p["targets"]:
            if p["datasource"]["uid"] != "prometheus":
                continue
            for used in re.findall(r"\behg:[a-z0-9_:]+", t["expr"]):
                assert used in recorded, f"{p['title']} uses {used}"
            for m in metrics_in(t["expr"]):
                assert m in EXTERNAL or m in EXTERNAL_PANEL_METRICS or re.sub(r"_total$", "", m) in text, f"{p['title']} uses {m}"


def test_dashboards_are_provisioned_read_only_and_mounted():
    prov = yaml.safe_load((OBS / "grafana/dashboards.yml").read_text())["providers"][0]
    assert prov["allowUiUpdates"] is False and prov["options"]["path"] == "/var/lib/grafana-dashboards"
    vols = yaml.safe_load((ROOT / "deploy/compose/docker-compose.cloud.yml").read_text())["services"]["grafana"]["volumes"]
    assert "../observability/grafana/dashboards:/var/lib/grafana-dashboards:ro" in vols and "../observability/grafana/dashboards.yml:/etc/grafana/provisioning/dashboards/dashboards.yml:ro" in vols


def test_the_alertmanager_example_is_not_loaded_and_the_live_config_stays_startable():
    live = yaml.safe_load((OBS / "alertmanager.yml").read_text())
    assert live["route"]["receiver"] == "null", "the live config must not reference a file or URL that does not exist yet"
    ex = yaml.safe_load((OBS / "alertmanager.ntfy.example.yml").read_text())
    assert ex["receivers"][0]["webhook_configs"][0]["url_file"].startswith("/") and "http" not in json.dumps(ex), "the webhook address is read from a secret file, never written in git"


def test_every_scrape_target_is_a_compose_service_and_none_is_known_to_be_down_by_design():
    """EhgTargetDown pages on `up == 0`: a target that can never be up (Grafana with metrics off) would page for ever (independent review)."""
    prom = yaml.safe_load((OBS / "prometheus.yml").read_text())
    compose = yaml.safe_load((ROOT / "deploy/compose/docker-compose.cloud.yml").read_text())["services"]
    for job in prom["scrape_configs"]:
        for target in job["static_configs"][0]["targets"]:
            host = target.split(":")[0]
            assert host in compose or host == "prometheus", f"{job['job_name']}: {host} is not a service in the cloud compose file"
            if host == "grafana":
                assert compose["grafana"]["environment"]["GF_METRICS_ENABLED"] == "true", "Grafana metrics are off: do not scrape it"


def test_healthchecks_that_the_review_found_fragile_are_robust():
    compose = yaml.safe_load((ROOT / "deploy/compose/docker-compose.cloud.yml").read_text())["services"]
    assert "-h 127.0.0.1" in compose["postgres"]["healthcheck"]["test"][1], "over TCP, so the init-time server (unix socket only) does not count as ready"
    assert "/grafana/api/health" in compose["grafana"]["healthcheck"]["test"][1], "Grafana is served from the sub-path"
    assert "nc -w 1" in compose["mosquitto"]["healthcheck"]["test"][1], "fallback for a busybox nc without -z"

