#include "sim_display.h"
#include <string.h>
#include <stdlib.h>
#include <stdio.h>
#include <SDL.h>

/* Border width in native pixels (scaled with window) */
#define LED_BORDER_PX 4

/* Native display aspect ratio (including border) */
#define NATIVE_W (SIM_LCD_H_RES + LED_BORDER_PX * 2)
#define NATIVE_H (SIM_LCD_V_RES + LED_BORDER_PX * 2)

/* Framebuffer — always maintained, both modes read from this */
static uint16_t s_framebuffer[SIM_LCD_H_RES * SIM_LCD_V_RES];

/* Mode flag */
static bool s_headless = false;
static bool s_quit = false;
static bool s_hidden = false;

/* Simulated tick for headless mode */
static uint32_t s_sim_tick = 0;

/* SDL state (interactive mode) */
static SDL_Window   *s_window   = NULL;
static SDL_Renderer *s_renderer = NULL;
static SDL_Texture  *s_texture  = NULL;
static int s_scale = 3;

/* RGB LED color (updated by led_strip shim via sim_rgb_led_update) */
static uint8_t s_led_r = 0, s_led_g = 0, s_led_b = 0;

/* Previous window size for aspect-ratio enforcement */
static int s_prev_w = 0, s_prev_h = 0;

/* ---- RGB LED bridge (called from led_strip shim) ---- */

void sim_rgb_led_update(uint8_t r, uint8_t g, uint8_t b)
{
    s_led_r = r;
    s_led_g = g;
    s_led_b = b;
}

/* ---- Simulated time ---- */

uint32_t sim_get_tick(void)
{
    return s_sim_tick;
}

void sim_advance_tick(uint32_t ms)
{
    s_sim_tick += ms;
}

static uint32_t sdl_tick_cb(void)
{
    return SDL_GetTicks();
}

/* ---- Hit-test callback for borderless window dragging + resizing ---- */
#define RESIZE_GRIP 8  /* pixels from edge that count as resize grip */

static SDL_HitTestResult hit_test_cb(SDL_Window *win, const SDL_Point *area, void *data)
{
    (void)data;
    int w, h;
    SDL_GetWindowSize(win, &w, &h);

    bool left   = area->x < RESIZE_GRIP;
    bool right  = area->x >= w - RESIZE_GRIP;
    bool top    = area->y < RESIZE_GRIP;
    bool bottom = area->y >= h - RESIZE_GRIP;

    if (top && left)     return SDL_HITTEST_RESIZE_TOPLEFT;
    if (top && right)    return SDL_HITTEST_RESIZE_TOPRIGHT;
    if (bottom && left)  return SDL_HITTEST_RESIZE_BOTTOMLEFT;
    if (bottom && right) return SDL_HITTEST_RESIZE_BOTTOMRIGHT;
    if (left)            return SDL_HITTEST_RESIZE_LEFT;
    if (right)           return SDL_HITTEST_RESIZE_RIGHT;
    if (top)             return SDL_HITTEST_RESIZE_TOP;
    if (bottom)          return SDL_HITTEST_RESIZE_BOTTOM;

    return SDL_HITTEST_DRAGGABLE;
}

/* ---- LVGL flush callback ---- */

static void flush_cb(lv_display_t *disp, const lv_area_t *area, uint8_t *px_map)
{
    int x1 = area->x1;
    int y1 = area->y1;
    int w  = area->x2 - area->x1 + 1;
    int h  = area->y2 - area->y1 + 1;

    uint16_t *src = (uint16_t *)px_map;
    for (int y = 0; y < h; y++) {
        memcpy(&s_framebuffer[(y1 + y) * SIM_LCD_H_RES + x1],
               &src[y * w],
               w * sizeof(uint16_t));
    }

    lv_display_flush_ready(disp);
}

/* ---- Init ---- */

lv_display_t *sim_display_init(bool headless, int scale, bool bordered)
{
    s_headless = headless;
    s_scale = scale > 0 ? scale : 3;
    memset(s_framebuffer, 0, sizeof(s_framebuffer));

    /* Set LVGL tick source */
    if (headless) {
        lv_tick_set_cb(sim_get_tick);
    } else {
        /* Interactive: use SDL_GetTicks */
        SDL_SetHint(SDL_HINT_MAC_BACKGROUND_APP, "1");  /* Don't show in Dock */
        SDL_Init(SDL_INIT_VIDEO);
        lv_tick_set_cb(sdl_tick_cb);

        int border = LED_BORDER_PX * s_scale;
        int win_w = SIM_LCD_H_RES * s_scale + border * 2;
        int win_h = SIM_LCD_V_RES * s_scale + border * 2;

        Uint32 flags = SDL_WINDOW_RESIZABLE;
        if (!bordered) flags |= SDL_WINDOW_BORDERLESS;
        s_window = SDL_CreateWindow(
            "Clawd Tank Simulator",
            SDL_WINDOWPOS_CENTERED, SDL_WINDOWPOS_CENTERED,
            win_w, win_h,
            flags);
        if (!s_window) {
            fprintf(stderr, "SDL_CreateWindow failed: %s\n", SDL_GetError());
            exit(1);
        }

        /* Minimum size = 1x native */
        SDL_SetWindowMinimumSize(s_window, NATIVE_W, NATIVE_H);

        s_prev_w = win_w;
        s_prev_h = win_h;

        if (!bordered) {
            SDL_SetWindowHitTest(s_window, hit_test_cb, NULL);
        }
        SDL_RaiseWindow(s_window);

        s_renderer = SDL_CreateRenderer(s_window, -1, SDL_RENDERER_ACCELERATED | SDL_RENDERER_PRESENTVSYNC);
        if (!s_renderer) {
            fprintf(stderr, "SDL_CreateRenderer failed: %s\n", SDL_GetError());
            exit(1);
        }

        /* Nearest-neighbor scaling for crisp pixels */
        SDL_SetHint(SDL_HINT_RENDER_SCALE_QUALITY, "0");

        s_texture = SDL_CreateTexture(
            s_renderer,
            SDL_PIXELFORMAT_RGB565,
            SDL_TEXTUREACCESS_STREAMING,
            SIM_LCD_H_RES, SIM_LCD_V_RES);
        if (!s_texture) {
            fprintf(stderr, "SDL_CreateTexture failed: %s\n", SDL_GetError());
            exit(1);
        }
    }

    /* Create LVGL display */
    lv_display_t *disp = lv_display_create(SIM_LCD_H_RES, SIM_LCD_V_RES);

    /* Allocate render buffers */
    size_t buf_sz = SIM_LCD_H_RES * 20 * sizeof(uint16_t); /* 20-line partial buffer */
    void *buf1 = malloc(buf_sz);
    void *buf2 = malloc(buf_sz);

    lv_display_set_buffers(disp, buf1, buf2, buf_sz, LV_DISPLAY_RENDER_MODE_PARTIAL);
    lv_display_set_color_format(disp, LV_COLOR_FORMAT_RGB565);
    lv_display_set_flush_cb(disp, flush_cb);

    return disp;
}

uint16_t *sim_display_get_framebuffer(void)
{
    return s_framebuffer;
}

/* ---- Tick ---- */

void sim_display_tick(void)
{
    if (s_headless || s_hidden) return;

    int win_w, win_h;
    SDL_GetWindowSize(s_window, &win_w, &win_h);

    /* Window aspect ratio is enforced on resize, so content always fills
     * the entire window.  Compute border from the current scale. */
    float scale = (float)win_w / NATIVE_W;
    int border = (int)(LED_BORDER_PX * scale + 0.5f);

    /* Fill entire window with LED color (border glow) */
    SDL_SetRenderDrawColor(s_renderer, s_led_r, s_led_g, s_led_b, 255);
    SDL_RenderClear(s_renderer);

    /* Render framebuffer texture inset by the border */
    SDL_UpdateTexture(s_texture, NULL, s_framebuffer, SIM_LCD_H_RES * sizeof(uint16_t));
    SDL_Rect dst = {
        .x = border,
        .y = border,
        .w = win_w - 2 * border,
        .h = win_h - 2 * border
    };
    SDL_RenderCopy(s_renderer, s_texture, NULL, &dst);

    SDL_RenderPresent(s_renderer);
}

bool sim_display_should_quit(void)
{
    return s_quit;
}

void sim_display_set_quit(void)
{
    s_quit = true;
}

/* ---- Aspect-ratio enforcement ---- */

void sim_display_enforce_aspect_ratio(void)
{
    static bool s_enforcing = false;
    if (!s_window || s_enforcing) return;
    s_enforcing = true;

    int win_w, win_h;
    SDL_GetWindowSize(s_window, &win_w, &win_h);

    /* Determine which dimension the user changed */
    bool w_changed = (win_w != s_prev_w);
    bool h_changed = (win_h != s_prev_h);

    int new_w, new_h;
    if (w_changed && !h_changed) {
        /* Horizontal edge drag — compute height from width */
        new_w = win_w;
        new_h = (int)((float)win_w * NATIVE_H / NATIVE_W + 0.5f);
    } else if (h_changed && !w_changed) {
        /* Vertical edge drag — compute width from height */
        new_h = win_h;
        new_w = (int)((float)win_h * NATIVE_W / NATIVE_H + 0.5f);
    } else {
        /* Corner drag or programmatic — keep the larger proportional axis */
        float scale_x = (float)win_w / NATIVE_W;
        float scale_y = (float)win_h / NATIVE_H;
        if (scale_x >= scale_y) {
            new_w = win_w;
            new_h = (int)((float)win_w * NATIVE_H / NATIVE_W + 0.5f);
        } else {
            new_h = win_h;
            new_w = (int)((float)win_h * NATIVE_W / NATIVE_H + 0.5f);
        }
    }

    /* Enforce minimum size */
    if (new_w < NATIVE_W) { new_w = NATIVE_W; new_h = NATIVE_H; }
    if (new_h < NATIVE_H) { new_w = NATIVE_W; new_h = NATIVE_H; }

    s_prev_w = new_w;
    s_prev_h = new_h;

    if (new_w != win_w || new_h != win_h) {
        SDL_SetWindowSize(s_window, new_w, new_h);
    }

    s_enforcing = false;
}

/* ---- Always-on-top ---- */

void sim_display_set_pinned(bool pinned)
{
    if (!s_window) return;
    SDL_SetWindowAlwaysOnTop(s_window, pinned ? SDL_TRUE : SDL_FALSE);
}

/* ---- Show / Hide / Clear-quit ---- */

void sim_display_show_window(void)
{
    if (!s_window) return;
    SDL_ShowWindow(s_window);
    SDL_RaiseWindow(s_window);
    SDL_GetWindowSize(s_window, &s_prev_w, &s_prev_h);
    s_hidden = false;
}

void sim_display_hide_window(void)
{
    if (!s_window) return;
    SDL_HideWindow(s_window);
    s_hidden = true;
}

bool sim_display_is_hidden(void)
{
    return s_hidden;
}

void sim_display_clear_quit(void)
{
    s_quit = false;
}

/* ---- Shutdown ---- */

void sim_display_shutdown(void)
{
    if (!s_headless) {
        if (s_texture)  SDL_DestroyTexture(s_texture);
        if (s_renderer) SDL_DestroyRenderer(s_renderer);
        if (s_window)   SDL_DestroyWindow(s_window);
        SDL_Quit();
    }
}
