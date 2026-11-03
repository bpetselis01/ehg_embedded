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

## Gateway outbox

Background: [decision 0009](decisions/0009-gateway-durable-outbox.md). Added 2026-11-03.

| Symptom | Cause | Fix |
|---|---|---|
| `[outbox] FULL (cap …): dropped N oldest unsent packets of EHG-… (seq a..b)` | The broker was unreachable for so long that the file passed `--outbox-max-mb` | Those packets are lost and `eval/integrity.py` will show the gap `a..b`. Fix the link, or raise the cap (disk permitting) |
| `[outbox] ERROR writing …: … sending … WITHOUT durability` | The disk write failed (full disk, permissions, a locked file) | Free space or fix the path. Packets still go out while the link is up, but nothing is queued for an outage |
| `[outbox] link down; N on disk` repeats | The broker is unreachable or refusing the certificate | Expected during an outage; the packets are kept. For a refusal see the TLS section above. Backoff is 1, 2, 4 … 60 s (no jitter) |
| The database has packets twice, or `retrans` and `replayed` both appear | A reconnect resends what was not yet acknowledged; ingest keeps one copy per (node, seq, t_inject) | Nothing to fix. `replayed` is not the same as `retrans` (`retrans` = the board re-sent it over Bluetooth) |
| H1 looks much worse after an outage | Replayed packets waited on disk, so their end-to-end time includes the outage | `uv run python eval/latency.py --node … --exclude-replayed` (the default run prints the live-only figure beside the total) |
| `gateway/outbox.db` is left behind after a run | Unconfirmed packets stay on disk by design and go out at the next start | Start the gateway with the same `--outbox` path to flush it; delete the file only if you accept losing those packets |
| `gateway: gateway/outbox.db is in use by another gateway process` | Two gateways were started from the same folder; the outbox allows one process per file | Give the second its own file (`--outbox gateway/outbox2.db`), or `--no-outbox`. If no other gateway is running, check `pgrep -fl gateway.gateway` (the lock is released when its process ends) |
| `[mqtt] cannot reach the broker at host:port; retrying. Packets wait in the outbox` | The broker is down or the address is wrong. With the outbox the gateway no longer exits at start-up | Start the broker (`cd backend && docker compose up -d`) or fix `--mqtt`/`--mqtt-port`. Nothing is lost meanwhile, up to the size cap |
| Old packets from an earlier run appear in the database, marked `replayed` | `gateway/outbox.db` still held unconfirmed packets from that run (the outbox is on by default) and sent them at this start | Expected. Use `--exclude-replayed` in `eval/latency.py`. For a clean start delete `gateway/outbox.db*` before running |

## Deployment code (deploy/)

Background: [deploy/README.md](../deploy/README.md). Added 2026-11-03.

| Symptom | Cause | Fix |
|---|---|---|
| `terraform validate`: `templatefile` ... "Invalid expression; Expected the start of an expression" | A literal dollar-brace in `user-data.yaml.tftpl` (even in a comment) is read as a Terraform expression | Write `$${` for a literal one, or reword. Shell variables in the script use `$name`, never `${name}` |
| `terraform plan`: `admin_cidr must be a valid CIDR block and must not be 0.0.0.0/0` | By design: SSH may never be open to the internet | Use your own public address as `a.b.c.d/32` |
| `terraform apply`: "Out of host capacity" for the A1 shape | Oracle has no free A1 capacity in that availability domain right now (common) | Set `availability_domain_index` to 1 or 2, or retry later. The home region cannot be changed |
| `docker compose ... up` stops with "required variable X is missing a value" | The decrypted secrets are not loaded | Run through `deploy/update.sh` (it decrypts into `/run/ehg/runtime.env`), or `--bootstrap` after the age key is in place |
| `update.sh`: "tag ... points at ..., not at ...: refusing" | The tag moved, or you gave the wrong hash | `git rev-parse TAG` on your Mac and pass that full hash. A moved tag should be investigated, not forced |
| `update.sh`: "another update is running" | A previous run crashed and left `/opt/ehg/state/update.lock` | Make sure no update is running, then remove that directory |
| `update.sh`: "sops is not installed" | cloud-init does not install it (no distribution package) | Install it by hand and verify its checksum (`deploy/secrets/README.md`) |

## Data platform (NATS, TimescaleDB, writers)

Background: [decision 0012](decisions/0012-timescaledb-nats-data-platform.md). Added 2026-11-03; none of these has been seen on a running stack yet.

| Symptom | Cause | Fix |
|---|---|---|
| `migrate` fails with `extension "timescaledb" is not available` | The database server has no TimescaleDB (a plain PostgreSQL image) | By design the failure is loud and leaves revision 0001. Use the `timescaledb-ha` image, or set `EHG_MIGRATE_TARGET=0001` for a plain schema |
| ts-writer logs `DEGRADED, database write failed` | PostgreSQL is down or unreachable | Nothing is lost: the messages wait in NATS (7 days). Start the database; the writer recovers by itself and reports `ehg_writer_degraded 0` |
| ts-writer logs `cannot parse a message ... terminated` | A message in the stream is not a valid packet | It is kept as a dead letter (JetStream advisory); inspect it with `nats` tools. It will not be retried |
| `eval/parity.py` says FAIL | The two stores differ | Do not switch the cloud profile to TimescaleDB. Read which check failed; check that `ingest` was stopped and both writers read the same stream |
| `eval/*.py --backend timescale`: "needs EHG_TS_DSN" | The connection string is not set | `export EHG_TS_DSN="host=localhost port=5433 dbname=ehg user=ehg password=..."` (5433 is the local platform database; Supabase uses 5432) |
| Tests using real SQL are skipped | No local PostgreSQL | `brew install postgresql@14` (tests start a throwaway cluster in /tmp; TimescaleDB tests still need Docker) |

## Triage, FHIR, observability, backups, Swift (Units 8 to 15; none of these has run against real servers)

| Symptom | Cause | Fix |
|---|---|---|
| Triage page shows "Your session ended" after a few minutes | Access tokens last 5 minutes and the page holds no refresh token | Sign in again (by design: nothing is kept in the browser) |
| Page asks to sign in but the Keycloak login returns "invalid audience" at the API | The token carries no `aud: triage-api` | The `triage-ui` client needs the audience mapper in `deploy/keycloak/realm-ehg.json`; re-import the realm (found and fixed 2026-11-04, not tested on a real Keycloak) |
| `AUTH_MODE=dev refuses to run` | Dev mode has fixed users and only runs on loopback | Bind to 127.0.0.1 or use `AUTH_MODE=oidc` |
| `/fhir/Observation` answers 403 for a node you can see on the worklist | The FHIR routes need ACTIVE consent; a node with no subject is refused (the worklist is more lenient) | Register the node with a subject whose `consent_status` is `active` |
| `/fhir/*` answers 404 | No FHIR server is configured (`fhir_upstream` unset) | Give the triage service a FHIR base URL |
| FHIR sync sends nothing | The node has no consent, or no chunk is complete yet (a chunk is sent once a later frame arrives) | Check the worker log line per node: `skipped: consent`, `pending: N` |
| `swift test` cannot find `tests/fixtures/packet_vectors.json` | Run from a copy of the package outside the repository | Run it in `ios/EHGWire` inside the repository (the test finds the file relative to its own path) |
| `tests/test_ios_wire_format.py` says the vectors are stale | The wire format changed, or `gateway/packet.py` did | `uv run python tools/gen_packet_vectors.py`, then update the Swift copy and run `swift test` |
| `tests/test_api_docs.py` says an API document is stale | A route or model changed | `uv run python tools/export_api_docs.py` and commit the result |
| `restore_drill.sh` says `REFUSING: database ... already exists` | It never overwrites | Run it on a clean server, not on a live database |
| `EhgTargetDown` for bridge, ts-writer or triage straight after deployment | Those services are not in the cloud compose file yet | Expected until they are added (docs/progress.md, Unit 12) |
