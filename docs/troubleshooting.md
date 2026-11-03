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

## Cloud profile (TLS 1.3 with client certificates)

Background: [security.md](security.md), [decision 0008](decisions/0008-mqtt-mtls-and-topic-acls.md). Added 2026-11-03.

| Symptom | Cause | Fix |
|---|---|---|
| `tools/pki.sh` says "needs OpenSSL 3" | macOS's own `/usr/bin/openssl` is LibreSSL; `PATH` may also hold an older OpenSSL | `brew install openssl@3`, then `OPENSSL=/opt/homebrew/opt/openssl@3/bin/openssl tools/pki.sh …` |
| `tools/pki.sh init` refuses: "already holds a PKI" | By design, `init` never overwrites keys | Add certificates with `tools/pki.sh client NAME`, or use another `--out`. Deleting `backend/pki` yourself starts again, and every client then needs a new certificate |
| Gateway prints `[mqtt] connection failed (TLS handshake or network)` and nothing is stored | paho does the TLS handshake in its own thread, so a refused certificate raises no error; the message is the only sign | Check `--ca/--cert/--key`; that the broker name you pass in `--mqtt` is in the broker certificate's SANs (`openssl x509 -in backend/pki/broker.crt -noout -ext subjectAltName`; create it with `tools/pki.sh init --host <that name>`); that the certificate is not expired or revoked |
| TLS error mentioning the host name / `certificate verify failed: Hostname mismatch` | The name you connect to is not a SAN of `broker.crt` (a container reaches the broker as `mosquitto`; the host as `localhost` or `127.0.0.1`) | Connect with a name in the SANs, or reissue with `--host` set to the real DNS name or IP |
| `ssl.HAS_TLSv1_3` is `False` or "TLSv1_3 not supported" | The Python is linked to an old OpenSSL | `uv run python -c "import ssl; print(ssl.HAS_TLSv1_3, ssl.OPENSSL_VERSION)"`; use the uv-managed Python (3.12 or later with OpenSSL 3) |
| Every client is refused, including valid ones, after months of working | `ca.crl` passed its next-update date; OpenSSL then treats all certificates as unverifiable (tested with Python's OpenSSL, [security.md](security.md)) | `tools/pki.sh crl`, then reload the broker (`kill -HUP <pid>`, or `docker kill -s HUP <container>`) |
| A revoked certificate still connects | The broker reads `ca.crl` at start-up and on SIGHUP, not on its own (Mosquitto 2.1.2) | After `tools/pki.sh revoke NAME`, send SIGHUP or restart the broker |
| Publish silently does nothing for one identity | The ACL denies the topic, and the publisher still gets its PUBACK (observed in `tests/test_mqtt_tls_config.py` on Mosquitto 2.1.2), so nothing fails on the sender's side | Check `backend/mosquitto/acl` for that identity's `topic write` lines, and look from an allowed reader's side (`mosquitto_sub` with the reader's certificate) |
| Broker container exits: "Unable to load server key file" | **Untested.** `broker.key` is mode 600 (owned by the host user), and the container's `mosquitto` user may not be able to read it, mainly on Linux | Not verified with Docker yet. If confirmed: make the key group-readable for the container's group, or run the container as the host user (record the fix here) |
| A gateway, viewer or ingest keeps getting disconnected and reconnecting | Two processes use the same certificate, so the broker sees the same client id (forced to the CN) and each kicks the other off | One certificate per process: `tools/pki.sh client ehg-gw-02`, then add its two lines to `backend/mosquitto/acl` |
| `docker compose up` creates an empty directory called `broker.crt` (or similar) inside `backend/pki` | The cloud override mounts single files, and Docker makes a directory when the source file does not exist yet | Remove the directory, run `tools/pki.sh init` first, then `up` |
| `mosquitto_pub --tls-version tlsv1.2` appears to succeed against the TLS 1.3 broker | The client's option does not force the version as a probe would | Probe with `openssl s_client -tls1_2 -connect localhost:8883 -CAfile backend/pki/ca-chain.crt -cert … -key …`: it ends in `alert protocol version` |
