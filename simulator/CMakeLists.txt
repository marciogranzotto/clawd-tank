cmake_minimum_required(VERSION 3.16)
project(clawd-sim LANGUAGES C)

set(CMAKE_C_STANDARD 11)

# SDL2 (from Homebrew)
find_package(SDL2 REQUIRED)

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
add_executable(clawd-sim
    sim_main.c
    sim_display.c
    sim_screenshot.c
    # Shared firmware sources (compiled unmodified)
    ${FIRMWARE_DIR}/scene.c
    ${FIRMWARE_DIR}/notification_ui.c
    ${FIRMWARE_DIR}/ui_manager.c
    ${FIRMWARE_DIR}/notification.c
)

# Include path order matters:
# 1. shims/ first — shadows ESP-IDF headers
# 2. simulator/ — for lv_conf.h (already handled by LVGL's LV_BUILD_CONF_DIR)
# 3. LVGL headers — handled by linking lvgl target
# 4. firmware/main/ — for scene.h, notification.h, etc.
target_include_directories(clawd-sim PRIVATE
    ${CMAKE_SOURCE_DIR}/shims    # ESP-IDF shims (must be first)
    ${CMAKE_SOURCE_DIR}          # lv_conf.h lives here
    ${FIRMWARE_DIR}              # firmware headers (scene.h, notification.h, etc.)
    ${FIRMWARE_DIR}/assets       # sprite asset headers
)

target_link_libraries(clawd-sim PRIVATE
    lvgl
    SDL2::SDL2
)

# macOS: SDL2 needs Cocoa framework
if(APPLE)
    target_link_libraries(clawd-sim PRIVATE "-framework Cocoa")
endif()
