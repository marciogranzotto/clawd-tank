# Firmware Sprite Loading Investigation

**Date**: 2026-03-16
**Status**: Fixed

## Problem

After flashing the firmware with the multi-session display features, the new sprites (walking, going_away, mini_crab) did not load on the Waveshare ESP32-C6 hardware, despite working correctly in the simulator.

## Root Causes Found

### 1. Build failures (FIXED in commit cfffd9d)

The firmware could not compile at all due to two issues:

- **`pixel_font.c` missing from `firmware/main/CMakeLists.txt`**: The file was added to the simulator's `CMakeLists.txt` (commit 753432d) but not the firmware's. This caused an `undefined reference to pixel_font_draw` linker error. The HUD overlay (subagent counter, overflow badge) depends on this.

- **Compiler warnings treated as errors in `scene.c`**:
  - `%d` format specifiers used for `int32_t` return values from LVGL functions (should be `PRId32`)
  - Unused variable `walk_def` in `scene_set_sessions`
  - Unused functions `fade_complete_cb` and `scene_deactivate_slot`
  - `snprintf` buffer too small for potential output (truncation warning)

These errors meant the device was running OLD firmware from before the multi-session feature branch merge — none of the new sprites, walk-in/walk-out animations, HUD overlay, or v2 protocol support existed on the hardware.

### 2. No PSRAM on ESP32-C6 — frame buffers exhaust internal SRAM (FIXED)

**This is the critical runtime issue.**

`sdkconfig.defaults` configures PSRAM (`CONFIG_SPIRAM=y`, etc.), but **ESP-IDF 5.3 does not support PSRAM on ESP32-C6**. The `esp_psram` component only has Kconfig for ESP32, ESP32-S2, ESP32-S3, and ESP32-P4. The PSRAM settings in `sdkconfig.defaults` are silently ignored.

**Impact**: Without PSRAM, `malloc` only uses internal SRAM (~200-300 KB free after FreeRTOS, NimBLE, LVGL, display buffers). Each sprite frame buffer required **180x180x4 = 129,600 bytes (~126 KB)** for ARGB8888. A single session's sprite barely fits, but any additional sessions (walk-in crabs, departing crabs) cause `malloc` to return NULL in `ensure_frame_buf`, causing `decode_and_apply_frame` to silently skip rendering.

The simulator doesn't hit this because macOS has effectively unlimited `malloc` heap.

**Fix**: Two-part optimization:

1. **Switch to RGB565A8 pixel format on firmware** (3 bytes/pixel instead of 4). This is LVGL's native-with-alpha format for 16-bit displays. No visual difference since the display is RGB565 anyway — ARGB8888 was wasting 25% memory on 8-bit-to-5/6-bit channel expansion that gets thrown away during rendering. Buffer per slot: **180x180x3 = 97,200 bytes (~95 KB)**.

2. **Limit concurrent slots on firmware**: `MAX_VISIBLE=2`, `MAX_SLOTS=3` (vs 4/8 on simulator). Two visible sessions + one departing animation. With 95 KB per slot, worst case is 3 x 95 KB = 285 KB — fits within available SRAM. Extra sessions beyond 2 are shown in the "+N" overflow HUD badge.

A new `rle_decode_rgb565a8` function was added to `rle_sprite.h` that decodes RLE data directly to the RGB565A8 layout (RGB565 color array followed by alpha array). This is faster than `rle_decode_argb8888` since it skips the RGB565→RGB888 channel expansion.

### 3. Silent OOM in frame buffer allocation (FIXED)

`ensure_frame_buf` in `scene.c` had no error logging when `malloc` failed:
```c
slot->frame_buf = malloc(needed);
slot->frame_buf_size = slot->frame_buf ? needed : 0;
// No log, no warning — completely silent failure
```

Added `ESP_LOGW` on firmware to report allocation failures with the requested size.

### 4. No heap diagnostics at startup (FIXED)

Added heap stats logging in `app_main` (internal SRAM + PSRAM breakdown) so available memory is visible in serial monitor at boot time.

## What Was NOT the Issue

The investigation confirmed these are all correctly implemented and identical between firmware and simulator:

- **Parser logic**: `ble_service.c` `set_sessions` parsing is byte-for-byte equivalent to `sim_ble_parse.c`
- **Event struct**: `ble_evt_t` is identical in both `ble_service.h` and `shims/ble_service.h`
- **Animation name mapping**: Daemon sends exactly the 6 names (`idle`, `typing`, `thinking`, `building`, `confused`, `sweeping`) that both parsers recognize
- **Protocol version**: Firmware correctly advertises v2 via GATT characteristic; daemon reads it on connect
- **UI state machine**: `ui_manager.c` handles `BLE_EVT_SET_SESSIONS` identically on both platforms
- **Scene rendering**: `scene.c` is compiled unmodified into both firmware and simulator (same source file)
- **Sprite assets**: Both share the same headers from `firmware/main/assets/`
- **LVGL config**: Both use 64 KB builtin allocator, same color depth, same widgets

## Fixes Applied

| Fix | File | Description |
|-----|------|-------------|
| Add `pixel_font.c` to build | `firmware/main/CMakeLists.txt` | Was missing, causing linker error |
| Fix format specifiers | `firmware/main/scene.c` | `%d` -> `PRId32` for `int32_t` values |
| Fix unused code warnings | `firmware/main/scene.c` | `__attribute__((unused))`, remove unused var |
| Fix snprintf truncation | `firmware/main/scene.c` | Buffer 8 -> 16 bytes |
| RGB565A8 frame buffers | `firmware/main/scene.c`, `rle_sprite.h` | 3 bytes/pixel on firmware (was 4) |
| Limit slots on firmware | `firmware/main/scene.c` | MAX_VISIBLE=2, MAX_SLOTS=3 (was 4/8) |
| Add `rle_decode_rgb565a8` | `firmware/main/rle_sprite.h` | New decoder for RGB565A8 layout |
| Add OOM logging | `firmware/main/scene.c` | `ESP_LOGW` when frame buffer malloc fails |
| Add heap diagnostics | `firmware/main/main.c` | Log free heap after init |

## Notes

- The `sdkconfig.defaults` PSRAM settings should be removed or commented out since ESP32-C6 doesn't support PSRAM in ESP-IDF 5.3. They are harmless (silently ignored) but misleading.
- The CLAUDE.md claim of "4MB PSRAM (octal)" for the Waveshare ESP32-C6-LCD-1.47 appears to be incorrect — the ESP32-C6FH8 QFN32 package does not support external PSRAM.
- The simulator retains ARGB8888 and MAX_VISIBLE=4/MAX_SLOTS=8 for full-fidelity testing.

## Flash Command

```bash
cd firmware && idf.py build flash monitor -p /dev/cu.usbmodem1214301
```
