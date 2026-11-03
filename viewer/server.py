"""Raw EHG waveform viewer (Gantt 3b): MQTT -> WebSocket bridge + static page.

  uv run python viewer/server.py            then open http://localhost:8765
  MQTT_TLS=1 MQTT_CA=backend/pki/ca-chain.crt MQTT_CERT=backend/pki/clients/ehg-viewer.crt \\
      MQTT_KEY=backend/pki/clients/ehg-viewer.key uv run python viewer/server.py   # cloud profile (port 8883); also MQTT_HOST, MQTT_PORT

PURPOSE: Shows the raw EHG waves live in a browser, with less delay than Grafana.
INPUT: MQTT ehg/+/pkt (QoS 0, live only), same JSON the ingest service gets (docs/interface-spec.md 4).
  e.g. the JSON example in gateway/gateway.py (EHG-93C0, seq 0, samples [[1093, 171, -748], ...])
OUTPUT: WebSocket /ws, 1 slimmed JSON per packet: {node_id, fs_hz, frame_idx, flags, samples}.
  e.g. {"node_id": "EHG-93C0", "fs_hz": 20, "frame_idx": 0, "flags": 0,
        "samples": [[1093, 171, -748], [1145, 180, -790], ... 10 frames ...]}
  Plus the page itself at / (viewer/index.html).
HOW: Passes a copy of each message to every open browser tab. WebSocket = connection that stays open
  so the server can push data. Grafana does overview/alerts; raw multichannel traces use this path
  (as in ICU Cockpit [8] and RHMS [14], report 6.11).
ROLE IN THESIS: Gantt 3b clinician-style raw-trace view. Sits beside the database path, so watching
  does not affect storage or the H1/H2 measurements.
BEFORE -> AFTER: Before: gateway/gateway.py publishing via Mosquitto. After: viewer/index.html draws the traces.
"""

import asyncio
import json
import os
import ssl
from http import HTTPStatus
from pathlib import Path

import paho.mqtt.client as mqtt
from websockets.asyncio.server import broadcast, serve
from websockets.datastructures import Headers
from websockets.http11 import Response

PAGE = (Path(__file__).parent / "index.html").read_bytes()
clients = set()


def page(connection, request):
    """Serve index.html for normal requests; let /ws through to become a WebSocket."""
    if request.path == "/ws":
        return None  # proceed with the WebSocket handshake
    return Response(HTTPStatus.OK, "OK", Headers({"Content-Type": "text/html"}), PAGE)


async def handler(ws):
    """Track one open browser tab until it closes."""
    clients.add(ws)
    try:
        await ws.wait_closed()
    finally:
        clients.discard(ws)


def mqtt_settings(env=os.environ):
    """(host, port, TLS context or None) from MQTT_HOST / MQTT_PORT / MQTT_TLS / MQTT_CA / MQTT_CERT / MQTT_KEY.

    With none of the TLS variables set this is the plain local broker on 1883, exactly as before. This is the same code as
    backend/ingest/ingest.py: that file runs alone in a container and this one runs as a script, so neither can import a shared module.
    """
    host = env.get("MQTT_HOST", "localhost")
    files = [env.get(k) for k in ("MQTT_CA", "MQTT_CERT", "MQTT_KEY")]
    if env.get("MQTT_TLS", "").lower() not in ("1", "true", "yes"):
        if any(files):
            raise SystemExit("MQTT_CA/MQTT_CERT/MQTT_KEY are set but MQTT_TLS is not: refusing to fall back to plain MQTT")
        return host, int(env.get("MQTT_PORT", 1883)), None
    if not all(files):
        raise SystemExit("MQTT_TLS is on, so MQTT_CA, MQTT_CERT and MQTT_KEY must all be set")
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)  # checks the broker certificate and its name
    ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    ctx.load_verify_locations(cafile=files[0])
    ctx.load_cert_chain(certfile=files[1], keyfile=files[2])
    return host, int(env.get("MQTT_PORT", 8883)), ctx


async def main(mqtt_host="localhost", port=8765):
    """Subscribe to MQTT and forward each message to all browser tabs; serve on port 8765."""
    mqtt_host = os.environ.get("MQTT_HOST", mqtt_host)
    _, mqtt_port, tls = mqtt_settings()
    loop = asyncio.get_running_loop()

    def on_message(client, userdata, msg):
        m = json.loads(msg.payload)
        out = json.dumps({k: m[k] for k in ("node_id", "fs_hz", "frame_idx", "flags", "samples")})
        loop.call_soon_threadsafe(broadcast, clients, out)

    # QoS 0, clean session: the viewer is live-only and must never create a backlog in the broker
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    c.on_connect = lambda cl, u, f, rc, p: cl.subscribe("ehg/+/pkt", qos=0)
    c.on_message = on_message
    if tls:  # the TLS handshake runs inside paho's loop, so failures need to be reported, not retried silently
        c.tls_set_context(tls)
        c.on_connect_fail = lambda cl, u: print("MQTT connection failed (TLS handshake or network); retrying. Check the certificate files, the broker name and the CRL")
        c.on_disconnect = lambda cl, u, f, rc, p: print(f"MQTT disconnected ({rc})")
    c.connect(mqtt_host, mqtt_port)
    c.loop_start()
    async with serve(handler, "localhost", port, process_request=page):
        print(f"viewer on http://localhost:{port}")
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
