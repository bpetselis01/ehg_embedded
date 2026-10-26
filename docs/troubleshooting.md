# Troubleshooting

Known failures, each as **symptom → cause → fix**. Found something new? Add a row here, with the date, rather than in
[progress.md](progress.md) only.

## Board and serial port

| Symptom | Cause | Fix |
|---|---|---|
| `feed.py` (or `screen`) cannot open `/dev/cu.usbmodem0010502082941`: "Resource busy" | Only one program can hold the serial port, and VS Code's nRF Terminal counts | Close the other program. `feed.py` prints the board log itself, so nothing else is needed ([firmware.md](firmware.md), serial port) |
| Garbled log text | Terminal set to the wrong speed | Use 460800 baud (set in [`boards/nrf52840dk_nrf52840.overlay`](../firmware/ehg_node/boards/nrf52840dk_nrf52840.overlay)) |
| Build fails inside sysbuild with path errors | NCS sysbuild breaks on folder names with spaces (`2026 Term 3/Thesis B`) | Always build with [`firmware/build.sh`](../firmware/build.sh); it goes through the `~/thesisb` symlink and launches the toolchain (`west` is not on PATH) |
| `feed.py` stops with `@ERR built-in record mode` | The board was flashed with `build.sh flash --record …`, which refuses streamed data and faults | Reflash with a plain `firmware/build.sh flash`, or keep the built-in image and run `feed.py --sync-only --node EHG-93C0` ([decision 0003](decisions/0003-built-in-recording-build-time-option.md)) |
| `starved=` climbs in the 10 s log line | The laptop did not deliver frames before their due time (test-rig loss, not radio loss) | Start `feed.py` before the gateway's START; keep the laptop awake; nothing else on the port |
| `ring_overflow=` climbs | Sending fell behind the replay timer (link too slow or stalled) | Check the link parameters in the log (2M PHY, interval); fewer channels or a larger `CONFIG_EHG_FRAMES_PER_PACKET` |
| `resend_miss=` > 0 and delivery < 100 % | A resend was asked for after the copy left the history (`CONFIG_EHG_HISTORY_PACKETS`, 128 s at 20 Hz) | Expected after very long disconnects; the loss is still identified by seq (H2). Raise the history size if it happens in normal runs |
| Some board log lines look damaged | The J-Link VCOM loses a few board→laptop bytes while Bluetooth is busy | By design: machine lines (`@…`) carry a CRC and damaged ones are ignored ([interface-spec.md](interface-spec.md) §3b) |
| Board unresponsive after a bad flash | Flash or protection state stuck | `nrfutil device recover --serial-number 1050208294` (erases everything), then `firmware/build.sh flash` |

## Bluetooth and gateway

| Symptom | Cause | Fix |
|---|---|---|
| `gateway.gateway` scan hangs or finds nothing, with no error | macOS never granted Bluetooth access to the app running the terminal | System Settings → Privacy & Security → Bluetooth → enable the terminal app (or VS Code). Or run `uv run python -m gateway.gateway --no-mqtt` once and click Allow |
| Gateway finds the board but no data arrives | Sampling starts on START; in serial-feed mode the board also needs `feed.py`'s CONFIG first | Run `feed.py` first, then the gateway (or press DK Button 1). LED2 on = sampling |

## Backend

| Symptom | Cause | Fix |
|---|---|---|
| `docker compose up -d` seems stuck | Docker Desktop on this Mac takes 5–15 min to start containers | Wait. Do not restart Docker Desktop: another project (Supabase) runs in it. Once up, the stack stays up (`restart: unless-stopped`) |
| Grafana not at `localhost:3000` | It is on port **3300** (VS Code holds 3000–3003) | http://localhost:3300 (admin/admin) |
| `eval/*.py` finds no rows | `--since` does not cover the run, or the node name is wrong | `--since 1h`; node names are `EHG-XXXX` (board) or `SIM-0000` (simulated) |
| H1 reads a few ms low, `offset method: BLE lower envelope` | No serial clock pings for this run (`eval/results/sync_<node>.csv` missing) | Run `feed.py` (or `feed.py --sync-only` for a built-in board) during the run ([decision 0005](decisions/0005-h1-clock-offset-from-serial-sync.md)) |
| H1 values shifted by a constant | Ingest stamps `t_db_ack` with the Docker VM clock | Check the VM-to-macOS clock offset before final H1 runs, or run ingest on the host ([progress.md](progress.md)) |
