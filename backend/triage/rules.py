"""Pipeline-health alert rules, hysteresis, severity score and worklist order, as pure functions (decision 0011).

PURPOSE: Decide, from numbers about the data path, which operational alerts are active, how serious a node looks, and in what order an operator should look at nodes. Nothing here is clinical:
  no vital sign, no contraction index, no classifier. The authoritative H1/H2/H4 numbers still come from eval/latency.py and eval/integrity.py.
INPUT: one Snapshot per node per second (what the stream consumer measured): seconds since the last packet, packet interval, gateway status, packets missing for longer than the retry budget,
  received/missing counts over 60 s, 60 s p95 latency, flat and saturated channel measures, whether the sequence numbering restarted.
  e.g. Snapshot(now=1000.0, last_packet_t=990.0, packet_interval_s=0.5, gateway_status="online", rx_60=100, missing_60=0, ...)
OUTPUT: evaluate(snapshot) -> {rule_id: "warning" | "critical" | None}; RuleTracker.update(node, conditions) -> events [("open", rule, severity), ("update", rule, severity), ("clear", rule)];
  node_score(alerts, now) -> int; band(score); worklist(rows) -> rows sorted most urgent first.
  e.g. a node silent for 12 s with the gateway online -> {"NODE_OFFLINE": "critical", ...}; the same silence with the gateway's status "offline" -> {"GATEWAY_OFFLINE": "critical"} (the silence is the gateway's, not the node's).
HOW: Rules and defaults follow the prompt's section 6; every threshold is a field of Config. Anti-fatigue: a rule opens only after RAISE_AFTER (3) consecutive bad evaluations and clears after
  CLEAR_AFTER (10) consecutive healthy ones; one open alert per (node, rule) (the tracker holds one state per pair). Score: highest weight (critical 100, warning 50) + 5 per extra distinct open
  rule (capped 20) + min(10, minutes the oldest unacknowledged alert has been open / 6). Bands: >= 100 critical, 50 to 99 warning, 0 stable.
ROLE IN THESIS: The operator view of pipeline health (docs/decisions/0011-triage-service-pipeline-health-not-clinical.md). It makes no diagnostic or predictive claim.
BEFORE -> AFTER: Before: nothing watched the pipeline except Grafana panels. After: rules with hysteresis and an ordered worklist (consumed by backend/triage/stream.py and the API).
"""

from dataclasses import dataclass, field

WEIGHT = {"critical": 100, "warning": 50}
RULES = ("NODE_OFFLINE", "GATEWAY_OFFLINE", "LOSS_UNRECOVERED", "LOSS_RATE", "LATENCY_P95", "FLATLINE", "SATURATION", "SEQ_RESTART")
RAISE_AFTER, CLEAR_AFTER = 3, 10


@dataclass
class Config:
    """Thresholds (all configurable; defaults from the prompt's section 6)."""

    offline_min_s: float = 5.0
    offline_intervals: float = 3.0
    loss_rate: float = 0.01
    loss_rate_min_packets: int = 10
    latency_warn_ms: float = 2000.0
    latency_crit_ms: float = 5000.0
    latency_enabled: bool = True  # off until the clock offset between the gateway host and the triage host is configured
    saturation_pct: float = 5.0


@dataclass
class Snapshot:
    """What was measured about one node at one second."""

    now: float
    last_packet_t: float | None
    packet_interval_s: float
    gateway_status: str | None = None
    unrecovered: int = 0
    rx_60: int = 0
    missing_60: int = 0
    latency_p95_ms: float | None = None
    flatline_channels: int = 0
    n_channels: int = 0
    saturated_pct: float = 0.0
    seq_restarted: bool = False


def evaluate(s: Snapshot, cfg: Config = Config()) -> dict[str, str | None]:
    """The instantaneous condition of every rule for this snapshot (None = healthy). Pure: does not touch s."""
    out: dict[str, str | None] = {r: None for r in RULES}
    silent_for = None if s.last_packet_t is None else s.now - s.last_packet_t
    gateway_down = s.gateway_status == "offline"
    out["GATEWAY_OFFLINE"] = "critical" if gateway_down else None
    if silent_for is not None and silent_for > max(cfg.offline_min_s, cfg.offline_intervals * s.packet_interval_s) and not gateway_down:
        out["NODE_OFFLINE"] = "critical"  # silence while the gateway is fine is the node's
    out["LOSS_UNRECOVERED"] = "critical" if s.unrecovered > 0 else None
    total = s.rx_60 + s.missing_60
    if total >= cfg.loss_rate_min_packets and s.missing_60 / total > cfg.loss_rate:
        out["LOSS_RATE"] = "warning"
    if cfg.latency_enabled and s.latency_p95_ms is not None:
        out["LATENCY_P95"] = "critical" if s.latency_p95_ms > cfg.latency_crit_ms else "warning" if s.latency_p95_ms > cfg.latency_warn_ms else None
    if s.flatline_channels > 0:
        out["FLATLINE"] = "critical" if s.n_channels and s.flatline_channels >= s.n_channels else "warning"
    if s.saturated_pct >= cfg.saturation_pct:
        out["SATURATION"] = "warning"
    out["SEQ_RESTART"] = "warning" if s.seq_restarted else None
    return out


@dataclass
class _Track:
    bad: int = 0
    good: int = 0
    open: bool = False
    severity: str | None = None


@dataclass
class RuleTracker:
    """Hysteresis per (node, rule): open after RAISE_AFTER bad evaluations in a row, clear after CLEAR_AFTER healthy ones in a row."""

    raise_after: int = RAISE_AFTER
    clear_after: int = CLEAR_AFTER
    _t: dict = field(default_factory=dict)

    def update(self, node: str, conditions: dict[str, str | None]) -> list[tuple]:
        """Feed one evaluation; returns the events it causes, e.g. [("open", "NODE_OFFLINE", "critical")]."""
        events = []
        for rule, severity in conditions.items():
            t = self._t.setdefault((node, rule), _Track())
            if severity is not None:
                t.bad, t.good = t.bad + 1, 0
                if not t.open and t.bad >= self.raise_after:
                    t.open, t.severity = True, severity
                    events.append(("open", rule, severity))
                elif t.open and severity != t.severity:
                    t.severity = severity
                    events.append(("update", rule, severity))
            else:
                t.good, t.bad = t.good + 1, 0
                if t.open and t.good >= self.clear_after:
                    t.open, t.severity = False, None
                    events.append(("clear", rule))
        return events

    def is_open(self, node: str, rule: str) -> bool:
        """Whether the alert for (node, rule) is currently open."""
        return self._t.get((node, rule), _Track()).open


@dataclass
class AlertView:
    """The few fields of an alert that the score needs."""

    rule: str
    severity: str
    state: str  # open | acknowledged | escalated (resolved alerts are not passed in)
    opened_at: float


def node_score(alerts: list[AlertView], now: float) -> int:
    """0 for no open alerts; otherwise the highest weight + 5 per extra distinct rule (cap 20) + min(10, minutes the oldest UNACKNOWLEDGED alert has been open / 6)."""
    if not alerts:
        return 0
    extra = min(20, 5 * (len({a.rule for a in alerts}) - 1))
    unacked = [a.opened_at for a in alerts if a.state == "open"]
    age = min(10.0, (now - min(unacked)) / 60 / 6) if unacked else 0.0
    return int(max(WEIGHT[a.severity] for a in alerts) + extra + age)


def band(score: int) -> str:
    """critical (>= 100), warning (50 to 99), stable (0)."""
    return "critical" if score >= 100 else "warning" if score >= 50 else "stable"


def worklist(rows: list[dict]) -> list[dict]:
    """Rows {"node", "score", "oldest_unacked": float | None, ...} most urgent first: score high to low, then the oldest unacknowledged alert, then node id."""
    return sorted(rows, key=lambda r: (-r["score"], float("inf") if r["oldest_unacked"] is None else r["oldest_unacked"], r["node"]))
