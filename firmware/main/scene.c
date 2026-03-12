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

#ifdef ALERT_FRAMES_H
/* already included */
#else
#include "assets/sprite_alert.h"
#endif

#ifdef HAPPY_FRAMES_H
/* already included */
#else
#include "assets/sprite_happy.h"
#endif

/* Sleeping and disconnected sprites may not exist yet */
#if __has_include("assets/sprite_sleeping.h")
#include "assets/sprite_sleeping.h"
#define HAS_SLEEPING_SPRITE 1
#else
#define HAS_SLEEPING_SPRITE 0
#define SLEEPING_FRAME_COUNT 0
#endif

#if __has_include("assets/sprite_disconnected.h")
#include "assets/sprite_disconnected.h"
#define HAS_DISCONNECTED_SPRITE 1
#else
#define HAS_DISCONNECTED_SPRITE 0
#define DISCONNECTED_FRAME_COUNT 0
#endif

/* ---------- Constants ---------- */

#define SCENE_HEIGHT       172
#define SPRITE_W           64
#define SPRITE_H           64
#define GRASS_HEIGHT       14
#define STAR_COUNT         6
#define STAR_TWINKLE_MIN   2000
#define STAR_TWINKLE_MAX   4000
#define TRANSPARENT_KEY    0x18C5

/* Frame timing in ms per animation */
#define IDLE_FRAME_MS      (1000 / 6)
#define ALERT_FRAME_MS     (1000 / 8)
#define HAPPY_FRAME_MS     (1000 / 8)
#define SLEEPING_FRAME_MS  (1000 / 4)
#define DISCONN_FRAME_MS   (1000 / 6)

/* ---------- Animation metadata ---------- */

typedef struct {
    const uint16_t *const *frames;
    int frame_count;
    int frame_ms;
    bool looping;
} anim_def_t;

static const anim_def_t anim_defs[] = {
    [CLAWD_ANIM_IDLE] = {
        .frames = idle_frames,
        .frame_count = IDLE_FRAME_COUNT,
        .frame_ms = IDLE_FRAME_MS,
        .looping = true,
    },
    [CLAWD_ANIM_ALERT] = {
        .frames = alert_frames,
        .frame_count = ALERT_FRAME_COUNT,
        .frame_ms = ALERT_FRAME_MS,
        .looping = false,
    },
    [CLAWD_ANIM_HAPPY] = {
        .frames = happy_frames,
        .frame_count = HAPPY_FRAME_COUNT,
        .frame_ms = HAPPY_FRAME_MS,
        .looping = false,
    },
#if HAS_SLEEPING_SPRITE
    [CLAWD_ANIM_SLEEPING] = {
        .frames = sleeping_frames,
        .frame_count = SLEEPING_FRAME_COUNT,
        .frame_ms = SLEEPING_FRAME_MS,
        .looping = true,
    },
#else
    [CLAWD_ANIM_SLEEPING] = {
        .frames = idle_frames,
        .frame_count = IDLE_FRAME_COUNT,
        .frame_ms = SLEEPING_FRAME_MS,
        .looping = true,
    },
#endif
#if HAS_DISCONNECTED_SPRITE
    [CLAWD_ANIM_DISCONNECTED] = {
        .frames = disconnected_frames,
        .frame_count = DISCONNECTED_FRAME_COUNT,
        .frame_ms = DISCONN_FRAME_MS,
        .looping = true,
    },
#else
    [CLAWD_ANIM_DISCONNECTED] = {
        .frames = idle_frames,
        .frame_count = IDLE_FRAME_COUNT,
        .frame_ms = DISCONN_FRAME_MS,
        .looping = true,
    },
#endif
};

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

    /* Clawd sprite */
    lv_obj_t *sprite_img;
    lv_image_dsc_t frame_dscs[16]; /* max frames across all anims */
    clawd_anim_id_t cur_anim;
    int frame_idx;
    uint32_t last_frame_tick;

    /* Time label */
    lv_obj_t *time_label;

    /* BLE icon */
    lv_obj_t *ble_icon;

    /* No-connection label */
    lv_obj_t *noconn_label;
};

/* ---------- Helpers ---------- */

static void build_frame_dscs(scene_t *s, const anim_def_t *def)
{
    for (int i = 0; i < def->frame_count && i < 16; i++) {
        lv_image_dsc_t *d = &s->frame_dscs[i];
        d->header.w = SPRITE_W;
        d->header.h = SPRITE_H;
        d->header.cf = LV_COLOR_FORMAT_RGB565;
        d->header.stride = SPRITE_W * 2;
        d->data = (const uint8_t *)def->frames[i];
        d->data_size = SPRITE_W * SPRITE_H * 2;
    }
}

static void apply_sprite_frame(scene_t *s)
{
    const anim_def_t *def = &anim_defs[s->cur_anim];
    int idx = s->frame_idx;
    if (idx >= def->frame_count) idx = def->frame_count - 1;
    lv_image_set_src(s->sprite_img, &s->frame_dscs[idx]);
}

static uint32_t random_range(uint32_t min_val, uint32_t max_val)
{
    return min_val + (lv_rand(0, max_val - min_val));
}

static void width_anim_cb(void *var, int32_t val)
{
    lv_obj_set_width((lv_obj_t *)var, val);
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

    /* Clawd sprite image — centered above grass */
    s->sprite_img = lv_image_create(s->container);
    lv_obj_align(s->sprite_img, LV_ALIGN_BOTTOM_MID, 0, -GRASS_HEIGHT);
    lv_image_set_inner_align(s->sprite_img, LV_IMAGE_ALIGN_CENTER);

    /* Set up idle animation as default */
    s->cur_anim = CLAWD_ANIM_IDLE;
    s->frame_idx = 0;
    s->last_frame_tick = lv_tick_get();
    build_frame_dscs(s, &anim_defs[CLAWD_ANIM_IDLE]);
    apply_sprite_frame(s);

    /* Time label — top-right */
    s->time_label = lv_label_create(s->container);
    lv_obj_set_style_text_font(s->time_label, &lv_font_montserrat_18, 0);
    lv_obj_set_style_text_color(s->time_label, lv_color_hex(0x4466aa), 0);
    lv_obj_align(s->time_label, LV_ALIGN_TOP_RIGHT, -6, 4);
    lv_label_set_text(s->time_label, "");
    lv_obj_add_flag(s->time_label, LV_OBJ_FLAG_HIDDEN);

    /* BLE icon — small circle, upper-right below time */
    s->ble_icon = lv_obj_create(s->container);
    lv_obj_remove_style_all(s->ble_icon);
    lv_obj_set_size(s->ble_icon, 8, 8);
    lv_obj_align(s->ble_icon, LV_ALIGN_TOP_RIGHT, -8, 26);
    lv_obj_set_style_bg_opa(s->ble_icon, LV_OPA_COVER, 0);
    lv_obj_set_style_bg_color(s->ble_icon, lv_color_hex(0x4488ff), 0);
    lv_obj_set_style_radius(s->ble_icon, 4, 0);
    lv_obj_add_flag(s->ble_icon, LV_OBJ_FLAG_HIDDEN);

    /* No-connection label — bottom center */
    s->noconn_label = lv_label_create(s->container);
    lv_obj_set_style_text_font(s->noconn_label, &lv_font_montserrat_10, 0);
    lv_obj_set_style_text_color(s->noconn_label, lv_color_hex(0x556677), 0);
    lv_obj_align(s->noconn_label, LV_ALIGN_BOTTOM_MID, 0, -GRASS_HEIGHT - 2);
    lv_label_set_text(s->noconn_label, "No connection");
    lv_obj_add_flag(s->noconn_label, LV_OBJ_FLAG_HIDDEN);

    return s;
}

/* ---------- Width animation ---------- */

void scene_set_width(scene_t *scene, int width_px, int anim_ms)
{
    if (!scene) return;

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
    if (anim == scene->cur_anim) return;

    scene->cur_anim = anim;
    scene->frame_idx = 0;
    scene->last_frame_tick = lv_tick_get();

    const anim_def_t *def = &anim_defs[anim];
    build_frame_dscs(scene, def);
    apply_sprite_frame(scene);

    /* Disconnected state: desaturate + show no-connection label */
    if (anim == CLAWD_ANIM_DISCONNECTED) {
        lv_obj_set_style_image_recolor(scene->sprite_img, lv_color_hex(0x888888), 0);
        lv_obj_set_style_image_recolor_opa(scene->sprite_img, LV_OPA_30, 0);
        lv_obj_clear_flag(scene->noconn_label, LV_OBJ_FLAG_HIDDEN);
    } else {
        lv_obj_set_style_image_recolor_opa(scene->sprite_img, LV_OPA_TRANSP, 0);
        lv_obj_add_flag(scene->noconn_label, LV_OBJ_FLAG_HIDDEN);
    }
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
    lv_label_set_text_fmt(scene->time_label, "%d:%02d", hour, minute);
}

/* ---------- BLE icon ---------- */

void scene_set_ble_icon_visible(scene_t *scene, bool visible)
{
    if (!scene) return;
    if (visible)
        lv_obj_clear_flag(scene->ble_icon, LV_OBJ_FLAG_HIDDEN);
    else
        lv_obj_add_flag(scene->ble_icon, LV_OBJ_FLAG_HIDDEN);
}

/* ---------- Tick (call from UI loop) ---------- */

void scene_tick(scene_t *scene)
{
    if (!scene) return;

    uint32_t now = lv_tick_get();
    const anim_def_t *def = &anim_defs[scene->cur_anim];

    /* Advance sprite frame */
    uint32_t elapsed = now - scene->last_frame_tick;
    if (elapsed >= (uint32_t)def->frame_ms) {
        scene->last_frame_tick = now;

        if (def->looping) {
            scene->frame_idx = (scene->frame_idx + 1) % def->frame_count;
        } else {
            if (scene->frame_idx < def->frame_count - 1) {
                scene->frame_idx++;
            }
        }
        apply_sprite_frame(scene);
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
    const anim_def_t *def = &anim_defs[scene->cur_anim];
    if (def->looping) return false;
    return scene->frame_idx < def->frame_count - 1;
}
