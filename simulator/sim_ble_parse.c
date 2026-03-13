// simulator/sim_ble_parse.c
#include "sim_ble_parse.h"
#include "cJSON.h"
#include "config_store.h"
#include <stdio.h>
#include <string.h>
#include <sys/time.h>
#include <stdlib.h>

static void safe_strncpy(char *dst, const char *src, size_t n) {
    if (!src) { dst[0] = '\0'; return; }
    strncpy(dst, src, n - 1);
    dst[n - 1] = '\0';
}

int sim_ble_parse_json(const char *buf, uint16_t len, ble_evt_t *out) {
    cJSON *json = cJSON_ParseWithLength(buf, len);
    if (!json) return -1;

    cJSON *action = cJSON_GetObjectItem(json, "action");
    if (!action || !cJSON_IsString(action)) {
        cJSON_Delete(json);
        return -1;
    }

    memset(out, 0, sizeof(*out));

    if (strcmp(action->valuestring, "add") == 0) {
        out->type = BLE_EVT_NOTIF_ADD;
        cJSON *id = cJSON_GetObjectItem(json, "id");
        if (!id || !cJSON_IsString(id)) { cJSON_Delete(json); return -1; }
        safe_strncpy(out->id, id->valuestring, sizeof(out->id));
        cJSON *project = cJSON_GetObjectItem(json, "project");
        cJSON *message = cJSON_GetObjectItem(json, "message");
        safe_strncpy(out->project,
                     project && cJSON_IsString(project) ? project->valuestring : "",
                     sizeof(out->project));
        safe_strncpy(out->message,
                     message && cJSON_IsString(message) ? message->valuestring : "",
                     sizeof(out->message));
    } else if (strcmp(action->valuestring, "dismiss") == 0) {
        out->type = BLE_EVT_NOTIF_DISMISS;
        cJSON *id = cJSON_GetObjectItem(json, "id");
        if (!id || !cJSON_IsString(id)) { cJSON_Delete(json); return -1; }
        safe_strncpy(out->id, id->valuestring, sizeof(out->id));
    } else if (strcmp(action->valuestring, "clear") == 0) {
        out->type = BLE_EVT_NOTIF_CLEAR;
    } else if (strcmp(action->valuestring, "set_time") == 0) {
        cJSON *epoch = cJSON_GetObjectItem(json, "epoch");
        if (epoch && cJSON_IsNumber(epoch)) {
            struct timeval tv = { .tv_sec = (time_t)epoch->valuedouble, .tv_usec = 0 };
            settimeofday(&tv, NULL);
            printf("[tcp] System time set to epoch %lld\n", (long long)tv.tv_sec);
        }
        cJSON *tz = cJSON_GetObjectItem(json, "tz");
        if (tz && cJSON_IsString(tz)) {
            setenv("TZ", tz->valuestring, 1);
            tzset();
            printf("[tcp] Timezone set to %s\n", tz->valuestring);
        }
        cJSON_Delete(json);
        return 1;
    } else if (strcmp(action->valuestring, "write_config") == 0 ||
               strcmp(action->valuestring, "read_config") == 0) {
        cJSON_Delete(json);
        return 2;
    } else {
        cJSON_Delete(json);
        return -1;
    }

    cJSON_Delete(json);
    return 0;
}
