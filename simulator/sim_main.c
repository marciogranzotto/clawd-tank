#include "lvgl.h"
#include "sim_display.h"
#include "sim_screenshot.h"
#include "ui_manager.h"
#include <stdio.h>

int main(int argc, char *argv[])
{
    (void)argc;
    (void)argv;

    lv_init();
    sim_display_init(true, 3); /* headless for now */
    ui_manager_init();

    /* Run a few ticks to let LVGL render */
    for (int i = 0; i < 10; i++) {
        sim_advance_tick(33);
        ui_manager_tick();
    }

    sim_screenshot_init("./shots");
    sim_screenshot_capture(sim_display_get_framebuffer(),
                           SIM_LCD_H_RES, SIM_LCD_V_RES, 0, NULL);
    printf("Screenshot saved to ./shots/frame_000000.png\n");
    return 0;
}
