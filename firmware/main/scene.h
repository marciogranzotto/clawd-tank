#pragma once
#include "lvgl.h"

typedef struct scene_t scene_t;

typedef enum {
    CLAWD_ANIM_IDLE,
    CLAWD_ANIM_ALERT,
    CLAWD_ANIM_HAPPY,
    CLAWD_ANIM_SLEEPING,
    CLAWD_ANIM_DISCONNECTED,
} clawd_anim_id_t;

scene_t *scene_create(lv_obj_t *parent);
void scene_set_width(scene_t *scene, int width_px, int anim_ms);
void scene_set_clawd_anim(scene_t *scene, clawd_anim_id_t anim);
void scene_set_time_visible(scene_t *scene, bool visible);
void scene_update_time(scene_t *scene, int hour, int minute);
void scene_set_ble_icon_visible(scene_t *scene, bool visible);
void scene_tick(scene_t *scene);
bool scene_is_playing_oneshot(scene_t *scene);
