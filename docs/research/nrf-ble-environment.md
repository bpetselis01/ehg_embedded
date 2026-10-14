# nRF52840 DK + NCS v3.4.1 + Bleak: environment and BLE notes

Researched 2026-09-29. NCS v3.4.1 = Zephyr 4.4.2 (`~/ncs/v3.4.1/zephyr/VERSION`).
"Verified" = checked against the local tree or run on this Mac. Anything not checked is marked **UNVERIFIED**.

---

## 1. Build and flash (verified)

- `~/ncs/v3.4.1` is a real west workspace (`.west/config`: manifest `nrf/west.yml`, zephyr base `zephyr`).
- `nrfutil toolchain-manager launch --ncs-version v3.4.1 -- west --version` gives `West version: v1.5.0`.
- **Gotcha:** outside the workspace, `west build` fails with `unknown command "build"; do you need to run this inside a workspace?`. To fix it, export `ZEPHYR_BASE`. The launcher passes the variable through.

```sh
# Option A: one launcher call per command (non-interactive, works from any cwd)
export ZEPHYR_BASE="$HOME/ncs/v3.4.1/zephyr"
NCS="nrfutil toolchain-manager launch --ncs-version v3.4.1 --"
$NCS west build -b nrf52840dk/nrf52840 -d build path/to/app          # add -p for pristine
$NCS west flash -d build --dev-id 1050208294                          # runner = nrfutil (default)
$NCS west flash -d build --dev-id 1050208294 --recover                # if AP-Protect is on (full erase)

# Option B: load the toolchain env once into a sh/zsh script (verified: west ends up on PATH)
eval "$(nrfutil toolchain-manager env --as-script sh --ncs-version v3.4.1)"
export ZEPHYR_BASE="$HOME/ncs/v3.4.1/zephyr"
west build -b nrf52840dk/nrf52840 -d build path/to/app && west flash -d build --dev-id 1050208294
```

- Proof: I built `nrf/samples/bluetooth/peripheral_lbs` into `/tmp/lbs/build` with exit code 0.
  - The cold build took 370 s. CMake configure took most of that (about 230 s). Incremental builds are fast (ccache is in the toolchain).
  - Memory use: FLASH 193,908 B (18.5 %), RAM 30,360 B (11.6 %).
- The build uses sysbuild. The output hex is at `build/<app_name>/zephyr/zephyr.hex`. No `merged.hex` is produced for a single image.
- `west flash --dry-run` (verified; this path runs no device commands, see `zephyr/scripts/west_commands/runners/nrfutil.py:_exec`) shows that the flash runs:
  ```
  nrfutil --json device program --firmware build/peripheral_lbs/zephyr/zephyr.hex \
     --options chip_erase_mode=ERASE_RANGES_TOUCHED_BY_FIRMWARE,verify=VERIFY_READ --x-family nrf52 ...
  nrfutil --json device reset --reset-kind RESET_SYSTEM ...
  nrfutil --json device ... x-execute-batch ... --serial-number 1050208294
  ```
  Runners are listed in `build/peripheral_lbs/zephyr/runners.yaml`: `nrfutil` is the default for flash, `jlink` for debug. `nrfjprog` is listed but not installed, so it is not needed. `--runner jlink` uses `/usr/local/bin/JLinkExe`.
- `nrfutil` 8.2.1 is at `/opt/homebrew/bin/nrfutil`, with the `device` command at 2.21.0.
- Standalone recover: `nrfutil device recover --serial-number 1050208294`.
- AP-Protect: the default build sets `CONFIG_NRF_APPROTECT_USE_UICR=y` and leaves `CONFIG_NRF_APPROTECT_LOCK` unset (build `.config`; `zephyr/soc/nordic/Kconfig:151`). Debug access therefore stays open after a recover and reflash.
- Serial log: VCOM0 `/dev/tty.usbmodem0010502082941` at 115200. Note that `peripheral_uart` sends logs to RTT (`CONFIG_LOG_BACKEND_RTT=y`, `CONFIG_LOG_BACKEND_UART=n`), not UART.

## 2. BLE Kconfig and API (grepped in the v3.4.1 tree)

### Recommended `prj.conf` (peripheral, streaming)

```conf
CONFIG_BT=y
CONFIG_BT_PERIPHERAL=y
CONFIG_BT_DEVICE_NAME="EHG_00"
CONFIG_BT_MAX_CONN=1                       # per peripheral; SDC range 1..20 (nrf/subsys/bluetooth/Kconfig:23)

# ATT MTU 247 / DLE 251
CONFIG_BT_L2CAP_TX_MTU=247                 # zephyr/subsys/bluetooth/host/Kconfig.l2cap:28 (default 23, 65 if SMP)
CONFIG_BT_BUF_ACL_RX_SIZE=251              # common/Kconfig:51 ; RX MTU = ACL_RX_SIZE-4 (include/zephyr/bluetooth/l2cap.h:45)
CONFIG_BT_BUF_ACL_TX_SIZE=251              # common/Kconfig:9
CONFIG_BT_CTLR_DATA_LENGTH_MAX=251         # controller/Kconfig:611 (defaults to ACL_RX_SIZE if <=251)
# local ATT MTU = MIN(BT_L2CAP_RX_MTU, BT_L2CAP_TX_MTU)  (host/att_internal.h:38)

# PHY / DLE
CONFIG_BT_USER_PHY_UPDATE=y                # host/Kconfig:414 -> bt_conn_le_phy_update()
CONFIG_BT_USER_DATA_LEN_UPDATE=y           # host/Kconfig:475 -> bt_conn_le_data_len_update()
CONFIG_BT_CTLR_PHY_2M=y                    # already y by default (build .config)
# or no-code alternative:
# CONFIG_BT_AUTO_PHY_PERIPHERAL_2M=y       # choice, host/Kconfig (replaces deprecated BT_AUTO_PHY_UPDATE)
# CONFIG_BT_AUTO_DATA_LEN_UPDATE=y         # default y only if !BT_USER_DATA_LEN_UPDATE

# TX buffering (notification flow control)
CONFIG_BT_BUF_ACL_TX_COUNT=10              # common/Kconfig:34 (default 3)
CONFIG_BT_ATT_TX_COUNT=10                  # host/Kconfig.gatt:16 (default = ACL_TX_COUNT)
CONFIG_BT_L2CAP_TX_BUF_COUNT=10            # host/Kconfig.l2cap:9 (default = ACL_TX_COUNT)
CONFIG_BT_CTLR_SDC_TX_PACKET_COUNT=3       # nrf/subsys/bluetooth/controller/Kconfig:221 (per-conn LL buffers, 1..20)
CONFIG_BT_BUF_EVT_RX_COUNT=11              # throughput sample value

# Connection-parameter preference (PPCP + auto L2CAP conn-param request 5 s after connect)
CONFIG_BT_GAP_AUTO_UPDATE_CONN_PARAMS=y    # Kconfig.gatt:226, default y
CONFIG_BT_GAP_PERIPHERAL_PREF_PARAMS=y     # Kconfig.gatt:235, default y
CONFIG_BT_PERIPHERAL_PREF_MIN_INT=12       # units 1.25 ms -> 15 ms  (default 24 = 30 ms)
CONFIG_BT_PERIPHERAL_PREF_MAX_INT=24       # 30 ms (Apple: max >= min+15 ms) (default 40 = 50 ms)
CONFIG_BT_PERIPHERAL_PREF_LATENCY=0
CONFIG_BT_PERIPHERAL_PREF_TIMEOUT=600      # 6 s, units 10 ms (default 42 = 420 ms). Satisfies both Apple ADG versions (sec. 4)
```

- Reference config: `nrf/samples/bluetooth/throughput/prj.conf`. It uses ACL 502, L2CAP_TX_MTU 498, ATT/ACL_TX 10, `CONFIG_BT_CTLR_SDC_MAX_CONN_EVENT_LEN_DEFAULT=4000000`, and `CONFIG_BT_GAP_AUTO_UPDATE_CONN_PARAMS=n`, and calls the APIs directly (`src/main.c:144,504,518,538`).
- Connection event length: `CONFIG_BT_CTLR_SDC_MAX_CONN_EVENT_LEN_DEFAULT` defaults to 7500 µs, and `CONFIG_BT_CTLR_SDC_CONN_EVENT_EXTEND_DEFAULT=y` by default (`nrf/subsys/bluetooth/controller/Kconfig:143,159`). The defaults are fine at our data rates.
- `CONFIG_BT_GATT_AUTO_UPDATE_MTU` exists (`Kconfig.gatt:219`), but it `depends on BT_GATT_CLIENT`, so it only matters on a Zephyr **central**. A peripheral just responds to the central's MTU exchange. macOS and BlueZ always start the exchange themselves.

### APIs (`zephyr/include/zephyr/bluetooth/conn.h`, `gatt.h`)

```c
bt_conn_le_phy_update(conn, BT_CONN_LE_PHY_PARAM_2M);          // conn.h:1709, macro :141
bt_conn_le_data_len_update(conn, BT_LE_DATA_LEN_PARAM_MAX);    // conn.h:1695, macro :203 (251 B / max time)
bt_conn_le_param_update(conn, BT_LE_CONN_PARAM(12, 24, 0, 600)); // conn.h:1684
bt_gatt_get_mtu(conn);                                         // gatt.h:1661 ; payload = mtu-3
bt_gatt_cb_register(&(struct bt_gatt_cb){ .att_mtu_updated = ... });  // gatt.h:382,625
BT_CONN_CB_DEFINE(cb) = { .le_phy_updated, .le_data_len_updated, .le_param_updated, ... }; // conn.h:2230-2314
bt_gatt_is_subscribed(conn, attr, BT_GATT_CCC_NOTIFY);          // gatt.h:1649

struct bt_gatt_notify_params p = { .attr = attr, .data = buf, .len = n,
                                   .func = sent_cb, .user_data = NULL };
bt_gatt_notify_cb(conn, &p);                                   // gatt.h:1410
```

Flow-control semantics (verified in `zephyr/subsys/bluetooth/host/att.c:808-826`):
- A notify issued from **an app thread** blocks with `K_FOREVER` until a buffer from `BT_ATT_TX_COUNT` frees up. This gives natural backpressure.
- A notify issued from **the system workqueue**, or from a BT callback context, uses `K_NO_WAIT`. It returns an error (`-ENOMEM`) when no buffer is free.
- Recommendation: send from a dedicated thread, and pace it with a `k_sem` that the `.func` completion callback gives (the callback runs on the system workqueue, `gatt.h:1396`). Put a sequence number in every packet.
- The link layer is reliable: packets are ACKed and retransmitted. Notifications are therefore not lost over the air unless the link drops. In practice, loss means a TX queue overflow in the app, or host-side drops.

### Renamed or deprecated in Zephyr 4.x (as shipped in NCS 3.4)

| Item | Status | Source |
|---|---|---|
| `CONFIG_BT_CONN_TX_MAX` | **Deprecated** in 4.2; now tied to `BT_BUF_ACL_TX_COUNT`. Do not set it. | `doc/releases/release-notes-4.2.rst:153`, `host/Kconfig:380` |
| `CONFIG_BT_AUTO_PHY_UPDATE` | **Deprecated**; use the choices `BT_AUTO_PHY_PERIPHERAL_2M` / `BT_AUTO_PHY_CENTRAL_2M` (central defaults to 2M) | `host/Kconfig:422` |
| `CONFIG_BT_BUF_ACL_RX_COUNT` | **Deprecated** in 4.1; count is now `BT_MAX_CONN+1`; use `CONFIG_BT_BUF_ACL_RX_COUNT_EXTRA` | `doc/releases/migration-guide-4.1.rst:614` |
| `bt_conn_le_info.interval` (1.25 ms units) | **Deprecated** in 4.4; use `interval_us` | `migration-guide-4.4.rst:1149`, `conn.h:1085-1092` |
| `BT_GATT_CCC_INITIALIZER` / `struct _bt_gatt_ccc` | Renamed to `BT_GATT_CCC_MANAGED_USER_DATA_INIT` / `bt_gatt_ccc_managed_user_data` (4.2) | `migration-guide-4.2.rst:551-555` |
| Host no longer selects `CONFIG_POLL` | Enable it yourself if you use `k_poll` | `migration-guide-4.4.rst` |
| NCS Partition Manager | Marked `[DEPRECATED]` in sysbuild | `nrf/sysbuild/Kconfig.sysbuild:9` |

### Multiple connections
- **Peripheral side:** each DK accepts one central, so `CONFIG_BT_MAX_CONN=1` is enough.
- **Using a DK as central** (fallback if macOS cannot handle 8):
  - SoftDevice Controller supports up to 20 links (`BT_MAX_CONN range 1 20`).
  - Set central links as `BT_MAX_CONN - BT_CTLR_SDC_PERIPHERAL_COUNT` (`nrf/subsys/bluetooth/controller/Kconfig:128`).
  - Space the links with `CONFIG_BT_CTLR_SDC_CENTRAL_ACL_EVENT_SPACING_DEFAULT`, and set `CONFIG_BT_GATT_CLIENT=y` and `CONFIG_BT_GATT_AUTO_UPDATE_MTU=y`.
  - Reference samples: `nrf/samples/bluetooth/central_uart`, `zephyr/samples/bluetooth/central_multilink`.

### Payload budget
- Main stream: 3 ch × 20 Hz × 2 B = 120 B/s, i.e. 6 B per frame. Send one notification per frame (header plus 6 B) at 20 notif/s. This is trivial.
- Stretch stream: 16 ch × 200 Hz × 2 B = 6400 B/s (51.2 kbit/s), i.e. 32 B per frame.
  - With MTU 247 there are 244 B of payload. That fits a header (u32 seq + u32 ts = 8 B) plus 7 frames (224 B). Batching adds 35 ms of latency.
  - About 29 notif/s, or fewer than 1 packet per 30 ms event. 1M PHY is enough for one link.
  - 8 links × 51 kbit/s = 410 kbit/s aggregate into one Mac. **UNVERIFIED** whether macOS can sustain this.

## 3. Device timestamps and host/device clock alignment

- nRF52 system timer = RTC1 on LFCLK:
  - `CONFIG_SYS_CLOCK_HW_CYCLES_PER_SEC=32768` and `CONFIG_SYS_CLOCK_TICKS_PER_SEC=32768` (build `.config`; `soc/nordic/nrf52/Kconfig.defconfig:10`, `soc/nordic/Kconfig.defconfig:13`).
  - The RTC counter is 24 bits (`NRF_RTC_COUNTER_BIT_WIDTH=24`), and the kernel extends it in software.
- Therefore:
  - `k_cycle_get_32()` has **30.52 µs** resolution, not CPU cycles, and wraps after 2^32/32768 ≈ 36.4 h.
  - `k_uptime_ticks()` returns int64 at the same resolution and never wraps. Convert with `k_ticks_to_us_floor64()`. **Use this.** The resolution is far finer than the 5 ms (200 Hz) to 50 ms (20 Hz) sample period and the ms-scale BLE jitter.
  - True µs or cycle counters (optional, not recommended):
    - DWT CYCCNT (`CONFIG_CORTEX_M_DWT`, 64 MHz): stops when the CPU sleeps. Its HFCLK source is HFINT RC unless HFXO is requested.
    - nrfx TIMER at 1 MHz: needs an HFXO request, which costs current (**UNVERIFIED** figure: a few hundred µA).
- LFCLK source on the DK is `CONFIG_CLOCK_CONTROL_NRF_K32SRC_XTAL=y`, with declared accuracy `CONFIG_CLOCK_CONTROL_NRF_K32SRC_50PPM=y` (the default, `zephyr/drivers/clock_control/Kconfig.nrf:164-204`). That setting is the budget declared to the BLE stack, not a measurement.
  - The PCA10056 crystal is probably ±20 ppm initial tolerance (**UNVERIFIED**: check the nRF52840 DK hardware guide / BOM). Temperature adds more.
  - Scale of the problem: 20 ppm = 72 ms/h and 50 ppm = 180 ms/h. That is significant against a 1-2 s p95 budget over long runs, so **fit drift; don't assume zero**.
- Alignment method (one-way timestamps):
  1. Each packet carries `seq` and `t_dev` (the device time of the first sample, or of enqueue). The host records `t_host = time.monotonic_ns()` in the Bleak callback.
  2. Compute `d_i = t_host_i − t_dev_i`. This equals the true offset + drift·t + the latency (≥ 0).
  3. Fit the **lower envelope** of `d_i` against `t_dev` with a linear function (skew + offset). Use the lower convex hull or LP method: Moon, Skelly & Towsley, "Estimation and removal of clock skew from network delay measurements", IEEE INFOCOM 1999. See also Paxson, "On calibrating measurements of packet transit times", SIGMETRICS 1998.
     - A simple version: take the minimum of `d` per 10-60 s window, then run `numpy.polyfit(t, mins, 1)`.
  4. Latency_i = `d_i − envelope(t_dev_i)`. The result is **relative to the minimum-latency packet**, so the one-way floor is unknown.
     - To bound the floor, add a round-trip "ping" characteristic. The host writes `t_host` without response; the device notifies back `t_host` + `t_dev`. That gives an NTP-style offset and RTT, and RTT/2 bounds the floor. With a 30 ms interval, RTT is about 1-2 connection intervals (**UNVERIFIED**, measure it).
  5. Report p50/p95/p99 end-to-end (sample time to host callback). Loss = gaps in `seq`.
- Host clock: use `time.monotonic_ns()` or `time.perf_counter_ns()`, not `time.time()`, because the latter jumps with NTP.

## 4. macOS CoreBluetooth + Bleak; BlueZ

- **Bleak to pin: `bleak==3.0.2`** (latest on PyPI, uploaded 2026-05-02, requires Python ≥ 3.10; source: https://pypi.org/pypi/bleak/json).
  - 3.0.0 breaking changes: the `adapter` kwarg is deprecated; GATT errors now raise `BleakGATTProtocolError`; writing the CCCD 0x2902 directly raises `ValueError`.
  - 3.0.0 also fixed `start_notify()` after reconnect and CoreBluetooth memory leaks.
  - Changelog: https://github.com/hbldh/bleak/blob/develop/CHANGELOG.rst
- `BleakClient.mtu_size` on macOS returns `peripheral.maximumWriteValueLengthForType_(CBCharacteristicWriteWithoutResponse) + 3` (`bleak/backends/corebluetooth/client.py:186`).
  - The value is negotiated asynchronously and can read low right after connect. Read it after service discovery, or just size payloads from the firmware side using `bt_gatt_get_mtu()`.
- MTU macOS negotiates: modern Apple centrals request a large ATT MTU (commonly 517/527). The result is min(central, peripheral), so our peripheral set to 247 should yield **247** (payload 244).
  - The often-quoted 185 comes from older iOS versions and devices.
  - **UNVERIFIED** on macOS 26 / Apple Silicon: log `att_mtu_updated` on the DK to confirm.
- PHY, DLE and connection interval: CoreBluetooth has **no API** for any of these; the central decides.
  - The peripheral can request 2M PHY and DLE; Apple Silicon Macs generally accept them (**UNVERIFIED**; log `le_phy_updated` and `le_data_len_updated`).
  - Connection interval: macOS picks it, typically 15 ms or 30 ms.
    - Peripheral requests must follow Apple's Accessory Design Guidelines: Interval Min ≥ 15 ms and a multiple of 15 ms; Interval Max ≥ Min + 15 ms (or both = 15 ms); latency ≤ 30; Interval Max × (latency+1) ≤ 6 s; supervision timeout > Interval Max × (latency+1) × 3.
    - Timeout range: the Silabs summary says 6-18 s; older ADG revisions said 2-6 s, **UNVERIFIED** which applies to macOS 26.
    - Sources: https://docs.silabs.com/bluetooth/7.2.0/bluetooth-mobile-applications/suitable-connection-parameters , https://developer.apple.com/library/archive/qa/qa1931/_index.html , Punch Through (iOS minimum 15 ms): https://punchthrough.com/ble-throughput-part-4/
    - The prj.conf above (15-30 ms) complies. **Set `PREF_TIMEOUT` to about 600 (6 s) to be safe for both versions of the rule.**
- Max simultaneous connections on macOS: not documented by Apple. **UNVERIFIED**; community reports are in the 5-10+ range depending on the controller.
  - Test it empirically with 8 DKs or dongles. Fallback: a DK or nRF52840 dongle as a multi-link central, bridged over USB-CDC to Python (sec. 2).
- Notification behaviour: CoreBluetooth delivers `didUpdateValueForCharacteristic` callbacks, and Bleak forwards each one to the asyncio loop with `call_soon_threadsafe`.
  - A slow callback makes the queue grow; it does not drop packets (**UNVERIFIED** at high rates).
  - Keep callbacks minimal: append `(monotonic_ns, bytes)` to a list or queue and parse later.
  - Known issue: macOS identifies devices by per-host UUID, not MAC address (Bleak macOS backend doc: https://bleak.readthedocs.io/en/latest/backends/macos.html).
  - The terminal or Python needs the Bluetooth privacy permission (System Settings → Privacy & Security → Bluetooth).
- BlueZ / Raspberry Pi:
  - `mtu_size` comes from `AcquireWrite`/`AcquireNotify` (`bluezdbus/client.py:598 _acquire_mtu`). Bleak 3.0.2 reverts to `StartNotify` by default; pass `bluez={"use_start_notify": False}` to use `AcquireNotify` (Bleak `docs/backends/linux.rst`).
  - The kernel's default LE connection interval is 30-50 ms (`/sys/kernel/debug/bluetooth/hci0/conn_min_interval` = 24, `conn_max_interval` = 40), and it accepts the peripheral's conn-param request (**UNVERIFIED** on current Pi OS).
  - BlueZ requests MTU 517.
  - The Pi's on-board CYW43455 limits concurrent links (**UNVERIFIED**, reportedly fewer than 8 in practice). Plan on a USB dongle or an nRF central for 8 links.

## 5. Tooling automation

- **nRF Connect for Desktop** (Electron GUI apps: Bluetooth Low Energy, Serial Terminal, Power Profiler): no CLI, API or MCP for driving these apps was found. The launcher is open source (https://github.com/NordicSemiconductor/pc-nrfconnect-launcher). Use scripts instead:
  - BLE central: Bleak.
  - Serial: `pyserial` / `screen /dev/tty.usbmodem0010502082941 115200`.
  - Device ops: `nrfutil device {program,reset,recover,erase,list}`.
  - Other installable nrfutil commands: `ble-sniffer` 0.21.0 (needs a sniffer-firmware dongle or DK), `trace`, `mcu-manager`, `sdk-manager` (from `nrfutil search`).
  - `pc-ble-driver-py` is dead for this use (0.17.0, 2022, Python < 3.11).
- **PPK2 without the GUI:** `ppk2-api` on PyPI, v0.9.2 (release 2023-06-09; repo last commit 2026-05-19), https://github.com/IRNAS/ppk2-api-python, GPL-2.0.
  - API: `PPK2_API.list_devices()`, `PPK2_API(port)`, `get_modifiers()`, `use_source_meter()` / `use_ampere_meter()`, `set_source_voltage(mV)`, `toggle_DUT_power("ON")`, `start_measuring()`, `get_data()`, `get_samples(data)`. `PPK2_MP` is a multiprocessing variant with a background buffer.
  - Pure serial, so it should work on macOS (**UNVERIFIED** on this Mac).
  - The official Power Profiler app is alive: https://github.com/NordicSemiconductor/pc-nrfconnect-ppk (pushed 2026-09-24).
- **MCP servers:**
  - Nordic official: "Nordic MCP" (semantic search over NCS docs, DevAcademy, DevZone) and "nRF Cloud MCP". Both need a myNordic / nRF Cloud login.
    - Info: https://www.nordicsemi.com/Products/Technologies/AI-assisted-development/Get-started
    - Docs: https://docs.nordicsemi.com/r/bundle/nordic-mcp/page/index.html (the fetch failed, so the endpoint URL is **UNVERIFIED**).
    - This is the only one worth considering; it is docs-only, with no device control.
  - Community: `pshanesmith/nrf-mcp` (docs via the GitHub API, 0 stars) and `Aarav-J/zephyr-mcp-server` (Kconfig/DT/API docs, 3 stars). Not recommended; grepping the local `~/ncs/v3.4.1` is authoritative for this exact version.
  - No MCP was found for nrfutil or device control.

## 6. Project layout and sysbuild

- **Recommended: a freestanding app in the thesis repo**, e.g. `firmware/ehg_ble/{CMakeLists.txt, prj.conf, src/main.c, boards/nrf52840dk_nrf52840.overlay?}`, using the installed v3.4.1 workspace via `ZEPHYR_BASE` (sec. 1).
  - It needs no west manifest of its own and no second SDK copy.
  - A "workspace app" (own `west.yml` importing sdk-nrf) is only worth it for pinning or CI. It would duplicate a multi-GB checkout.
  - Minimal `CMakeLists.txt`:
    ```cmake
    cmake_minimum_required(VERSION 3.20.0)
    find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
    project(ehg_ble)
    target_sources(app PRIVATE src/main.c)
    ```
- **Sysbuild:** it is the default (the build log shows `sysbuild_default`), and for a single image it is harmless. Keep it; `--no-sysbuild` exists, but NCS expects sysbuild. Gotchas:
  - Output path is `build/<app>/zephyr/zephyr.hex`, not `build/zephyr/zephyr.hex`. The app's `.config` is at `build/<app>/zephyr/.config`.
  - Image-level Kconfig goes in `prj.conf`. Sysbuild-level options (`SB_CONFIG_*`, e.g. MCUboot) go in `sysbuild.conf` or `Kconfig.sysbuild`.
  - `-DEXTRA_CONF_FILE=...` on the command line applies to the main app. For a specific image, prefix it: `-D<image>_EXTRA_CONF_FILE`.
  - Partition Manager is off for this board by default and is marked DEPRECATED in NCS 3.4, so don't enable it. There's no need for MCUboot/DFU.
  - Change the board or sysbuild config and rebuild with `-p` (pristine) if configure errors appear.
  - `west build` outside the workspace needs `ZEPHYR_BASE` (sec. 1).
