// simulator/sim_socket.c
#include "sim_socket.h"
#include "sim_ble_parse.h"
#include "ble_service.h"
#include "config_store.h"
#include "ui_manager.h"
#include "cJSON.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <pthread.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <errno.h>

/* ---- Thread-safe event queue ---- */
#define EVT_QUEUE_SIZE 16

static ble_evt_t s_queue[EVT_QUEUE_SIZE];
static int s_queue_head = 0;
static int s_queue_tail = 0;
static int s_queue_count = 0;
static pthread_mutex_t s_queue_mutex = PTHREAD_MUTEX_INITIALIZER;

static bool queue_push(const ble_evt_t *evt) {
    pthread_mutex_lock(&s_queue_mutex);
    if (s_queue_count >= EVT_QUEUE_SIZE) {
        pthread_mutex_unlock(&s_queue_mutex);
        printf("[tcp] Event queue full, dropping event\n");
        return false;
    }
    s_queue[s_queue_tail] = *evt;
    s_queue_tail = (s_queue_tail + 1) % EVT_QUEUE_SIZE;
    s_queue_count++;
    pthread_mutex_unlock(&s_queue_mutex);
    return true;
}

static bool queue_pop(ble_evt_t *out) {
    pthread_mutex_lock(&s_queue_mutex);
    if (s_queue_count == 0) {
        pthread_mutex_unlock(&s_queue_mutex);
        return false;
    }
    *out = s_queue[s_queue_head];
    s_queue_head = (s_queue_head + 1) % EVT_QUEUE_SIZE;
    s_queue_count--;
    pthread_mutex_unlock(&s_queue_mutex);
    return true;
}

/* ---- Pending config update (socket thread -> main thread) ---- */
static volatile bool s_pending_config_update = false;
static volatile uint32_t s_pending_sleep_timeout_ms = 0;

/* ---- TCP listener thread ---- */
static int s_listen_fd = -1;
static pthread_t s_thread;
static bool s_thread_started = false;
static volatile bool s_running = false;

/* Handle config read/write actions on the socket thread.
 * read_config: atomic read of config_store state (no LVGL interaction).
 * write_config: stores values in config_store (atomic word-sized writes)
 *   and signals main thread to call ui_manager_set_sleep_timeout(). */
static void handle_config_action(const char *buf, uint16_t len, int client_fd) {
    /* Note: sim_ble_parse_json() already parsed this buffer to identify it as
     * a config action (return 2). We parse again here rather than threading
     * the cJSON object through the shared parser interface — this is a
     * pragmatic trade-off since the config path is cold (rare, interactive). */
    cJSON *json = cJSON_ParseWithLength(buf, len);
    if (!json) return;

    cJSON *action = cJSON_GetObjectItem(json, "action");
    if (!action || !cJSON_IsString(action)) { cJSON_Delete(json); return; }

    if (strcmp(action->valuestring, "read_config") == 0) {
        char config_buf[128];
        uint16_t config_len = config_store_serialize_json(config_buf, sizeof(config_buf));
        if (config_len > 0) {
            send(client_fd, config_buf, config_len, 0);
            send(client_fd, "\n", 1, 0);
            printf("[tcp] Config read: %s\n", config_buf);
        }
    } else if (strcmp(action->valuestring, "write_config") == 0) {
        cJSON *brightness = cJSON_GetObjectItem(json, "brightness");
        if (brightness && cJSON_IsNumber(brightness)) {
            config_store_set_brightness((uint8_t)brightness->valueint);
            printf("[tcp] Config: brightness=%d\n", brightness->valueint);
        }
        cJSON *sleep_t = cJSON_GetObjectItem(json, "sleep_timeout");
        if (sleep_t && cJSON_IsNumber(sleep_t)) {
            config_store_set_sleep_timeout((uint16_t)sleep_t->valueint);
            printf("[tcp] Config: sleep_timeout=%d\n", sleep_t->valueint);
            s_pending_sleep_timeout_ms = (uint32_t)sleep_t->valueint * 1000;
            s_pending_config_update = true;
        }
    }

    cJSON_Delete(json);
}

static void handle_client(int client_fd) {
    printf("[tcp] Client connected\n");

    ble_evt_t connect_evt = { .type = BLE_EVT_CONNECTED };
    queue_push(&connect_evt);

    char buf[4096];
    int buf_len = 0;

    while (s_running) {
        int n = (int)recv(client_fd, buf + buf_len, sizeof(buf) - buf_len - 1, 0);
        if (n <= 0) break;  /* EOF or error */
        buf_len += n;
        buf[buf_len] = '\0';

        /* Process complete lines */
        char *line_start = buf;
        char *newline;
        while ((newline = strchr(line_start, '\n')) != NULL) {
            *newline = '\0';
            int line_len = (int)(newline - line_start);
            if (line_len > 0) {
                ble_evt_t evt;
                int rc = sim_ble_parse_json(line_start, (uint16_t)line_len, &evt);
                if (rc == 0) {
                    queue_push(&evt);
                } else if (rc == 2) {
                    /* Config action — handle on socket thread */
                    handle_config_action(line_start, (uint16_t)line_len, client_fd);
                } else if (rc < 0) {
                    printf("[tcp] Parse error, ignoring: %.*s\n", line_len, line_start);
                }
                /* rc == 1 means set_time handled inline by parser */
            }
            line_start = newline + 1;
        }

        /* Shift remaining partial line to start of buffer */
        int remaining = buf_len - (int)(line_start - buf);
        if (remaining > 0 && line_start != buf) {
            memmove(buf, line_start, remaining);
        }
        buf_len = remaining;

        /* If the buffer is full with no newline, the next recv() would be
         * called with nbytes=0, causing a silent disconnect. Flush instead. */
        if (buf_len == (int)(sizeof(buf) - 1)) {
            printf("[tcp] Oversized line (>%zu bytes), discarding\n", sizeof(buf) - 1);
            buf_len = 0;
        }
    }

    printf("[tcp] Client disconnected\n");
    ble_evt_t disconnect_evt = { .type = BLE_EVT_DISCONNECTED };
    queue_push(&disconnect_evt);

    close(client_fd);
}

static void *listener_thread(void *arg) {
    (void)arg;
    printf("[tcp] Listener thread started\n");

    while (s_running) {
        struct sockaddr_in client_addr;
        socklen_t addr_len = sizeof(client_addr);
        int client_fd = accept(s_listen_fd, (struct sockaddr *)&client_addr, &addr_len);
        if (client_fd < 0) {
            if (s_running) {
                printf("[tcp] Accept error: %s\n", strerror(errno));
            }
            continue;
        }
        handle_client(client_fd);
    }

    printf("[tcp] Listener thread exiting\n");
    return NULL;
}

/* ---- Public API ---- */

int sim_socket_init(int port) {
    s_listen_fd = socket(AF_INET, SOCK_STREAM, 0);
    if (s_listen_fd < 0) {
        fprintf(stderr, "[tcp] Failed to create socket: %s\n", strerror(errno));
        return -1;
    }

    int opt = 1;
    setsockopt(s_listen_fd, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));

    struct sockaddr_in addr = {
        .sin_family = AF_INET,
        .sin_port = htons((uint16_t)port),
        .sin_addr.s_addr = htonl(INADDR_ANY),
    };

    if (bind(s_listen_fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        fprintf(stderr, "[tcp] Failed to bind port %d: %s\n", port, strerror(errno));
        close(s_listen_fd);
        s_listen_fd = -1;
        return -1;
    }

    if (listen(s_listen_fd, 1) < 0) {
        fprintf(stderr, "[tcp] Failed to listen: %s\n", strerror(errno));
        close(s_listen_fd);
        s_listen_fd = -1;
        return -1;
    }

    printf("[tcp] Listening on port %d\n", port);
    s_running = true;

    if (pthread_create(&s_thread, NULL, listener_thread, NULL) != 0) {
        fprintf(stderr, "[tcp] Failed to create listener thread\n");
        close(s_listen_fd);
        s_listen_fd = -1;
        s_running = false;
        return -1;
    }
    s_thread_started = true;

    return 0;
}

bool sim_socket_process(void) {
    bool any = false;
    ble_evt_t evt;
    while (queue_pop(&evt)) {
        ui_manager_handle_event(&evt);
        any = true;
    }
    /* Apply pending config updates from socket thread */
    if (s_pending_config_update) {
        s_pending_config_update = false;
        ui_manager_set_sleep_timeout(s_pending_sleep_timeout_ms);
    }
    return any;
}

void sim_socket_shutdown(void) {
    s_running = false;
    if (s_listen_fd >= 0) {
        shutdown(s_listen_fd, SHUT_RDWR);
        close(s_listen_fd);
        s_listen_fd = -1;
    }
    if (s_thread_started) {
        pthread_join(s_thread, NULL);
    }
    printf("[tcp] Shut down\n");
}
