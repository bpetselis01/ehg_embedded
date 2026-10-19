"""MQTT -> InfluxDB ingestion service.

PURPOSE: Stores every packet the gateway publishes into the time-series database (InfluxDB).
INPUT: MQTT topic ehg/+/pkt (QoS 1, persistent session), 1 JSON per packet (docs/interface-spec.md 4):
    {node_id, seq, frame_idx, t_inject, t_inject_host, n_ch, fs_hz, record_id, flags, samples, t_gw_rx, t_mqtt_pub}
    e.g. the JSON example in gateway/gateway.py (EHG-93C0, seq 0, samples [[1093, 171, -748], ...])
OUTPUT: InfluxDB bucket `ehg` (docs/interface-spec.md 5), 2 measurements:
  ehg_raw  1 point per frame. time = injection time of that frame (host clock).
           tags node_id, record_id; fields c0..cN (int, raw ADC units), seq, frame_idx
  ehg_pkt  1 point per packet. time = injection time of frame 0.
           fields seq, n_frames, retrans, t_inject_dev (raw ticks, offline drift fit), t_inject_host,
           t_gw_rx, t_mqtt_pub, t_ingest_rx, t_db_ack
           t_db_ack = stamped after the synchronous ehg_raw write returns = samples queryable = H1 end point.
  e.g. that message as InfluxDB rows (line protocol: measurement,tags fields time-in-ns; i = integer).
    Made with points_for(); t_ingest_rx / t_db_ack are illustrative (backend not running when written):
    ehg_raw,node_id=EHG-93C0,record_id=0 c0=1093i,c1=171i,c2=-748i,frame_idx=0i,seq=0i 1791243068018676224
    ehg_raw,node_id=EHG-93C0,record_id=0 c0=1145i,c1=180i,c2=-790i,frame_idx=1i,seq=0i 1791243068068676352
    ... 10 ehg_raw rows, 50 ms apart ...
    ehg_pkt,node_id=EHG-93C0 n_frames=10i,retrans=false,seq=0i,t_db_ack=1791243068.47498,
      t_gw_rx=1791243068.468658,t_ingest_rx=1791243068.47101,t_inject_dev=98304i,
      t_inject_host=1791243068.0186763,t_mqtt_pub=1791243068.46912 1791243068018676224
HOW: Per message: drop if already seen (QoS 1 can deliver twice), write 1 row per frame (ehg_raw), then
  1 timing row for the packet (ehg_pkt). Waits for each write ("SYNCHRONOUS") so the confirmation time is real.
ROLE IN THESIS: Ingest box in report Fig. 10. t_ingest_rx and t_db_ack close the latency chain (H1).
  Rows keyed by frame_idx let eval/integrity.py check delivery (H2/H4).
BEFORE -> AFTER: Before: gateway/gateway.py via the Mosquitto broker. Runs in Docker
  (backend/docker-compose.yml, backend/ingest/Dockerfile). After: InfluxDB, read by Grafana
  (backend/grafana/), eval/backend_client.py, and a future classifier (docs/interface-spec.md 5).
"""

import json
import os
import time
from collections import deque

import paho.mqtt.client as mqtt
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

BUCKET = os.environ["INFLUX_BUCKET"]
influx = InfluxDBClient(url=os.environ["INFLUX_URL"], token=os.environ["INFLUX_TOKEN"], org=os.environ["INFLUX_ORG"])
write = influx.write_api(write_options=SYNCHRONOUS)

# QoS 1 is at-least-once, so the same packet can arrive twice. Remember recent (node, seq).
seen, seen_order = set(), deque(maxlen=100_000)


def ns(t: float) -> int:
    """Seconds -> whole nanoseconds (InfluxDB's time unit)."""
    return int(round(t * 1e9))


def points_for(m: dict) -> list[Point]:
    """One ehg_raw row per frame; frame i is timed at the packet start + i / sample rate."""
    pts = []
    for i, frame in enumerate(m["samples"]):
        p = (Point("ehg_raw").tag("node_id", m["node_id"]).tag("record_id", str(m["record_id"]))
             .field("seq", m["seq"]).field("frame_idx", m["frame_idx"] + i)
             .time(ns(m["t_inject_host"] + i / m["fs_hz"]), WritePrecision.NS))
        for c, v in enumerate(frame):
            p = p.field(f"c{c}", int(v))
        pts.append(p)
    return pts


def on_message(client, userdata, msg):
    """Handle one MQTT message: de-duplicate, write samples, stamp t_db_ack, write timing row."""
    t_ingest_rx = time.time()
    m = json.loads(msg.payload)
    key = (m["node_id"], m["seq"], m["t_inject"])  # t_inject disambiguates seq reuse after node reboot
    if key in seen:
        return
    if len(seen_order) == seen_order.maxlen:
        seen.discard(seen_order[0])
    seen.add(key)
    seen_order.append(key)

    write.write(BUCKET, record=points_for(m))
    t_db_ack = time.time()
    write.write(BUCKET, record=(
        Point("ehg_pkt").tag("node_id", m["node_id"])
        .field("seq", m["seq"]).field("n_frames", len(m["samples"])).field("retrans", bool(m["flags"] & 1))
        .field("t_inject_dev", m["t_inject"]).field("t_inject_host", m["t_inject_host"]).field("t_gw_rx", m["t_gw_rx"])
        .field("t_mqtt_pub", m["t_mqtt_pub"]).field("t_ingest_rx", t_ingest_rx).field("t_db_ack", t_db_ack)
        .time(ns(m["t_inject_host"]), WritePrecision.NS)))


def main():
    """Connect to the broker (retrying until it is up) and process messages forever."""
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="ehg-ingest", clean_session=False)
    c.on_connect = lambda cl, u, f, rc, p: cl.subscribe("ehg/+/pkt", qos=1)
    c.on_message = on_message
    while True:
        try:
            c.connect(os.environ.get("MQTT_HOST", "localhost"), 1883)
            break
        except OSError:
            print("waiting for broker...")
            time.sleep(2)
    print("ingest running")
    c.loop_forever()


if __name__ == "__main__":
    main()
