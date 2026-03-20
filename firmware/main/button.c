// firmware/main/button.c
#include "button.h"
#include "ble_service.h"
#include "driver/gpio.h"
#include "esp_timer.h"
#include "esp_log.h"

#define BUTTON_GPIO     GPIO_NUM_9  /* BOOT button on ESP32-C6 */
#define DEBOUNCE_US     200000  /* 200ms debounce */

static const char *TAG = "button";
static QueueHandle_t s_queue;
static int64_t s_last_press_us;

static void IRAM_ATTR button_isr(void *arg)
{
    int64_t now = esp_timer_get_time();
    if (now - s_last_press_us < DEBOUNCE_US) return;
    s_last_press_us = now;

    ble_evt_t evt = { .type = BLE_EVT_NOTIF_CLEAR };
    BaseType_t woken = pdFALSE;
    xQueueSendFromISR(s_queue, &evt, &woken);
    if (woken) portYIELD_FROM_ISR();
}

void button_init(QueueHandle_t evt_queue)
{
    s_queue = evt_queue;
    s_last_press_us = 0;

    gpio_config_t cfg = {
        .intr_type    = GPIO_INTR_NEGEDGE,
        .mode         = GPIO_MODE_INPUT,
        .pin_bit_mask = 1ULL << BUTTON_GPIO,
        .pull_up_en   = GPIO_PULLUP_ENABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
    };
    gpio_config(&cfg);
    gpio_install_isr_service(0);
    gpio_isr_handler_add(BUTTON_GPIO, button_isr, NULL);

    ESP_LOGI(TAG, "GPIO %d button initialized (clear notifications)", BUTTON_GPIO);
}
