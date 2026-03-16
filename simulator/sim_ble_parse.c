// simulator/sim_ble_parse.c
#include "sim_ble_parse.h"
#include "cJSON.h"
#include "config_store.h"
#include "scene.h"
#include <stdio.h>
#include <string.h>
#include <sys/time.h>
#include <stdlib.h>

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
    } else if (strcmp(action->valuestring, "set_status") == 0) {
        cJSON *status = cJSON_GetObjectItem(json, "status");
        if (!status || !cJSON_IsString(status)) {
            cJSON_Delete(json);
            return -1;
        }
        int s = parse_display_status(status->valuestring);
        if (s < 0) {
            cJSON_Delete(json);
            return -1;
        }
        out->type = BLE_EVT_SET_STATUS;
        out->status = (uint8_t)s;
    } else if (strcmp(action->valuestring, "set_sessions") == 0) {
        cJSON *anims = cJSON_GetObjectItem(json, "anims");
        cJSON *ids = cJSON_GetObjectItem(json, "ids");
        cJSON *subagents = cJSON_GetObjectItem(json, "subagents");
        if (!anims || !cJSON_IsArray(anims) || !ids || !cJSON_IsArray(ids)) {
            cJSON_Delete(json);
            return -1;
        }
        out->type = BLE_EVT_SET_SESSIONS;
        out->session_anim_count = 0;
        out->subagent_count = subagents && cJSON_IsNumber(subagents) ? (uint8_t)subagents->valueint : 0;
        cJSON *overflow = cJSON_GetObjectItem(json, "overflow");
        out->session_overflow = overflow && cJSON_IsNumber(overflow) ? (uint8_t)overflow->valueint : 0;

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
            out->session_anims[out->session_anim_count] = (uint8_t)anim;
            out->session_ids[out->session_anim_count] = (uint16_t)id->valueint;
            out->session_anim_count++;
        }
    } else if (strcmp(action->valuestring, "write_config") == 0 ||
               strcmp(action->valuestring, "read_config") == 0) {
        cJSON_Delete(json);
        return 2;
    } else if (strcmp(action->valuestring, "show_window") == 0 ||
               strcmp(action->valuestring, "hide_window") == 0 ||
               strcmp(action->valuestring, "set_window") == 0) {
        cJSON_Delete(json);
        return 3;
    } else if (strcmp(action->valuestring, "query_state") == 0) {
        cJSON_Delete(json);
        return 4;
    } else {
        cJSON_Delete(json);
        return -1;
    }

    cJSON_Delete(json);
    return 0;
}
