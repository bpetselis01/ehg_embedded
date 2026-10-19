#!/bin/sh
# Build (and optionally flash) the EHG node.
#   firmware/build.sh                 build
#   firmware/build.sh flash           build + flash the DK
#   firmware/build.sh pristine flash  clean build + flash
# What each run does (all three start with steps 1-3, they differ at steps 4-5):
#   1. make the ~/thesisb shortcut if missing   2. point at the SDK + launcher, pick the board
#   3. read the "pristine" / "flash" words
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
# BEFORE -> AFTER: Needs only the source. Makes ehg_node/build/; with "flash", programs the nRF52840 DK.
#   No recording is built in: tools/feed.py streams one at run time. SEE: docs/firmware.md
set -e
REPO="$(cd "$(dirname "$0")/.." && pwd -P)"   # -P: real folder, even if run via ~/thesisb
LINK="$HOME/thesisb"
[ -L "$LINK" ] || ln -s "$REPO" "$LINK"
export ZEPHYR_BASE="$HOME/ncs/v3.4.1/zephyr"
WEST="nrfutil toolchain-manager launch --ncs-version v3.4.1 -- west"
DEV_ID="${DEV_ID:-1050208294}"   # nRF52840 DK serial; `nrfutil device list` shows yours
APP="$LINK/firmware/ehg_node"
P=""; [ "$1" = pristine ] && { P="-p"; shift; }
$WEST build $P -b nrf52840dk/nrf52840 -d "$APP/build" "$APP"
# only "file" lines: the "command" lines must keep space-free paths
sed '/"file":/s#'"$LINK"'#'"$REPO"'#' "$APP/build/ehg_node/compile_commands.json" > "$APP/build/compile_commands.json"
[ "$1" = flash ] && $WEST flash -d "$APP/build" --dev-id "$DEV_ID"
exit 0
