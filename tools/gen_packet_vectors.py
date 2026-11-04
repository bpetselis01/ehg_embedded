"""Generate the golden vectors that guard every copy of the packet decoder (Python, C firmware header, Swift) and of the gap tracker and clock map (Python, Swift).

Usage: uv run python tools/gen_packet_vectors.py          # rewrite tests/fixtures/packet_vectors.json
       uv run python tools/gen_packet_vectors.py --check  # exit 1 if the file is not what gateway/packet.py produces now

PURPOSE: The wire format has three copies (firmware/ehg_node/src/ehg_packet.h, gateway/packet.py, ios/EHGWire/Sources/EHGWire/Packet.swift). gateway/packet.py is the reference; its output is frozen here as JSON, and the Swift test suite
  must decode every vector to exactly the same values and refuse every bad one with the same reason (decision 0015).
INPUT: gateway/packet.py and gateway/tracking.py only. No hardware, no network.
OUTPUT: tests/fixtures/packet_vectors.json: {"valid": [{"name", "hex", "packet": {version, n_ch, fs_hz, seq, frame_idx, t_inject, n_frames, flags, record_id, samples}}], "invalid": [{"name", "hex", "error": kind}]}
  with kind one of too_short, unknown_version, wrong_length, crc_mismatch (the order Python checks them in, which the other copies must follow).
  e.g. {"name": "default_3ch_10frames_16bit", "hex": "0203 1400 ...", "packet": {"version": 2, "n_ch": 3, "fs_hz": 20, ...}}
  plus "tracking": {"gap": [{"name", "seqs", "returns" (what observe() returned for each), "final": {last, missing, received, duplicates, streams}}], "resend": [{"name", "steps": [[time, seq or null]], "requests": [[[start, count]]] per step}], "clock": [{"name", "updates": [[ticks, host_seconds]], "offset", "queries": [{"ticks", "host"}]}]}
HOW: Deterministic: fixed field values and a fixed pseudo-random sample sequence (a small linear generator, not random), so --check is stable. Extremes (-32768, 32767, 24-bit limits) and the flag bits are included on purpose.
ROLE IN THESIS: Keeps H2/H4 (bit-exact samples, damaged packets rejected) true on the phone as well as on the Mac.
BEFORE -> AFTER: Before: two copies guarded by tests/test_docs.py only for the C/Python constants. After: a third copy (Swift) guarded by shared vectors.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))  # lets `python tools/gen_packet_vectors.py` find gateway/
from gateway.packet import FLAG_24BIT, FLAG_RETRANS, HEADER, Packet, pack, parse  # noqa: E402
from gateway.gateway import packet_message, update_clock  # noqa: E402
from gateway.tracking import ClockMap, GapTracker, ResendScheduler  # noqa: E402

OUT = Path(__file__).parent.parent / "tests/fixtures/packet_vectors.json"


def samples(n_frames: int, n_ch: int, lo: int, hi: int, seed: int) -> list[list[int]]:
    """A repeatable spread of values between lo and hi, always including both extremes first."""
    vals, x = [lo, hi], seed
    while len(vals) < n_frames * n_ch:
        x = (x * 1103515245 + 12345) % (2**31)
        vals.append(lo + x % (hi - lo + 1))
    return [vals[i * n_ch:(i + 1) * n_ch] for i in range(n_frames)]


def make(name: str, n_ch=3, n_frames=10, flags=0, seed=1, **kw) -> tuple[str, Packet]:
    """One named valid packet."""
    lo, hi = (-8388608, 8388607) if flags & FLAG_24BIT else (-32768, 32767)
    fields = {"version": 2, "fs_hz": 20, "seq": 40, "frame_idx": 400, "t_inject": 98304, "record_id": 7, **kw}
    return name, Packet(n_ch=n_ch, n_frames=n_frames, flags=flags, samples=samples(n_frames, n_ch, lo, hi, seed), **fields)


def build() -> dict:
    """The whole vector file as a dict."""
    valid = [
        make("default_3ch_10frames_16bit"),
        make("one_channel", n_ch=1, seed=2),
        make("retransmission_flag", flags=FLAG_RETRANS, seed=3, seq=41),
        make("24bit_samples", flags=FLAG_24BIT, seed=4),
        make("24bit_and_retransmission", flags=FLAG_24BIT | FLAG_RETRANS, n_ch=2, n_frames=5, seed=5),
        make("no_frames", n_frames=0),
        make("max_field_values", seq=2**32 - 1, frame_idx=2**32 - 1, t_inject=2**32 - 1, record_id=65535, fs_hz=65535, seed=6),
        make("sequence_zero", seq=0, frame_idx=0, t_inject=0, record_id=0, seed=7),
    ]
    out_valid = []
    for name, p in valid:
        raw = pack(p)
        assert parse(raw) == p, name
        out_valid.append({"name": name, "hex": raw.hex(), "packet": p.__dict__})
    good = bytes.fromhex(out_valid[0]["hex"])
    flipped = bytearray(good)
    flipped[30] ^= 0x01
    bad_version = bytearray(good)
    bad_version[0] = 3
    cases = [("too_short_empty", b"", "too_short"), ("too_short_header_minus_one", good[:HEADER.size - 1], "too_short"),
             ("unknown_version", bytes(bad_version), "unknown_version"), ("wrong_length_one_byte_short", good[:-1], "wrong_length"),
             ("wrong_length_one_byte_long", good + b"\x00", "wrong_length"), ("crc_mismatch_sample_bit_flipped", bytes(flipped), "crc_mismatch"),
             ("crc_mismatch_trailer_zeroed", good[:-2] + b"\x00\x00", "crc_mismatch")]
    invalid = []
    for name, raw, kind in cases:
        try:
            parse(raw)
        except ValueError as e:
            got = ("too_short" if "too short" in str(e) else "unknown_version" if "unknown version" in str(e) else "wrong_length" if "expected" in str(e) else "crc_mismatch")
            assert got == kind, (name, str(e))
        else:
            raise AssertionError(f"{name} parsed")
        invalid.append({"name": name, "hex": raw.hex(), "error": kind})
    messages = []
    for i, v in enumerate(out_valid):  # the JSON the gateway publishes for each valid packet (fresh clock map, packet i received at 1791243068.5 + i)
        pk, t_rx, clock = parse(bytes.fromhex(v["hex"])), 1791243068.5 + i, ClockMap()
        update_clock(clock, pk, t_rx)
        messages.append({"name": v["name"], "node_id": "EHG-TEST", "t_rx": t_rx, "message": packet_message("EHG-TEST", pk, clock, t_rx)})
    return {"_note": "Generated by tools/gen_packet_vectors.py from gateway/packet.py, gateway/tracking.py and gateway/gateway.py. Do not edit by hand.", "valid": out_valid, "invalid": invalid, "tracking": tracking(), "messages": messages}


GAP_CASES = {
    "in_order": [0, 1, 2, 3],
    "gap_then_late_fill": [0, 1, 2, 5, 3, 4],
    "duplicate_ignored": [0, 1, 1, 2, 2, 0],
    "gap_never_filled": [10, 11, 15, 16],
    "late_arrival_below_first_is_new_gap_free": [5, 3],
    "backstep_over_1000_is_a_new_stream": [1500, 1501, 2, 3, 5, 9],
    "new_stream_resets_missing": [1100, 1103, 5, 1],
    "starts_mid_stream": [4000, 4001, 4003],
    "near_uint32_limit": [4294967293, 4294967294, 4294967295, 0, 1],
    "two_gaps_and_partial_fill": [0, 4, 9, 2, 7, 3],
}
# (time in s, seq) arrivals; plus times at which only the clock is looked at (seq None) so a retry can fall due without a new packet
RESEND_CASES = {
    "no_gap_no_request": [(0.0, 0), (0.1, 1), (0.2, 2)],
    "gap_requested_at_once_then_filled": [(0.0, 0), (0.1, 1), (0.2, 4), (0.3, 2), (0.4, 3), (2.0, 5)],
    "gap_retried_each_second": [(0.0, 0), (0.1, 3), (0.5, None), (1.1, None), (1.2, 4), (2.2, 5), (3.3, 6)],
    "gap_given_up_after_five_requests": [(0.0, 0), (0.1, 2)] + [(1.1 * k, None) for k in range(1, 9)],
    "ranges_are_merged": [(0.0, 0), (0.1, 5), (0.2, 7)],
    "new_stream_forgets_old_requests": [(0.0, 1500), (0.1, 1503), (0.5, 2), (1.6, 3)],
    "retry_not_before_one_second": [(0.0, 0), (0.1, 2), (1.0, None), (1.1, None), (1.2, 4)],
}
CLOCK_CASES = {
    "single_update": ([(113049, 1791243068.468658)], [98304, 113049, 200000]),
    "keeps_the_smallest_offset": ([(32768, 101.0), (65536, 102.9), (98304, 103.5)], [0, 32768, 98304, 131072]),
    "tick_counter_wraps": ([(2**32 - 65536, 5000.0), (2**32 - 32768, 5001.0), (100, 5002.5), (32868, 5003.4)], [2**32 - 65536, 2**32 - 1, 0, 100, 40000]),
    "late_packet_after_wrap_unwraps_backwards": ([(10, 9000.0), (2**32 - 10, 9000.001)], [2**32 - 10, 5, 2**31]),
}


def tracking() -> dict:
    """Golden cases for GapTracker and ClockMap, evaluated by the Python reference."""
    gap = []
    for name, seqs in GAP_CASES.items():
        t = GapTracker()
        returns = [t.observe(q) for q in seqs]
        gap.append({"name": name, "seqs": seqs, "returns": returns, "final": {"last": t.last, "missing": sorted(t.missing), "received": t.received, "duplicates": t.duplicates, "streams": t.streams}})
    clock = []
    for name, (updates, queries) in CLOCK_CASES.items():
        c = ClockMap()
        for ticks, host in updates:
            c.update(ticks, host)
        clock.append({"name": name, "updates": [list(u) for u in updates], "offset": c.offset, "queries": [{"ticks": q, "host": c.to_host(q)} for q in queries]})
    resend = []
    for name, steps in RESEND_CASES.items():
        t, s, requests = GapTracker(), ResendScheduler(), []
        for when, seq in steps:
            new = t.observe(seq) if seq is not None else []
            requests.append([list(r) for r in s.due(t, new, when)])
        resend.append({"name": name, "steps": [[when, seq] for when, seq in steps], "requests": requests})
    return {"gap": gap, "clock": clock, "resend": resend}


def main(argv: list[str]) -> int:
    """Write the file, or with --check compare."""
    text = json.dumps(build(), indent=1) + "\n"
    if "--check" in argv:
        if not OUT.exists() or OUT.read_text() != text:
            print(f"{OUT} is out of date: run uv run python tools/gen_packet_vectors.py")
            return 1
        print("packet vectors are current")
        return 0
    OUT.write_text(text)
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
