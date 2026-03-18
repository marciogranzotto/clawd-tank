// firmware/main/rgb_led.c
#include "rgb_led.h"
#include "led_strip.h"
#include "esp_log.h"
#include "esp_timer.h"
#include <stdatomic.h>

static const char *TAG = "rgb_led";

#define RGB_LED_GPIO    8
#define STEP_MS         30   /* timer period */

static led_strip_handle_t s_strip = NULL;
static esp_timer_handle_t s_timer = NULL;

/* Color cycling state — atomic for portability to dual-core ESP32 variants
 * where timer_cb runs on a different core than rgb_led_flash(). */
static atomic_int s_steps_left;

/* Palette of colors to cycle through */
static const uint8_t s_palette[][3] = {
    {255, 100,  10},  /* warm orange */
    { 80,  40, 255},  /* purple */
    { 10, 200, 255},  /* cyan */
    {255,  40, 100},  /* pink */
    {255, 200,  10},  /* gold */
    { 40, 255, 120},  /* green */
};
#define PALETTE_SIZE (sizeof(s_palette) / sizeof(s_palette[0]))

/* Steps per color in the cycle */
#define STEPS_PER_COLOR 12

/* Flash modes */
#define FLASH_MODE_PALETTE  0
#define FLASH_MODE_ERROR    1

static atomic_int s_flash_mode;

/* Error flash: 3 red pulses.
 * Each pulse: 5 steps on (150ms) + 3 steps off (90ms) = 8 steps per pulse.
 * 3 pulses = 24 steps + 7 steps fade = 31 total steps.
 * At 30ms/step, total ~930ms. */
#define ERROR_PULSE_ON_STEPS   5
#define ERROR_PULSE_OFF_STEPS  3
#define ERROR_PULSE_STEPS      (ERROR_PULSE_ON_STEPS + ERROR_PULSE_OFF_STEPS)
#define ERROR_PULSE_COUNT      3
#define ERROR_FADE_STEPS       7
#define ERROR_TOTAL_STEPS      (ERROR_PULSE_COUNT * ERROR_PULSE_STEPS + ERROR_FADE_STEPS)

static void apply_color(uint8_t r, uint8_t g, uint8_t b)
{
    if (!s_strip) return;
    led_strip_set_pixel(s_strip, 0, r, g, b);
    led_strip_refresh(s_strip);
}

static void timer_cb(void *arg)
{
    (void)arg;
    s_steps_left--;

    if (s_steps_left <= 0) {
        apply_color(0, 0, 0);
        esp_timer_stop(s_timer);
        return;
    }

    if (s_flash_mode == FLASH_MODE_ERROR) {
        /* Error mode: 3 red pulses then fade */
        int step = ERROR_TOTAL_STEPS - s_steps_left;
        int in_fade = step >= ERROR_PULSE_COUNT * ERROR_PULSE_STEPS;
        if (in_fade) {
            /* Fade out from red */
            int fade_step = step - ERROR_PULSE_COUNT * ERROR_PULSE_STEPS;
            float fade = 1.0f - (float)fade_step / (float)ERROR_FADE_STEPS;
            apply_color((uint8_t)(255 * fade), 0, 0);
        } else {
            int in_pulse = step % ERROR_PULSE_STEPS;
            if (in_pulse < ERROR_PULSE_ON_STEPS) {
                apply_color(255, 0, 0);
            } else {
                apply_color(0, 0, 0);
            }
        }
        return;
    }

    /* Palette cycle mode (existing behavior) */
    int color_idx = s_steps_left / STEPS_PER_COLOR;
    int step_in_color = s_steps_left % STEPS_PER_COLOR;

    int from = color_idx % PALETTE_SIZE;
    int to = (color_idx + 1) % PALETTE_SIZE;

    float t = (float)step_in_color / (float)STEPS_PER_COLOR;
    uint8_t r = (uint8_t)(s_palette[from][0] * (1.0f - t) + s_palette[to][0] * t);
    uint8_t g = (uint8_t)(s_palette[from][1] * (1.0f - t) + s_palette[to][1] * t);
    uint8_t b = (uint8_t)(s_palette[from][2] * (1.0f - t) + s_palette[to][2] * t);

    int total = PALETTE_SIZE * STEPS_PER_COLOR;
    if (s_steps_left < total / 4) {
        float fade = (float)s_steps_left / (float)(total / 4);
        r = (uint8_t)(r * fade);
        g = (uint8_t)(g * fade);
        b = (uint8_t)(b * fade);
    }

    apply_color(r, g, b);
}

void rgb_led_init(void)
{
    led_strip_config_t strip_config = {
        .strip_gpio_num = RGB_LED_GPIO,
        .max_leds = 1,
        .led_pixel_format = LED_PIXEL_FORMAT_GRB,
        .led_model = LED_MODEL_WS2812,
        .flags.invert_out = false,
    };
    led_strip_rmt_config_t rmt_config = {
        .clk_src = RMT_CLK_SRC_DEFAULT,
        .resolution_hz = 10 * 1000 * 1000, /* 10 MHz */
        .flags.with_dma = false,
    };

    esp_err_t err = led_strip_new_rmt_device(&strip_config, &rmt_config, &s_strip);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Failed to init LED strip: %s", esp_err_to_name(err));
        return;
    }

    /* Start dark */
    led_strip_clear(s_strip);

    esp_timer_create_args_t timer_args = {
        .callback = timer_cb,
        .name = "rgb_cycle",
    };
    esp_timer_create(&timer_args, &s_timer);

    ESP_LOGI(TAG, "RGB LED initialized on GPIO%d", RGB_LED_GPIO);
}

void rgb_led_set(uint8_t r, uint8_t g, uint8_t b)
{
    if (s_timer) {
        esp_timer_stop(s_timer);
    }
    apply_color(r, g, b);
}

void rgb_led_flash(uint8_t r, uint8_t g, uint8_t b, int duration_ms)
{
    (void)r; (void)g; (void)b; (void)duration_ms;
    if (!s_strip || !s_timer) return;

    esp_timer_stop(s_timer);

    s_flash_mode = FLASH_MODE_PALETTE;
    s_steps_left = PALETTE_SIZE * STEPS_PER_COLOR;

    apply_color(s_palette[0][0], s_palette[0][1], s_palette[0][2]);

    esp_timer_start_periodic(s_timer, STEP_MS * 1000);
}

void rgb_led_flash_error(void)
{
    if (!s_strip || !s_timer) return;

    esp_timer_stop(s_timer);

    s_flash_mode = FLASH_MODE_ERROR;
    s_steps_left = ERROR_TOTAL_STEPS;

    apply_color(255, 0, 0);  /* Start with red */

    esp_timer_start_periodic(s_timer, STEP_MS * 1000);
}
