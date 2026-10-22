#!/bin/sh
# Start the whole pipeline for a live demo, with one window per hop so packets can be watched moving.
#   tools/demo.sh                                       normal run (TPEHG tpehg1007)
#   tools/demo.sh --faults drop=0.05,burst=3,disc=60    same, with injected link faults (docs/fault-injection.md)
#   tools/demo.sh --built-in                            board flashed with a built-in recording (build.sh flash --record ...):
#                                                       window 1 only sends clock pings (feed.py --sync-only)
#
# PURPOSE: One command for a demo instead of 6 terminals started in the right order.
# INPUT: a flashed DK on USB (firmware/build.sh flash), the backend up (cd backend && docker compose up -d),
#   mosquitto_sub (brew install mosquitto). Extra arguments go to tools/feed.py (e.g. --faults ...).
# OUTPUT: 6 Terminal.app windows tiled 3 x 2 in pipeline order, plus 2 browser tabs:
#   1 BOARD + FEEDER  board log every 10 s (sent / resent / starved ...)
#   2 BLUETOOTH       each packet the gateway receives, e.g. "254,2540,0,1792465051.533023,82" (seq,frame_idx,flags,t_rx,bytes)
#   3 GATEWAY         link status every 10 s, e.g. "[EHG-93C0] rx=300 missing=0 dup=0 streams=1 bad=0"
#   4 MQTT            each message the broker passes on, e.g. 'ehg/EHG-93C0/pkt {"node_id": "EHG-93C0", "seq": 300, ...'
#   5 DATABASE        each packet stored, e.g. "seq 340  10 frames  stored after 457 ms (radio+batching 452, ...)"
#   6 VIEWER SERVER   serves http://localhost:8765 (live raw traces); Grafana http://localhost:3300/d/ehg-overview (admin/admin)
# HOW: Terminal.app windows because only Terminal.app has macOS Bluetooth permission for the gateway.
#   Order matters: the feeder must have sent CONFIG before the gateway's START, so it starts first.
#   Restarting only the feeder mid-session fails (the board refuses a new CONFIG while sampling):
#   stop the gateway first (it sends STOP), or press Button 1.
# BEFORE -> AFTER: Before: flashed board, running backend. After: Ctrl-C each window to stop;
#   then eval/integrity.py and eval/latency.py --since <run length> give the H2/H4 and H1 numbers.
set -e
REPO="$(cd "$(dirname "$0")/.." && pwd -P)"
docker info >/dev/null 2>&1 || { echo "Docker is not running: open Docker Desktop, then: cd backend && docker compose up -d"; exit 1; }
NODE=EHG-93C0
mkdir -p "$REPO/gateway/logs" && touch "$REPO/gateway/logs/$NODE.csv"

# open a Terminal window titled $1 running $2; prints its window id
win() {
	osascript -e "tell application \"Terminal\"
		set t to do script \"cd '$REPO' && $2\"
		set custom title of t to \"$1\"
		set title displays custom title of t to true
		return id of window 1
	end tell"
}
if [ "$1" = --built-in ]; then
	IDS="$(win '1 BOARD: built-in recording; clock pings only; board log every 10 s' "uv run python tools/feed.py --sync-only --node $NODE")"
else
	IDS="$(win '1 BOARD + FEEDER: laptop streams frames in; board log every 10 s' "uv run python tools/feed.py data/converted/tpehgdb/tpehg1007.npz $*")"
fi
sleep 4
IDS="$IDS $(win '2 BLUETOOTH: each packet received (seq, frame_idx, flags 1=re-sent)' "tail -n 0 -f gateway/logs/$NODE.csv")"
IDS="$IDS $(win '3 GATEWAY: link status, missing / bad every 10 s' 'uv run python -m gateway.gateway')"
IDS="$IDS $(win '4 MQTT: each message the broker passes on' "mosquitto_sub -h localhost -v -t 'ehg/+/pkt' | cut -c1-170")"
IDS="$IDS $(win '5 DATABASE: each packet stored in InfluxDB + latency' "uv run python tools/watch_db.py --node $NODE")"
IDS="$IDS $(win '6 VIEWER SERVER: http://localhost:8765' 'uv run python viewer/server.py')"

# tile 3 x 2 over the screen, in pipeline order
SIZE="$(osascript -l JavaScript -e 'ObjC.import("AppKit"); var f=$.NSScreen.mainScreen.visibleFrame; [f.size.width, f.size.height].join(" ")')"
W=$(( ${SIZE% *} / 3 )); H=$(( ${SIZE#* } / 2 )); i=0
for id in $IDS; do
	c=$(( i % 3 )); r=$(( i / 3 ))
	osascript -e "tell application \"Terminal\" to set bounds of window id $id to {$((c * W)), $((38 + r * H)), $(((c + 1) * W)), $((38 + (r + 1) * H))}"
	i=$((i + 1))
done
sleep 3
open http://localhost:8765
open "http://localhost:3300/d/ehg-overview?refresh=5s&from=now-5m&to=now"
echo "started 6 windows. Ctrl-C in each to stop (gateway first: it sends STOP to the board)."
