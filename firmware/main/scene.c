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
#define IDLE_FRAME_MS      (1000 / 6)   /* 167ms @ 6fps */
#define ALERT_FRAME_MS     (1000 / 10)  /* 100ms @ 10fps */
#define HAPPY_FRAME_MS     (1000 / 10)  /* 100ms @ 10fps */
#define SLEEPING_FRAME_MS  (1000 / 6)   /* 167ms @ 6fps */
#define DISCONN_FRAME_MS   (1000 / 6)   /* 167ms @ 6fps */

/* ---------- Animation metadata ---------- */

typedef struct {
    const uint16_t *const *frames;
    int frame_count;
    int frame_ms;
    bool looping;
    int width;    /* sprite width in pixels (0 = use SPRITE_W) */
    int height;   /* sprite height in pixels (0 = use SPRITE_H) */
    int y_offset; /* LVGL bottom-align y offset (accounts for canvas padding below feet) */
} anim_def_t;

static const anim_def_t anim_defs[] = {
    [CLAWD_ANIM_IDLE] = {
        .frames = idle_frames,
        .frame_count = IDLE_FRAME_COUNT,
        .frame_ms = IDLE_FRAME_MS,
        .looping = true,
        .width = IDLE_WIDTH,
        .height = IDLE_HEIGHT,
        .y_offset = 8,
    },
    [CLAWD_ANIM_ALERT] = {
        .frames = alert_frames,
        .frame_count = ALERT_FRAME_COUNT,
        .frame_ms = ALERT_FRAME_MS,
        .looping = false,
        .width = ALERT_WIDTH,
        .height = ALERT_HEIGHT,
        .y_offset = 8,
    },
    [CLAWD_ANIM_HAPPY] = {
        .frames = happy_frames,
        .frame_count = HAPPY_FRAME_COUNT,
        .frame_ms = HAPPY_FRAME_MS,
        .looping = false,
        .width = HAPPY_WIDTH,
        .height = HAPPY_HEIGHT,
        .y_offset = 28,
    },
    [CLAWD_ANIM_SLEEPING] = {
        .frames = sleeping_frames,
        .frame_count = SLEEPING_FRAME_COUNT,
        .frame_ms = SLEEPING_FRAME_MS,
        .looping = true,
        .width = SLEEPING_WIDTH,
        .height = SLEEPING_HEIGHT,
        .y_offset = 8,
    },
    [CLAWD_ANIM_DISCONNECTED] = {
        .frames = disconnected_frames,
        .frame_count = DISCONNECTED_FRAME_COUNT,
        .frame_ms = DISCONN_FRAME_MS,
        .looping = true,
        .width = DISCONNECTED_WIDTH,
        .height = DISCONNECTED_HEIGHT,
        .y_offset = 8,
    },
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
    lv_image_dsc_t frame_dscs[96]; /* max frames across all anims */
    uint8_t *frame_bufs[96];       /* converted ARGB8888 buffers (PSRAM) */
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

static void free_frame_bufs(scene_t *s)
{
    for (int i = 0; i < 96; i++) {
        if (s->frame_bufs[i]) {
            free(s->frame_bufs[i]);
            s->frame_bufs[i] = NULL;
        }
    }
}

static void build_frame_dscs(scene_t *s, const anim_def_t *def)
{
    free_frame_bufs(s);

    int w = def->width  ? def->width  : SPRITE_W;
    int h = def->height ? def->height : SPRITE_H;

    for (int i = 0; i < def->frame_count && i < 96; i++) {
        /* Convert RGB565 → ARGB8888, replacing chroma key with alpha=0 */
        int pixel_count = w * h;
        size_t buf_size = pixel_count * 4;
        uint8_t *buf = malloc(buf_size);
        if (!buf) continue;

        const uint16_t *src = def->frames[i];
        for (int p = 0; p < pixel_count; p++) {
            uint16_t pixel = src[p];
            uint8_t r5 = (pixel >> 11) & 0x1F;
            uint8_t g6 = (pixel >> 5) & 0x3F;
            uint8_t b5 = pixel & 0x1F;
            /* Expand 5/6-bit to 8-bit */
            uint8_t r = (r5 << 3) | (r5 >> 2);
            uint8_t g = (g6 << 2) | (g6 >> 4);
            uint8_t b = (b5 << 3) | (b5 >> 2);
            uint8_t a = (pixel == TRANSPARENT_KEY) ? 0x00 : 0xFF;
            /* LVGL ARGB8888 little-endian: [B, G, R, A] */
            buf[p * 4 + 0] = b;
            buf[p * 4 + 1] = g;
            buf[p * 4 + 2] = r;
            buf[p * 4 + 3] = a;
        }
        s->frame_bufs[i] = buf;

        lv_image_dsc_t *d = &s->frame_dscs[i];
        d->header.magic = LV_IMAGE_HEADER_MAGIC;
        d->header.w = w;
        d->header.h = h;
        d->header.cf = LV_COLOR_FORMAT_ARGB8888;
        d->header.stride = w * 4;
        d->data = buf;
        d->data_size = buf_size;
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

    /* Clawd sprite image — y_offset per animation pushes feet into grass */
    s->sprite_img = lv_image_create(s->container);
    lv_obj_align(s->sprite_img, LV_ALIGN_BOTTOM_MID, 0, anim_defs[CLAWD_ANIM_IDLE].y_offset);
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

    /* BLE icon — Bluetooth symbol using a canvas with pixel art */
    {
        /* 12x16 pixel-art Bluetooth rune on blue circle background */
        static const uint8_t bt_bitmap[] = {
            /* Row 0  */ 0,0,0,0,0,1,0,0,0,0,0,0,
            /* Row 1  */ 0,0,0,0,0,1,1,0,0,0,0,0,
            /* Row 2  */ 0,0,0,0,0,1,0,1,0,0,0,0,
            /* Row 3  */ 0,1,0,0,0,1,0,0,1,0,0,0,
            /* Row 4  */ 0,0,1,0,0,1,0,1,0,0,0,0,
            /* Row 5  */ 0,0,0,1,0,1,1,0,0,0,0,0,
            /* Row 6  */ 0,0,0,0,1,1,0,0,0,0,0,0,
            /* Row 7  */ 0,0,0,1,0,1,1,0,0,0,0,0,
            /* Row 8  */ 0,0,1,0,0,1,0,1,0,0,0,0,
            /* Row 9  */ 0,1,0,0,0,1,0,0,1,0,0,0,
            /* Row 10 */ 0,0,0,0,0,1,0,1,0,0,0,0,
            /* Row 11 */ 0,0,0,0,0,1,1,0,0,0,0,0,
            /* Row 12 */ 0,0,0,0,0,1,0,0,0,0,0,0,
        };
        #define BT_W 12
        #define BT_H 13
        #define BT_ICON_SIZE 18 /* background circle size */

        s->ble_icon = lv_obj_create(s->container);
        lv_obj_remove_style_all(s->ble_icon);
        lv_obj_set_size(s->ble_icon, BT_ICON_SIZE, BT_ICON_SIZE);
        lv_obj_align(s->ble_icon, LV_ALIGN_TOP_RIGHT, -6, 24);
        lv_obj_set_style_bg_opa(s->ble_icon, LV_OPA_COVER, 0);
        lv_obj_set_style_bg_color(s->ble_icon, lv_color_hex(0x2255aa), 0);
        lv_obj_set_style_radius(s->ble_icon, BT_ICON_SIZE / 2, 0);
        lv_obj_set_scrollbar_mode(s->ble_icon, LV_SCROLLBAR_MODE_OFF);
        lv_obj_clear_flag(s->ble_icon, LV_OBJ_FLAG_SCROLLABLE);

        /* Draw white pixels for the Bluetooth rune */
        int ox = (BT_ICON_SIZE - BT_W) / 2;
        int oy = (BT_ICON_SIZE - BT_H) / 2;
        for (int row = 0; row < BT_H; row++) {
            for (int col = 0; col < BT_W; col++) {
                if (bt_bitmap[row * BT_W + col]) {
                    lv_obj_t *px = lv_obj_create(s->ble_icon);
                    lv_obj_remove_style_all(px);
                    lv_obj_set_size(px, 1, 1);
                    lv_obj_set_pos(px, ox + col, oy + row);
                    lv_obj_set_style_bg_opa(px, LV_OPA_COVER, 0);
                    lv_obj_set_style_bg_color(px, lv_color_hex(0xffffff), 0);
                }
            }
        }
        lv_obj_add_flag(s->ble_icon, LV_OBJ_FLAG_HIDDEN);
    }

    /* No-connection label — bottom center */
    s->noconn_label = lv_label_create(s->container);
    lv_obj_set_style_text_font(s->noconn_label, &lv_font_montserrat_14, 0);
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

    /* Re-align sprite for this animation's ground offset */
    lv_obj_align(scene->sprite_img, LV_ALIGN_BOTTOM_MID, 0, def->y_offset);

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
    lv_label_set_text_fmt(scene->time_label, "%02d:%02d", hour, minute);
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
            } else {
                /* Oneshot finished — auto-return to idle */
                scene_set_clawd_anim(scene, CLAWD_ANIM_IDLE);
                return;
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
