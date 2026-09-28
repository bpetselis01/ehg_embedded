"""MQTT -> InfluxDB ingestion service.

Subscribes ehg/+/pkt (QoS 1, persistent session) and writes two measurements:
  ehg_raw  one point per frame. time = injection time of that frame (host clock).
           tags node_id, record_id; fields c0..cN (int, raw ADC units), seq, frame_idx
  ehg_pkt  one point per packet. time = injection time of frame 0.
           fields seq, n_frames, retrans, t_inject_dev (raw ticks, for offline drift fit), t_inject_host, t_gw_rx, t_mqtt_pub, t_ingest_rx, t_db_ack
           t_db_ack is stamped after the synchronous ehg_raw write returns, i.e. when the
           samples are queryable - the H1 end point.
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
    return int(round(t * 1e9))


def points_for(m: dict) -> list[Point]:
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
