"""BLE (Bleak) central -> MQTT gateway for EHG nodes.

  uv run python -m gateway.gateway                 # connect to 1 BLE node named EHG-*, publish to localhost MQTT
  uv run python -m gateway.gateway --nodes 2       # up to 2 nodes
  uv run python -m gateway.gateway --no-mqtt       # print instead of publishing
  uv run python -m gateway.gateway --simulate 3    # 3 software nodes, no BLE (backend tests, H3 load)

Every notification is timestamped on arrival and logged to gateway/logs/<node>.csv
(seq, frame_idx, flags, t_gw_rx, bytes) so loss can be attributed to the radio vs the
host stack (report Section 6.8/6.10).
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

from gateway.packet import FLAG_RETRANS, TICK_HZ, Packet, pack, parse
from gateway.tracking import ClockMap, GapTracker

# Must match firmware/ehg_node/src/ehg_service.c
SERVICE_UUID = "4e480001-a0e1-4c3b-8747-9a1b2c3d4e5f"
DATA_UUID = "4e480002-a0e1-4c3b-8747-9a1b2c3d4e5f"
CTRL_UUID = "4e480003-a0e1-4c3b-8747-9a1b2c3d4e5f"
OP_START, OP_STOP, OP_RESEND = 0x01, 0x02, 0x03
NAME_PREFIX = "EHG-"
LOGS = Path("gateway/logs")


class Node:
    """Per-node state: loss tracking, clock map, arrival log, publishing."""

    def __init__(self, node_id: str, publish, request_resend):
        self.id = node_id
        self.gaps, self.clock = GapTracker(), ClockMap()
        self.publish, self.request_resend = publish, request_resend
        LOGS.mkdir(parents=True, exist_ok=True)
        self._log_file = open(LOGS / f"{node_id}.csv", "a", newline="")
        self._log = csv.writer(self._log_file)
        if self._log_file.tell() == 0:
            self._log.writerow(["seq", "frame_idx", "flags", "t_gw_rx", "bytes"])

    def on_bytes(self, b: bytes, t_rx: float):
        try:
            p = parse(b)
        except ValueError as e:
            print(f"[{self.id}] bad packet: {e}")
            return
        self._log.writerow([p.seq, p.frame_idx, p.flags, f"{t_rx:.6f}", len(b)])
        if not p.flags & FLAG_RETRANS:  # resends are late by design; keep them out of the offset estimate
            self.clock.update(p.t_inject, t_rx)
        for start, count in ranges(self.gaps.observe(p.seq)):
            self.request_resend(start, count)
        msg = {"node_id": self.id, "seq": p.seq, "frame_idx": p.frame_idx, "t_inject": p.t_inject,
               "t_inject_host": self.clock.to_host(p.t_inject), "n_ch": p.n_ch, "fs_hz": p.fs_hz,
               "record_id": p.record_id, "flags": p.flags, "samples": p.samples, "t_gw_rx": t_rx}
        self.publish(self.id, msg)

    def stats(self) -> str:
        g = self.gaps
        return f"[{self.id}] rx={g.received} missing={len(g.missing)} dup={g.duplicates} streams={g.streams}"

    def close(self):
        self._log_file.close()


def ranges(seqs: list[int]):
    """[2,3,4,9] -> [(2,3),(9,1)]"""
    out = []
    for s in seqs:
        if out and out[-1][0] + out[-1][1] == s and out[-1][1] < 0xFFFF:
            out[-1] = (out[-1][0], out[-1][1] + 1)
        else:
            out.append((s, 1))
    return out


def make_publisher(args):
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
    from bleak import BleakClient

    node_id = device.name
    while not stop.is_set():
        try:
            async with BleakClient(device) as client:
                def resend(start, count):
                    asyncio.get_running_loop().create_task(
                        client.write_gatt_char(CTRL_UUID, struct.pack("<BIH", OP_RESEND, start, count), response=False))

                node = Node(node_id, publish, resend)
                print(f"[{node_id}] connected, mtu={client.mtu_size}")
                await client.start_notify(DATA_UUID, lambda _, data: node.on_bytes(bytes(data), time.time()))
                await client.write_gatt_char(CTRL_UUID, bytes([OP_START]), response=True)
                while client.is_connected and not stop.is_set():
                    await asyncio.sleep(10)
                    print(node.stats())
                if client.is_connected:
                    await client.write_gatt_char(CTRL_UUID, bytes([OP_STOP]), response=True)
                node.close()
        except Exception as e:  # BLE drops are expected; log and reconnect
            print(f"[{node_id}] {type(e).__name__}: {e}; reconnecting in 2 s")
            await asyncio.sleep(2)


async def ble_main(args, publish):
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
    """Software node emitting the same wire format as the firmware, with optional loss."""
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
        p = Packet(1, n_ch, int(fs), seq, frame0, int(t_frame0 * TICK_HZ) & 0xFFFFFFFF, nf, 0, 0, samples)
        b = pack(p)
        history[seq] = pack(Packet(**{**p.__dict__, "flags": FLAG_RETRANS}))
        history.pop(seq - 1024, None)
        if random.random() >= args.sim_loss:
            node.on_bytes(b, time.time())
        seq += 1
        if seq % max(1, int(10 * fs / nf)) == 0:
            print(node.stats())


async def sim_main(args, publish):
    await asyncio.gather(*(sim_node(i, args, publish) for i in range(args.simulate)))


def main():
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
