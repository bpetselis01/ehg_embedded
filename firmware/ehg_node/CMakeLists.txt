# EHG sensor node: replays a public EHG record (digital injection) and streams it over BLE.
# Build: see firmware/README.md
cmake_minimum_required(VERSION 3.20.0)
find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(ehg_node)

if(NOT EXISTS ${CMAKE_CURRENT_SOURCE_DIR}/src/replay_data.h)
  message(FATAL_ERROR "src/replay_data.h missing - run: uv run python tools/make_replay.py <npz> --record-id N")
endif()

target_sources(app PRIVATE src/main.c src/replay.c src/ehg_service.c)
