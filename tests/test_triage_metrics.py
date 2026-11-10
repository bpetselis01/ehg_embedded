"""Checks what the triage service exposes to Prometheus: the counters the stream worker increments must actually appear on /metrics (they did not at first: they sat in a registry nobody served),
and the fleet-level SLI gauges that the SLO recording rules (deploy/observability/rules/) are built on."""

import math

from fastapi.testclient import TestClient
from prometheus_client import CollectorRegistry, Counter

from backend.jwtauth import Authenticator
from backend.triage.app import create_app
from backend.triage.slis import ConsumerLag, FleetSli


class NoStore:
    def list_alerts(self, **k):
        return []


def app_with(registry):
    return create_app(store=NoStore(), audit=None, auth=Authenticator("dev"), registry=None, metrics=registry)


def scrape(app):
    return TestClient(app).get("/metrics").text


def test_counters_registered_in_the_services_registry_are_served():
    reg = CollectorRegistry()
    bad = Counter("ehg_triage_bad_messages", "x", registry=reg)
    bad.inc(3)
    text = scrape(app_with(reg))
    assert "ehg_triage_bad_messages_total 3.0" in text and 'ehg_triage_alerts{state="open"} 0.0' in text


def test_fleet_sli_sums_packets_and_takes_the_worst_latency():
    reg = CollectorRegistry()
    sink_calls = []
    sli = FleetSli(reg)
    sink = sli.wrap(sink_calls.append)
    rows = [{"node": "A", "rx": 590, "missing": 10, "p95_latency_ms": 400.0}, {"node": "B", "rx": 600, "missing": 0, "p95_latency_ms": 900.0}, {"node": "C", "rx": 0, "missing": 0, "p95_latency_ms": None}]
    sink(rows)
    text = scrape(app_with(reg))
    assert 'ehg_fleet_packets_60s{kind="rx"} 1190.0' in text and 'ehg_fleet_packets_60s{kind="missing"} 10.0' in text
    assert "ehg_fleet_latency_p95_seconds 0.9" in text and "ehg_fleet_nodes_reporting 3.0" in text
    assert sink_calls == [rows], "the wrapped sink still gets the rows (the database write is not skipped)"


def test_no_latency_data_is_nan_not_zero_so_an_empty_minute_cannot_look_fast():
    reg = CollectorRegistry()
    FleetSli(reg).wrap(lambda rows: None)([{"node": "A", "rx": 0, "missing": 0, "p95_latency_ms": None}])
    assert math.isnan(reg.get_sample_value("ehg_fleet_latency_p95_seconds"))


def test_a_failing_inner_sink_does_not_stop_the_gauges_and_vice_versa():
    reg = CollectorRegistry()
    def boom(rows):
        raise RuntimeError("db down")
    sink = FleetSli(reg).wrap(boom)
    try:
        sink([{"node": "A", "rx": 5, "missing": 0, "p95_latency_ms": 100.0}])
    except RuntimeError:
        pass
    assert reg.get_sample_value("ehg_fleet_packets_60s", {"kind": "rx"}) == 5.0, "the gauges are set before the database write is tried"


def test_consumer_pending_gauge_reports_unprocessed_messages():
    import asyncio

    class Info:
        num_pending, num_ack_pending = 7, 3

    class Sub:
        async def consumer_info(self):
            return Info()

    class Broken:
        async def consumer_info(self):
            raise ConnectionError("nats down")

    reg = CollectorRegistry()
    lag = ConsumerLag(reg)
    asyncio.run(lag.observe("triage", Sub()))
    assert reg.get_sample_value("ehg_consumer_pending", {"consumer": "triage"}) == 10.0
    asyncio.run(lag.observe("triage", Broken()))
    assert math.isnan(reg.get_sample_value("ehg_consumer_pending", {"consumer": "triage"})), "an unreadable consumer is NaN (unknown), never a comforting 0"
