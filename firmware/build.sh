#!/bin/sh
# Build (and optionally flash) the EHG node.
#   firmware/build.sh                 build
#   firmware/build.sh flash           build + flash the DK
#   firmware/build.sh pristine flash  clean build + flash
# Nordic's build breaks on folder names with spaces ("2026 Term 3/Thesis B"), so everything
# is built through a space-free shortcut: ~/thesisb -> this repo.
#
# PURPOSE: 1 command to compile the board's program and copy it onto the board.
# HOW: Makes the ~/thesisb shortcut if missing, checks the recording file exists, runs Nordic's
#   build tool (west) through Nordic's toolchain launcher, and optionally flashes the board.
# ROLE IN THESIS: Makes firmware builds repeatable from the README.
# BEFORE -> AFTER: Needs ehg_node/src/replay_data.h (tools/make_replay.py). Makes ehg_node/build/;
#   with "flash", programs the nRF52840 DK. SEE: docs/firmware.md
set -e
REPO="$(cd "$(dirname "$0")/.." && pwd)"
LINK="$HOME/thesisb"
[ -L "$LINK" ] || ln -s "$REPO" "$LINK"
export ZEPHYR_BASE="$HOME/ncs/v3.4.1/zephyr"
WEST="nrfutil toolchain-manager launch --ncs-version v3.4.1 -- west"
DEV_ID="${DEV_ID:-1050208294}"   # nRF52840 DK serial; `nrfutil device list` shows yours
APP="$LINK/firmware/ehg_node"
[ -f "$APP/src/replay_data.h" ] || { echo "run first: uv run python tools/make_replay.py data/converted/tpehgdb/tpehg1007.npz --record-id 0"; exit 1; }
P=""; [ "$1" = pristine ] && { P="-p"; shift; }
$WEST build $P -b nrf52840dk/nrf52840 -d "$APP/build" "$APP"
[ "$1" = flash ] && $WEST flash -d "$APP/build" --dev-id "$DEV_ID"
exit 0
