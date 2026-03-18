#include "sim_events.h"
#include "ble_service.h"
#include "ui_manager.h"
#include "config_store.h"
#include "scene.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#include "cJSON.h"

#define MAX_EVENTS 64
#define MAX_NOTIF_IDS 16

typedef struct {
    uint32_t time_ms;       /* absolute simulated time */
    ble_evt_t evt;          /* event to fire */
    bool is_wait;           /* true = just a time advance, no event */
    char suffix[32];        /* for screenshot naming */
} sim_event_t;

static sim_event_t s_events[MAX_EVENTS];
static int s_event_count = 0;
static int s_event_cursor = 0;  /* next event to process */
static char s_last_suffix[32] = "";
static uint32_t s_timeline_end = 0;  /* final time including trailing waits */

/* Track injected notification IDs for dismiss-by-index */
static char s_notif_ids[MAX_NOTIF_IDS][NOTIF_MAX_ID_LEN];
static int s_notif_id_count = 0;
static int s_next_id = 1;

static void add_event(uint32_t time_ms, const ble_evt_t *evt, const char *suffix)
{
    if (s_event_count >= MAX_EVENTS) return;
    sim_event_t *e = &s_events[s_event_count++];
    e->time_ms = time_ms;
    e->is_wait = false;
    if (evt) {
        e->evt = *evt;
    }
    if (suffix) {
        snprintf(e->suffix, sizeof(e->suffix), "%s", suffix);
    } else {
        e->suffix[0] = '\0';
    }
}

/* Skip whitespace */
static const char *skip_ws(const char *s)
{
    while (*s && isspace((unsigned char)*s)) s++;
    return s;
}

/* Parse a quoted string, returns pointer past closing quote */
static const char *parse_quoted(const char *s, char *out, int out_sz)
{
    s = skip_ws(s);
    if (*s != '"') {
        /* Unquoted — read until space or semicolon */
        int i = 0;
        while (*s && *s != ';' && *s != ' ' && i < out_sz - 1) {
            out[i++] = *s++;
        }
        out[i] = '\0';
        return s;
    }
    s++; /* skip opening quote */
    int i = 0;
    while (*s && *s != '"' && i < out_sz - 1) {
        out[i++] = *s++;
    }
    out[i] = '\0';
    if (*s == '"') s++; /* skip closing quote */
    return s;
}

void sim_events_init_inline(const char *events_str)
{
    if (!events_str) return;

    uint32_t current_time = 0;
    const char *p = events_str;

    while (*p) {
        p = skip_ws(p);
        if (!*p) break;

        if (strncmp(p, "connect", 7) == 0 && (!p[7] || p[7] == ';' || isspace((unsigned char)p[7]))) {
            ble_evt_t evt = { .type = BLE_EVT_CONNECTED };
            add_event(current_time, &evt, "connect");
            p += 7;
        }
        else if (strncmp(p, "disconnect", 10) == 0 && (!p[10] || p[10] == ';' || isspace((unsigned char)p[10]))) {
            ble_evt_t evt = { .type = BLE_EVT_DISCONNECTED };
            add_event(current_time, &evt, "disconnect");
            p += 10;
        }
        else if (strncmp(p, "clear", 5) == 0 && (!p[5] || p[5] == ';' || isspace((unsigned char)p[5]))) {
            ble_evt_t evt = { .type = BLE_EVT_NOTIF_CLEAR };
            add_event(current_time, &evt, "clear");
            p += 5;
        }
        else if (strncmp(p, "config", 6) == 0) {
            p += 6;
            char json_str[256];
            p = parse_quoted(p, json_str, sizeof(json_str));

            /* Parse and apply config directly */
            cJSON *json = cJSON_Parse(json_str);
            if (json) {
                cJSON *brightness = cJSON_GetObjectItem(json, "brightness");
                if (brightness && cJSON_IsNumber(brightness)) {
                    config_store_set_brightness((uint8_t)brightness->valueint);
                    printf("[sim] Config: brightness=%d\n", brightness->valueint);
                }
                cJSON *sleep_t = cJSON_GetObjectItem(json, "sleep_timeout");
                if (sleep_t && cJSON_IsNumber(sleep_t)) {
                    config_store_set_sleep_timeout((uint16_t)sleep_t->valueint);
                    printf("[sim] Config: sleep_timeout=%d (daemon-driven, stored only)\n", sleep_t->valueint);
                }
                cJSON_Delete(json);
            }
        }
        else if (strncmp(p, "status", 6) == 0) {
            p += 6;
            char status_str[32];
            p = parse_quoted(p, status_str, sizeof(status_str));
            int s = -1;
            if (strcmp(status_str, "sleeping") == 0) s = DISPLAY_STATUS_SLEEPING;
            else if (strcmp(status_str, "idle") == 0) s = DISPLAY_STATUS_IDLE;
            else if (strcmp(status_str, "thinking") == 0) s = DISPLAY_STATUS_THINKING;
            else if (strcmp(status_str, "working_1") == 0) s = DISPLAY_STATUS_WORKING_1;
            else if (strcmp(status_str, "working_2") == 0) s = DISPLAY_STATUS_WORKING_2;
            else if (strcmp(status_str, "working_3") == 0) s = DISPLAY_STATUS_WORKING_3;
            else if (strcmp(status_str, "confused") == 0) s = DISPLAY_STATUS_CONFUSED;
            else if (strcmp(status_str, "sweeping") == 0) s = DISPLAY_STATUS_SWEEPING;
            if (s >= 0) {
                ble_evt_t evt = { .type = BLE_EVT_SET_STATUS, .status = (uint8_t)s };
                add_event(current_time, &evt, status_str);
            } else {
                fprintf(stderr, "[sim] Unknown status: %s\n", status_str);
            }
        }
        else if (strncmp(p, "notify", 6) == 0) {
            p += 6;
            ble_evt_t evt = { .type = BLE_EVT_NOTIF_ADD };
            /* Generate a unique ID */
            snprintf(evt.id, sizeof(evt.id), "sim_%d", s_next_id);
            /* Track ID for dismiss-by-index */
            if (s_notif_id_count < MAX_NOTIF_IDS) {
                strncpy(s_notif_ids[s_notif_id_count], evt.id, NOTIF_MAX_ID_LEN - 1);
                s_notif_id_count++;
            }
            s_next_id++;
            p = parse_quoted(p, evt.project, sizeof(evt.project));
            p = parse_quoted(p, evt.message, sizeof(evt.message));
            add_event(current_time, &evt, "notify");
        }
        else if (strncmp(p, "dismiss", 7) == 0) {
            p += 7;
            p = skip_ws(p);
            int index = atoi(p);
            while (*p && *p != ';' && !isspace((unsigned char)*p)) p++;

            ble_evt_t evt = { .type = BLE_EVT_NOTIF_DISMISS };
            if (index >= 0 && index < s_notif_id_count) {
                strncpy(evt.id, s_notif_ids[index], sizeof(evt.id) - 1);
            }
            add_event(current_time, &evt, "dismiss");
        }
        else if (strncmp(p, "sessions", 8) == 0 && (!p[8] || p[8] == ';' || isspace((unsigned char)p[8]))) {
            p += 8;
            /* Parse: sessions anim1 id1 [anim2 id2 ...] [subagents N] [overflow N]
             * Supports 1-4 anim/id pairs for multi-session display. */
            ble_evt_t evt = { .type = BLE_EVT_SET_SESSIONS };
            evt.session_anim_count = 0;
            evt.subagent_count = 0;
            evt.session_overflow = 0;
            char suffix[64] = "";

            while (1) {
                p = skip_ws(p);
                if (!*p || *p == ';') break;

                /* Check for keyword args: subagents N, overflow N */
                if (strncmp(p, "subagents", 9) == 0 && isspace((unsigned char)p[9])) {
                    p += 9; p = skip_ws(p);
                    evt.subagent_count = (uint8_t)atoi(p);
                    while (*p && *p != ';' && !isspace((unsigned char)*p)) p++;
                    continue;
                }
                if (strncmp(p, "overflow", 8) == 0 && isspace((unsigned char)p[8])) {
                    p += 8; p = skip_ws(p);
                    evt.session_overflow = (uint8_t)atoi(p);
                    while (*p && *p != ';' && !isspace((unsigned char)*p)) p++;
                    continue;
                }

                /* Parse anim_name id pair (stop accepting pairs at max) */
                if (evt.session_anim_count >= MAX_VISIBLE_SESSIONS) break;
                char anim_str[32];
                p = parse_quoted(p, anim_str, sizeof(anim_str));
                if (!anim_str[0]) break;

                p = skip_ws(p);
                if (!*p || *p == ';' || !isdigit((unsigned char)*p)) {
                    fprintf(stderr, "[sim] sessions: expected id after '%s'\n", anim_str);
                    break;
                }
                int display_id = atoi(p);
                while (*p && *p != ';' && !isspace((unsigned char)*p)) p++;

                int anim = -1;
                if (strcmp(anim_str, "idle") == 0) anim = CLAWD_ANIM_IDLE;
                else if (strcmp(anim_str, "typing") == 0) anim = CLAWD_ANIM_TYPING;
                else if (strcmp(anim_str, "thinking") == 0) anim = CLAWD_ANIM_THINKING;
                else if (strcmp(anim_str, "building") == 0) anim = CLAWD_ANIM_BUILDING;
                else if (strcmp(anim_str, "confused") == 0) anim = CLAWD_ANIM_CONFUSED;
                else if (strcmp(anim_str, "sleeping") == 0) anim = CLAWD_ANIM_SLEEPING;
                else if (strcmp(anim_str, "juggling") == 0) anim = CLAWD_ANIM_JUGGLING;
                else if (strcmp(anim_str, "sweeping") == 0) anim = CLAWD_ANIM_SWEEPING;
                else if (strcmp(anim_str, "dizzy") == 0) anim = CLAWD_ANIM_DIZZY;

                if (anim < 0) {
                    fprintf(stderr, "[sim] Unknown session anim: %s\n", anim_str);
                    continue;
                }

                int idx = evt.session_anim_count;
                evt.session_anims[idx] = (uint8_t)anim;
                evt.session_ids[idx] = (uint16_t)display_id;
                evt.session_anim_count++;

                /* Build suffix from first anim name */
                if (idx == 0) snprintf(suffix, sizeof(suffix), "%s", anim_str);
            }

            if (evt.session_anim_count > 0) {
                add_event(current_time, &evt, suffix);
            }
        }
        else if (strncmp(p, "wait", 4) == 0) {
            p += 4;
            p = skip_ws(p);
            uint32_t ms = (uint32_t)atoi(p);
            while (*p && *p != ';' && !isspace((unsigned char)*p)) p++;
            current_time += ms;
        }
        else {
            /* Unknown token — skip to next semicolon */
            while (*p && *p != ';') p++;
        }

        /* Skip to next command */
        p = skip_ws(p);
        if (*p == ';') p++;
    }
    s_timeline_end = current_time;
}

void sim_events_init_scenario(const char *path)
{
    if (!path) return;

    /* Read file */
    FILE *f = fopen(path, "rb");
    if (!f) {
        fprintf(stderr, "Cannot open scenario file: %s\n", path);
        return;
    }
    fseek(f, 0, SEEK_END);
    long sz = ftell(f);
    fseek(f, 0, SEEK_SET);
    char *json = malloc(sz + 1);
    fread(json, 1, sz, f);
    json[sz] = '\0';
    fclose(f);

    /* Parse JSON array */
    cJSON *root = cJSON_Parse(json);
    free(json);
    if (!root || !cJSON_IsArray(root)) {
        fprintf(stderr, "Invalid scenario JSON\n");
        if (root) cJSON_Delete(root);
        return;
    }

    cJSON *item;
    cJSON_ArrayForEach(item, root) {
        uint32_t time_ms = 0;
        cJSON *t = cJSON_GetObjectItem(item, "time_ms");
        if (t && cJSON_IsNumber(t)) time_ms = (uint32_t)t->valuedouble;

        cJSON *ev = cJSON_GetObjectItem(item, "event");
        if (!ev || !cJSON_IsString(ev)) continue;

        const char *event_name = ev->valuestring;

        if (strcmp(event_name, "connect") == 0) {
            ble_evt_t evt = { .type = BLE_EVT_CONNECTED };
            add_event(time_ms, &evt, "connect");
        }
        else if (strcmp(event_name, "disconnect") == 0) {
            ble_evt_t evt = { .type = BLE_EVT_DISCONNECTED };
            add_event(time_ms, &evt, "disconnect");
        }
        else if (strcmp(event_name, "clear") == 0) {
            ble_evt_t evt = { .type = BLE_EVT_NOTIF_CLEAR };
            add_event(time_ms, &evt, "clear");
        }
        else if (strcmp(event_name, "notify") == 0) {
            ble_evt_t evt = { .type = BLE_EVT_NOTIF_ADD };
            snprintf(evt.id, sizeof(evt.id), "scn_%d", s_next_id);
            if (s_notif_id_count < MAX_NOTIF_IDS) {
                strncpy(s_notif_ids[s_notif_id_count], evt.id, NOTIF_MAX_ID_LEN - 1);
                s_notif_id_count++;
            }
            s_next_id++;

            cJSON *proj = cJSON_GetObjectItem(item, "project");
            cJSON *msg  = cJSON_GetObjectItem(item, "message");
            if (proj && cJSON_IsString(proj))
                snprintf(evt.project, sizeof(evt.project), "%s", proj->valuestring);
            if (msg && cJSON_IsString(msg))
                snprintf(evt.message, sizeof(evt.message), "%s", msg->valuestring);

            add_event(time_ms, &evt, "notify");
        }
        else if (strcmp(event_name, "dismiss") == 0) {
            ble_evt_t evt = { .type = BLE_EVT_NOTIF_DISMISS };
            cJSON *idx = cJSON_GetObjectItem(item, "index");
            if (idx && cJSON_IsNumber(idx)) {
                int index = (int)idx->valuedouble;
                if (index >= 0 && index < s_notif_id_count) {
                    strncpy(evt.id, s_notif_ids[index], sizeof(evt.id) - 1);
                }
            }
            add_event(time_ms, &evt, "dismiss");
        }
    }

    cJSON_Delete(root);
    /* Set timeline end from the last event's timestamp */
    if (s_event_count > 0 && s_events[s_event_count - 1].time_ms > s_timeline_end)
        s_timeline_end = s_events[s_event_count - 1].time_ms;
    printf("[sim] Loaded %d events from %s\n", s_event_count, path);
}

bool sim_events_process(uint32_t current_time_ms)
{
    bool fired = false;

    while (s_event_cursor < s_event_count &&
           s_events[s_event_cursor].time_ms <= current_time_ms) {

        sim_event_t *e = &s_events[s_event_cursor];
        s_event_cursor++;

        if (!e->is_wait) {
            ui_manager_handle_event(&e->evt);
            snprintf(s_last_suffix, sizeof(s_last_suffix), "%s", e->suffix);
            fired = true;
        }
    }

    return fired;
}

bool sim_events_all_done(void)
{
    return s_event_cursor >= s_event_count;
}

uint32_t sim_events_get_end_time(void)
{
    uint32_t last_evt = (s_event_count > 0) ? s_events[s_event_count - 1].time_ms : 0;
    return (s_timeline_end > last_evt) ? s_timeline_end : last_evt;
}

const char *sim_events_last_suffix(void)
{
    return s_last_suffix;
}
