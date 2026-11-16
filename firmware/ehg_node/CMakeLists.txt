# EHG board firmware: plays a stored EHG recording and sends it over Bluetooth.
cmake_minimum_required(VERSION 3.20.0)
find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(ehg_node)

if(CONFIG_EHG_BAKED_RECORD AND NOT EXISTS ${CMAKE_CURRENT_SOURCE_DIR}/src/replay_data.h)
  message(FATAL_ERROR "built-in record mode needs src/replay_data.h: build with firmware/build.sh --record <npz>")
endif()

target_sources(app PRIVATE src/main.c src/replay.c src/ehg_service.c)
