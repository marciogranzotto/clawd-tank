#pragma once
#include <stdint.h>
#include <stdbool.h>

/**
 * RGB LED driver for Waveshare ESP32-C6-LCD-1.47 onboard WS2812B.
 * Single addressable LED on GPIO8, driven via RMT peripheral.
 */

void rgb_led_init(void);

/** Set LED color (0-255 per channel). Set all zero to turn off. */
void rgb_led_set(uint8_t r, uint8_t g, uint8_t b);

/** Flash the LED briefly: ramps up, holds, fades out. Non-blocking (uses a timer). */
void rgb_led_flash(uint8_t r, uint8_t g, uint8_t b, int duration_ms);

/** Flash the LED red three times. Non-blocking (uses a timer). */
void rgb_led_flash_error(void);
