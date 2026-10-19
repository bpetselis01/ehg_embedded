# Fault injection: testing the pipeline under realistic link problems

What real telemonitoring links do to data, how this pipeline imitates it on purpose, and how to measure the
real numbers to plug in. Design and sources: [plans/realistic-injection-plan.md](plans/realistic-injection-plan.md).

## What a real BLE link does (and doesn't)

| Problem | Real? | Why |
|---|---|---|
| Lost packets, in **bursts** | yes | body shadowing, distance and 2.4 GHz interference come and go, so losses cluster. The usual model is Gilbert-Elliott |
| Delay / jitter | yes | the link layer retransmits until a packet gets through, so a bad patch becomes delay |
| Disconnects | yes | silence longer than the supervision timeout (6 s here) drops the link (reason 0x08) |
| Corrupted bits reaching the app | **no** | BLE checks a CRC on every radio packet and retransmits bad ones. We inject it anyway, to prove the end-to-end CRC (wire v2) catches a corruption that slips past, e.g. a firmware or memory bug |

## The injected faults (`tools/feed.py --faults …`)

All off at boot and on every feeder start without `--faults`. Seeded, so the same seed repeats the same pattern.
The board logs what it injected every 10 s: `injected: drop=… corrupt=… disc=…`. Each feeder start is recorded in
`eval/results/runs_<node>.csv`.

| Key | Meaning | Implemented in |
|---|---|---|
| `drop=0.05` | long-run share of packets lost "on the air" (the board counts them as sent, as a real loss looks) | [`ehg_service.c`](../firmware/ehg_node/src/ehg_service.c) `send()` |
| `burst=3` | mean length of a loss run, in packets (1 = independent losses) | same: Gilbert model, good→bad `p = drop·r/(1−drop)`, bad→good `r = 1/burst` |
| `corrupt=0.01` | chance a sent copy gets one random bit flipped (history keeps the clean copy) | same |
| `disc=60` | force a disconnect every 60 s | same, `disc_work_handler` |
| `seed=1` | random seed | same, xorshift32 |

Example: `uv run python tools/feed.py data/converted/tpehgdb/tpehg1007.npz --faults drop=0.05,burst=3,corrupt=0.01,disc=120`

## How the pipeline should recover (pass criteria, set before testing)

| Fault | Recovery path | Pass |
|---|---|---|
| drop | gateway sees the seq gap → RESEND → board re-sends from history (flag bit0). A re-sent copy can be lost too, so the gateway asks again after 1 s, up to 5 times (`RETRY_S`, `MAX_TRIES` in `gateway.py`) | `integrity.py` delivery 100 %, `resent` ≥ `injected drop` |
| corrupt | gateway rejects the CRC mismatch (`bad=` in its stats) → next packet shows the gap → RESEND | 0 bit-exact mismatches in the DB; `bad` ≈ `injected corrupt` |
| disc | board keeps sampling (packets counted `offline`), re-advertises; gateway reconnects with the same gap tracker and asks for the outage | one stream (`streams=1`), delivery 100 % while the outage < history (256 packets = 128 s at TPEHG) |
| outage > history | the oldest packets are gone | counted `resend_miss` on the board, listed as missing ranges by `integrity.py`; never silent |

## Measuring the real numbers (physical tests)

Run the normal pipeline with **no** `--faults`, change one condition at a time for 10 min each, and keep the gateway
CSV (`gateway/logs/<node>.csv`) and the board's 10 s counters.

1. **Distance:** DK and laptop 1, 3, 6, 10 m apart, line of sight.
2. **Body:** DK in a pocket or held against the body while walking around the room (body shadowing).
3. **Interference:** a large download over 2.4 GHz Wi-Fi next to the laptop; then a microwave oven running nearby.

From each run: the gateway's first-time gaps (`resent` on the board) give the loss share, and runs of consecutive
missing `seq` give the burst lengths. Their mean gives `burst`, and the share gives `drop`.
Prior art: Tipparaju et al., *Biosensors* 2021, saw no loss to 6 m and < 1 % with a microwave at about 6 m, and
removed the rest with a re-request queue like ours. Use the measured values in `--faults` for long repeatable runs.
