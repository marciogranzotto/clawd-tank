// firmware/main/ble_service.c
#include "ble_service.h"
#include "esp_log.h"
#include "nvs_flash.h"
#include "nimble/nimble_port.h"
#include "nimble/nimble_port_freertos.h"
#include "host/ble_hs.h"
#include "host/util/util.h"
#include "services/gap/ble_svc_gap.h"
#include "services/gatt/ble_svc_gatt.h"
#include "cJSON.h"
#include <string.h>

static const char *TAG = "ble";
static QueueHandle_t s_evt_queue;

// UUIDs in little-endian byte order
// Service: AECBEFD9-98A2-4773-9FED-BB2166DAA49A
static const ble_uuid128_t clawd_svc_uuid = BLE_UUID128_INIT(
    0x9a, 0xa4, 0xda, 0x66, 0x21, 0xbb, 0xed, 0x9f,
    0x73, 0x47, 0xa2, 0x98, 0xd9, 0xef, 0xcb, 0xae
);

// Characteristic: 71FFB137-8B7A-47C9-9A7A-4B1B16662D9A
static const ble_uuid128_t notif_chr_uuid = BLE_UUID128_INIT(
    0x9a, 0x2d, 0x66, 0x16, 0x1b, 0x4b, 0x7a, 0x9a,
    0xc9, 0x47, 0x7a, 0x8b, 0x37, 0xb1, 0xff, 0x71
);

// Forward declarations
static int ble_gap_event_cb(struct ble_gap_event *event, void *arg);
static void start_advertising(void);

static void safe_strncpy(char *dst, const char *src, size_t n) {
    if (!src) { dst[0] = '\0'; return; }
    strncpy(dst, src, n - 1);
    dst[n - 1] = '\0';
}

static void parse_notification_json(const char *buf, uint16_t len) {
    cJSON *json = cJSON_ParseWithLength(buf, len);
    if (!json) {
        ESP_LOGW(TAG, "Malformed JSON, ignoring");
        return;
    }

    cJSON *action = cJSON_GetObjectItem(json, "action");
    if (!action || !cJSON_IsString(action)) {
        ESP_LOGW(TAG, "Missing 'action' field, ignoring");
        cJSON_Delete(json);
        return;
    }

    ble_evt_t evt = {0};

    if (strcmp(action->valuestring, "add") == 0) {
        evt.type = BLE_EVT_NOTIF_ADD;
        cJSON *id = cJSON_GetObjectItem(json, "id");
        cJSON *project = cJSON_GetObjectItem(json, "project");
        cJSON *message = cJSON_GetObjectItem(json, "message");
        if (!id || !cJSON_IsString(id)) {
            cJSON_Delete(json);
            return;
        }
        safe_strncpy(evt.id, id->valuestring, sizeof(evt.id));
        safe_strncpy(evt.project,
                     project && cJSON_IsString(project) ? project->valuestring : "",
                     sizeof(evt.project));
        safe_strncpy(evt.message,
                     message && cJSON_IsString(message) ? message->valuestring : "",
                     sizeof(evt.message));
    } else if (strcmp(action->valuestring, "dismiss") == 0) {
        evt.type = BLE_EVT_NOTIF_DISMISS;
        cJSON *id = cJSON_GetObjectItem(json, "id");
        if (!id || !cJSON_IsString(id)) {
            cJSON_Delete(json);
            return;
        }
        safe_strncpy(evt.id, id->valuestring, sizeof(evt.id));
    } else if (strcmp(action->valuestring, "clear") == 0) {
        evt.type = BLE_EVT_NOTIF_CLEAR;
    } else {
        ESP_LOGW(TAG, "Unknown action '%s', ignoring", action->valuestring);
        cJSON_Delete(json);
        return;
    }

    cJSON_Delete(json);
    if (xQueueSend(s_evt_queue, &evt, pdMS_TO_TICKS(0)) != pdTRUE) {
        ESP_LOGW(TAG, "Event queue full, dropping notification");
    }
}

static int notification_write_cb(uint16_t conn_handle, uint16_t attr_handle,
                                  struct ble_gatt_access_ctxt *ctxt, void *arg) {
    if (ctxt->op != BLE_GATT_ACCESS_OP_WRITE_CHR) return 0;

    uint16_t len = OS_MBUF_PKTLEN(ctxt->om);
    if (len == 0 || len > 512) {
        return BLE_ATT_ERR_INVALID_ATTR_VALUE_LEN;
    }

    char buf[513];
    uint16_t copied;
    int rc = ble_hs_mbuf_to_flat(ctxt->om, buf, len, &copied);
    if (rc != 0) {
        ESP_LOGW(TAG, "mbuf_to_flat failed: %d", rc);
        return BLE_ATT_ERR_UNLIKELY;
    }
    buf[copied] = '\0';

    ESP_LOGD(TAG, "BLE write (%d bytes): %s", copied, buf);
    parse_notification_json(buf, copied);
    return 0;
}

static const struct ble_gatt_svc_def gatt_svcs[] = {
    {
        .type = BLE_GATT_SVC_TYPE_PRIMARY,
        .uuid = &clawd_svc_uuid.u,
        .characteristics = (struct ble_gatt_chr_def[]) {
            {
                .uuid = &notif_chr_uuid.u,
                .access_cb = notification_write_cb,
                .flags = BLE_GATT_CHR_F_WRITE | BLE_GATT_CHR_F_WRITE_NO_RSP,
            },
            { 0 },
        },
    },
    { 0 },
};

static void start_advertising(void) {
    struct ble_gap_adv_params adv_params = {0};
    struct ble_hs_adv_fields fields = {0};

    fields.flags = BLE_HS_ADV_F_DISC_GEN | BLE_HS_ADV_F_BREDR_UNSUP;
    fields.name = (uint8_t *)"Clawd Tank";
    fields.name_len = 10;
    fields.name_is_complete = 1;
    int rc = ble_gap_adv_set_fields(&fields);
    if (rc != 0) {
        ESP_LOGW(TAG, "Failed to set adv fields: %d", rc);
        return;
    }

    adv_params.conn_mode = BLE_GAP_CONN_MODE_UND;
    adv_params.disc_mode = BLE_GAP_DISC_MODE_GEN;
    rc = ble_gap_adv_start(BLE_OWN_ADDR_PUBLIC, NULL, BLE_HS_FOREVER,
                           &adv_params, ble_gap_event_cb, NULL);
    if (rc != 0) {
        ESP_LOGW(TAG, "Failed to start advertising: %d", rc);
    }
}

static int ble_gap_event_cb(struct ble_gap_event *event, void *arg) {
    switch (event->type) {
    case BLE_GAP_EVENT_CONNECT:
        ESP_LOGI(TAG, "BLE %s", event->connect.status == 0 ? "connected" : "connect failed");
        if (event->connect.status == 0) {
            ble_evt_t evt = { .type = BLE_EVT_CONNECTED };
            if (xQueueSend(s_evt_queue, &evt, pdMS_TO_TICKS(0)) != pdTRUE) {
                ESP_LOGW(TAG, "Event queue full, dropping connect event");
            }
        } else {
            start_advertising();
        }
        break;
    case BLE_GAP_EVENT_DISCONNECT:
        ESP_LOGI(TAG, "BLE disconnected");
        ble_evt_t evt = { .type = BLE_EVT_DISCONNECTED };
        if (xQueueSend(s_evt_queue, &evt, pdMS_TO_TICKS(0)) != pdTRUE) {
            ESP_LOGW(TAG, "Event queue full, dropping disconnect event");
        }
        start_advertising();
        break;
    case BLE_GAP_EVENT_ADV_COMPLETE:
        start_advertising();
        break;
    case BLE_GAP_EVENT_MTU:
        ESP_LOGI(TAG, "MTU updated to %d", event->mtu.value);
        break;
    }
    return 0;
}

static void ble_on_sync(void) {
    int rc = ble_hs_util_ensure_addr(0);
    if (rc != 0) {
        ESP_LOGE(TAG, "Failed to ensure address: %d", rc);
        return;
    }
    ESP_LOGI(TAG, "BLE synced, starting advertising as 'Clawd Tank'");
    start_advertising();
}

static void ble_host_task(void *param) {
    nimble_port_run();
    nimble_port_freertos_deinit();
}

void ble_service_init(QueueHandle_t evt_queue) {
    s_evt_queue = evt_queue;

    ESP_ERROR_CHECK(nvs_flash_init());
    ESP_ERROR_CHECK(nimble_port_init());

    int rc = ble_svc_gap_device_name_set("Clawd Tank");
    if (rc != 0) {
        ESP_LOGW(TAG, "Failed to set GAP device name: %d", rc);
    }

    ble_svc_gap_init();
    ble_svc_gatt_init();

    rc = ble_gatts_count_cfg(gatt_svcs);
    if (rc != 0) {
        ESP_LOGE(TAG, "GATT count_cfg failed: %d", rc);
        abort();
    }
    rc = ble_gatts_add_svcs(gatt_svcs);
    if (rc != 0) {
        ESP_LOGE(TAG, "GATT add_svcs failed: %d", rc);
        abort();
    }

    ble_hs_cfg.sync_cb = ble_on_sync;

    nimble_port_freertos_init(ble_host_task);

    ESP_LOGI(TAG, "BLE service initialized");
}
