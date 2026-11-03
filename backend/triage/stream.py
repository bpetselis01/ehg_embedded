"""From the packet stream to alerts: per-node measurement, the once-a-second evaluation, the once-a-minute health row, and the JetStream/HTTP service that runs them.

PURPOSE: Watch the pipeline, not the patient (decision 0011). NodeMonitor measures one node from the packets it sees; FleetMonitor holds all nodes and the gateways' online/offline status;
  Engine runs the rules (backend/triage/rules.py) every second, opens/updates/clears alerts (backend/triage/state.py) and writes an ehg_health row per node every minute.
INPUT: packet messages (the JSON of docs/interface-spec.md 4) with the time the triage worker received them; gateway status text from EHG.GW.<gateway>; the clock.
  e.g. fleet.on_packet(msg, t_recv=1000.2); fleet.on_status("ehg-gw-01", "offline", 1001.0); engine.tick(1002.0)
OUTPUT: Snapshots (rules.Snapshot) per node; alert changes in PostgreSQL; ehg_health rows {time, node, delivery_pct, p95_latency_ms, rx, missing, unrecovered, flatline_channels, saturated_pct, score}.
  `run()` serves the API (uvicorn), consumes the `triage` durable consumer on EHG.> and counts messages kept under EHG.BAD.* and JetStream max-delivery advisories (dead letters) as metrics.
HOW: Sequence gaps use gateway.tracking.GapTracker (reused, not copied): a packet is "unrecovered" when it has been missing longer than the gateway's retry budget (5 tries x 1 s) plus a 2 s margin.
  Loss over 60 s = packets that became missing in the last 60 s and are still missing / (received + those). Latency p95 = 60 s p95 of (receive time - clock offset - t_inject_host), live packets only; it is OFF
  (None) until a clock offset between the gateway host and this host is configured (an operational approximation: eval/latency.py remains the authority for H1). Flatline = zero variance over 10 s of
  frames on a channel; saturation = share of samples at the int16 (or 24-bit when flags & 2) rails over 10 s. A sequence restart is held for 5 s so that the 3-evaluation rule can see it.
  Node-to-gateway attribution uses the registry (node.assigned_gateway); without it, silence is attributed to the gateway only if exactly one gateway is known.
ROLE IN THESIS: The live health measurement behind the triage page. Operational, not clinical; it makes no diagnostic or predictive claim.
BEFORE -> AFTER: Before: rules without inputs. After: rules fed from the stream, alerts and history stored.
"""

import asyncio
import os
from collections import deque
from datetime import datetime, timezone

import numpy as np

from backend.ts_writer.writer import validate
from gateway.tracking import GapTracker

from backend.triage.rules import AlertView, Config, RuleTracker, Snapshot, evaluate, node_score

MAX_TRIES, RETRY_S, MARGIN_S = 5, 1.0, 2.0  # the gateway's resend budget (gateway/gateway.py) plus a margin
WINDOW_S, SIGNAL_S, RESTART_HOLD_S = 60.0, 10.0, 5.0
RAIL16, RAIL24 = (32767, -32768), (8388607, -8388608)
MAX_GAP = 10_000  # a forward jump bigger than this is a restart, never a list of missing numbers (GapTracker would build one entry per number)
MAX_KEPT_FRAMES = 4_000  # hard bound on the signal window, whatever fs_hz claims
MAX_NODES = 256  # monitors for node ids that nobody registered are capped


class NodeMonitor:
    """Measurements about one node, from its packets."""

    def __init__(self, node_id: str, clock_offset_s: float | None = None):
        self.id, self.clock_offset_s = node_id, clock_offset_s
        self.tracker, self.first_missing = GapTracker(), {}
        self.last_packet_t: float | None = None
        self.interval_s, self.fs, self.n_ch, self.flags = 0.5, 20.0, 0, 0
        self._rx, self._gaps, self._lat = deque(), deque(), deque()  # (t,), (t, seq), (t, ms)
        self._frames, self._total = deque(), 0  # (n_frames, array) newest last, and their frame count
        self._streams_seen, self._restart_until = 0, 0.0

    def on_packet(self, m: dict, t_recv: float) -> None:
        """Record one packet message received at t_recv. The message must already be valid (FleetMonitor.on_packet checks it)."""
        self.last_packet_t, self.fs, self.flags = t_recv, float(m["fs_hz"]), int(m["flags"])
        self.interval_s = len(m["samples"]) / self.fs
        arr = np.asarray(m["samples"], dtype=np.int64)
        if arr.shape[1] != self.n_ch:
            self._frames.clear()  # a different channel count: old frames can no longer be stacked with the new ones
            self._total = 0
        self.n_ch = arr.shape[1]
        self._frames.append((len(arr), arr))
        self._total += len(arr)
        self._trim()
        seq, last = int(m["seq"]), self.tracker.last
        forced_restart = last is not None and seq - last > MAX_GAP
        if forced_restart:
            self.tracker, self._streams_seen = GapTracker(), 0
        new = self.tracker.observe(seq)
        for s in new:
            self.first_missing[s] = t_recv
            self._gaps.append((t_recv, s))
        if self.tracker.streams != self._streams_seen:
            if self._streams_seen or forced_restart:  # the very first packet starts stream 1; that is not a restart
                self._restart_until = t_recv + RESTART_HOLD_S
            self._streams_seen, self.first_missing = self.tracker.streams, {}
            self._gaps.clear()
        self._rx.append(t_recv)
        live = not (self.flags & 1) and not m.get("replayed", False)
        if live and self.clock_offset_s is not None:
            self._lat.append((t_recv, (t_recv - self.clock_offset_s - float(m["t_inject_host"])) * 1000.0))

    def snapshot(self, now: float, gateway_status: str | None) -> Snapshot:
        """Everything the rules need at `now`."""
        while self._rx and self._rx[0] < now - WINDOW_S:
            self._rx.popleft()
        while self._lat and self._lat[0][0] < now - WINDOW_S:
            self._lat.popleft()
        while self._gaps and self._gaps[0][0] < now - WINDOW_S:
            self._gaps.popleft()
        for s in [s for s in self.first_missing if s not in self.tracker.missing]:
            del self.first_missing[s]  # it arrived after all
        unrecovered = sum(1 for t in self.first_missing.values() if now - t > MAX_TRIES * RETRY_S + MARGIN_S)
        missing_60 = sum(1 for _, s in self._gaps if s in self.tracker.missing)
        p95 = float(np.percentile([ms for _, ms in self._lat], 95)) if self._lat else None
        flat, sat = self._signal(now)
        return Snapshot(now=now, last_packet_t=self.last_packet_t, packet_interval_s=self.interval_s, gateway_status=gateway_status, unrecovered=unrecovered,
                        rx_60=len(self._rx), missing_60=missing_60, latency_p95_ms=p95, flatline_channels=flat, n_channels=self.n_ch, saturated_pct=sat,
                        seq_restarted=now < self._restart_until)

    def _trim(self) -> None:
        """Drop the oldest packets while the rest still cover the signal window: memory stays bounded whatever fs_hz or the packet size claim."""
        need = max(1, min(int(SIGNAL_S * self.fs), MAX_KEPT_FRAMES))
        while len(self._frames) > 1 and self._total - self._frames[0][0] >= need:  # never discard the newest packet
            self._total -= self._frames.popleft()[0]

    def _signal(self, now: float) -> tuple[int, float]:
        """(channels with zero variance, % of samples at the rails) over the last 10 s of frames; (0, 0) until 10 s have been seen."""
        need = max(1, min(int(SIGNAL_S * self.fs), MAX_KEPT_FRAMES))
        self._trim()
        if not self._frames or self._total < need or self.n_ch == 0:
            return 0, 0.0
        data = np.concatenate([a for _, a in self._frames])[-need:]
        hi, lo = RAIL24 if self.flags & 2 else RAIL16
        return int(np.sum(np.ptp(data, axis=0) == 0)), float(100.0 * np.mean((data >= hi) | (data <= lo)))


class FleetMonitor:
    """All nodes, plus gateway status. `gateway_of(node)` returns the node's gateway id (from the registry) or None."""

    def __init__(self, gateway_of=lambda node: None, clock_offset_s: float | None = None, max_nodes: int = MAX_NODES):
        self.nodes: dict[str, NodeMonitor] = {}
        self.gateways: dict[str, str] = {}
        self._gateway_of, self._offset, self._max_nodes = gateway_of, clock_offset_s, max_nodes

    def on_packet(self, m: dict, t_recv: float) -> None:
        """Route a packet to its node's monitor. Raises ValueError for a message the writer would refuse, or for a new node beyond the cap; the monitors are left unchanged."""
        validate(m)
        if m["node_id"] not in self.nodes:
            if len(self.nodes) >= self._max_nodes:
                raise ValueError(f"too many nodes ({self._max_nodes}) are already being monitored; ignoring {m['node_id']!r}")
            self.nodes[m["node_id"]] = NodeMonitor(m["node_id"], self._offset)
        self.nodes[m["node_id"]].on_packet(m, t_recv)

    def on_status(self, gateway_id: str, status: str, t: float) -> None:
        """Record a gateway's retained online/offline text."""
        self.gateways[gateway_id] = status.strip().lower()

    def gateway_status(self, node: str) -> str | None:
        """The status of the node's gateway: registry first, else the only known gateway, else unknown."""
        g = self._gateway_of(node)
        if g is None and len(self.gateways) == 1:
            g = next(iter(self.gateways))
        return self.gateways.get(g) if g else None

    def snapshots(self, now: float, log=print) -> dict[str, Snapshot]:
        """A snapshot for every node seen so far; a node whose state is broken is logged and skipped, never allowed to stop the others."""
        out = {}
        for n, m in self.nodes.items():
            try:
                out[n] = m.snapshot(now, self.gateway_status(n))
            except Exception as e:  # noqa: BLE001  (fail safe: fall back to a liveness-only view so NODE_OFFLINE and GATEWAY_OFFLINE can still fire for this node)
                log(f"triage: could not fully evaluate node {n} ({type(e).__name__}: {e}); using a liveness-only view this round")
                out[n] = Snapshot(now=now, last_packet_t=m.last_packet_t, packet_interval_s=m.interval_s, gateway_status=self.gateway_status(n))
        return out


class Engine:
    """Once a second: evaluate every node, apply hysteresis, change alerts. Once a minute: write a health row per node. `sink(rows)` stores health rows."""

    def __init__(self, fleet: FleetMonitor, store, cfg: Config | None = None, sink=lambda rows: None, tracker: RuleTracker | None = None, log=print):
        self.fleet, self.store, self.cfg, self.sink, self.tracker, self.log = fleet, store, cfg or Config(), sink, tracker or RuleTracker(), log
        self._last_health = 0.0

    def tick(self, now: float) -> list[tuple]:
        """One evaluation round; returns the (node, event...) tuples applied."""
        applied = []
        snaps = self.fleet.snapshots(now, self.log)
        for node, snap in snaps.items():
            for ev in self.tracker.update(node, evaluate(snap, self.cfg)):
                kind, rule = ev[0], ev[1]
                when = datetime.fromtimestamp(now, tz=timezone.utc)
                if kind == "open":
                    self.store.open_alert(node, rule, ev[2], when)
                elif kind == "update":
                    self.store.set_severity(node, rule, ev[2])
                else:
                    self.store.condition_cleared(node, rule, when)
                applied.append((node, *ev))
        if now - self._last_health >= 60.0 and snaps:
            self._last_health = now
            self.sink(self._health_rows(snaps, now))
        return applied

    def _health_rows(self, snaps: dict[str, Snapshot], now: float) -> list[dict]:
        """One ehg_health row per node from its snapshot."""
        t = datetime.fromtimestamp(now, tz=timezone.utc)
        rows = []
        for node, s in snaps.items():
            total = s.rx_60 + s.missing_60
            open_views = [AlertView(a["rule"], a["severity"], a["state"], a["opened_at"].timestamp()) for a in self.store.list_alerts(node=node) if a["state"] != "resolved"]
            rows.append({"time": t, "node": node, "delivery_pct": 100.0 * s.rx_60 / total if total else None, "p95_latency_ms": s.latency_p95_ms, "rx": s.rx_60, "missing": s.missing_60,
                         "unrecovered": s.unrecovered, "flatline_channels": s.flatline_channels, "saturated_pct": s.saturated_pct, "score": node_score(open_views, now)})
        return rows


HEALTH_SQL = ("INSERT INTO ehg_health (time, node, delivery_pct, p95_latency_ms, rx, missing, unrecovered, flatline_channels, saturated_pct, score) "
              "VALUES (%(time)s, %(node)s, %(delivery_pct)s, %(p95_latency_ms)s, %(rx)s, %(missing)s, %(unrecovered)s, %(flatline_channels)s, %(saturated_pct)s, %(score)s) ON CONFLICT DO NOTHING")


def pg_health_sink(connect):
    """A sink that writes health rows to PostgreSQL (a failure is logged and skipped: a missed minute of history must not stop alerting)."""
    def sink(rows):
        """Insert one minute's rows."""
        try:
            with connect() as c:
                c.cursor().executemany(HEALTH_SQL, rows)
        except Exception as e:  # noqa: BLE001
            print(f"triage: could not write health rows ({type(e).__name__}: {e})")
    return sink


async def run() -> None:
    """Service entry point: JetStream consumer + once-a-second engine + the HTTP API, in one process (needs NATS, PostgreSQL and AUTH_* settings)."""
    import json
    import time

    import nats
    import psycopg
    import uvicorn
    from prometheus_client import Counter

    from backend import bus
    from backend.jwtauth import from_env
    from backend.triage.app import create_app
    from backend.triage.audit import AuditLog
    from backend.triage.registry import Registry
    from backend.triage.state import AlertStore

    dsn = os.environ["EHG_TRIAGE_DSN"]
    connect = lambda: psycopg.connect(dsn, connect_timeout=5)  # noqa: E731
    audit = AuditLog(connect)
    store, registry = AlertStore(connect, audit), Registry(connect, audit)
    offset = os.environ.get("CLOCK_OFFSET_S")
    fleet = FleetMonitor(lambda node: (registry.node(node) or {}).get("assigned_gateway"), float(offset) if offset else None)
    engine = Engine(fleet, store, sink=pg_health_sink(connect))
    bad, dead = Counter("ehg_triage_bad_messages", "messages kept under EHG.BAD.*"), Counter("ehg_triage_dead_letters", "JetStream max-delivery advisories")
    host = os.environ.get("BIND_HOST", "127.0.0.1")
    app = create_app(store=store, audit=audit, auth=from_env(), registry=registry, known_nodes=lambda: list(fleet.nodes), config=engine.cfg)
    nc = await nats.connect(os.environ.get("NATS_URL", "nats://nats:4222"), name="ehg-triage", max_reconnect_attempts=-1)
    js = nc.jetstream()
    await js.add_stream(bus.stream_config())
    sub = await js.pull_subscribe("EHG.>", durable="triage", stream=bus.STREAM, config=bus.consumer_config("triage", "EHG.>"))
    await nc.subscribe(bus.DEAD_LETTER_ADVISORY, cb=lambda msg: dead.inc())

    async def consume():
        """Feed the monitors from the stream."""
        while True:
            try:
                msgs = await sub.fetch(100, timeout=1)
            except nats.errors.TimeoutError:
                continue
            for m in msgs:
                t = time.time()
                try:
                    if m.subject.startswith(bus.PKT_PREFIX):
                        fleet.on_packet(json.loads(m.data), t)
                    elif m.subject.startswith(bus.GW_PREFIX):
                        fleet.on_status(m.subject.rsplit(".", 1)[-1], m.data.decode(), t)
                    else:
                        bad.inc()
                except Exception as e:  # noqa: BLE001  (a message the monitor cannot use is counted and dropped from monitoring; the writers have their own copy)
                    bad.inc()
                    print(f"triage: ignored an unusable message on {m.subject} ({type(e).__name__}: {e})")
                await m.ack()

    async def evaluate_loop():
        """Once a second."""
        while True:
            try:
                await asyncio.to_thread(engine.tick, time.time())
            except Exception as e:  # noqa: BLE001  (a database hiccup in one round must not end the loop for good)
                print(f"triage: evaluation round failed ({type(e).__name__}: {e}); retrying in 1 s")
            await asyncio.sleep(1.0)

    server = uvicorn.Server(uvicorn.Config(app, host=host, port=int(os.environ.get("TRIAGE_PORT", "8780")), log_level="info"))
    await asyncio.gather(consume(), evaluate_loop(), server.serve())


if __name__ == "__main__":
    asyncio.run(run())
