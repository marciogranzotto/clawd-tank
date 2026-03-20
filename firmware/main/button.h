#pragma once
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"

// Initialize GPIO 0 (BOOT button) to post BLE_EVT_NOTIF_CLEAR on press.
void button_init(QueueHandle_t evt_queue);
