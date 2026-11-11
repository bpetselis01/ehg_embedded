#!/bin/sh
# Choose which firmware and software to run.
#   tools/run.sh                  numbered menu; Enter starts the default run
#   tools/run.sh default          start a run by name, skipping the menu
#   tools/run.sh --list           every run and its status
#   tools/run.sh --dry-run NAME   print the commands of a run; touches nothing
#
# PURPOSE: One place to start either the Thesis A default or an optional run that may still be in development.
# INPUT: the name of a run. A flashed-or-flashable DK on USB and a running Docker Desktop, for the runs that use them.
#   e.g. tools/run.sh measured
# OUTPUT: the board reflashed if you agree, the right backend containers, then the demo windows (tools/demo.sh).
#   .run-state (gitignored) remembers the last firmware and backend this script set up.
#   e.g. firmware=flash
#        backend=base
# HOW: spec() is the table of runs. Each run is a status, build.sh arguments, a backend and a launcher. run() applies them in order.
#   Firmware only goes through firmware/build.sh, and always after asking. Docker is never started or restarted from here,
#   containers are only started and stopped, and backend/.env is never written, so the InfluxDB volume and its token stay matched.
# ROLE IN THESIS: Separates the Thesis A MVP from later additions (docs/decisions/0007-enterprise-evolution-keep-influx-as-reference.md).
# BEFORE -> AFTER: Before: several build, compose and gateway commands to remember. After: one menu with honest status labels.
set -e
REPO="$(cd "$(dirname "$0")/.." && pwd -P)"
cd "$REPO"

DEV_ID="${DEV_ID:-1050208294}"
STATE="$REPO/.run-state"
DRY=""
RUNS="default measured faults built-in simulate status fhir passkey button tls iphone"
LISTED="cloud keycloak hapi-fhir observability oracle"
RECORD=data/converted/tpehgdb/tpehg1007.npz
FAULTS="--faults drop=0.05,burst=3,corrupt=0.01,disc=120,seed=3"
PKI=backend/pki
TLS_GW="--mqtt-tls --ca $PKI/ca-chain.crt --cert $PKI/clients/ehg-gw-01.crt --key $PKI/clients/ehg-gw-01.key"
TLS_VIEW="MQTT_TLS=1 MQTT_CA=$PKI/ca-chain.crt MQTT_CERT=$PKI/clients/ehg-viewer.crt MQTT_KEY=$PKI/clients/ehg-viewer.key"
TLS_SUB="-h localhost -p 8883 --cafile $PKI/ca-chain.crt --cert $PKI/clients/ehg-viewer.crt --key $PKI/clients/ehg-viewer.key"

# Sets STATUS, NOTE, FW (build.sh arguments, empty = no firmware), BACK, LAUNCH, DEMO (demo.sh arguments), GW, VIEW and SUB.
spec() {
	STATUS="In development"; NOTE=""; FW="flash"; BACK=base; LAUNCH=demo; DEMO=""; GW=""; VIEW=""; SUB=""
	case "$1" in
		default)  STATUS="Thesis A default"
		          NOTE="Thesis A MVP deployed locally, with alerts and signal metrics" ;;
		measured) STATUS="Tested on hardware"; DEMO="$FAULTS"; GW="--no-outbox"
		          NOTE="the 100% delivery, 478 ms run 3 configuration" ;;
		faults)   STATUS="Tested on hardware"; DEMO="$FAULTS"
		          NOTE="shows re-request working; faults tested, this mix with the outbox not" ;;
		built-in) FW="flash --record $RECORD"; DEMO="--built-in"
		          NOTE="recording compiled in; the board runs without the laptop feed" ;;
		simulate) FW=""; LAUNCH=simulate
		          NOTE="software node, no board or Bluetooth" ;;
		status)   GW="--gateway-id ehg-gw-01"
		          NOTE="publishes gateway online/offline; nothing displays it locally" ;;
		fhir)     FW=""; LAUNCH=fhir
		          NOTE="exports the last 10 minutes as FHIR R5, after a run" ;;
		passkey)  FW="--secure flash"
		          NOTE="LESC passkey pairing; the passkey appears in window 1" ;;
		button)   FW="--pairing-button flash"
		          NOTE="press Button 2 to pair; only exercised with the iPhone" ;;
		tls)      BACK=tls; GW="$TLS_GW"; VIEW="$TLS_VIEW"; SUB="$TLS_SUB"
		          NOTE="MQTT over TLS 1.3 with client certificates; no Mac hardware run yet" ;;
		iphone)   FW="--pairing-button flash"; BACK="tls-lan"; LAUNCH=iphone
		          NOTE="iPhone app replaces the Mac gateway; foreground, Wi-Fi" ;;
		cloud)    STATUS="Planned"; FW=""; BACK=none; LAUNCH=none
		          NOTE="hosted InfluxDB and Grafana; not built yet, needs accounts" ;;
		keycloak|hapi-fhir|observability|oracle)
		          STATUS="Not runnable locally yet"; FW=""; BACK=none; LAUNCH=none
		          NOTE="exists only in deploy/ (cloud compose and Terraform)" ;;
		*) return 1 ;;
	esac
}

step() {
	if [ -n "$DRY" ]; then echo "+ $*"; else "$@"; fi
}

state() {
	sed -n "s/^$1=//p" "$STATE" 2>/dev/null || true
}

set_state() {
	{ grep -v "^$1=" "$STATE" 2>/dev/null || true; echo "$1=$2"; } > "$STATE.tmp"
	mv "$STATE.tmp" "$STATE"
}

# ask "question" y|n: the second argument is what Enter means. True for yes.
ask() {
	printf '%s ' "$1"
	read -r answer || return 1
	case "${answer:-$2}" in [Yy]*) return 0 ;; *) return 1 ;; esac
}

list_runs() {
	for name in $RUNS $LISTED; do
		spec "$name"
		printf '%-14s %-25s %s\n' "$name" "$STATUS" "$NOTE"
	done
}

choose_from_menu() {
	i=0
	for name in $RUNS $LISTED; do
		i=$((i + 1)); spec "$name"
		printf '%3d  %-14s %-25s %s\n' "$i" "$name" "$STATUS" "$NOTE"
	done
	printf 'Choose a number or name [Enter = default]: '
	read -r CHOICE || CHOICE=""
	[ -n "$CHOICE" ] || CHOICE=default
	case "$CHOICE" in
		*[!0-9]*) ;;
		*) pick="$CHOICE"; i=0; for name in $RUNS $LISTED; do i=$((i + 1)); [ "$i" -ne "$pick" ] || CHOICE="$name"; done ;;
	esac
}

check_tools() {
	[ -z "$DRY" ] || return 0
	case "$LAUNCH" in
		demo|iphone)
			nrfutil device list 2>/dev/null | grep -q "$DEV_ID" || { echo "The board ($DEV_ID) is not connected. Plug in the DK and try again."; exit 1; } ;;
	esac
	docker info >/dev/null 2>&1 || { echo "Docker is not running. Open Docker Desktop, wait until it is ready, and try again."; exit 1; }
	case "$BACK" in
		tls|tls-lan)
			for cert in ehg-gw-01 ehg-viewer; do
				openssl x509 -checkend 0 -noout -in "$PKI/clients/$cert.crt" >/dev/null 2>&1 ||
					{ echo "Certificate $cert is missing or expired. Renew it with: tools/pki.sh client $cert --days 30"; exit 1; }
			done ;;
	esac
}

flash_firmware() {
	[ -n "$FW" ] || return 0
	case "$FW" in
		*--secure*|*--pairing-button*) export EHG_MCUBOOT_KEY="${EHG_MCUBOOT_KEY:-$HOME/.ehg-keys/mcuboot-p256.pem}" ;;
	esac
	if [ -n "$DRY" ]; then step firmware/build.sh $FW; return 0; fi
	last="$(state firmware)"
	echo "Last firmware flashed by this script: ${last:-unknown}"
	echo "This run needs: firmware/build.sh $FW"
	if [ "$last" = "$FW" ]; then default=n; hint="y/N"; else default=y; hint="Y/n"; fi
	if ask "Flash it to the board now? [$hint]" "$default"; then
		firmware/build.sh $FW
		set_state firmware "$FW"
		echo "If the gateway later reports a pairing mismatch, forget the board in macOS Bluetooth settings and rerun."
	elif [ "$last" != "$FW" ]; then
		echo "Keeping the current firmware, so this run may not behave as labelled."
	fi
}

start_backend() {
	[ "$BACK" != none ] || return 0
	tls="docker compose -f backend/docker-compose.yml -f backend/docker-compose.cloud.yml"
	case "$BACK" in
		base)     compose="docker compose -f backend/docker-compose.yml" ;;
		tls)      compose="$tls" ;;
		tls-lan)  compose="env MQTT_BIND=0.0.0.0 $tls" ;;
	esac
	step $compose up -d
	[ -n "$DRY" ] || set_state backend "$BACK"
}

check_outbox() {
	[ -z "$DRY" ] || return 0
	case "$LAUNCH" in demo|simulate) ;; *) return 0 ;; esac
	case "$GW" in *--no-outbox*) return 0 ;; esac
	[ -f gateway/outbox.db ] || return 0
	code=0
	waiting="$(uv run python -c "
import fcntl, sqlite3, sys
lock = open('gateway/outbox.db.lock', 'a')
try:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
except OSError:
    sys.exit(3)
print(sqlite3.connect('gateway/outbox.db').execute('SELECT count(*) FROM outbox').fetchone()[0])
" 2>/dev/null)" || code=$?
	if [ "$code" -eq 3 ]; then echo "A gateway is already running and holds the outbox. Stop it with Ctrl-C, then try again."; exit 1; fi
	if [ "$code" -ne 0 ]; then echo "Could not read gateway/outbox.db, so any packets left in it will be sent at the start of this run, marked replayed."; return 0; fi
	[ "$waiting" -gt 0 ] || return 0
	echo "gateway/outbox.db holds $waiting packets from an earlier run. Kept, they are sent at the start of this run, marked replayed."
	if ask "Delete them? [y/N]" n; then rm -f gateway/outbox.db gateway/outbox.db-wal gateway/outbox.db-shm; fi
}

start_demo() {
	if [ -n "$DRY" ]; then echo "+ GATEWAY_ARGS='$GW' VIEWER_ENV='$VIEW' MOSQ_SUB_ARGS='$SUB' tools/demo.sh $DEMO"; return 0; fi
	GATEWAY_ARGS="$GW" VIEWER_ENV="$VIEW" MOSQ_SUB_ARGS="$SUB" tools/demo.sh $DEMO
}

start_simulate() {
	if [ -n "$DRY" ]; then
		echo "+ uv run python viewer/server.py"
		echo "+ uv run python -m backend.signal_metrics.worker --node SIM-0000"
		echo "+ uv run python -m gateway.gateway --simulate 1"
		return 0
	fi
	uv run python viewer/server.py & viewer=$!
	uv run python -m backend.signal_metrics.worker --node SIM-0000 & metrics=$!
	trap 'kill "$viewer" "$metrics" 2>/dev/null || true' EXIT INT TERM
	sleep 3
	open http://localhost:8765
	open "http://localhost:3300/d/ehg-overview?refresh=5s&from=now-5m&to=now"
	uv run python -m gateway.gateway --simulate 1
}

launch() {
	case "$LAUNCH" in
		demo) start_demo ;;
		simulate) start_simulate ;;
		fhir) step uv run python tools/fhir_export.py --node EHG-93C0 --since 10m -o eval/results/window.json ;;
		iphone)
			echo "Open the EHG Gateway app on the iPhone and start it. Steps: docs/ios-gateway.md. Press Button 2 to pair for the first time."
			step uv run python tools/feed.py "$RECORD" ;;
		none) ;;
	esac
}

run() {
	if ! spec "$1"; then
		echo "Unknown run: $1"
		list_runs
		exit 2
	fi
	echo "$1: $STATUS. $NOTE."
	[ "$LAUNCH" != none ] || return 0
	check_tools
	flash_firmware
	start_backend
	check_outbox
	launch
	if [ -z "$DRY" ] && [ "$BACK" != none ]; then
		echo "Grafana: http://localhost:3300 (user admin, password GRAFANA_ADMIN_PASSWORD in backend/.env)."
	fi
}

NAME=""
for arg in "$@"; do
	case "$arg" in
		--dry-run) DRY=1 ;;
		--list) list_runs; exit 0 ;;
		*) NAME="$arg" ;;
	esac
done
if [ -z "$NAME" ]; then
	choose_from_menu
	NAME="$CHOICE"
fi
run "$NAME"
