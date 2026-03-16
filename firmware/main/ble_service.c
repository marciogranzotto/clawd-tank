// firmware/main/ble_service.c
#include "ble_service.h"
#include "esp_log.h"
#include "nimble/nimble_port.h"
#include "nimble/nimble_port_freertos.h"
#include "host/ble_hs.h"
#include "host/util/util.h"
#include "services/gap/ble_svc_gap.h"
#include "services/gatt/ble_svc_gatt.h"
#include "cJSON.h"
#include "config_store.h"
#include "display.h"
#include "scene.h"
#include "ui_manager.h"
#include <string.h>
#include <stdlib.h>
#include <sys/time.h>

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

// Config Characteristic: E9F6E626-5FCA-4201-B80C-4D2B51C40F51
static const ble_uuid128_t config_chr_uuid = BLE_UUID128_INIT(
    0x51, 0x0f, 0xc4, 0x51, 0x2b, 0x4d, 0x0c, 0xb8,
    0x01, 0x42, 0xca, 0x5f, 0x26, 0xe6, 0xf6, 0xe9
);

// Version Characteristic: B6DC9A5B-5041-4B32-9F8D-34321DF8637C
static const ble_uuid128_t version_chr_uuid = BLE_UUID128_INIT(
    0x7c, 0x63, 0xf8, 0x1d, 0x32, 0x34, 0x8d, 0x9f,
    0x32, 0x4b, 0x41, 0x50, 0x5b, 0x9a, 0xdc, 0xb6
);

// Forward declarations
static int ble_gap_event_cb(struct ble_gap_event *event, void *arg);
static void start_advertising(void);

static void safe_strncpy(char *dst, const char *src, size_t n) {
    if (!src) { dst[0] = '\0'; return; }
    strncpy(dst, src, n - 1);
    dst[n - 1] = '\0';
}

static int parse_anim_name(const char *str) {
    if (strcmp(str, "idle") == 0)     return CLAWD_ANIM_IDLE;
    if (strcmp(str, "typing") == 0)   return CLAWD_ANIM_TYPING;
    if (strcmp(str, "thinking") == 0) return CLAWD_ANIM_THINKING;
    if (strcmp(str, "building") == 0) return CLAWD_ANIM_BUILDING;
    if (strcmp(str, "confused") == 0) return CLAWD_ANIM_CONFUSED;
    if (strcmp(str, "sweeping") == 0) return CLAWD_ANIM_SWEEPING;
    return -1;
}

static int parse_display_status(const char *str) {
    if (strcmp(str, "sleeping") == 0) return DISPLAY_STATUS_SLEEPING;
    if (strcmp(str, "idle") == 0) return DISPLAY_STATUS_IDLE;
    if (strcmp(str, "thinking") == 0) return DISPLAY_STATUS_THINKING;
    if (strcmp(str, "working_1") == 0) return DISPLAY_STATUS_WORKING_1;
    if (strcmp(str, "working_2") == 0) return DISPLAY_STATUS_WORKING_2;
    if (strcmp(str, "working_3") == 0) return DISPLAY_STATUS_WORKING_3;
    if (strcmp(str, "confused") == 0) return DISPLAY_STATUS_CONFUSED;
    if (strcmp(str, "sweeping") == 0) return DISPLAY_STATUS_SWEEPING;
    return -1;
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
    } else if (strcmp(action->valuestring, "set_time") == 0) {
        cJSON *epoch = cJSON_GetObjectItem(json, "epoch");
        if (epoch && cJSON_IsNumber(epoch)) {
            struct timeval tv = { .tv_sec = (time_t)epoch->valuedouble, .tv_usec = 0 };
            settimeofday(&tv, NULL);
            ESP_LOGI(TAG, "System time set to epoch %lld", (long long)tv.tv_sec);
        }
        cJSON *tz = cJSON_GetObjectItem(json, "tz");
        if (tz && cJSON_IsString(tz)) {
            setenv("TZ", tz->valuestring, 1);
            tzset();
            ESP_LOGI(TAG, "Timezone set to %s", tz->valuestring);
        }
        cJSON_Delete(json);
        return;
    } else if (strcmp(action->valuestring, "set_status") == 0) {
        cJSON *status = cJSON_GetObjectItem(json, "status");
        if (!status || !cJSON_IsString(status)) {
            ESP_LOGW(TAG, "set_status: missing 'status' field");
            cJSON_Delete(json);
            return;
        }
        int s = parse_display_status(status->valuestring);
        if (s < 0) {
            ESP_LOGW(TAG, "set_status: unknown status '%s'", status->valuestring);
            cJSON_Delete(json);
            return;
        }
        evt.type = BLE_EVT_SET_STATUS;
        evt.status = (uint8_t)s;
    } else if (strcmp(action->valuestring, "set_sessions") == 0) {
        cJSON *anims = cJSON_GetObjectItem(json, "anims");
        cJSON *ids = cJSON_GetObjectItem(json, "ids");
        cJSON *subagents = cJSON_GetObjectItem(json, "subagents");
        if (!anims || !cJSON_IsArray(anims) || !ids || !cJSON_IsArray(ids)) {
            cJSON_Delete(json);
            return;
        }
        evt.type = BLE_EVT_SET_SESSIONS;
        evt.session_anim_count = 0;
        evt.subagent_count = subagents && cJSON_IsNumber(subagents) ? (uint8_t)subagents->valueint : 0;
        cJSON *overflow = cJSON_GetObjectItem(json, "overflow");
        evt.session_overflow = overflow && cJSON_IsNumber(overflow) ? (uint8_t)overflow->valueint : 0;

        int anim_size = cJSON_GetArraySize(anims);
        int id_size = cJSON_GetArraySize(ids);
        int count = anim_size < id_size ? anim_size : id_size;
        if (count > MAX_VISIBLE_SESSIONS) count = MAX_VISIBLE_SESSIONS;

        for (int i = 0; i < count; i++) {
            cJSON *a = cJSON_GetArrayItem(anims, i);
            cJSON *id = cJSON_GetArrayItem(ids, i);
            if (!a || !cJSON_IsString(a) || !id || !cJSON_IsNumber(id)) continue;
            int anim = parse_anim_name(a->valuestring);
            if (anim < 0) continue;
            evt.session_anims[evt.session_anim_count] = (uint8_t)anim;
            evt.session_ids[evt.session_anim_count] = (uint16_t)id->valueint;
            evt.session_anim_count++;
        }
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

static int config_access_cb(uint16_t conn_handle, uint16_t attr_handle,
                             struct ble_gatt_access_ctxt *ctxt, void *arg)
{
    if (ctxt->op == BLE_GATT_ACCESS_OP_READ_CHR) {
        char buf[512];
        uint16_t len = config_store_serialize_json(buf, sizeof(buf));
        int rc = os_mbuf_append(ctxt->om, buf, len);
        return rc == 0 ? 0 : BLE_ATT_ERR_INSUFFICIENT_RES;
    }

    if (ctxt->op == BLE_GATT_ACCESS_OP_WRITE_CHR) {
        uint16_t len = OS_MBUF_PKTLEN(ctxt->om);
        if (len == 0 || len > 512) {
            return BLE_ATT_ERR_INVALID_ATTR_VALUE_LEN;
        }

        char wbuf[513];
        uint16_t copied;
        int rc = ble_hs_mbuf_to_flat(ctxt->om, wbuf, len, &copied);
        if (rc != 0) {
            return BLE_ATT_ERR_UNLIKELY;
        }
        wbuf[copied] = '\0';

        cJSON *json = cJSON_ParseWithLength(wbuf, copied);
        if (!json) {
            ESP_LOGW(TAG, "Config: malformed JSON");
            return BLE_ATT_ERR_UNLIKELY;
        }

        cJSON *brightness = cJSON_GetObjectItem(json, "brightness");
        if (brightness && cJSON_IsNumber(brightness)) {
            int val = brightness->valueint;
            if (val >= 0 && val <= 255) {
                config_store_set_brightness((uint8_t)val);
                display_set_brightness((uint8_t)val);
                ESP_LOGI(TAG, "Config: brightness=%d", val);
            }
        }

        cJSON *sleep_timeout = cJSON_GetObjectItem(json, "sleep_timeout");
        if (sleep_timeout && cJSON_IsNumber(sleep_timeout)) {
            int val = sleep_timeout->valueint;
            if (val >= 0 && val <= 3600) {
                config_store_set_sleep_timeout((uint16_t)val);
                ESP_LOGI(TAG, "Config: sleep_timeout=%d (daemon-driven, stored only)", val);
            }
        }

        cJSON_Delete(json);
        return 0;
    }

    return 0;
}

static int version_access_cb(uint16_t conn_handle, uint16_t attr_handle,
                              struct ble_gatt_access_ctxt *ctxt, void *arg) {
    if (ctxt->op == BLE_GATT_ACCESS_OP_READ_CHR) {
        const char *ver = "2";
        int rc = os_mbuf_append(ctxt->om, ver, strlen(ver));
        return rc == 0 ? 0 : BLE_ATT_ERR_INSUFFICIENT_RES;
    }
    return BLE_ATT_ERR_UNLIKELY;
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
            {
                .uuid = &config_chr_uuid.u,
                .access_cb = config_access_cb,
                .flags = BLE_GATT_CHR_F_READ | BLE_GATT_CHR_F_WRITE | BLE_GATT_CHR_F_WRITE_NO_RSP,
            },
            {
                .uuid = &version_chr_uuid.u,
                .access_cb = version_access_cb,
                .flags = BLE_GATT_CHR_F_READ,
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
