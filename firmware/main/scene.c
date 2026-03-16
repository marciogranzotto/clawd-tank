#include "scene.h"
#include <stdlib.h>
#include <stdio.h>
#include <string.h>

/* ---------- Sprite data includes ---------- */

#ifdef LV_LVGL_H_INCLUDE_SIMPLE
#include "lvgl.h"
#else
#include "lvgl.h"
#endif

#include "assets/sprite_idle.h"
#include "assets/sprite_alert.h"
#include "assets/sprite_happy.h"
#include "assets/sprite_sleeping.h"
#include "assets/sprite_disconnected.h"
#include "assets/sprite_thinking.h"
#include "assets/sprite_typing.h"
#include "assets/sprite_juggling.h"
#include "assets/sprite_building.h"
#include "assets/sprite_confused.h"
#include "assets/sprite_sweeping.h"
#include "assets/sprite_walking.h"
#include "rle_sprite.h"
#include "pixel_font.h"

/* ---------- Constants ---------- */

#define SCENE_HEIGHT       172
#define GRASS_HEIGHT       14
#define STAR_COUNT         6
#define STAR_TWINKLE_MIN   2000
#define STAR_TWINKLE_MAX   4000
#define TRANSPARENT_KEY    0x18C5

/* Frame timing in ms per animation */
#define IDLE_FRAME_MS      (1000 / 6)   /* 167ms @ 6fps */
#define ALERT_FRAME_MS     (1000 / 10)  /* 100ms @ 10fps */
#define HAPPY_FRAME_MS     (1000 / 10)  /* 100ms @ 10fps */
#define SLEEPING_FRAME_MS  (1000 / 6)   /* 167ms @ 6fps */
#define DISCONN_FRAME_MS   (1000 / 6)   /* 167ms @ 6fps */
#define THINKING_FRAME_MS  (1000 / 8)   /* 125ms @ 8fps */
#define TYPING_FRAME_MS    (1000 / 8)   /* 125ms @ 8fps */
#define JUGGLING_FRAME_MS  (1000 / 8)   /* 125ms @ 8fps */
#define BUILDING_FRAME_MS  (1000 / 8)   /* 125ms @ 8fps */
#define CONFUSED_FRAME_MS  (1000 / 8)   /* 125ms @ 8fps */
#define SWEEPING_FRAME_MS  (1000 / 8)   /* 125ms @ 8fps */

/* ---------- Animation metadata ---------- */

typedef struct {
    const uint16_t *rle_data;
    const uint32_t *frame_offsets;
    int frame_count;
    int frame_ms;
    bool looping;
    int width;
    int height;
    int y_offset;
} anim_def_t;

static const anim_def_t anim_defs[] = {
    [CLAWD_ANIM_IDLE] = {
        .rle_data = idle_rle_data,
        .frame_offsets = idle_frame_offsets,
        .frame_count = IDLE_FRAME_COUNT,
        .frame_ms = IDLE_FRAME_MS,
        .looping = true,
        .width = IDLE_WIDTH,
        .height = IDLE_HEIGHT,
        .y_offset = 8,
    },
    [CLAWD_ANIM_ALERT] = {
        .rle_data = alert_rle_data,
        .frame_offsets = alert_frame_offsets,
        .frame_count = ALERT_FRAME_COUNT,
        .frame_ms = ALERT_FRAME_MS,
        .looping = false,
        .width = ALERT_WIDTH,
        .height = ALERT_HEIGHT,
        .y_offset = 8,
    },
    [CLAWD_ANIM_HAPPY] = {
        .rle_data = happy_rle_data,
        .frame_offsets = happy_frame_offsets,
        .frame_count = HAPPY_FRAME_COUNT,
        .frame_ms = HAPPY_FRAME_MS,
        .looping = false,
        .width = HAPPY_WIDTH,
        .height = HAPPY_HEIGHT,
        .y_offset = 28,
    },
    [CLAWD_ANIM_SLEEPING] = {
        .rle_data = sleeping_rle_data,
        .frame_offsets = sleeping_frame_offsets,
        .frame_count = SLEEPING_FRAME_COUNT,
        .frame_ms = SLEEPING_FRAME_MS,
        .looping = true,
        .width = SLEEPING_WIDTH,
        .height = SLEEPING_HEIGHT,
        .y_offset = 8,
    },
    [CLAWD_ANIM_DISCONNECTED] = {
        .rle_data = disconnected_rle_data,
        .frame_offsets = disconnected_frame_offsets,
        .frame_count = DISCONNECTED_FRAME_COUNT,
        .frame_ms = DISCONN_FRAME_MS,
        .looping = true,
        .width = DISCONNECTED_WIDTH,
        .height = DISCONNECTED_HEIGHT,
        .y_offset = 8,
    },
    [CLAWD_ANIM_THINKING] = {
        .rle_data = thinking_rle_data,
        .frame_offsets = thinking_frame_offsets,
        .frame_count = THINKING_FRAME_COUNT,
        .frame_ms = THINKING_FRAME_MS,
        .looping = true,
        .width = THINKING_WIDTH,
        .height = THINKING_HEIGHT,
        .y_offset = 8,
    },
    [CLAWD_ANIM_TYPING] = {
        .rle_data = typing_rle_data,
        .frame_offsets = typing_frame_offsets,
        .frame_count = TYPING_FRAME_COUNT,
        .frame_ms = TYPING_FRAME_MS,
        .looping = true,
        .width = TYPING_WIDTH,
        .height = TYPING_HEIGHT,
        .y_offset = 8,
    },
    [CLAWD_ANIM_JUGGLING] = {
        .rle_data = juggling_rle_data,
        .frame_offsets = juggling_frame_offsets,
        .frame_count = JUGGLING_FRAME_COUNT,
        .frame_ms = JUGGLING_FRAME_MS,
        .looping = true,
        .width = JUGGLING_WIDTH,
        .height = JUGGLING_HEIGHT,
        .y_offset = 8,
    },
    [CLAWD_ANIM_BUILDING] = {
        .rle_data = building_rle_data,
        .frame_offsets = building_frame_offsets,
        .frame_count = BUILDING_FRAME_COUNT,
        .frame_ms = BUILDING_FRAME_MS,
        .looping = true,
        .width = BUILDING_WIDTH,
        .height = BUILDING_HEIGHT,
        .y_offset = 8,
    },
    [CLAWD_ANIM_CONFUSED] = {
        .rle_data = confused_rle_data,
        .frame_offsets = confused_frame_offsets,
        .frame_count = CONFUSED_FRAME_COUNT,
        .frame_ms = CONFUSED_FRAME_MS,
        .looping = true,
        .width = CONFUSED_WIDTH,
        .height = CONFUSED_HEIGHT,
        .y_offset = 8,
    },
    [CLAWD_ANIM_SWEEPING] = {
        .rle_data = sweeping_rle_data,
        .frame_offsets = sweeping_frame_offsets,
        .frame_count = SWEEPING_FRAME_COUNT,
        .frame_ms = SWEEPING_FRAME_MS,
        .looping = false,
        .width = SWEEPING_WIDTH,
        .height = SWEEPING_HEIGHT,
        .y_offset = 8,
    },
    [CLAWD_ANIM_WALKING] = {
        .rle_data = walking_rle_data,
        .frame_offsets = walking_frame_offsets,
        .frame_count = WALKING_FRAME_COUNT,
        .frame_ms = (1000 / 8),  /* 125ms @ 8fps */
        .looping = true,
        .width = WALKING_WIDTH,
        .height = WALKING_HEIGHT,
        .y_offset = 8,
    },
};

/* ---------- Multi-slot support ---------- */

#define MAX_SLOTS 4

typedef struct {
    lv_obj_t *sprite_img;
    lv_image_dsc_t frame_dsc;
    uint8_t *frame_buf;
    int frame_buf_size;
    clawd_anim_id_t cur_anim;
    clawd_anim_id_t fallback_anim;
    int frame_idx;
    uint32_t last_frame_tick;
    uint16_t display_id;   /* stable ID from daemon, for diffing */
    int x_off;             /* last alignment x offset (for re-align after oneshot) */
    bool active;
    bool walking_in;       /* true while walk-in slide animation is running */
} clawd_slot_t;

/* ---------- Star config ---------- */

static const struct {
    int x, y, size;
    lv_color_t color;
} star_cfg[STAR_COUNT] = {
    { 10,  8, 2, {.red = 0xFF, .green = 0xFF, .blue = 0x88} },  /* #ffff88 */
    { 45, 15, 3, {.red = 0x88, .green = 0xCC, .blue = 0xFF} },  /* #88ccff */
    { 80, 22, 2, {.red = 0xFF, .green = 0xAA, .blue = 0x88} },  /* #ffaa88 */
    {120,  5, 4, {.red = 0xAA, .green = 0xCC, .blue = 0xFF} },  /* #aaccff */
    {150, 18, 2, {.red = 0xFF, .green = 0xDD, .blue = 0x88} },  /* #ffdd88 */
    {160, 30, 3, {.red = 0x88, .green = 0xFF, .blue = 0xCC} },  /* #88ffcc */
};

/* ---------- Scene struct ---------- */

struct scene_t {
    lv_obj_t *container;

    /* Sky */
    lv_obj_t *sky;

    /* Stars */
    lv_obj_t *stars[STAR_COUNT];
    uint32_t star_next_toggle[STAR_COUNT];

    /* Grass */
    lv_obj_t *grass;

    /* Clawd sprite slots (1 per visible session) */
    clawd_slot_t slots[MAX_SLOTS];
    int active_slot_count;
    bool narrow;  /* true when scene is in notification-width mode (107px) */

    /* Time label */
    lv_obj_t *time_label;

    /* No-connection label */
    lv_obj_t *noconn_label;

    /* HUD overlay */
    lv_obj_t *hud_canvas;        /* canvas for pixel font rendering */
    uint8_t hud_subagent_count;
    uint8_t hud_overflow;
};

/* ---------- Helpers ---------- */

static void ensure_frame_buf(clawd_slot_t *slot, int w, int h)
{
    int needed = w * h * 4; /* ARGB8888 */
    if (slot->frame_buf && slot->frame_buf_size >= needed) return;
    free(slot->frame_buf);
    slot->frame_buf = malloc(needed);
    slot->frame_buf_size = slot->frame_buf ? needed : 0;
}

static void decode_and_apply_frame(clawd_slot_t *slot)
{
    const anim_def_t *def = &anim_defs[slot->cur_anim];
    int idx = slot->frame_idx;
    if (idx >= def->frame_count) idx = def->frame_count - 1;

    int w = def->width;
    int h = def->height;
    ensure_frame_buf(slot, w, h);
    if (!slot->frame_buf) return;

    /* Decompress this frame's RLE directly to ARGB8888 */
    const uint16_t *frame_rle = &def->rle_data[def->frame_offsets[idx]];
    rle_decode_argb8888(frame_rle, slot->frame_buf, w * h, TRANSPARENT_KEY);

    /* Update the LVGL image descriptor */
    slot->frame_dsc.header.magic = LV_IMAGE_HEADER_MAGIC;
    slot->frame_dsc.header.w = w;
    slot->frame_dsc.header.h = h;
    slot->frame_dsc.header.cf = LV_COLOR_FORMAT_ARGB8888;
    slot->frame_dsc.header.stride = w * 4;
    slot->frame_dsc.data = slot->frame_buf;
    slot->frame_dsc.data_size = w * h * 4;

    lv_image_set_src(slot->sprite_img, &slot->frame_dsc);
}

static uint32_t random_range(uint32_t min_val, uint32_t max_val)
{
    return min_val + (lv_rand(0, max_val - min_val));
}

static void width_anim_cb(void *var, int32_t val)
{
    lv_obj_set_width((lv_obj_t *)var, val);
}

/* ---------- Animation helpers for transitions ---------- */

static void set_sprite_opa(void *obj, int32_t v) {
    lv_obj_set_style_opa(obj, (lv_opa_t)v, 0);
}

static void fade_complete_cb(lv_anim_t *a) {
    lv_obj_t *obj = (lv_obj_t *)a->var;
    lv_obj_delete(obj);
}

static void walk_in_complete_cb(lv_anim_t *a) {
    /* The anim var is the sprite_img. We need to find the slot that owns it.
     * Walk through the scene's slots (stored in the container's user_data). */
    lv_obj_t *sprite = (lv_obj_t *)a->var;
    lv_obj_t *container = lv_obj_get_parent(sprite);
    scene_t *s = (scene_t *)lv_obj_get_user_data(container);
    if (!s) return;
    for (int i = 0; i < MAX_SLOTS; i++) {
        if (s->slots[i].sprite_img == sprite && s->slots[i].active) {
            s->slots[i].walking_in = false;
            clawd_anim_id_t target = s->slots[i].fallback_anim;
            if (s->slots[i].cur_anim == CLAWD_ANIM_WALKING && target != CLAWD_ANIM_WALKING) {
                s->slots[i].cur_anim = target;
                s->slots[i].frame_idx = 0;
                s->slots[i].last_frame_tick = lv_tick_get();
                decode_and_apply_frame(&s->slots[i]);
                const anim_def_t *def = &anim_defs[target];
                lv_obj_set_size(sprite, def->width, def->height);
                lv_obj_align(sprite, LV_ALIGN_BOTTOM_MID, s->slots[i].x_off, def->y_offset);
            }
            break;
        }
    }
}

static void slide_slot_to(clawd_slot_t *slot, int target_x, int duration_ms) {
    lv_anim_t a;
    lv_anim_init(&a);
    lv_anim_set_var(&a, slot->sprite_img);
    lv_anim_set_values(&a, lv_obj_get_x(slot->sprite_img), target_x);
    lv_anim_set_duration(&a, duration_ms);
    lv_anim_set_path_cb(&a, lv_anim_path_ease_out);
    lv_anim_set_exec_cb(&a, (lv_anim_exec_xcb_t)lv_obj_set_x);
    lv_anim_start(&a);
}

/* ---------- Slot helpers ---------- */

static void scene_activate_slot(scene_t *s, int idx, clawd_anim_id_t anim)
{
    clawd_slot_t *slot = &s->slots[idx];
    if (!slot->sprite_img) {
        slot->sprite_img = lv_image_create(s->container);
        lv_image_set_inner_align(slot->sprite_img, LV_IMAGE_ALIGN_CENTER);
    }
    slot->active = true;
    slot->cur_anim = anim;
    slot->fallback_anim = anim;
    slot->frame_idx = 0;
    slot->last_frame_tick = lv_tick_get();
    slot->x_off = 0;
    slot->walking_in = false;
    decode_and_apply_frame(slot);
    const anim_def_t *def = &anim_defs[anim];
    lv_obj_set_size(slot->sprite_img, def->width, def->height);
    lv_obj_align(slot->sprite_img, LV_ALIGN_BOTTOM_MID, 0, def->y_offset);
    lv_obj_clear_flag(slot->sprite_img, LV_OBJ_FLAG_HIDDEN);
    lv_obj_set_style_opa(slot->sprite_img, LV_OPA_COVER, 0);
}

static void scene_deactivate_slot(scene_t *s, int idx)
{
    clawd_slot_t *slot = &s->slots[idx];
    slot->active = false;
    if (slot->sprite_img) {
        lv_obj_add_flag(slot->sprite_img, LV_OBJ_FLAG_HIDDEN);
    }
    free(slot->frame_buf);
    slot->frame_buf = NULL;
    slot->frame_buf_size = 0;
}

/* ---------- Create ---------- */

scene_t *scene_create(lv_obj_t *parent)
{
    scene_t *s = calloc(1, sizeof(scene_t));
    if (!s) return NULL;

    /* Container */
    s->container = lv_obj_create(parent);
    lv_obj_remove_style_all(s->container);
    lv_obj_set_size(s->container, lv_pct(100), SCENE_HEIGHT);
    lv_obj_set_style_clip_corner(s->container, true, 0);
    lv_obj_set_scrollbar_mode(s->container, LV_SCROLLBAR_MODE_OFF);
    lv_obj_clear_flag(s->container, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_user_data(s->container, s);  /* for walk_in_complete_cb */

    /* Sky background — gradient top to bottom */
    s->sky = lv_obj_create(s->container);
    lv_obj_remove_style_all(s->sky);
    lv_obj_set_size(s->sky, lv_pct(100), SCENE_HEIGHT);
    lv_obj_set_style_bg_opa(s->sky, LV_OPA_COVER, 0);
    lv_obj_set_style_bg_color(s->sky, lv_color_hex(0x0a0e1a), 0);
    lv_obj_set_style_bg_grad_color(s->sky, lv_color_hex(0x1a1a2e), 0);
    lv_obj_set_style_bg_grad_dir(s->sky, LV_GRAD_DIR_VER, 0);

    /* Stars */
    uint32_t now = lv_tick_get();
    for (int i = 0; i < STAR_COUNT; i++) {
        s->stars[i] = lv_obj_create(s->container);
        lv_obj_remove_style_all(s->stars[i]);
        lv_obj_set_size(s->stars[i], star_cfg[i].size, star_cfg[i].size);
        lv_obj_set_pos(s->stars[i], star_cfg[i].x, star_cfg[i].y);
        lv_obj_set_style_bg_opa(s->stars[i], LV_OPA_COVER, 0);
        lv_obj_set_style_bg_color(s->stars[i], star_cfg[i].color, 0);
        lv_obj_set_style_radius(s->stars[i], star_cfg[i].size / 2, 0);
        s->star_next_toggle[i] = now + random_range(STAR_TWINKLE_MIN, STAR_TWINKLE_MAX);
    }

    /* Grass strip at bottom */
    s->grass = lv_obj_create(s->container);
    lv_obj_remove_style_all(s->grass);
    lv_obj_set_size(s->grass, lv_pct(100), GRASS_HEIGHT);
    lv_obj_align(s->grass, LV_ALIGN_BOTTOM_MID, 0, 0);
    lv_obj_set_style_bg_opa(s->grass, LV_OPA_COVER, 0);
    lv_obj_set_style_bg_color(s->grass, lv_color_hex(0x2d4a2d), 0);
    lv_obj_set_style_bg_grad_color(s->grass, lv_color_hex(0x1a331a), 0);
    lv_obj_set_style_bg_grad_dir(s->grass, LV_GRAD_DIR_VER, 0);

    /* Grass tufts — small lighter rectangles */
    static const struct { int x; int w; } tufts[] = {
        {8, 3}, {25, 2}, {50, 4}, {78, 2}, {100, 3}, {130, 2}, {155, 3},
    };
    for (int i = 0; i < (int)(sizeof(tufts) / sizeof(tufts[0])); i++) {
        lv_obj_t *tuft = lv_obj_create(s->grass);
        lv_obj_remove_style_all(tuft);
        lv_obj_set_size(tuft, tufts[i].w, 3);
        lv_obj_set_pos(tuft, tufts[i].x, 0);
        lv_obj_set_style_bg_opa(tuft, LV_OPA_COVER, 0);
        lv_obj_set_style_bg_color(tuft, lv_color_hex(0x3d6a3d), 0);
    }

    /* Clawd sprite slots — initialize all, activate slot 0 */
    for (int i = 0; i < MAX_SLOTS; i++) {
        s->slots[i].active = false;
        s->slots[i].sprite_img = NULL;
        s->slots[i].frame_buf = NULL;
        s->slots[i].frame_buf_size = 0;
        s->slots[i].display_id = 0;
    }
    s->active_slot_count = 1;
    s->narrow = false;
    scene_activate_slot(s, 0, CLAWD_ANIM_IDLE);

    /* Time label — top-right */
    s->time_label = lv_label_create(s->container);
    lv_obj_set_style_text_font(s->time_label, &lv_font_montserrat_18, 0);
    lv_obj_set_style_text_color(s->time_label, lv_color_hex(0x4466aa), 0);
    lv_obj_align(s->time_label, LV_ALIGN_TOP_MID, 0, 4);
    lv_label_set_text(s->time_label, "");
    lv_obj_add_flag(s->time_label, LV_OBJ_FLAG_HIDDEN);

    /* No-connection label — top center */
    s->noconn_label = lv_label_create(s->container);
    lv_obj_set_style_text_font(s->noconn_label, &lv_font_montserrat_14, 0);
    lv_obj_set_style_text_color(s->noconn_label, lv_color_hex(0x556677), 0);
    lv_obj_align(s->noconn_label, LV_ALIGN_TOP_MID, 0, 4);
    lv_label_set_text(s->noconn_label, "No connection");
    lv_obj_add_flag(s->noconn_label, LV_OBJ_FLAG_HIDDEN);

    /* HUD canvas — for subagent counter and overflow badge */
    s->hud_canvas = lv_canvas_create(s->container);
    /* Canvas buffer for pixel font: 80x12 pixels @ ARGB8888 */
    static uint8_t hud_buf[80 * 12 * 4];
    lv_canvas_set_buffer(s->hud_canvas, hud_buf, 80, 12, LV_COLOR_FORMAT_ARGB8888);
    lv_obj_align(s->hud_canvas, LV_ALIGN_TOP_LEFT, 4, 4);
    lv_obj_add_flag(s->hud_canvas, LV_OBJ_FLAG_HIDDEN);
    s->hud_subagent_count = 0;
    s->hud_overflow = 0;

    return s;
}

/* ---------- Multi-session X positions ---------- */

static const int x_centers[][4] = {
    {160},              /* 1 session */
    {107, 213},         /* 2 sessions */
    {80, 160, 240},     /* 3 sessions */
    {64, 128, 192, 256} /* 4 sessions */
};

/* ---------- Width animation ---------- */

void scene_set_width(scene_t *scene, int width_px, int anim_ms)
{
    if (!scene) return;

    bool was_narrow = scene->narrow;
    scene->narrow = (width_px < 320);

    /* In narrow mode, hide all slots except 0; restore when going wide */
    if (scene->narrow && !was_narrow) {
        for (int i = 1; i < MAX_SLOTS; i++) {
            if (scene->slots[i].active && scene->slots[i].sprite_img) {
                lv_obj_add_flag(scene->slots[i].sprite_img, LV_OBJ_FLAG_HIDDEN);
            }
        }
        /* Kill any orphan sprites from in-progress fade-out animations.
         * These are LVGL children of the container that aren't tracked in
         * any slot — they'd be visible within the narrow 107px container.
         * Check each child against all known scene elements. */
        uint32_t child_cnt = lv_obj_get_child_count(scene->container);
        for (int ci = (int)child_cnt - 1; ci >= 0; ci--) {
            lv_obj_t *child = lv_obj_get_child(scene->container, ci);
            /* Check if this child is a known scene element */
            bool is_known = (child == scene->sky || child == scene->grass ||
                             child == scene->time_label || child == scene->noconn_label ||
                             child == scene->hud_canvas);
            if (!is_known) {
                for (int si = 0; si < STAR_COUNT && !is_known; si++)
                    if (scene->stars[si] == child) is_known = true;
                for (int si = 0; si < MAX_SLOTS && !is_known; si++)
                    if (scene->slots[si].sprite_img == child) is_known = true;
            }
            if (!is_known) {
                lv_anim_delete(child, set_sprite_opa);
                lv_obj_delete(child);
            }
        }
        /* Re-center slot 0 for narrow container */
        if (scene->slots[0].active) {
            scene->slots[0].x_off = 0;
            const anim_def_t *def = &anim_defs[scene->slots[0].cur_anim];
            lv_obj_align(scene->slots[0].sprite_img, LV_ALIGN_BOTTOM_MID, 0, def->y_offset);
        }
    } else if (!scene->narrow && was_narrow) {
        /* Going wide: unhide slots 1+ and walk ALL slots to their
         * correct multi-session positions (slot 0 was centered for narrow). */
        int cnt = scene->active_slot_count;
        for (int i = 0; i < cnt; i++) {
            if (!scene->slots[i].active || !scene->slots[i].sprite_img) continue;
            if (i > 0) lv_obj_clear_flag(scene->slots[i].sprite_img, LV_OBJ_FLAG_HIDDEN);

            int target_x_off = (cnt >= 2) ? x_centers[cnt - 1][i] - 160 : 0;
            int old_x_off = scene->slots[i].x_off;
            scene->slots[i].x_off = target_x_off;

            if (old_x_off != target_x_off && !scene->slots[i].walking_in) {
                /* Walk to new position */
                clawd_anim_id_t target_anim = scene->slots[i].cur_anim;
                scene->slots[i].fallback_anim = target_anim;
                scene->slots[i].cur_anim = CLAWD_ANIM_WALKING;
                scene->slots[i].frame_idx = 0;
                scene->slots[i].last_frame_tick = lv_tick_get();
                decode_and_apply_frame(&scene->slots[i]);
                const anim_def_t *walk_def = &anim_defs[CLAWD_ANIM_WALKING];
                lv_obj_set_size(scene->slots[i].sprite_img, walk_def->width, walk_def->height);

                scene->slots[i].walking_in = true;
                lv_anim_t a;
                lv_anim_init(&a);
                lv_anim_set_var(&a, scene->slots[i].sprite_img);
                lv_anim_set_values(&a, old_x_off, target_x_off);
                lv_anim_set_duration(&a, 600);
                lv_anim_set_path_cb(&a, lv_anim_path_ease_out);
                lv_anim_set_exec_cb(&a, (lv_anim_exec_xcb_t)lv_obj_set_x);
                lv_anim_set_completed_cb(&a, walk_in_complete_cb);
                lv_anim_start(&a);
            } else if (!scene->slots[i].walking_in) {
                /* Same position or single session — just realign */
                const anim_def_t *def = &anim_defs[scene->slots[i].cur_anim];
                lv_obj_set_size(scene->slots[i].sprite_img, def->width, def->height);
                lv_obj_align(scene->slots[i].sprite_img, LV_ALIGN_BOTTOM_MID,
                             target_x_off, def->y_offset);
            }
        }
    }

    if (anim_ms <= 0) {
        lv_obj_set_width(scene->container, width_px);
        return;
    }

    lv_anim_t a;
    lv_anim_init(&a);
    lv_anim_set_var(&a, scene->container);
    lv_anim_set_values(&a, lv_obj_get_width(scene->container), width_px);
    lv_anim_set_duration(&a, anim_ms);
    lv_anim_set_exec_cb(&a, width_anim_cb);
    lv_anim_set_path_cb(&a, lv_anim_path_ease_out);
    lv_anim_start(&a);
}

/* ---------- Animation switching ---------- */

void scene_set_clawd_anim(scene_t *scene, clawd_anim_id_t anim)
{
    if (!scene) return;
    clawd_slot_t *slot = &scene->slots[0];
    if (!slot->active) return;
    if (anim == slot->cur_anim) return;

    slot->cur_anim = anim;
    slot->frame_idx = 0;
    slot->last_frame_tick = lv_tick_get();

    const anim_def_t *def = &anim_defs[anim];
    decode_and_apply_frame(slot);

    /* Force widget size to match sprite dimensions, then re-align */
    lv_obj_set_size(slot->sprite_img, def->width, def->height);
    lv_obj_align(slot->sprite_img, LV_ALIGN_BOTTOM_MID, 0, def->y_offset);
    lv_obj_update_layout(slot->sprite_img);
    printf("[scene] set_clawd_anim anim=%d size=%dx%d y_off=%d container=%dx%d pos=(%d,%d) w=%d h=%d\n",
           anim, def->width, def->height, def->y_offset,
           lv_obj_get_width(scene->container), lv_obj_get_height(scene->container),
           lv_obj_get_x(slot->sprite_img), lv_obj_get_y(slot->sprite_img),
           lv_obj_get_width(slot->sprite_img), lv_obj_get_height(slot->sprite_img));

    /* Disconnected state: desaturate + show no-connection label */
    if (anim == CLAWD_ANIM_DISCONNECTED) {
        lv_obj_set_style_image_recolor(slot->sprite_img, lv_color_hex(0x888888), 0);
        lv_obj_set_style_image_recolor_opa(slot->sprite_img, LV_OPA_30, 0);
        lv_obj_clear_flag(scene->noconn_label, LV_OBJ_FLAG_HIDDEN);
    } else {
        lv_obj_set_style_image_recolor_opa(slot->sprite_img, LV_OPA_TRANSP, 0);
        lv_obj_add_flag(scene->noconn_label, LV_OBJ_FLAG_HIDDEN);
    }
}

void scene_set_fallback_anim(scene_t *scene, clawd_anim_id_t anim)
{
    if (!scene) return;
    scene->slots[0].fallback_anim = anim;
}

/* ---------- Time ---------- */

void scene_set_time_visible(scene_t *scene, bool visible)
{
    if (!scene) return;
    if (visible)
        lv_obj_clear_flag(scene->time_label, LV_OBJ_FLAG_HIDDEN);
    else
        lv_obj_add_flag(scene->time_label, LV_OBJ_FLAG_HIDDEN);
}

void scene_update_time(scene_t *scene, int hour, int minute)
{
    if (!scene) return;
    lv_label_set_text_fmt(scene->time_label, "%02d:%02d", hour, minute);
}

/* ---------- Tick (call from UI loop) ---------- */

void scene_tick(scene_t *scene)
{
    if (!scene) return;

    uint32_t now = lv_tick_get();

    /* Advance sprite frames for all active slots */
    for (int i = 0; i < MAX_SLOTS; i++) {
        clawd_slot_t *slot = &scene->slots[i];
        if (!slot->active) continue;

        const anim_def_t *def = &anim_defs[slot->cur_anim];
        uint32_t elapsed = now - slot->last_frame_tick;
        if (elapsed >= (uint32_t)def->frame_ms) {
            slot->last_frame_tick = now;

            if (def->looping) {
                slot->frame_idx = (slot->frame_idx + 1) % def->frame_count;
            } else {
                if (slot->frame_idx < def->frame_count - 1) {
                    slot->frame_idx++;
                } else {
                    /* Oneshot finished — auto-return to fallback.
                     * Must update widget size and alignment since the fallback
                     * animation may have different dimensions than the oneshot. */
                    slot->cur_anim = slot->fallback_anim;
                    slot->frame_idx = 0;
                    slot->last_frame_tick = now;
                    const anim_def_t *fb = &anim_defs[slot->fallback_anim];
                    lv_obj_set_size(slot->sprite_img, fb->width, fb->height);
                    lv_obj_align(slot->sprite_img, LV_ALIGN_BOTTOM_MID,
                                 slot->x_off, fb->y_offset);
                }
            }
            decode_and_apply_frame(slot);
        }
    }

    /* Star twinkle */
    for (int i = 0; i < STAR_COUNT; i++) {
        if (now >= scene->star_next_toggle[i]) {
            lv_opa_t cur = lv_obj_get_style_bg_opa(scene->stars[i], 0);
            lv_opa_t next = (cur > LV_OPA_50) ? LV_OPA_30 : LV_OPA_COVER;
            lv_obj_set_style_bg_opa(scene->stars[i], next, 0);
            scene->star_next_toggle[i] = now + random_range(STAR_TWINKLE_MIN, STAR_TWINKLE_MAX);
        }
    }
}

/* ---------- Oneshot query ---------- */

bool scene_is_playing_oneshot(scene_t *scene)
{
    if (!scene) return false;
    clawd_slot_t *slot = &scene->slots[0];
    if (!slot->active) return false;
    const anim_def_t *def = &anim_defs[slot->cur_anim];
    if (def->looping) return false;
    return slot->frame_idx < def->frame_count - 1;
}

/* ---------- Multi-session positioning ---------- */

static int find_id_in(const uint16_t *ids, int count, uint16_t target)
{
    for (int i = 0; i < count; i++) {
        if (ids[i] == target) return i;
    }
    return -1;
}

/* ---------- HUD overlay ---------- */

static void scene_update_hud(scene_t *s, uint8_t subagent_count, uint8_t overflow) {
    s->hud_subagent_count = subagent_count;
    s->hud_overflow = overflow;

    if (subagent_count == 0 && overflow == 0) {
        lv_obj_add_flag(s->hud_canvas, LV_OBJ_FLAG_HIDDEN);
        return;
    }

    /* Clear canvas */
    lv_canvas_fill_bg(s->hud_canvas, lv_color_hex(0x000000), LV_OPA_TRANSP);

    int x = 0;

    if (subagent_count > 0) {
        /* Draw "xN" for subagent count */
        char buf[8];
        snprintf(buf, sizeof(buf), "x%d", subagent_count);
        pixel_font_draw(s->hud_canvas, buf, x, 1, 2, lv_color_hex(0x88ccff));
    }

    if (overflow > 0) {
        /* Draw "+N" for overflow at right side */
        char buf[8];
        snprintf(buf, sizeof(buf), "+%d", overflow);
        /* Position at right side of canvas */
        pixel_font_draw(s->hud_canvas, buf, 50, 1, 2, lv_color_hex(0xffaa88));
    }

    lv_obj_clear_flag(s->hud_canvas, LV_OBJ_FLAG_HIDDEN);
}

void scene_set_sessions(scene_t *s, const uint8_t *anims, const uint16_t *ids,
                        int count, uint8_t subagent_count, uint8_t overflow)
{
    if (!s) return;
    if (count < 1) count = 1;
    if (count > MAX_SLOTS) count = MAX_SLOTS;

    /* ------ Single-session fast path ------
     *
     * When count==1, update slot 0 IN PLACE rather than running the full
     * diffing/destroy/recreate cycle.  This guarantees identical behaviour
     * to scene_set_clawd_anim() — same LVGL image object, same Z-order,
     * same alignment maths — so single-session via set_sessions matches
     * the legacy set_status path pixel-for-pixel.
     *
     * The full diff path below is still used for count >= 2. */
    if (count == 1) {
        clawd_slot_t *slot = &s->slots[0];

        /* Ensure slot 0 is active (bootstrap from scene_create) */
        if (!slot->active || !slot->sprite_img) {
            scene_activate_slot(s, 0, (clawd_anim_id_t)anims[0]);
        }

        slot->display_id = ids[0];

        clawd_anim_id_t new_anim = (clawd_anim_id_t)anims[0];
        int old_x_off = slot->x_off;
        slot->x_off = 0;
        slot->fallback_anim = new_anim;

        /* Deactivate extra slots first (fade-out removed sessions) */
        for (int i = 1; i < MAX_SLOTS; i++) {
            if (s->slots[i].active) {
                if (s->narrow) {
                    /* Narrow: delete immediately */
                    if (s->slots[i].sprite_img) {
                        lv_anim_delete(s->slots[i].sprite_img, (lv_anim_exec_xcb_t)lv_obj_set_x);
                        lv_obj_delete(s->slots[i].sprite_img);
                        s->slots[i].sprite_img = NULL;
                    }
                    free(s->slots[i].frame_buf);
                    s->slots[i].frame_buf = NULL;
                    s->slots[i].frame_buf_size = 0;
                    s->slots[i].active = false;
                } else if (s->slots[i].sprite_img) {
                    /* Full width: fade out */
                    lv_anim_delete(s->slots[i].sprite_img, (lv_anim_exec_xcb_t)lv_obj_set_x);
                    lv_anim_t a;
                    lv_anim_init(&a);
                    lv_anim_set_var(&a, s->slots[i].sprite_img);
                    lv_anim_set_values(&a, LV_OPA_COVER, LV_OPA_TRANSP);
                    lv_anim_set_duration(&a, 400);
                    lv_anim_set_exec_cb(&a, set_sprite_opa);
                    lv_anim_set_completed_cb(&a, fade_complete_cb);
                    lv_anim_start(&a);
                    free(s->slots[i].frame_buf);
                    s->slots[i].frame_buf = NULL;
                    s->slots[i].frame_buf_size = 0;
                    s->slots[i].sprite_img = NULL; /* orphaned to fade_complete_cb */
                    s->slots[i].active = false;
                }
            }
        }

        if (slot->walking_in) {
            /* Already walking — just update fallback and target */
        } else if (old_x_off != 0 && !s->narrow) {
            /* Position changed (returning from multi-session to center) — walk */
            slot->cur_anim = CLAWD_ANIM_WALKING;
            slot->frame_idx = 0;
            slot->last_frame_tick = lv_tick_get();
            decode_and_apply_frame(slot);
            const anim_def_t *walk_def = &anim_defs[CLAWD_ANIM_WALKING];
            lv_obj_set_size(slot->sprite_img, walk_def->width, walk_def->height);

            slot->walking_in = true;
            lv_anim_t a;
            lv_anim_init(&a);
            lv_anim_set_var(&a, slot->sprite_img);
            lv_anim_set_values(&a, old_x_off, 0);
            lv_anim_set_duration(&a, 600);
            lv_anim_set_path_cb(&a, lv_anim_path_ease_out);
            lv_anim_set_exec_cb(&a, (lv_anim_exec_xcb_t)lv_obj_set_x);
            lv_anim_set_completed_cb(&a, walk_in_complete_cb);
            lv_anim_start(&a);
        } else {
            /* Same position or narrow — update animation in place */
            const anim_def_t *cur_def = &anim_defs[slot->cur_anim];
            bool playing_oneshot = !cur_def->looping &&
                                   slot->frame_idx < cur_def->frame_count - 1;
            if (!playing_oneshot && slot->cur_anim != new_anim) {
                slot->cur_anim = new_anim;
                slot->frame_idx = 0;
                slot->last_frame_tick = lv_tick_get();
                decode_and_apply_frame(slot);
            }
            const anim_def_t *def = &anim_defs[slot->cur_anim];
            lv_obj_set_size(slot->sprite_img, def->width, def->height);
            lv_obj_align(slot->sprite_img, LV_ALIGN_BOTTOM_MID, 0, def->y_offset);
        }

        scene_update_hud(s, subagent_count, overflow);
        s->active_slot_count = 1;

        printf("[scene] set_sessions single id=%d anim=%d walking=%d old_x=%d\n",
               ids[0], new_anim, slot->walking_in, old_x_off);
        return;
    }

    /* ------ Multi-session diff path ------ */

    /* Save old state to temp — must copy BEFORE reassigning to avoid
     * data corruption when indices overlap (the whole point of diffing). */
    clawd_slot_t old_slots[MAX_SLOTS];
    uint16_t old_ids[MAX_SLOTS];
    int old_count = s->active_slot_count;
    for (int i = 0; i < MAX_SLOTS; i++) {
        old_slots[i] = s->slots[i];
        old_ids[i] = s->slots[i].display_id;
    }

    /* Reset live slots — we'll reassign from old_slots or create fresh */
    for (int i = 0; i < MAX_SLOTS; i++) {
        s->slots[i].active = false;
        s->slots[i].sprite_img = NULL;
        s->slots[i].frame_buf = NULL;
        s->slots[i].frame_buf_size = 0;
        s->slots[i].display_id = 0;
    }

    /* Assign new slots by matching display IDs.
     *
     * Positioning: use lv_obj_align(BOTTOM_MID) for correct Y placement
     * (feet in grass). For multiple sessions, x_off distributes them
     * across the container. x_centers[] are absolute pixel positions
     * assuming 320px width; convert to offsets from center (160). */
    for (int new_i = 0; new_i < count; new_i++) {
        int old_i = find_id_in(old_ids, old_count, ids[new_i]);
        int x_off = x_centers[count - 1][new_i] - 160;
        if (old_i >= 0 && old_slots[old_i].active) {
            /* Existing session — move slot data, update animation if changed */
            s->slots[new_i] = old_slots[old_i];
            old_slots[old_i].sprite_img = NULL; /* transferred ownership */
            old_slots[old_i].frame_buf = NULL;
            s->slots[new_i].display_id = ids[new_i];
            s->slots[new_i].x_off = x_off;

            clawd_anim_id_t new_anim = (clawd_anim_id_t)anims[new_i];

            if (s->slots[new_i].walking_in) {
                /* Walk-in animation still running — don't interrupt it.
                 * Just update fallback and target position. */
                s->slots[new_i].fallback_anim = new_anim;
            } else {
                int old_x_off = old_slots[old_i].x_off;
                s->slots[new_i].fallback_anim = new_anim;

                if (old_x_off != x_off) {
                    /* Position changed — walk to new position.
                     * Switch to walking animation, slide from old to new x_off,
                     * gate the target animation in fallback_anim. */
                    s->slots[new_i].cur_anim = CLAWD_ANIM_WALKING;
                    s->slots[new_i].frame_idx = 0;
                    s->slots[new_i].last_frame_tick = lv_tick_get();
                    decode_and_apply_frame(&s->slots[new_i]);
                    const anim_def_t *walk_def = &anim_defs[CLAWD_ANIM_WALKING];
                    lv_obj_set_size(s->slots[new_i].sprite_img, walk_def->width, walk_def->height);

                    s->slots[new_i].walking_in = true;
                    lv_anim_t a;
                    lv_anim_init(&a);
                    lv_anim_set_var(&a, s->slots[new_i].sprite_img);
                    lv_anim_set_values(&a, old_x_off, x_off);
                    lv_anim_set_duration(&a, 600);
                    lv_anim_set_path_cb(&a, lv_anim_path_ease_out);
                    lv_anim_set_exec_cb(&a, (lv_anim_exec_xcb_t)lv_obj_set_x);
                    lv_anim_set_completed_cb(&a, walk_in_complete_cb);
                    lv_anim_start(&a);
                } else {
                    /* Same position — update animation in place */
                    if (s->slots[new_i].cur_anim != new_anim) {
                        s->slots[new_i].cur_anim = new_anim;
                        s->slots[new_i].frame_idx = 0;
                        s->slots[new_i].last_frame_tick = lv_tick_get();
                        decode_and_apply_frame(&s->slots[new_i]);
                    }
                    const anim_def_t *def = &anim_defs[s->slots[new_i].cur_anim];
                    lv_obj_set_size(s->slots[new_i].sprite_img, def->width, def->height);
                    lv_obj_align(s->slots[new_i].sprite_img, LV_ALIGN_BOTTOM_MID,
                                 x_off, def->y_offset);
                }
            }
        } else {
            /* New session — walk in from off-screen right.
             *
             * LVGL 9 coordinate model: lv_obj_set_x/set_pos set OFFSETS
             * from the alignment anchor (LV_ALIGN_BOTTOM_MID), not absolute
             * pixel positions. So x=0 means "at center", x=250 means
             * "250px right of center" (off-screen on a 320px display). */
            scene_activate_slot(s, new_i, CLAWD_ANIM_WALKING);
            s->slots[new_i].display_id = ids[new_i];
            s->slots[new_i].x_off = x_off;
            s->slots[new_i].fallback_anim = (clawd_anim_id_t)anims[new_i];

            const anim_def_t *walk_def = &anim_defs[CLAWD_ANIM_WALKING];
            /* Start off-screen right: large positive X offset from BOTTOM_MID.
             * Y stays as y_offset (already set by scene_activate_slot's align). */
            int start_x_off = 250;  /* well past right edge from center */
            lv_obj_set_x(s->slots[new_i].sprite_img, start_x_off);

            /* Target X is just the alignment offset for this slot position */
            s->slots[new_i].walking_in = true;
            lv_anim_t walk_a;
            lv_anim_init(&walk_a);
            lv_anim_set_var(&walk_a, s->slots[new_i].sprite_img);
            lv_anim_set_values(&walk_a, start_x_off, x_off);
            lv_anim_set_duration(&walk_a, 800);
            lv_anim_set_path_cb(&walk_a, lv_anim_path_ease_out);
            lv_anim_set_exec_cb(&walk_a, (lv_anim_exec_xcb_t)lv_obj_set_x);
            lv_anim_set_completed_cb(&walk_a, walk_in_complete_cb);
            lv_anim_start(&walk_a);
        }
    }

    /* Clean up removed slots — fade out and delete when done.
     * In narrow mode, skip fade animation and delete immediately
     * to avoid orphan sprites visible within the 107px container. */
    for (int i = 0; i < MAX_SLOTS; i++) {
        if (old_slots[i].sprite_img) {
            lv_anim_delete(old_slots[i].sprite_img, (lv_anim_exec_xcb_t)lv_obj_set_x);
            if (s->narrow) {
                /* Narrow mode: delete immediately, no fade */
                lv_obj_delete(old_slots[i].sprite_img);
            } else {
                /* Full width: fade out over 400ms */
                lv_anim_t a;
                lv_anim_init(&a);
                lv_anim_set_var(&a, old_slots[i].sprite_img);
                lv_anim_set_values(&a, LV_OPA_COVER, LV_OPA_TRANSP);
                lv_anim_set_duration(&a, 400);
                lv_anim_set_exec_cb(&a, set_sprite_opa);
                lv_anim_set_completed_cb(&a, fade_complete_cb);
                lv_anim_start(&a);
            }
            free(old_slots[i].frame_buf);
        } else {
            free(old_slots[i].frame_buf);
        }
    }

    /* Deactivate remaining slots beyond count */
    for (int i = count; i < MAX_SLOTS; i++) {
        s->slots[i].active = false;
    }

    /* In narrow mode, hide all slots except 0 and re-center slot 0 */
    if (s->narrow) {
        for (int i = 1; i < count; i++) {
            if (s->slots[i].sprite_img) {
                lv_obj_add_flag(s->slots[i].sprite_img, LV_OBJ_FLAG_HIDDEN);
            }
        }
        if (s->slots[0].active && s->slots[0].sprite_img) {
            s->slots[0].x_off = 0;
            const anim_def_t *def = &anim_defs[s->slots[0].cur_anim];
            lv_obj_align(s->slots[0].sprite_img, LV_ALIGN_BOTTOM_MID, 0, def->y_offset);
        }
    }

    scene_update_hud(s, subagent_count, overflow);
    s->active_slot_count = count;
}

#ifdef SIMULATOR
void scene_get_anim_info(scene_t *scene, int *frame_count, int *frame_ms)
{
    if (!scene) { *frame_count = 0; *frame_ms = 0; return; }
    clawd_slot_t *slot = &scene->slots[0];
    const anim_def_t *def = &anim_defs[slot->cur_anim];
    *frame_count = def->frame_count;
    *frame_ms = def->frame_ms;
}

int scene_get_frame_idx(scene_t *scene)
{
    if (!scene) return 0;
    return scene->slots[0].frame_idx;
}
#endif
