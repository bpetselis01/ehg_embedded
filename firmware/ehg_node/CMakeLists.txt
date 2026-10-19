# EHG board firmware: plays a stored EHG recording and sends it over Bluetooth.
#
# PURPOSE: Tells the build tool which C files make up the firmware.
# INPUT: src/*.c + src/replay_data.h (made by tools/make_replay.py; build stops without it).
#   e.g. src/main.c, src/replay.c, src/ehg_service.c (+ the .h files they include), prj.conf, Kconfig
# OUTPUT: the firmware image in build/ (flashed onto the nRF52840 DK).
#   e.g. build/ehg_node/zephyr/zephyr.hex (Intel-HEX text, lines like ":10000000...")
#        last build: FLASH 348300 B (33.22 %), RAM 73756 B (28.14 %)
# HOW: Loads Zephyr (the board's small operating system), checks replay_data.h exists, lists the C files.
# BEFORE -> AFTER: Run by firmware/build.sh, together with prj.conf (settings) and Kconfig (our settings).
# SEE: docs/firmware.md
cmake_minimum_required(VERSION 3.20.0)
find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(ehg_node)

if(NOT EXISTS ${CMAKE_CURRENT_SOURCE_DIR}/src/replay_data.h)
  message(FATAL_ERROR "src/replay_data.h missing - run: uv run python tools/make_replay.py <npz> --record-id N")
endif()

target_sources(app PRIVATE src/main.c src/replay.c src/ehg_service.c)
