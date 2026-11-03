"""Fleet-level service-level indicators for Prometheus: what the SLO recording rules are computed from (docs/slo.md, deploy/observability/rules/slo.yml).

PURPOSE: The SLOs of decision 0016 need numbers Prometheus can scrape. The per-node health rows already exist (ehg_health, written once a minute by the triage engine); this exposes the same minute as fleet gauges, and the lag of the bus consumers.
INPUT: the health rows the engine hands its sink, e.g. [{"node": "EHG-1", "rx": 590, "missing": 10, "p95_latency_ms": 480.0}, ...]; a JetStream pull subscription for observe_consumer().
OUTPUT: gauges in the registry given at construction:
    ehg_fleet_packets_60s{kind="rx"|"missing"}   packets received / still missing over the last 60 s, summed over nodes (a RATIO is computed in PromQL, so an empty minute is 0/0 = no data, never "100 %")
    ehg_fleet_latency_p95_seconds                the WORST node's 60 s p95 latency; NaN when no node had latency data
    ehg_fleet_nodes_reporting                    number of nodes in the last minute's rows
    ehg_consumer_pending{consumer}               (class ConsumerLag) messages the consumer has not yet been given or has not yet acknowledged; NaN when the broker could not be asked
HOW: wrap(sink) returns a sink that sets the gauges FIRST and then calls the original (the database write), so a failing database does not blank the gauges. Unknown is NaN, not zero.
  LIMIT: the latency is the triage worker's approximation (decision 0011); the authoritative H1 figure still comes from eval/latency.py. Consumer lag is a count of messages, not seconds: with the nominal 2 packets/s per node it converts to a time, which docs/slo.md states.
ROLE IN THESIS: Measured service levels of the cloud core (reported as measured, inferred or not verified; no SLA).
BEFORE -> AFTER: Before: only per-node rows in the database. After: fleet gauges Prometheus can alert on.
"""

from prometheus_client import Gauge

NAN = float("nan")


class FleetSli:
    """Create once per process with the CollectorRegistry that the API serves on /metrics."""

    def __init__(self, registry):
        self.packets = Gauge("ehg_fleet_packets_60s", "Packets over the last 60 s, summed over nodes", ["kind"], registry=registry)
        self.latency = Gauge("ehg_fleet_latency_p95_seconds", "Worst node's 60 s p95 latency in seconds (NaN if unknown)", registry=registry)
        self.nodes = Gauge("ehg_fleet_nodes_reporting", "Nodes in the last health minute", registry=registry)
        self.latency.set(NAN)

    def update(self, rows: list[dict]) -> None:
        """Set the fleet gauges from one minute of health rows."""
        self.packets.labels("rx").set(sum(r["rx"] or 0 for r in rows))
        self.packets.labels("missing").set(sum(r["missing"] or 0 for r in rows))
        lat = [r["p95_latency_ms"] for r in rows if r["p95_latency_ms"] is not None]
        self.latency.set(max(lat) / 1000.0 if lat else NAN)
        self.nodes.set(len(rows))

    def wrap(self, inner):
        """A sink: gauges first, then `inner(rows)`."""
        def sink(rows):
            """Update the gauges, then store the rows."""
            self.update(rows)
            return inner(rows)
        return sink


class ConsumerLag:
    """One gauge per JetStream consumer; used by the triage service and the writer."""

    def __init__(self, registry):
        self.pending = Gauge("ehg_consumer_pending", "Messages not yet delivered or acknowledged for a JetStream consumer (NaN if unknown)", ["consumer"], registry=registry)

    async def observe(self, name: str, sub) -> None:
        """Read the consumer's backlog from the broker (call every few seconds)."""
        try:
            info = await sub.consumer_info()
            self.pending.labels(name).set(info.num_pending + info.num_ack_pending)
        except Exception:  # noqa: BLE001  (any failure to ask means "unknown")
            self.pending.labels(name).set(NAN)
