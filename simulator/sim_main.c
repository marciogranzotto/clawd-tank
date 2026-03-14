#include "lvgl.h"
#include "sim_display.h"
#include "sim_events.h"
#include "sim_screenshot.h"
#include "sim_socket.h"
#include "ui_manager.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <SDL.h>

#define TICK_MS 33  /* ~30 fps */

/* ---- CLI options ---- */
static bool     opt_headless = false;
static int      opt_scale = 3;
static const char *opt_events = NULL;
static const char *opt_scenario = NULL;
static const char *opt_screenshot_dir = NULL;
static int      opt_screenshot_interval = 0;
static bool     opt_screenshot_on_event = false;
static uint32_t opt_run_ms = 0;
static int      opt_listen_port = 0;  /* 0 = disabled */

static void print_usage(void)
{
    printf(
        "Usage: clawd-tank-sim [OPTIONS]\n"
        "\n"
        "Display:\n"
        "  --headless              Run without SDL2 window\n"
        "  --scale <N>             Window scale factor (default: 3)\n"
        "\n"
        "Events:\n"
        "  --events '<commands>'   Inline event string (semicolon-separated)\n"
        "  --scenario <file.json>  Load timed events from JSON file\n"
        "\n"
        "Screenshots:\n"
        "  --screenshot-dir <dir>  Output directory for PNGs\n"
        "  --screenshot-interval <ms>  Periodic screenshot interval\n"
        "  --screenshot-on-event   Screenshot after each event\n"
        "\n"
        "General:\n"
        "  --run-ms <ms>           Simulation duration (headless)\n"
        "  --listen [port]         Listen for daemon TCP connections (default: 19872)\n"
        "  --help                  Show this help\n"
    );
}

static void parse_args(int argc, char *argv[])
{
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--headless") == 0) {
            opt_headless = true;
        } else if (strcmp(argv[i], "--scale") == 0 && i + 1 < argc) {
            opt_scale = atoi(argv[++i]);
        } else if (strcmp(argv[i], "--events") == 0 && i + 1 < argc) {
            opt_events = argv[++i];
        } else if (strcmp(argv[i], "--scenario") == 0 && i + 1 < argc) {
            opt_scenario = argv[++i];
        } else if (strcmp(argv[i], "--screenshot-dir") == 0 && i + 1 < argc) {
            opt_screenshot_dir = argv[++i];
        } else if (strcmp(argv[i], "--screenshot-interval") == 0 && i + 1 < argc) {
            opt_screenshot_interval = atoi(argv[++i]);
        } else if (strcmp(argv[i], "--screenshot-on-event") == 0) {
            opt_screenshot_on_event = true;
        } else if (strcmp(argv[i], "--run-ms") == 0 && i + 1 < argc) {
            opt_run_ms = (uint32_t)atoi(argv[++i]);
        } else if (strcmp(argv[i], "--listen") == 0) {
            /* Port is optional — check if next arg is a number */
            if (i + 1 < argc && argv[i + 1][0] >= '0' && argv[i + 1][0] <= '9') {
                opt_listen_port = atoi(argv[++i]);
            } else {
                opt_listen_port = SIM_SOCKET_DEFAULT_PORT;
            }
        } else if (strcmp(argv[i], "--help") == 0) {
            print_usage();
            exit(0);
        } else {
            fprintf(stderr, "Unknown option: %s\n", argv[i]);
            print_usage();
            exit(1);
        }
    }
}

/* ---- Screenshot helpers ---- */

static uint32_t s_last_screenshot_time = 0;

static void maybe_capture_periodic(uint32_t time_ms)
{
    if (!opt_screenshot_dir || opt_screenshot_interval <= 0) return;

    while (s_last_screenshot_time <= time_ms) {
        sim_screenshot_capture(sim_display_get_framebuffer(),
                               SIM_LCD_H_RES, SIM_LCD_V_RES,
                               s_last_screenshot_time, NULL);
        s_last_screenshot_time += (uint32_t)opt_screenshot_interval;
    }
}

static void capture_on_event(uint32_t time_ms, const char *suffix)
{
    if (!opt_screenshot_dir || !opt_screenshot_on_event) return;
    sim_screenshot_capture(sim_display_get_framebuffer(),
                           SIM_LCD_H_RES, SIM_LCD_V_RES,
                           time_ms, suffix);
}

/* ---- Headless main loop ---- */

static void run_headless(void)
{
    uint32_t end_time = opt_run_ms;
    if (end_time == 0) {
        end_time = sim_events_get_end_time() + 500;
    }

    bool indefinite = (opt_listen_port > 0);
    if (indefinite) {
        printf("[sim] Headless + listen mode: running indefinitely (Ctrl-C to stop)\n");
    } else {
        printf("[sim] Headless mode: running %u ms of simulated time\n", end_time);
    }

    uint32_t time = 0;

    /* Initial render */
    ui_manager_tick();
    maybe_capture_periodic(time);

    while (indefinite || time < end_time) {
        if (indefinite) {
            usleep(TICK_MS * 1000);
        }
        time += TICK_MS;
        sim_advance_tick(TICK_MS);

        /* Fire any due events */
        bool event_fired = sim_events_process(time);

        /* Process TCP socket events */
        if (opt_listen_port > 0) {
            sim_socket_process();
        }

        /* LVGL tick */
        ui_manager_tick();

        /* Screenshots */
        if (event_fired) {
            capture_on_event(time, sim_events_last_suffix());
        }
        maybe_capture_periodic(time);
    }

    printf("[sim] Done. Processed %u ms.\n", time);
}

/* ---- Interactive main loop ---- */

/* Forward declaration — keyboard handler defined below */
static void handle_sdl_events(void);

static void run_interactive(void)
{
    printf("[sim] Interactive mode (scale=%dx). Keys: c=connect d=disconnect n=notify x=clear s=screenshot q=quit\n",
           opt_scale);

    while (!sim_display_should_quit()) {
        handle_sdl_events();

        /* Process scripted events if any (using wall time) */
        if (opt_events || opt_scenario) {
            sim_events_process(SDL_GetTicks());
        }

        /* Process TCP socket events */
        if (opt_listen_port > 0) {
            sim_socket_process();
        }

        ui_manager_tick();
        sim_display_tick();  /* present to SDL window */

        SDL_Delay(TICK_MS);
    }
}

/* ---- Keyboard event handling (interactive) ---- */

static int s_sample_notif_idx = 0;
static const struct { const char *project; const char *message; } sample_notifs[] = {
    {"clawd-tank",     "Waiting for input"},
    {"my-api",         "Running tests..."},
    {"web-app",        "Task completed"},
    {"data-pipeline",  "Build failed"},
    {"mobile-app",     "Reviewing changes"},
    {"infra",          "Waiting for approval"},
};
#define SAMPLE_COUNT (sizeof(sample_notifs) / sizeof(sample_notifs[0]))

static void handle_sdl_events(void)
{
    SDL_Event e;
    while (SDL_PollEvent(&e)) {
        if (e.type == SDL_QUIT) {
            sim_display_set_quit();
            return;
        }
        if (e.type == SDL_KEYDOWN) {
            switch (e.key.keysym.sym) {
            case SDLK_q:
            case SDLK_ESCAPE:
                sim_display_set_quit();
                return;

            case SDLK_c: {
                ble_evt_t evt = { .type = BLE_EVT_CONNECTED };
                ui_manager_handle_event(&evt);
                printf("[key] connect\n");
                break;
            }
            case SDLK_d: {
                ble_evt_t evt = { .type = BLE_EVT_DISCONNECTED };
                ui_manager_handle_event(&evt);
                printf("[key] disconnect\n");
                break;
            }
            case SDLK_n: {
                ble_evt_t evt = { .type = BLE_EVT_NOTIF_ADD };
                const char *proj = sample_notifs[s_sample_notif_idx % SAMPLE_COUNT].project;
                const char *msg  = sample_notifs[s_sample_notif_idx % SAMPLE_COUNT].message;
                snprintf(evt.id, sizeof(evt.id), "key_%d", s_sample_notif_idx);
                snprintf(evt.project, sizeof(evt.project), "%s", proj);
                snprintf(evt.message, sizeof(evt.message), "%s", msg);
                s_sample_notif_idx++;
                ui_manager_handle_event(&evt);
                printf("[key] notify: %s — %s\n", proj, msg);
                break;
            }
            case SDLK_x: {
                ble_evt_t evt = { .type = BLE_EVT_NOTIF_CLEAR };
                ui_manager_handle_event(&evt);
                printf("[key] clear all\n");
                break;
            }
            case SDLK_s: {
                sim_screenshot_init(".");
                sim_screenshot_capture(sim_display_get_framebuffer(),
                                       SIM_LCD_H_RES, SIM_LCD_V_RES,
                                       SDL_GetTicks(), "manual");
                printf("[key] screenshot saved\n");
                break;
            }
            case SDLK_z: {
                /* Force sleeping state via set_status event */
                ble_evt_t sleep_evt = { .type = BLE_EVT_SET_STATUS, .status = DISPLAY_STATUS_SLEEPING };
                ui_manager_handle_event(&sleep_evt);
                printf("[key] sleep\n");
                break;
            }
            case SDLK_1: case SDLK_2: case SDLK_3: case SDLK_4:
            case SDLK_5: case SDLK_6: case SDLK_7: case SDLK_8: {
                /* Dismiss by index — requires tracking, simplified for interactive */
                int idx = e.key.keysym.sym - SDLK_1;
                ble_evt_t evt = { .type = BLE_EVT_NOTIF_DISMISS };
                snprintf(evt.id, sizeof(evt.id), "key_%d", idx);
                ui_manager_handle_event(&evt);
                printf("[key] dismiss %d\n", idx);
                break;
            }
            default:
                break;
            }
        }
    }
}

/* ---- Main ---- */

int main(int argc, char *argv[])
{
    parse_args(argc, argv);

    /* 1. Init LVGL */
    lv_init();

    /* 2. Init display */
    sim_display_init(opt_headless, opt_scale);

    /* 3. Init events */
    if (opt_events) sim_events_init_inline(opt_events);
    if (opt_scenario) sim_events_init_scenario(opt_scenario);

    /* 3b. Init TCP listener */
    if (opt_listen_port > 0) {
        if (sim_socket_init(opt_listen_port) != 0) {
            fprintf(stderr, "Failed to start TCP listener on port %d\n", opt_listen_port);
            return 1;
        }
    }

    /* 4. Init UI manager (creates scene + notification panel) */
    ui_manager_init();

    /* 5. Init screenshots */
    if (opt_screenshot_dir) sim_screenshot_init(opt_screenshot_dir);

    /* 6. Run */
    if (opt_headless) {
        run_headless();
    } else {
        run_interactive();
    }

    /* 7. Cleanup */
    /* 7a. Shutdown TCP listener */
    if (opt_listen_port > 0) {
        sim_socket_shutdown();
    }
    sim_display_shutdown();

    return 0;
}
