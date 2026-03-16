// firmware/main/main.c
#include <stdio.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "esp_log.h"
#include "nvs_flash.h"
#include "display.h"
#include "ble_service.h"
#include "ui_manager.h"
#include "config_store.h"
#include "esp_heap_caps.h"

static const char *TAG = "clawd-tank";

#define EVT_QUEUE_LEN 16

static QueueHandle_t s_evt_queue;

static void ui_task(void *arg) {
    ui_manager_init();

    ble_evt_t evt;
    while (1) {
        // Process any pending BLE events
        while (xQueueReceive(s_evt_queue, &evt, 0) == pdTRUE) {
            ui_manager_handle_event(&evt);
        }

        // Run LVGL
        ui_manager_tick();

        vTaskDelay(pdMS_TO_TICKS(5));
    }
}

void app_main(void) {
    ESP_LOGI(TAG, "Clawd Tank starting...");

    // Create event queue (BLE -> UI)
    s_evt_queue = xQueueCreate(EVT_QUEUE_LEN, sizeof(ble_evt_t));
    assert(s_evt_queue);

    // Init NVS (moved from ble_service_init — needed by config_store before BLE)
    ESP_ERROR_CHECK(nvs_flash_init());

    // Init config store — must be before display_init for brightness
    config_store_init();

    // Init display (SPI + LVGL).
    // Return value intentionally discarded: display_init() registers the
    // lv_display_t* as LVGL's default display internally, so callers never
    // need to hold the pointer — lv_display_get_default() retrieves it later.
    display_init();

    // Init BLE (NimBLE GATT server, posts events to queue)
    ble_service_init(s_evt_queue);

    // Start UI task
    BaseType_t ret = xTaskCreate(ui_task, "ui_task", 8192, NULL, 5, NULL);
    if (ret != pdPASS) {
        ESP_LOGE(TAG, "Failed to create ui_task");
        abort();
    }

    ESP_LOGI(TAG, "Clawd Tank running — free heap: %lu bytes (internal: %lu, PSRAM: %lu)",
             (unsigned long)esp_get_free_heap_size(),
             (unsigned long)heap_caps_get_free_size(MALLOC_CAP_INTERNAL),
             (unsigned long)heap_caps_get_free_size(MALLOC_CAP_SPIRAM));
}
