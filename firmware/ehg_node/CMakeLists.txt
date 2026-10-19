# EHG board firmware: plays a stored EHG recording and sends it over Bluetooth.
#
# PURPOSE: Tells the build tool which C files make up the firmware.
# INPUT: src/*.c + their .h files, prj.conf, Kconfig, boards/nrf52840dk_nrf52840.overlay.
#   e.g. src/main.c, src/replay.c, src/ehg_service.c
# OUTPUT: the firmware image in build/ (flashed onto the nRF52840 DK).
#   e.g. build/ehg_node/zephyr/zephyr.signed.hex (Intel-HEX text, lines like ":10000000..."), started by MCUboot
#        last build (2026-10-20, with MCUboot): app 167336 B (35 % of its 464 KB slot), RAM 181000 B (69 %)
# HOW: Loads Zephyr (the board's small operating system), lists the C files. The recording is not
#   built in: tools/feed.py streams it over the serial port at run time.
# BEFORE -> AFTER: Run by firmware/build.sh, together with prj.conf (settings) and Kconfig (our settings).
# SEE: docs/firmware.md
cmake_minimum_required(VERSION 3.20.0)
find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(ehg_node)

target_sources(app PRIVATE src/main.c src/replay.c src/ehg_service.c)
