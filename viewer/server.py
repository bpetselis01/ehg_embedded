"""Raw EHG waveform viewer (Gantt 3b): MQTT -> WebSocket bridge + static page.

Grafana handles overview/alerts; raw multichannel traces go through this custom path,
the split used by ICU Cockpit [8] and RHMS [14] (report 6.11).

  uv run python viewer/server.py            then open http://localhost:8765

PURPOSE: Shows the raw EHG waves live in a browser, with less delay than Grafana.
HOW: Listens to the same MQTT messages as the ingest service and passes a slimmed-down copy of
  each to every open browser tab over a WebSocket (a connection that stays open so the server
  can push data). The same small server also serves the page itself (index.html).
ROLE IN THESIS: Gantt 3b, the clinician-style raw-trace view. It sits beside the database
  path, so watching does not affect storage or the H1/H2 measurements.
BEFORE -> AFTER: Before: gateway/gateway.py publishing via Mosquitto. After: viewer/index.html
  draws the traces.
"""

import asyncio
import json
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


async def main(mqtt_host="localhost", port=8765):
    """Subscribe to MQTT and forward each message to all browser tabs; serve on port 8765."""
    loop = asyncio.get_running_loop()

    def on_message(client, userdata, msg):
        m = json.loads(msg.payload)
        out = json.dumps({k: m[k] for k in ("node_id", "fs_hz", "frame_idx", "flags", "samples")})
        loop.call_soon_threadsafe(broadcast, clients, out)

    # QoS 0, clean session: the viewer is live-only and must never create a backlog in the broker
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    c.on_connect = lambda cl, u, f, rc, p: cl.subscribe("ehg/+/pkt", qos=0)
    c.on_message = on_message
    c.connect(mqtt_host, 1883)
    c.loop_start()
    async with serve(handler, "localhost", port, process_request=page):
        print(f"viewer on http://localhost:{port}")
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
