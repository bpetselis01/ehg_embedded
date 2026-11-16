"""Stream a converted recording into the board over the J-Link serial port (digital injection).

Usage: uv run python tools/feed.py data/converted/tpehgdb/tpehg1007.npz [--port /dev/cu.usbmodem…] [--lead 2]
"""

import argparse
import json
import os
import re
import struct
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # run as a script: make gateway/ importable
from gateway.packet import crc16  # noqa: E402

PORT = "/dev/cu.usbmodem0010502082941"
BAUD = 460800  # 1 Mbaud lost board->laptop bytes on the J-Link VCOM (Unit 0 check, 2026-10-20)
MSG_CONFIG, MSG_DATA, MSG_SYNC, MSG_FAULT = 0x10, 0x11, 0x12, 0x13
RESULTS = Path(__file__).resolve().parents[1] / "eval" / "results"
MAX_FRAMES_PER_MSG = 10
AT_LINE = re.compile(r"@((HELLO|LVL|SYNC|ERR)\b.*) #([0-9a-f]{4})$")


def cobs_encode(data: bytes) -> bytes:
    """COBS: rewrite data so it contains no 0x00, then the 0x00 can mark the end of a message."""
    out, block = bytearray(), bytearray()
    for b in data:
        if b == 0:
            out += bytes([len(block) + 1]) + block
            block.clear()
        else:
            block.append(b)
            if len(block) == 254:
                out += bytes([255]) + block
                block.clear()
    out += bytes([len(block) + 1]) + block
    return bytes(out)


def cobs_decode(data: bytes) -> bytes:
    """Inverse of cobs_encode (without the trailing 0x00). Raises ValueError on a malformed block."""
    out, i = bytearray(), 0
    while i < len(data):
        n = data[i]
        if n == 0 or i + n > len(data):
            raise ValueError("bad COBS block")
        out += data[i + 1:i + n]
        i += n
        if n < 255 and i < len(data):
            out.append(0)
    return bytes(out)


def frame_msg(kind: int, payload: bytes) -> bytes:
    """type | payload | CRC-16, COBS-encoded, 0x00-terminated: one message ready for the wire."""
    body = bytes([kind]) + payload
    return cobs_encode(body + struct.pack("<H", crc16(body))) + b"\x00"


def config_msg(n_ch: int, fs: int, record_id: int, bits: int, session: int) -> bytes:
    """session: random per feeder start. The board empties its queue only for a new session, so
    the CONFIG repeats sent while waiting for @HELLO can't throw away frames already sent."""
    return frame_msg(MSG_CONFIG, struct.pack("<BHHBH", n_ch, fs, record_id, bits, session))


def data_msg(frame_idx: int, frames: np.ndarray, bits: int) -> bytes:
    """frames: [n, n_ch] raw counts, n <= MAX_FRAMES_PER_MSG. Samples little-endian, 2 or 3 bytes each."""
    head = struct.pack("<IB", frame_idx & 0xFFFFFFFF, len(frames))
    if bits == 16:
        body = frames.astype("<i2").tobytes()
    else:
        body = b"".join(int(v).to_bytes(3, "little", signed=True) for v in frames.ravel())
    return frame_msg(MSG_DATA, head + body)


def sync_msg(ping_id: int) -> bytes:
    return frame_msg(MSG_SYNC, struct.pack("<I", ping_id & 0xFFFFFFFF))


def parse_faults(spec: str) -> dict:
    """'drop=0.02,burst=4,corrupt=0.001,disc=60,seed=1' -> the board's settings.

    drop = long-run share of packets lost, burst = mean length of a loss run (packets). Gilbert model:
    bad->good r = 1/burst, good->bad p = drop*r/(1-drop), so the share of time in "bad" is drop.
    Probabilities go out of 65536; missing keys = off (burst defaults to 1 = independent losses)."""
    kv = {k: float(v) for k, v in (item.split("=") for item in spec.split(",") if item)}
    unknown = set(kv) - {"drop", "burst", "corrupt", "disc", "seed"}
    if unknown:
        raise SystemExit(f"unknown --faults keys {sorted(unknown)}")
    drop, burst = kv.get("drop", 0.0), max(1.0, kv.get("burst", 1.0))
    if not 0 <= drop < 1:
        raise SystemExit("--faults drop must be in [0, 1)")
    r = 1 / burst
    q = lambda x: min(65535, round(x * 65536))  # noqa: E731
    return {"seed": int(kv.get("seed", 1)), "p_gb": q(drop * r / (1 - drop)), "p_bg": q(r),
            "corrupt": q(kv.get("corrupt", 0.0)), "disc_s": int(kv.get("disc", 0))}


def fault_msg(f: dict) -> bytes:
    return frame_msg(MSG_FAULT, struct.pack("<IHHHH", f["seed"], f["p_gb"], f["p_bg"], f["corrupt"], f["disc_s"]))


def parse_at(line: str):
    """'@LVL 40 #<crc>' -> ('LVL', ['40']). A normal log line, an unknown @ word, or a damaged line
    (CRC mismatch: the VCOM drops a few board->laptop bytes, Unit 0 check) -> None."""
    m = AT_LINE.search(line)
    if not m or crc16(m.group(1).encode()) != int(m.group(3), 16):
        return None
    word, *args = m.group(1).split()
    return word, args


def record_id_for(npz: Path) -> int:
    """record_id of this.npz from the manifest.json in the same folder (tools/convert.py)."""
    for e in json.loads((npz.parent / "manifest.json").read_text()):
        if e["file"] == npz.name:
            return e["record_id"]
    raise SystemExit(f"{npz.name} not in {npz.parent / 'manifest.json'}")


class Feeder:
    """Keeps about `lead` seconds of frames queued on the board, using its @LVL reports."""

    def __init__(self, samples: np.ndarray, fs: int, lead: float):
        self.src, self.target = samples, max(1, int(lead * fs))
        self.next_idx = 0    # next frame_idx to send; keeps counting across record loops
        self.level = 0       # frames queued on the board at its last @LVL
        self.sent_since = 0  # frames sent since that report

    def on_level(self, level: int):
        self.level, self.sent_since = level, 0

    def due(self) -> int:
        """Frames to send now to bring the board's queue back to the target (0 until the next @LVL)."""
        return max(0, self.target - self.level - self.sent_since)

    def take(self, n: int) -> tuple[int, np.ndarray]:
        """Next n frames (n <= MAX_FRAMES_PER_MSG) and the frame_idx of the first; loops the record."""
        rows = (self.next_idx + np.arange(n)) % len(self.src)
        first = self.next_idx
        self.next_idx += n
        self.sent_since += n
        return first, self.src[rows]


def open_sync_log(node: str):
    """Append-mode eval/results/sync_<node>.csv, with the header line if the file is new."""
    RESULTS.mkdir(parents=True, exist_ok=True)
    path = RESULTS / f"sync_{node}.csv"
    new = not path.exists()
    f = path.open("a")
    if new:
        f.write("ping,t_send,t_recv,tick\n")
    return f


def sync_only(a):
    """Clock pings only, for a board playing its built-in recording: log them, print the board log."""
    import serial

    port, log, buf, pings, ping_id, t_ping = serial.Serial(a.port, a.baud, timeout=0.05), open_sync_log(a.node), b"", {}, 0, 0.0
    print(f"clock pings only for {a.node} -> {log.name}")
    try:
        while True:
            t = time.monotonic()
            if t - t_ping >= 1:
                ping_id, t_ping = ping_id + 1, t
                pings = {k: v for k, v in pings.items() if k > ping_id - 10}
                pings[ping_id] = time.time()
                port.write(sync_msg(ping_id))
            buf += port.read(max(1, port.in_waiting))
            *lines, buf = buf.split(b"\n")
            for raw in lines:
                line = raw.decode(errors="replace").replace("\x1b[0m", "").rstrip("\r")
                at = parse_at(line)
                if at and at[0] == "SYNC" and len(at[1]) == 2 and int(at[1][0]) in pings:
                    log.write(f"{at[1][0]},{pings.pop(int(at[1][0])):.6f},{time.time():.6f},{at[1][1]}\n")
                    log.flush()
                elif at is None:
                    print(line)
    except KeyboardInterrupt:
        print(f"stopped after {ping_id} pings")
    finally:
        port.close()
        log.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("npz", type=Path, nargs="?", help="recording to stream (not with --sync-only)")
    ap.add_argument("--port", default=PORT)
    ap.add_argument("--baud", type=int, default=BAUD)
    ap.add_argument("--lead", type=float, default=2.0, help="seconds of frames kept queued on the board (> 1)")
    ap.add_argument("--faults", default="", help="e.g. drop=0.02,burst=4,corrupt=0.001,disc=60,seed=1 "
                    "(docs/fault-injection.md); default: none")
    ap.add_argument("--verbose", action="store_true", help="also print the board's @ lines and what was sent")
    ap.add_argument("--sync-only", action="store_true", help="only clock pings, no samples: for a board built with "
                    "build.sh --record (built-in recording); needs --node")
    ap.add_argument("--node", help="board name for --sync-only, e.g. EHG-93C0 (normally learnt from @HELLO)")
    ap.add_argument("--dry-run", action="store_true", help="print the CONFIG and first DATA message, send nothing")
    a = ap.parse_args()
    if a.lead <= 1:
        raise SystemExit("--lead must be over 1 s (the board reports its queue once a second)")
    if a.sync_only:
        if not a.node or a.npz or a.faults:
            raise SystemExit("--sync-only needs --node <name> and takes no recording or --faults")
        return sync_only(a)
    if not a.npz:
        raise SystemExit("give a recording to stream, e.g. data/converted/tpehgdb/tpehg1007.npz (or --sync-only)")

    rec = np.load(a.npz)
    samples, fs = rec["samples"], float(rec["fs"])
    if fs != int(fs):
        raise SystemExit(f"fs must be a whole number of Hz for the board timer, got {fs}")
    fs, bits, n_ch, rid = int(fs), (24 if samples.dtype == np.int32 else 16), samples.shape[1], record_id_for(a.npz)
    cfg = config_msg(n_ch, fs, rid, bits, int.from_bytes(os.urandom(2), "little"))
    faults = parse_faults(a.faults)
    if a.dry_run:
        print("CONFIG", cfg.hex(" "))
        print("DATA  ", data_msg(0, samples[:2], bits).hex(" "))
        return

    import serial

    port = serial.Serial(a.port, a.baud, timeout=0.05)
    feeder, buf, hello, t_cfg, t_start = Feeder(samples, fs, a.lead), b"", None, 0.0, time.monotonic()
    t_lvl, t_ping, ping_id, pings, sync_log = t_start, 0.0, 0, {}, None
    print(f"feeding {a.npz.name}: {n_ch} ch @ {fs} Hz, {bits}-bit, record_id {rid}, lead {a.lead} s")
    try:
        while True:
            t = time.monotonic()
            if hello is not None and t - t_lvl > 3:  # board only reports after CONFIG: it was reset
                print("board stopped reporting (reset?): sending CONFIG again", file=sys.stderr)
                hello, t_start = None, t
                feeder.on_level(0)
            if hello is None:  # repeat CONFIG until the board answers
                if t - t_start > 5:
                    raise SystemExit("no @HELLO from the board after 5 s: is ehg_node flashed and the port free?")
                if t - t_cfg > 0.5:
                    port.write(cfg)
                    t_cfg = t
            buf += port.read(max(1, port.in_waiting))  # returns at the 1st byte, so @SYNC is timed right
            *lines, buf = buf.split(b"\n")
            for raw in lines:
                line = raw.decode(errors="replace").replace("\x1b[0m", "").rstrip("\r")
                at = parse_at(line)
                if a.verbose and "@" in line:
                    print(f"{t - t_start:9.3f} {line}  -> {at}")
                if at is None:
                    print(line)
                elif at[0] == "HELLO" and hello is None:
                    hello, t_lvl = (at[1][0] if at[1] else "?"), t
                    print(f"board {hello} ready")
                    if sync_log is None:
                        sync_log = open_sync_log(hello)
                        runs = RESULTS / f"runs_{hello}.csv"
                        new = not runs.exists()
                        with runs.open("a") as f:  # what was injected, for the results table
                            if new:
                                f.write("t_start,record,record_id,faults\n")
                            f.write(f"{time.time():.3f},{a.npz.name},{rid},\"{a.faults}\"\n")
                    port.write(fault_msg(faults))  # always sent, so a reset board never keeps old faults
                    if a.faults:
                        print(f"faults: {a.faults} -> {faults}")
                elif at[0] == "SYNC" and len(at[1]) == 2 and int(at[1][0]) in pings:
                    t_recv = time.time()  # wall clock, like the gateway's t_gw_rx
                    sync_log.write(f"{at[1][0]},{pings.pop(int(at[1][0])):.6f},{t_recv:.6f},{at[1][1]}\n")
                    sync_log.flush()
                elif at[0] == "LVL" and at[1] and at[1][0].isdigit():
                    feeder.on_level(int(at[1][0]))
                    t_lvl = t
                elif at[0] == "ERR":
                    print(line, file=sys.stderr)
                    if "built-in record mode" in line:
                        raise SystemExit("this board was built with a built-in recording (build.sh --record): "
                                         "rebuild without --record to stream, or use --sync-only --node <name>")
            if hello is not None and t - t_ping >= 1:
                ping_id, t_ping = ping_id + 1, t
                pings = {k: v for k, v in pings.items() if k > ping_id - 10}  # forget unanswered ones
                pings[ping_id] = time.time()
                port.write(sync_msg(ping_id))
            if hello is not None:
                n = feeder.due()
                while n > 0:
                    k = min(n, MAX_FRAMES_PER_MSG)
                    first, frames = feeder.take(k)
                    port.write(data_msg(first, frames, bits))
                    if a.verbose:
                        print(f"{t - t_start:9.3f} sent frames {first}..{first + k - 1}")
                    n -= k
    except KeyboardInterrupt:
        print(f"stopped after frame_idx {feeder.next_idx - 1}")
    finally:
        port.close()
        if sync_log:
            sync_log.close()


if __name__ == "__main__":
    main()
