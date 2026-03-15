cmake_minimum_required(VERSION 3.16)
project(clawd-tank-sim LANGUAGES C)

set(CMAKE_C_STANDARD 11)

# SDL2 — static or system
option(STATIC_SDL2 "Download and statically link SDL2" OFF)

if(STATIC_SDL2)
    include(FetchContent)
    set(SDL_SHARED OFF CACHE BOOL "" FORCE)
    set(SDL_STATIC ON CACHE BOOL "" FORCE)
    set(SDL_TEST OFF CACHE BOOL "" FORCE)
    set(SDL2_DISABLE_SDL2MAIN ON CACHE BOOL "" FORCE)
    FetchContent_Declare(
        SDL2
        URL https://github.com/libsdl-org/SDL/releases/download/release-2.30.10/SDL2-2.30.10.tar.gz
        URL_HASH SHA256=f59adf36a0fcf4c94198e7d3d776c1b3824211ab7aeebeb31fe19836661196aa
    )
    FetchContent_MakeAvailable(SDL2)
    set(SDL2_TARGET SDL2-static)
else()
    find_package(SDL2 REQUIRED)
    set(SDL2_TARGET SDL2::SDL2)
endif()

# Paths
set(FIRMWARE_DIR ${CMAKE_SOURCE_DIR}/../firmware/main)
set(LVGL_DIR ${CMAKE_SOURCE_DIR}/../firmware/managed_components/lvgl__lvgl)

# Build LVGL as a subdirectory — it detects non-ESP and uses os_desktop.cmake
# Point it to our lv_conf.h via LV_BUILD_CONF_DIR
set(LV_BUILD_CONF_DIR ${CMAKE_SOURCE_DIR} CACHE PATH "" FORCE)
set(CONFIG_LV_BUILD_DEMOS OFF CACHE BOOL "" FORCE)
set(CONFIG_LV_BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)
set(CONFIG_LV_USE_THORVG_INTERNAL OFF CACHE BOOL "" FORCE)
add_subdirectory(${LVGL_DIR} ${CMAKE_BINARY_DIR}/lvgl)

# Simulator executable
add_executable(clawd-tank-sim
    sim_main.c
    sim_display.c
    sim_display_shim.c
    sim_screenshot.c
    sim_events.c
    sim_ble_parse.c
    sim_socket.c
    sim_timer.c
    cjson/cJSON.c
    # Shared firmware sources (compiled unmodified)
    ${FIRMWARE_DIR}/scene.c
    ${FIRMWARE_DIR}/notification_ui.c
    ${FIRMWARE_DIR}/ui_manager.c
    ${FIRMWARE_DIR}/notification.c
    ${FIRMWARE_DIR}/config_store.c
    ${FIRMWARE_DIR}/rgb_led.c
    ${FIRMWARE_DIR}/pixel_font.c
)

# Include path order matters:
# 1. shims/ first — shadows ESP-IDF headers
# 2. simulator/ — for lv_conf.h (already handled by LVGL's LV_BUILD_CONF_DIR)
# 3. LVGL headers — handled by linking lvgl target
# 4. firmware/main/ — for scene.h, notification.h, etc.
target_include_directories(clawd-tank-sim PRIVATE
    ${CMAKE_SOURCE_DIR}/shims    # ESP-IDF shims (must be first)
    ${CMAKE_SOURCE_DIR}          # lv_conf.h lives here
    ${FIRMWARE_DIR}              # firmware headers (scene.h, notification.h, etc.)
    ${FIRMWARE_DIR}/assets       # sprite asset headers
    ${CMAKE_SOURCE_DIR}/cjson    # cJSON
)

target_compile_definitions(clawd-tank-sim PRIVATE SIMULATOR)

target_link_libraries(clawd-tank-sim PRIVATE
    lvgl
    ${SDL2_TARGET}
)

if(APPLE)
    target_link_libraries(clawd-tank-sim PRIVATE
        "-framework Cocoa"
        "-framework IOKit"
        "-framework CoreAudio"
        "-framework CoreVideo"
        "-framework Metal"
        "-framework Carbon"
        "-framework ForceFeedback"
        "-framework AudioToolbox"
        "-framework GameController"
        "-framework CoreHaptics"
    )
endif()
