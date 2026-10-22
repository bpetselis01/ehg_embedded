#!/bin/sh
# Build (and optionally flash) the EHG node.
#   firmware/build.sh                 build
#   firmware/build.sh flash           build + flash the DK
#   firmware/build.sh pristine flash  clean build + flash
#   firmware/build.sh flash --record data/converted/tpehgdb/tpehg1007.npz [--seconds 120]
#                                     built-in mode: the recording is compiled in (no laptop feed needed)
# Two modes, picked here at every build (a plain build always goes back to the serial feed):
#   serial feed (default)  tools/feed.py streams the recording over the serial port at run time
#   built-in (--record)    tools/make_replay.py writes src/replay_data.h, compiled in; stand-alone runs
# What each run does (all three start with steps 1-3, they differ at steps 4-5):
#   1. make the ~/thesisb shortcut if missing   2. point at the SDK + launcher, pick the board
#   3. read the "pristine" / "flash" / "--record" words (--record: also run tools/make_replay.py)
#   run                       4. compile (west build)                  5. copy onto the board (west flash)
#   build.sh                  incremental (seconds once built)          no
#   build.sh flash            incremental                               yes
#   build.sh pristine flash   clean: deletes old build output (6-11 min) yes
# After compiling, every run also writes build/compile_commands.json: the compiler's list of
#   flags per file, with file paths swapped from ~/thesisb to the real folder so VS Code
#   (.vscode/settings.json) matches the files you open and stops showing false red errors.
# Use "pristine" after changing settings or board, or when the build behaves oddly.
# Nordic's build breaks on folder names with spaces ("2026 Term 3/Thesis B"), so everything
# is built through a space-free shortcut: ~/thesisb -> this repo.
#
# PURPOSE: 1 command to compile the board's program and copy it onto the board.
# HOW: Makes the ~/thesisb shortcut if missing, runs Nordic's
#   build tool (west) through Nordic's toolchain launcher, and optionally flashes the board.
# ROLE IN THESIS: Makes firmware builds repeatable from the README.
# BEFORE -> AFTER: Needs the source (+ a converted .npz for --record). Makes ehg_node/build/; with "flash",
#   programs the nRF52840 DK. Prints which mode it built. SEE: docs/firmware.md
set -e
REPO="$(cd "$(dirname "$0")/.." && pwd -P)"   # -P: real folder, even if run via ~/thesisb
LINK="$HOME/thesisb"
[ -L "$LINK" ] || ln -s "$REPO" "$LINK"
export ZEPHYR_BASE="$HOME/ncs/v3.4.1/zephyr"
WEST="nrfutil toolchain-manager launch --ncs-version v3.4.1 -- west"
DEV_ID="${DEV_ID:-1050208294}"   # nRF52840 DK serial; `nrfutil device list` shows yours
APP="$LINK/firmware/ehg_node"
P=""; FLASH=""; RECORD=""; SECS=""
while [ $# -gt 0 ]; do
	case "$1" in
		pristine) P="-p" ;;
		flash) FLASH=1 ;;
		--record) RECORD="$2"; shift ;;
		--seconds) SECS="$2"; shift ;;
		*) echo "unknown argument: $1 (use: [pristine] [flash] [--record <npz> [--seconds S]])"; exit 1 ;;
	esac
	shift
done
if [ -n "$RECORD" ]; then
	[ -f "$RECORD" ] || { echo "--record: no such file: $RECORD"; exit 1; }
	uv run --project "$REPO" python "$REPO/tools/make_replay.py" "$RECORD" ${SECS:+--seconds "$SECS"}
	MODE="built-in $(basename "$RECORD" .npz)"; BAKED=y
else
	MODE="serial feed (tools/feed.py)"; BAKED=n
fi
# Always set explicitly: west remembers -D options, so a built-in build would otherwise stick.
# The "ehg_node_" prefix sends it to the app image, not to sysbuild/MCUboot.
$WEST build $P -b nrf52840dk/nrf52840 -d "$APP/build" "$APP" -- -Dehg_node_CONFIG_EHG_BAKED_RECORD=$BAKED
# only "file" lines: the "command" lines must keep space-free paths
sed '/"file":/s#'"$LINK"'#'"$REPO"'#' "$APP/build/ehg_node/compile_commands.json" > "$APP/build/compile_commands.json"
[ -n "$FLASH" ] && $WEST flash -d "$APP/build" --dev-id "$DEV_ID"
echo "built: $MODE"
exit 0
