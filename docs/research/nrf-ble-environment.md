# nRF52840 board + Bluetooth: setup notes

Research notes from 2026-09-29, used to set up the firmware and the laptop Bluetooth code.
Tool versions: Nordic SDK (NCS) v3.4.1, which contains Zephyr 4.4.2 (`~/ncs/v3.4.1/zephyr/VERSION`).

- **Verified** = checked in the SDK files on this Mac, or actually run.
- **UNVERIFIED** = from docs or forums, not checked yet.
- File references like `host/Kconfig:414` point into `~/ncs/v3.4.1` (the SDK), for anyone who wants the source.

For what each firmware file does, see [firmware.md](../firmware.md). Terms are explained in the glossary at the end.

## In short

| Topic | Takeaway |
|---|---|
| Build + flash | works; use [`firmware/build.sh`](../../firmware/build.sh) (it hides the details in §1) |
| Bluetooth settings | biggest message 247 bytes, 2 Mbit/s radio, talk every 15–30 ms; already in [`prj.conf`](../../firmware/ehg_node/prj.conf) |
| Sending | send from our own thread: if the radio is busy it waits instead of losing data |
| Over-the-air loss | Bluetooth re-sends lost radio packets itself; real loss happens in queues on either end |
| Board clock | 32768 ticks/s, never wraps; drifts up to ~180 ms per hour, so latency code must correct for drift |
| macOS | the Mac decides speed + timing; the board can only ask. Max number of boards per Mac is unknown (test it) |

---

## 1. Build and flash (verified)

**Problem:** Nordic's build tool (`west`) only works inside the SDK folder, and isn't on the normal PATH.
**Fix:** run it through Nordic's launcher (`nrfutil toolchain-manager launch`) and set `ZEPHYR_BASE` so it can find the SDK from anywhere. Without it you get: `unknown command "build"; do you need to run this inside a workspace?`

`build.sh` does this for you. The raw commands, for reference:

```sh
# Option A: launcher in front of every command (works from any folder)
export ZEPHYR_BASE="$HOME/ncs/v3.4.1/zephyr"
NCS="nrfutil toolchain-manager launch --ncs-version v3.4.1 --"
$NCS west build -b nrf52840dk/nrf52840 -d build path/to/app          # add -p for a clean build
$NCS west flash -d build --dev-id 1050208294                          # copy onto the board
$NCS west flash -d build --dev-id 1050208294 --recover                # if the board is locked: full erase first

# Option B: load the tools once into the shell, then use west directly
eval "$(nrfutil toolchain-manager env --as-script sh --ncs-version v3.4.1)"
export ZEPHYR_BASE="$HOME/ncs/v3.4.1/zephyr"
west build -b nrf52840dk/nrf52840 -d build path/to/app && west flash -d build --dev-id 1050208294
```

What was checked:
- **A Nordic example app built fine** (`peripheral_lbs`). The first build took 370 s, mostly set-up (~230 s); later builds are quick (results are cached). It used 18.5 % of flash, 11.6 % of RAM.
- **The file that gets flashed** is `build/<app>/zephyr/zephyr.hex`. There's no `merged.hex`, because we only build one program.
- **Flashing** uses `nrfutil` (installed: 8.2.1, at `/opt/homebrew/bin/nrfutil`). It erases only the parts of memory the new program needs, writes, checks, then resets the board. `nrfjprog` (an older tool) isn't installed and isn't needed. A J-Link debugger tool is also available (`/usr/local/bin/JLinkExe`).
- **Unlocking a stuck board:** `nrfutil device recover --serial-number 1050208294` (erases everything).
- **Read-out protection** ("AP-Protect", stops anyone reading the chip's memory): our builds don't lock it, so the board stays debuggable after a recover + reflash.
- **Serial log:** USB port `/dev/tty.usbmodem0010502082941`, 115200 baud. (Some Nordic examples send logs to a debugger channel called RTT instead; ours uses the USB serial port.)

## 2. Bluetooth settings and functions (checked in the SDK)

### Settings ([`prj.conf`](../../firmware/ehg_node/prj.conf))

What each group does, in plain words. The real file has the final values.

```conf
# --- Board is a Bluetooth "peripheral" (waits to be connected), 1 connection at a time
CONFIG_BT=y
CONFIG_BT_PERIPHERAL=y
CONFIG_BT_DEVICE_NAME="EHG_00"
CONFIG_BT_MAX_CONN=1                       # Nordic's radio software allows 1..20

# --- Bigger messages: up to 247 bytes per message (MTU), 251 per radio packet
CONFIG_BT_L2CAP_TX_MTU=247                 # default is only 23
CONFIG_BT_BUF_ACL_RX_SIZE=251
CONFIG_BT_BUF_ACL_TX_SIZE=251
CONFIG_BT_CTLR_DATA_LENGTH_MAX=251
# the message size used = the smaller of what each side allows

# --- Let our code ask for the fast radio mode (2M) and big radio packets
CONFIG_BT_USER_PHY_UPDATE=y                # enables bt_conn_le_phy_update()
CONFIG_BT_USER_DATA_LEN_UPDATE=y           # enables bt_conn_le_data_len_update()
CONFIG_BT_CTLR_PHY_2M=y                    # already on by default
# alternative with no code: CONFIG_BT_AUTO_PHY_PERIPHERAL_2M=y / CONFIG_BT_AUTO_DATA_LEN_UPDATE=y

# --- Send buffers: how many messages can wait to go out (default 3)
CONFIG_BT_BUF_ACL_TX_COUNT=10
CONFIG_BT_ATT_TX_COUNT=10
CONFIG_BT_L2CAP_TX_BUF_COUNT=10
CONFIG_BT_CTLR_SDC_TX_PACKET_COUNT=3       # radio-side buffers. Our prj.conf uses 10 (matches the above, avoids a start-up warning)
CONFIG_BT_BUF_EVT_RX_COUNT=11              # must be more than the send-buffer count

# --- Preferred timing, sent to the laptop 5 s after connecting
CONFIG_BT_GAP_AUTO_UPDATE_CONN_PARAMS=y    # on by default
CONFIG_BT_GAP_PERIPHERAL_PREF_PARAMS=y     # on by default
CONFIG_BT_PERIPHERAL_PREF_MIN_INT=12       # talk at least every 15 ms (units of 1.25 ms)
CONFIG_BT_PERIPHERAL_PREF_MAX_INT=24       # ... at most every 30 ms (Apple: max must be >= min + 15 ms)
CONFIG_BT_PERIPHERAL_PREF_LATENCY=0        # board may not skip turns
CONFIG_BT_PERIPHERAL_PREF_TIMEOUT=600      # link lost after 6 s of silence (units of 10 ms); fits both Apple rule versions (§4)
```

Notes:
- **Comparison:** Nordic's "throughput" example (`nrf/samples/bluetooth/throughput/prj.conf`) uses even bigger messages (498 bytes) and sets the timing itself in code. We don't need that much.
- **Time per radio turn:** default 7.5 ms, and it stretches when there's more to send. Fine at our data rates.
- **`CONFIG_BT_GATT_AUTO_UPDATE_MTU`** only matters when the board is the one connecting (a "central"). As a peripheral, the board just answers the laptop's size request. macOS and Linux always ask.

### Functions our code uses

```c
bt_conn_le_phy_update(conn, BT_CONN_LE_PHY_PARAM_2M);          // ask for 2 Mbit/s radio
bt_conn_le_data_len_update(conn, BT_LE_DATA_LEN_PARAM_MAX);    // ask for 251-byte radio packets
bt_conn_le_param_update(conn, BT_LE_CONN_PARAM(12, 24, 0, 600)); // ask for 15-30 ms timing, 6 s timeout
bt_gatt_get_mtu(conn);                                         // agreed message size; usable = mtu - 3
bt_gatt_cb_register(&(struct bt_gatt_cb){ .att_mtu_updated = ... });  // get told when size is agreed
BT_CONN_CB_DEFINE(cb) = { .le_phy_updated, .le_data_len_updated, .le_param_updated, ... }; // get told what was granted
bt_gatt_is_subscribed(conn, attr, BT_GATT_CCC_NOTIFY);          // is the laptop listening?

struct bt_gatt_notify_params p = { .attr = attr, .data = buf, .len = n,
                                   .func = sent_cb, .user_data = NULL };
bt_gatt_notify_cb(conn, &p);                                   // send + get a callback when sent
```
(Declared in `zephyr/include/zephyr/bluetooth/conn.h` and `gatt.h`.)

### What happens when the send buffers are full (verified in `host/att.c:808-826`)

- **Sending from our own thread:** it **waits** until a buffer frees up. Data backs up, nothing is lost. ← what [`ehg_service.c`](../../firmware/ehg_node/src/ehg_service.c) does.
- **Sending from a Bluetooth callback or the system's shared job queue:** it **fails straight away** (`-ENOMEM`) instead of waiting.
- So: send from a dedicated thread, and put a packet number (seq) in every packet.
- **Bluetooth fixes radio errors itself:** each radio packet is acknowledged and re-sent if needed. So data isn't lost "in the air" unless the whole link drops. Real loss = a queue overflowing in our code, or drops on the laptop side. That's why we count queue overflows and track seq gaps.

### Renamed or retired settings in Zephyr 4.x (avoid these)

| Old | Use instead | Source |
|---|---|---|
| `CONFIG_BT_CONN_TX_MAX` | nothing: now follows `BT_BUF_ACL_TX_COUNT` | release notes 4.2 |
| `CONFIG_BT_AUTO_PHY_UPDATE` | `BT_AUTO_PHY_PERIPHERAL_2M` / `BT_AUTO_PHY_CENTRAL_2M` | `host/Kconfig:422` |
| `CONFIG_BT_BUF_ACL_RX_COUNT` | `CONFIG_BT_BUF_ACL_RX_COUNT_EXTRA` (count is now max connections + 1) | migration guide 4.1 |
| `bt_conn_le_info.interval` (1.25 ms units) | `interval_us` | migration guide 4.4 |
| `BT_GATT_CCC_INITIALIZER` / `struct _bt_gatt_ccc` | `BT_GATT_CCC_MANAGED_USER_DATA_INIT` / `bt_gatt_ccc_managed_user_data` | migration guide 4.2 |
| Bluetooth no longer turns on `CONFIG_POLL` | turn it on yourself if you use `k_poll` | migration guide 4.4 |
| Nordic "Partition Manager" | don't use (marked deprecated) | `nrf/sysbuild/Kconfig.sysbuild:9` |

### Many boards at once (H3)

- **Each board** accepts 1 laptop, so `CONFIG_BT_MAX_CONN=1` is enough.
- **Backup plan if a Mac can't handle 8 boards:** use one nRF board as the "central" that connects to all of them and passes data to Python over USB. Nordic's radio software supports up to 20 links. Settings to look at: `BT_MAX_CONN`, `CONFIG_BT_CTLR_SDC_CENTRAL_ACL_EVENT_SPACING_DEFAULT`, `CONFIG_BT_GATT_CLIENT=y`, `CONFIG_BT_GATT_AUTO_UPDATE_MTU=y`. Examples: `nrf/samples/bluetooth/central_uart`, `zephyr/samples/bluetooth/central_multilink`.

### How much data (payload budget)

| Stream | Data rate | Fits easily? |
|---|---|---|
| TPEHG (main): 3 ch × 20 Hz × 2 bytes | 120 bytes/s | yes, trivially |
| Icelandic (stretch): 16 ch × 200 Hz × 2 bytes | 6400 bytes/s (51 kbit/s) | yes: 7 frames (224 B) + header fit in 244 bytes, ~29 messages/s |
| 8 Icelandic boards into 1 Mac | 410 kbit/s | **UNVERIFIED** whether macOS keeps up |

(These notes assumed 1 frame per message for TPEHG. The firmware now batches 10 frames per packet: see [firmware.md](../firmware.md).)

## 3. Board clock and lining it up with the laptop clock

**The board's clock:**
- Runs at **32768 ticks per second** (1 tick ≈ 30.5 µs), from a small crystal. Plenty fine compared with the 5–50 ms between samples and the ms-scale Bluetooth timing.
- Use `k_uptime_ticks()`: a 64-bit count that never wraps. (`k_cycle_get_32()` has the same resolution but wraps after ~36 hours.)
- Faster counters exist (CPU cycle counter, 1 MHz timer) but they stop when the CPU sleeps or cost extra power. Not needed.

**Drift:** no two clocks run at exactly the same speed.
- The board's crystal is rated ±50 ppm (parts per million) to the Bluetooth software. The real crystal is probably ±20 ppm (**UNVERIFIED**: check the DK hardware guide). Temperature adds more.
- 20 ppm = 72 ms per hour; 50 ppm = 180 ms per hour. Against a 1–2 s latency target over a 6 h test, that matters. So **correct for drift; don't assume it's zero**.

**How to line the clocks up** (what [`eval/latency.py`](../../eval/latency.py) does):
1. Every packet carries its board time (`t_inject`). The laptop notes its own time on arrival.
2. Difference = laptop time − board time = (clock offset) + (drift × time) + (travel delay, never negative).
3. The packets with the **smallest** differences had the least delay. Fit a straight line through those lowest points (e.g. lowest value per 10–60 s window, then a line fit). That line = offset + drift. Method: Moon, Skelly & Towsley, IEEE INFOCOM 1999; also Paxson, SIGMETRICS 1998.
4. Delay of each packet = its difference − the line.
   - Catch: this measures delay **relative to the fastest packet**, so the minimum delay itself is unknown. Bounding it would need a "ping" round trip: laptop sends its time, board sends it back; half the round trip bounds the minimum (~1–2 radio turns, **UNVERIFIED**).
5. Report median / 95th / 99th percentile delay. Lost packets = gaps in seq.

**Laptop clock:** for measuring durations, a steady clock (`time.monotonic_ns()`) is safest; `time.time()` can jump when the Mac syncs its clock over the internet.

## 4. macOS (and Linux) Bluetooth from Python (Bleak)

**Library:** Bleak, version **3.0.2** (newest on PyPI as of 2026-05-02; needs Python ≥ 3.10).
- Version 3.0.0 changed some things: the `adapter` option is retired; Bluetooth errors raise `BleakGATTProtocolError`; you can't write the "start notifications" switch (CCCD 0x2902) directly any more.
- It also fixed notifications after reconnecting and memory leaks on macOS. Changelog: https://github.com/hbldh/bleak/blob/develop/CHANGELOG.rst

**Message size (MTU):**
- Macs ask for a large size (usually 517 or 527 bytes). The size used is the smaller of the two sides, so with our board at 247 we should get **247** (244 usable). The often-quoted 185 is from older iPhones. **UNVERIFIED** on this Mac: the board logs `att_mtu_updated` to confirm.
- Bleak's `mtu_size` on macOS can read too low right after connecting. Read it after setup, or just size packets on the board side (`bt_gatt_get_mtu()`), which is what we do.

**Radio speed and timing:**
- macOS has **no way for apps to set** radio speed (PHY), radio packet size or timing. The Mac decides.
- The board can ask for 2 Mbit/s and big radio packets; Apple Silicon Macs usually say yes (**UNVERIFIED**; the board logs what was granted).
- Timing: macOS usually picks every 15 or 30 ms. The board's request must follow Apple's accessory rules:
  - minimum interval ≥ 15 ms and a multiple of 15 ms; maximum ≥ minimum + 15 ms (or both 15 ms)
  - latency (turns the board may skip) ≤ 30
  - maximum × (latency + 1) ≤ 6 s
  - timeout > maximum × (latency + 1) × 3
  - Allowed timeout: 6–18 s (Silabs summary) vs 2–6 s (older Apple rules); **UNVERIFIED** which applies to current macOS. 6 s fits both, so that's what we use.
  - Sources: https://docs.silabs.com/bluetooth/7.2.0/bluetooth-mobile-applications/suitable-connection-parameters , https://developer.apple.com/library/archive/qa/qa1931/_index.html , https://punchthrough.com/ble-throughput-part-4/

**How many boards per Mac:** Apple doesn't say. Forum reports: 5–10+, depends on the Bluetooth chip (**UNVERIFIED**). Test with 8 boards; backup plan in §2.

**Receiving notifications:**
- macOS hands each message to Bleak, which passes it to Python's event loop. A slow handler makes a queue grow; it doesn't drop messages (**UNVERIFIED** at high rates).
- Keep the handler tiny: note the time + bytes, process later.
- macOS gives each board its own made-up ID per Mac, not the board's real hardware address. See https://bleak.readthedocs.io/en/latest/backends/macos.html
- The terminal app running Python needs Bluetooth permission: System Settings → Privacy & Security → Bluetooth.

**Linux / Raspberry Pi (BlueZ), if used instead of a Mac:**
- Asks for message size 517. Default timing 30–50 ms; accepts the board's timing request (**UNVERIFIED** on current Pi OS).
- Bleak 3.0.2 uses `StartNotify` by default; pass `bluez={"use_start_notify": False}` for the other method (`AcquireNotify`).
- The Pi's built-in Bluetooth chip reportedly handles fewer than 8 links (**UNVERIFIED**); plan on a USB dongle or an nRF central.

## 5. Tools that can be scripted

- **nRF Connect for Desktop** (Nordic's point-and-click apps): no way to drive them from scripts. Use these instead:
  - Bluetooth from Python: Bleak.
  - Serial log: `pyserial`, or `screen /dev/tty.usbmodem0010502082941 115200`.
  - Board actions: `nrfutil device {program,reset,recover,erase,list}`.
  - Other `nrfutil` add-ons: `ble-sniffer` (needs a sniffer board), `trace`, `mcu-manager`, `sdk-manager`.
  - `pc-ble-driver-py`: abandoned (2022, Python < 3.11). Don't use.
- **Power measurement (PPK2) without the app:** `ppk2-api` on PyPI (v0.9.2), https://github.com/IRNAS/ppk2-api-python. Main calls: `PPK2_API.list_devices()`, `use_ampere_meter()`, `set_source_voltage(mV)`, `toggle_DUT_power("ON")`, `start_measuring()`, `get_data()`. Talks over plain serial, so it should work on macOS (**UNVERIFIED** here). The official Power Profiler app is still maintained: https://github.com/NordicSemiconductor/pc-nrfconnect-ppk
- **AI assistant plug-ins (MCP servers):** Nordic's own "Nordic MCP" searches their docs (needs a myNordic login; https://www.nordicsemi.com/Products/Technologies/AI-assisted-development/Get-started). Docs only, no board control. Community ones aren't worth it: searching the local `~/ncs/v3.4.1` is more exact for this version.

## 6. Folder layout and the build system

- **Our firmware is a standalone app inside the thesis repo** ([`firmware/ehg_node/`](../../firmware/ehg_node)), built against the installed SDK via `ZEPHYR_BASE` (§1). No second multi-GB SDK copy needed.
- Minimal [`CMakeLists.txt`](../../firmware/ehg_node/CMakeLists.txt) for such an app:
  ```cmake
  cmake_minimum_required(VERSION 3.20.0)
  find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
  project(ehg_ble)
  target_sources(app PRIVATE src/main.c)
  ```
- **"Sysbuild"** = Nordic's wrapper that can build several programs together (e.g. a bootloader + app). On by default; with one program it's harmless, so we keep it. Things to know:
  - The output is at `build/<app>/zephyr/zephyr.hex` (not `build/zephyr/`), and the final settings at `build/<app>/zephyr/.config`.
  - App settings go in [`prj.conf`](../../firmware/ehg_node/prj.conf); wrapper settings (`SB_CONFIG_*`) would go in `sysbuild.conf`. We have none.
  - No bootloader (MCUboot) or over-the-air updates needed.
  - If the build complains after changing the board or settings, do a clean build (`build.sh pristine`).

## Glossary

| Term | Meaning |
|---|---|
| NCS / Zephyr | Nordic's SDK / the small operating system inside it |
| west / nrfutil | Nordic's build tool / Nordic's flashing + toolchain tool |
| Kconfig / `prj.conf` | build-time settings (`CONFIG_…=value`) |
| peripheral / central | board (advertises, waits) / laptop (connects) |
| MTU | biggest Bluetooth message; 247 bytes here, 244 usable |
| DLE (data length) | bigger radio packets (251 bytes) |
| PHY | radio speed mode: 1M or 2M (Mbit/s) |
| connection interval | how often board and laptop talk (15–30 ms) |
| supervision timeout | silence after which the link counts as lost (6 s) |
| notify / CCCD | board pushes data / switch the laptop flips to receive it |
| ppm | parts per million: clock speed error (20 ppm = 72 ms/hour) |
| SoftDevice Controller (SDC) | Nordic's low-level radio software |
| sysbuild | Nordic's wrapper for building several programs at once |
| AP-Protect | chip read-out protection |
