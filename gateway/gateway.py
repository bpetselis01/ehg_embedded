"""BLE (Bleak) central -> MQTT gateway for EHG nodes.

  uv run python -m gateway.gateway                 # connect to 1 BLE node named EHG-*, publish to localhost MQTT
  uv run python -m gateway.gateway --nodes 2       # up to 2 nodes
  uv run python -m gateway.gateway --no-mqtt       # print instead of publishing
  uv run python -m gateway.gateway --simulate 3    # 3 software nodes, no BLE (backend tests, H3 load)

PURPOSE: Laptop-side bridge. Packets from the board (Bluetooth) -> database side (MQTT).
INPUT: 1 BLE notification per packet = bytes in the wire format (header + int16 samples + CRC;
  docs/interface-spec.md 3, code gateway/packet.py). --simulate builds the same bytes from
  data/converted/tpehgdb/tpehg1007.npz (--sim-record).
  e.g. 82 bytes: 02 03 14 00 00 00 00 00 00 00 00 00 00 80 01 00 0a 00 00 00 | 45 04 ab 00 14 fd ... | 78 bb
       = header (seq 0, frame_idx 0, t_inject 98304, 10 frames) | samples 1093 171 -748 ... | CRC
       (byte-by-byte breakdown: firmware/ehg_node/src/ehg_service.c header)
OUTPUT:
  MQTT topic ehg/<node>/pkt, QoS 1, 1 JSON per packet (docs/interface-spec.md 4):
    {node_id, seq, frame_idx, t_inject, t_inject_host, n_ch, fs_hz, record_id, flags,
     samples [[ch1, ch2, ch3], ...], t_gw_rx, t_mqtt_pub}
    e.g. topic ehg/EHG-93C0/pkt, the packet above (made with Node.on_bytes):
      {"node_id": "EHG-93C0", "seq": 0, "frame_idx": 0, "t_inject": 98304,
       "t_inject_host": 1791243068.0186763, "n_ch": 3, "fs_hz": 20, "record_id": 0, "flags": 0,
       "samples": [[1093, 171, -748], [1145, 180, -790], ... 10 frames ..., [748, -101, -897]],
       "t_gw_rx": 1791243068.468658, "t_mqtt_pub": 1791243068.46912}
  gateway/logs/<node>.csv, 1 line per packet: seq, frame_idx, flags, t_gw_rx, bytes
    (splits loss into radio vs host stack, report 6.8/6.10)
    e.g. seq,frame_idx,flags,t_gw_rx,bytes
         0,0,0,1791243068.468658,82
  RESEND to the board when a gap shows: e.g. seq 3 arrives after 0 -> bytes 03 01 00 00 00 02 00
    (resend seq 1, count 2). --no-mqtt prints instead: EHG-93C0 seq=0 frame=0 first=[1093, 171, -748]
HOW: Scan for "EHG-*", connect, subscribe to the data slot, send START. Per packet: decode (CRC
  checked), write CSV line, update loss tracker + clock map, ask board to re-send gaps (again after 1 s,
  up to 5 times, if the re-sent copy is lost too), publish JSON. One tracker per board for the whole
  run, so a reconnect recovers what the board made while the link was down.
  MQTT = post office: publishers drop messages on a topic, subscribers receive.
  QoS 1 = post office keeps a message until delivery confirmed.
ROLE IN THESIS: Gateway box in report Fig. 10 (Gantt Phase 2). Adds t_gw_rx and t_mqtt_pub (H1 stage
  timing), drives loss recovery (H2), handles several nodes (H3).
BEFORE -> AFTER: Before: firmware/ehg_node/src/ehg_service.c (or the simulator). Decodes with
  gateway/packet.py, tracks with gateway/tracking.py. After: Mosquitto broker (backend/), read by
  backend/ingest/ingest.py (stores to InfluxDB) and viewer/server.py (live plot).
"""

import argparse
import asyncio
import csv
import json
import random
import struct
import time
from pathlib import Path

import numpy as np

from gateway.packet import FLAG_24BIT, FLAG_RETRANS, TICK_HZ, VERSION, Packet, pack, parse
from gateway.tracking import ClockMap, GapTracker

# Must match firmware/ehg_node/src/ehg_service.c
SERVICE_UUID = "4e480001-a0e1-4c3b-8747-9a1b2c3d4e5f"
DATA_UUID = "4e480002-a0e1-4c3b-8747-9a1b2c3d4e5f"
CTRL_UUID = "4e480003-a0e1-4c3b-8747-9a1b2c3d4e5f"
OP_START, OP_STOP, OP_RESEND = 0x01, 0x02, 0x03
NAME_PREFIX = "EHG-"
RETRY_S, MAX_TRIES = 1.0, 5  # re-ask for a still-missing packet after 1 s, at most 5 times in all
LOGS = Path("gateway/logs")


class Node:
    """Per-node state: loss tracking, clock map, arrival log, publishing.

    The same class serves real boards and simulated ones; only `publish` and
    `request_resend` (passed in) differ.
    """

    def __init__(self, node_id: str, publish, request_resend):
        self.id = node_id
        self.gaps, self.clock = GapTracker(), ClockMap()
        self.bad = 0  # packets rejected by parse (wrong CRC, length or version)
        self._asked: dict[int, tuple[float, int]] = {}  # missing seq -> (last request time, requests so far)
        self._stream = 0
        self.publish, self.request_resend = publish, request_resend
        LOGS.mkdir(parents=True, exist_ok=True)
        self._log_file = open(LOGS / f"{node_id}.csv", "a", newline="")
        self._log = csv.writer(self._log_file)
        if self._log_file.tell() == 0:
            self._log.writerow(["seq", "frame_idx", "flags", "t_gw_rx", "bytes"])

    def on_bytes(self, b: bytes, t_rx: float):
        """Handle one received packet: decode, log, track, request resends, publish."""
        try:
            p = parse(b)
        except ValueError as e:  # e.g. crc mismatch: dropped, so the next packet shows it as a gap
            self.bad += 1
            print(f"[{self.id}] bad packet: {e}")
            return
        self._log.writerow([p.seq, p.frame_idx, p.flags, f"{t_rx:.6f}", len(b)])
        if not p.flags & FLAG_RETRANS:  # resends are late by design; keep them out of the offset estimate
            # Sendable once the LAST frame is injected: align that instant with t_rx, otherwise
            # the batching delay is folded into the clock offset and latency reads ~0.
            t_last = p.t_inject + (p.n_frames - 1) * TICK_HZ // p.fs_hz
            self.clock.update(t_last & 0xFFFFFFFF, t_rx)
        self._request_missing(self.gaps.observe(p.seq), t_rx)
        msg = {"node_id": self.id, "seq": p.seq, "frame_idx": p.frame_idx, "t_inject": p.t_inject,
               "t_inject_host": self.clock.to_host(p.t_inject), "n_ch": p.n_ch, "fs_hz": p.fs_hz,
               "record_id": p.record_id, "flags": p.flags, "samples": p.samples, "t_gw_rx": t_rx}
        self.publish(self.id, msg)

    def _request_missing(self, new_gaps: list[int], t: float):
        """Ask the board for new gaps at once, and again (every RETRY_S, up to MAX_TRIES) for any
        still missing: the re-sent copy can be lost on the air too, and one request is not enough."""
        if self.gaps.streams != self._stream:  # board restarted numbering: old requests mean nothing
            self._stream, self._asked = self.gaps.streams, {}
        due = list(new_gaps)
        for s in list(self._asked):
            if s not in self.gaps.missing:
                del self._asked[s]  # arrived
            elif t - self._asked[s][0] >= RETRY_S and self._asked[s][1] < MAX_TRIES:
                due.append(s)
        for s in due:
            self._asked[s] = (t, self._asked.get(s, (0, 0))[1] + 1)
        for start, count in ranges(sorted(due)):
            self.request_resend(start, count)

    def stats(self) -> str:
        """One-line summary printed every ~10 s."""
        g = self.gaps
        return (f"[{self.id}] rx={g.received} missing={len(g.missing)} dup={g.duplicates} streams={g.streams}"
                f" bad={self.bad}")

    def close(self):
        self._log_file.close()


def ranges(seqs: list[int]):
    """Group missing seqs into (start, count) runs, one RESEND each: [2,3,4,9] -> [(2,3),(9,1)]"""
    out = []
    for s in seqs:
        if out and out[-1][0] + out[-1][1] == s and out[-1][1] < 0xFFFF:
            out[-1] = (out[-1][0], out[-1][1] + 1)
        else:
            out.append((s, 1))
    return out


def make_publisher(args):
    """Return (publish, close). Publishes to MQTT, or just prints when --no-mqtt."""
    if args.no_mqtt:
        def show(node_id, msg):
            print(f"{node_id} seq={msg['seq']} frame={msg['frame_idx']} first={msg['samples'][0]}")
        return show, lambda: None
    import paho.mqtt.client as mqtt
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"ehg-gateway-{int(time.time())}")
    c.max_queued_messages_set(0)  # unbounded: never drop at the gateway
    c.connect(args.mqtt, 1883)
    c.loop_start()

    def publish(node_id, msg):
        msg["t_mqtt_pub"] = time.time()
        c.publish(f"ehg/{node_id}/pkt", json.dumps(msg), qos=1)

    return publish, c.loop_stop


# ---------------------------------------------------------------- BLE source

async def run_ble_node(device, publish, stop: asyncio.Event):
    """Connect to one board, stream until stopped, reconnect automatically if the link drops."""
    from bleak import BleakClient

    node_id = device.name
    # One Node for the whole run: after a reconnect its gap tracker still knows the last seq, so the
    # packets the board made while disconnected (it keeps sampling) are requested from its history.
    node = Node(node_id, publish, lambda start, count: None)
    while not stop.is_set():
        try:
            async with BleakClient(device) as client:
                def resend(start, count):
                    asyncio.get_running_loop().create_task(
                        client.write_gatt_char(CTRL_UUID, struct.pack("<BIH", OP_RESEND, start, count), response=False))

                node.request_resend = resend
                print(f"[{node_id}] connected, mtu={client.mtu_size}")
                await client.start_notify(DATA_UUID, lambda _, data: node.on_bytes(bytes(data), time.time()))
                await client.write_gatt_char(CTRL_UUID, bytes([OP_START]), response=True)  # no-op if already running
                while client.is_connected and not stop.is_set():
                    await asyncio.sleep(10)
                    print(node.stats())
                if client.is_connected:
                    await client.write_gatt_char(CTRL_UUID, bytes([OP_STOP]), response=True)
        except Exception as e:  # BLE drops are expected; log and reconnect
            print(f"[{node_id}] {type(e).__name__}: {e}; reconnecting in 2 s")
            await asyncio.sleep(2)
    node.close()


async def ble_main(args, publish):
    """Scan for up to --nodes boards named EHG-*, then run one run_ble_node per board in parallel."""
    from bleak import BleakScanner

    print(f"scanning for up to {args.nodes} node(s) named {NAME_PREFIX}* ...")
    found = {}
    async with BleakScanner() as scanner:
        deadline = time.time() + args.scan_s
        while time.time() < deadline and len(found) < args.nodes:
            for d, _ in scanner.discovered_devices_and_advertisement_data.values():
                if d.name and d.name.startswith(NAME_PREFIX):
                    found.setdefault(d.name, d)
            await asyncio.sleep(0.5)
    if not found:
        raise SystemExit("no EHG nodes found - is the DK flashed and advertising?")
    stop = asyncio.Event()
    await asyncio.gather(*(run_ble_node(d, publish, stop) for d in list(found.values())[:args.nodes]))


# ---------------------------------------------------------------- simulated source

async def sim_node(i: int, args, publish):
    """Software node emitting the same wire format as the firmware, with optional loss.

    Lets the whole backend be tested without hardware. Keeps its own history so resend
    requests are answered just like the firmware's (flag = retransmission).
    """
    rec = np.load(args.sim_record)
    src, fs = rec["samples"], float(rec["fs"])
    n_ch, nf = src.shape[1], args.frames
    history = {}
    node = Node(f"SIM-{i:04d}", publish, lambda s, c: [node.on_bytes(history[q], time.time())
                                                       for q in range(s, s + c) if q in history])
    t0, seq = time.monotonic(), 0
    while True:
        frame0 = seq * nf
        t_frame0 = t0 + frame0 / fs
        await asyncio.sleep(max(0.0, t_frame0 + (nf - 1) / fs - time.monotonic()))  # last frame injected
        samples = [src[(frame0 + k) % len(src)].tolist() for k in range(nf)]
        flags = FLAG_24BIT if src.dtype == np.int32 else 0  # convert.py --bits 24 records
        p = Packet(VERSION, n_ch, int(fs), seq, frame0, int(t_frame0 * TICK_HZ) & 0xFFFFFFFF, nf, flags, 0, samples)
        b = pack(p)
        history[seq] = pack(Packet(**{**p.__dict__, "flags": flags | FLAG_RETRANS}))
        history.pop(seq - 1024, None)
        if random.random() >= args.sim_loss:
            node.on_bytes(b, time.time())
        seq += 1
        if seq % max(1, int(10 * fs / nf)) == 0:
            print(node.stats())


async def sim_main(args, publish):
    """Run --simulate N software nodes in parallel."""
    await asyncio.gather(*(sim_node(i, args, publish) for i in range(args.simulate)))


def main():
    """Parse command-line options, then run either the BLE or the simulated source."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--nodes", type=int, default=1)
    ap.add_argument("--scan-s", type=float, default=10)
    ap.add_argument("--mqtt", default="localhost")
    ap.add_argument("--no-mqtt", action="store_true")
    ap.add_argument("--simulate", type=int, default=0, help="N software nodes instead of BLE")
    ap.add_argument("--sim-record", default="data/converted/tpehgdb/tpehg1007.npz")
    ap.add_argument("--sim-loss", type=float, default=0.0, help="drop probability per packet (sim only)")
    ap.add_argument("--frames", type=int, default=10, help="frames per packet (sim only; firmware uses Kconfig)")
    args = ap.parse_args()
    publish, close = make_publisher(args)
    try:
        asyncio.run(sim_main(args, publish) if args.simulate else ble_main(args, publish))
    except KeyboardInterrupt:
        pass
    finally:
        close()


if __name__ == "__main__":
    main()
