"""Raw EHG waveform viewer (Gantt 3b): MQTT -> WebSocket bridge + static page.

Grafana handles overview/alerts; raw multichannel traces go through this custom path,
the split used by ICU Cockpit [8] and RHMS [14] (report 6.11).

  uv run python viewer/server.py            then open http://localhost:8765
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
    if request.path == "/ws":
        return None  # proceed with the WebSocket handshake
    return Response(HTTPStatus.OK, "OK", Headers({"Content-Type": "text/html"}), PAGE)


async def handler(ws):
    clients.add(ws)
    try:
        await ws.wait_closed()
    finally:
        clients.discard(ws)


async def main(mqtt_host="localhost", port=8765):
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
