#pragma once
#include "lvgl.h"

typedef struct scene_t scene_t;

typedef enum {
    CLAWD_ANIM_IDLE,
    CLAWD_ANIM_ALERT,
    CLAWD_ANIM_HAPPY,
    CLAWD_ANIM_SLEEPING,
    CLAWD_ANIM_DISCONNECTED,
    CLAWD_ANIM_THINKING,
    CLAWD_ANIM_TYPING,
    CLAWD_ANIM_JUGGLING,
    CLAWD_ANIM_BUILDING,
    CLAWD_ANIM_CONFUSED,
    CLAWD_ANIM_DIZZY,
    CLAWD_ANIM_SWEEPING,
    CLAWD_ANIM_WALKING,     /* crab-walking sideways (placeholder uses idle sprite) */
    CLAWD_ANIM_GOING_AWAY,  /* burrowing into ground (oneshot, session exit) */
    CLAWD_ANIM_MINI_CLAWD,  /* tiny 12x12 crab for HUD subagent counter */
} clawd_anim_id_t;

scene_t *scene_create(lv_obj_t *parent);
void scene_set_width(scene_t *scene, int width_px, int anim_ms);
void scene_set_clawd_anim(scene_t *scene, clawd_anim_id_t anim);
void scene_set_fallback_anim(scene_t *scene, clawd_anim_id_t anim);
void scene_set_time_visible(scene_t *scene, bool visible);
void scene_update_time(scene_t *scene, int hour, int minute);
void scene_tick(scene_t *scene);
bool scene_is_playing_oneshot(scene_t *scene);
void scene_set_sessions(scene_t *scene, const uint8_t *anims, const uint16_t *ids,
                        int count, uint8_t subagent_count, uint8_t overflow);
#ifdef SIMULATOR
void scene_get_anim_info(scene_t *scene, int *frame_count, int *frame_ms);
int scene_get_frame_idx(scene_t *scene);
const char *anim_id_to_name(clawd_anim_id_t id);
char *scene_get_state_json(scene_t *scene);
#endif
