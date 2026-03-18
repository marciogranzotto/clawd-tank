// firmware/main/ui_manager.c
#include "ui_manager.h"
#include "scene.h"
#include "notification_ui.h"
#include "notification.h"
#include "config_store.h"
#include "display.h"
#include "rgb_led.h"
#include "esp_log.h"
#include <stdio.h>
#include <time.h>
#include <sys/time.h>

static const char *TAG = "ui";

/* ---------- State machine ---------- */

typedef enum {
    UI_STATE_FULL_IDLE,
    UI_STATE_NOTIFICATION,
    UI_STATE_DISCONNECTED,
} ui_state_t;

#define SCENE_FULL_WIDTH   320
#define SCENE_NOTIF_WIDTH  107
#define SCENE_ANIM_MS      400

/* ---------- Module state ---------- */

static ui_state_t s_state = UI_STATE_DISCONNECTED;
static notification_store_t s_store;

/* Protects all LVGL calls and store mutations that happen from two contexts:
 *   - ui_task (via ui_manager_handle_event + ui_manager_tick → lv_timer_handler)
 * The lock must be held whenever calling rebuild_ui() or lv_timer_handler()
 * because LVGL is not thread-safe and both paths touch the same widget tree.
 *
 * TODO(future): Consider migrating from _lock_t to LVGL's built-in
 * lv_lock() / lv_unlock() API (available in LVGL 9.x). lv_lock() integrates
 * with the flush-ready mechanism and avoids priority-inversion issues that
 * can arise with a plain spinlock when the display driver signals flush-done
 * from an ISR while the UI task holds s_lock. See LVGL docs:
 * https://docs.lvgl.io/9.2/overview/threading.html
 */
static _lock_t s_lock;

static scene_t *s_scene = NULL;
static notification_ui_t *s_notif_ui = NULL;

static bool s_connected = false;
static uint32_t s_last_activity_tick = 0;
static display_status_t s_display_status = DISPLAY_STATUS_IDLE;
static int s_last_minute = -1;

static void scene_animate_width(int target_px, int anim_ms)
{
    if (!s_scene) return;

    /* Get the container from scene for animation */
    /* Use scene_set_width for instant, but for animated we need the callback */
    if (anim_ms <= 0) {
        scene_set_width(s_scene, target_px, 0);
        if (s_notif_ui) {
            notification_ui_set_x(s_notif_ui, target_px);
        }
        return;
    }

    scene_set_width(s_scene, target_px, anim_ms);
    /* notification_ui tracks via scene_set_width's own animation —
       for simplicity, just set the final x immediately.
       The visual gap is small at 400ms. */
    if (s_notif_ui) {
        notification_ui_set_x(s_notif_ui, target_px);
    }
}

static clawd_anim_id_t status_to_anim(display_status_t status) {
    switch (status) {
    case DISPLAY_STATUS_SLEEPING:  return CLAWD_ANIM_SLEEPING;
    case DISPLAY_STATUS_IDLE:      return CLAWD_ANIM_IDLE;
    case DISPLAY_STATUS_THINKING:  return CLAWD_ANIM_THINKING;
    case DISPLAY_STATUS_WORKING_1: return CLAWD_ANIM_TYPING;
    case DISPLAY_STATUS_WORKING_2: return CLAWD_ANIM_JUGGLING;
    case DISPLAY_STATUS_WORKING_3: return CLAWD_ANIM_BUILDING;
    case DISPLAY_STATUS_CONFUSED:  return CLAWD_ANIM_CONFUSED;
    case DISPLAY_STATUS_SWEEPING:  return CLAWD_ANIM_SWEEPING;
    default:                       return CLAWD_ANIM_IDLE;
    }
}

/* ---------- Transition ---------- */

static void transition_to(ui_state_t new_state)
{
    if (new_state == s_state) return;

    ui_state_t old_state = s_state;
    s_state = new_state;

    switch (new_state) {
    case UI_STATE_FULL_IDLE:
        scene_animate_width(SCENE_FULL_WIDTH, SCENE_ANIM_MS);
        notification_ui_show(s_notif_ui, false, 0);

        scene_set_time_visible(s_scene, true);

        /* Don't overwrite a oneshot animation (happy/alert) */
        if (!scene_is_playing_oneshot(s_scene)) {
            scene_set_clawd_anim(s_scene, status_to_anim(s_display_status));
        }
        scene_set_fallback_anim(s_scene, status_to_anim(s_display_status));

        s_last_activity_tick = lv_tick_get();
        break;

    case UI_STATE_NOTIFICATION:
        scene_animate_width(SCENE_NOTIF_WIDTH,
                            old_state == UI_STATE_FULL_IDLE ? SCENE_ANIM_MS : 0);
        notification_ui_show(s_notif_ui, true, 300);

        scene_set_time_visible(s_scene, false);
        scene_set_clawd_anim(s_scene, CLAWD_ANIM_ALERT);

        s_last_activity_tick = lv_tick_get();
        break;

    case UI_STATE_DISCONNECTED:
        scene_animate_width(SCENE_FULL_WIDTH, SCENE_ANIM_MS);
        notification_ui_show(s_notif_ui, false, 0);

        scene_set_time_visible(s_scene, false);
        scene_set_clawd_anim(s_scene, CLAWD_ANIM_DISCONNECTED);
        break;
    }
}

/* ---------- Init ---------- */

void ui_manager_init(void)
{
    _lock_init(&s_lock);
    notif_store_init(&s_store);

    lv_obj_t *screen = lv_screen_active();
    lv_obj_set_style_bg_opa(screen, LV_OPA_COVER, 0);
    lv_obj_set_style_bg_color(screen, lv_color_hex(0x0f1320), 0);

    /* Create scene (left panel — starts full width) */
    s_scene = scene_create(screen);
    scene_set_width(s_scene, SCENE_FULL_WIDTH, 0);
    scene_set_clawd_anim(s_scene, CLAWD_ANIM_DISCONNECTED);

    /* Create notification UI (right panel — starts hidden) */
    s_notif_ui = notification_ui_create(screen);

    s_state = UI_STATE_DISCONNECTED;
    s_connected = false;
    s_last_activity_tick = lv_tick_get();
    s_last_minute = -1;

    rgb_led_init();

    ESP_LOGI(TAG, "UI manager initialized with scene + notification panel");
}

/* ---------- Event handling ---------- */

void ui_manager_handle_event(const ble_evt_t *evt)
{
    _lock_acquire(&s_lock);

    switch (evt->type) {
    case BLE_EVT_CONNECTED:
        ESP_LOGI(TAG, "Connected");
        s_connected = true;
        /* Transition based on notification count */
        if (notif_store_count(&s_store) > 0) {
            transition_to(UI_STATE_NOTIFICATION);
        } else {
            transition_to(UI_STATE_FULL_IDLE);
        }
        break;

    case BLE_EVT_DISCONNECTED:
        ESP_LOGI(TAG, "Disconnected");
        s_connected = false;
        notif_store_clear(&s_store);
        transition_to(UI_STATE_DISCONNECTED);
        break;

    case BLE_EVT_NOTIF_ADD:
        ESP_LOGI(TAG, "Add: %s (%s)", evt->id, evt->project);
        notif_store_add(&s_store, evt->id, evt->project, evt->message);
        notification_ui_rebuild(s_notif_ui, &s_store);
        /* Show new notification in expanded hero view, then auto-collapse */
        notification_ui_trigger_hero(s_notif_ui);
        /* Flash RGB LED behind acrylic */
        if (evt->alert) {
            rgb_led_flash_error();
        } else {
            rgb_led_flash(255, 140, 30, 800);
        }

        if (s_state != UI_STATE_NOTIFICATION) {
            transition_to(UI_STATE_NOTIFICATION);
        } else {
            /* Already in notification view — just play alert */
            scene_set_clawd_anim(s_scene, CLAWD_ANIM_ALERT);
        }
        s_last_activity_tick = lv_tick_get();
        break;

    case BLE_EVT_NOTIF_DISMISS:
        ESP_LOGI(TAG, "Dismiss: %s", evt->id);
        notif_store_dismiss(&s_store, evt->id);

        if (notif_store_count(&s_store) == 0) {
            transition_to(UI_STATE_FULL_IDLE);
        } else {
            notification_ui_rebuild(s_notif_ui, &s_store);
        }
        s_last_activity_tick = lv_tick_get();
        break;

    case BLE_EVT_NOTIF_CLEAR:
        ESP_LOGI(TAG, "Clear all");
        notif_store_clear(&s_store);
        transition_to(UI_STATE_FULL_IDLE);
        s_last_activity_tick = lv_tick_get();
        break;

    case BLE_EVT_SET_SESSIONS: {
        ESP_LOGI(TAG, "Set sessions: %d anims (anim0=%d), %d subagents, %d overflow",
                 evt->session_anim_count,
                 evt->session_anim_count > 0 ? evt->session_anims[0] : -1,
                 evt->subagent_count, evt->session_overflow);

        /* Wake from sleep if needed */
        if (s_display_status == DISPLAY_STATUS_SLEEPING) {
            display_set_brightness(config_store_get_brightness());
        }
        s_display_status = DISPLAY_STATUS_IDLE;

        /* Always use scene_set_sessions() — it has a single-session fast
         * path that updates slot 0 in place (same sprite object, same
         * Z-order, oneshot protection) so positioning is identical to the
         * legacy scene_set_clawd_anim() path.  No shortcut needed. */
        scene_set_sessions(s_scene,
            evt->session_anims, evt->session_ids,
            evt->session_anim_count, evt->subagent_count, evt->session_overflow);

        s_last_activity_tick = lv_tick_get();
        break;
    }

    case BLE_EVT_SET_STATUS: {
        display_status_t new_status = (display_status_t)evt->status;
        ESP_LOGI(TAG, "Set status: %d", new_status);
        display_status_t old_status = s_display_status;
        s_display_status = new_status;

        clawd_anim_id_t anim = status_to_anim(new_status);
        scene_set_fallback_anim(s_scene, anim);

        /* Handle backlight for sleep/wake */
        if (new_status == DISPLAY_STATUS_SLEEPING && old_status != DISPLAY_STATUS_SLEEPING) {
            display_set_brightness(0);
        } else if (new_status != DISPLAY_STATUS_SLEEPING && old_status == DISPLAY_STATUS_SLEEPING) {
            display_set_brightness(config_store_get_brightness());
        }

        /* Don't interrupt a playing oneshot — the fallback will take effect when it finishes */
        if (!scene_is_playing_oneshot(s_scene)) {
            scene_set_clawd_anim(s_scene, anim);
        }

        s_last_activity_tick = lv_tick_get();
        break;
    }
    }

    _lock_release(&s_lock);
}

#ifdef SIMULATOR
void ui_manager_get_anim_info(int *frame_count, int *frame_ms)
{
    scene_get_anim_info(s_scene, frame_count, frame_ms);
}

int ui_manager_get_frame_idx(void)
{
    return scene_get_frame_idx(s_scene);
}

scene_t *ui_manager_get_scene(void)
{
    return s_scene;
}
#endif

/* ---------- Tick ---------- */

void ui_manager_tick(void)
{
    _lock_acquire(&s_lock);

    /* Scene animation tick (sprite frame advance, star twinkle) */
    scene_tick(s_scene);

    /* Time update: check once per tick, update when minute changes */
    if (s_state != UI_STATE_NOTIFICATION && s_state != UI_STATE_DISCONNECTED) {
        struct timeval tv;
        gettimeofday(&tv, NULL);
        struct tm tm;
        localtime_r(&tv.tv_sec, &tm);
        int cur_minute = tm.tm_hour * 60 + tm.tm_min;
        if (cur_minute != s_last_minute) {
            s_last_minute = cur_minute;
            scene_update_time(s_scene, tm.tm_hour, tm.tm_min);
        }
    }

    /* LVGL timer handler */
    lv_timer_handler();

    _lock_release(&s_lock);
}

