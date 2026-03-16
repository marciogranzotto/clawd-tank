#define STB_IMAGE_WRITE_IMPLEMENTATION
#include "stb_image_write.h"
#include "sim_screenshot.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

static char s_output_dir[256] = "";

void sim_screenshot_init(const char *output_dir)
{
    if (!output_dir) return;
    snprintf(s_output_dir, sizeof(s_output_dir), "%s", output_dir);
    /* Strip trailing slashes to avoid double-slash in output paths */
    size_t len = strlen(s_output_dir);
    while (len > 1 && s_output_dir[len - 1] == '/') s_output_dir[--len] = '\0';

    /* Create directory if it doesn't exist */
    mkdir(s_output_dir, 0755);
}

void sim_screenshot_capture(const uint16_t *framebuffer, int w, int h,
                            uint32_t time_ms, const char *suffix)
{
    if (!s_output_dir[0]) return;

    /* Build filename */
    char path[512];
    if (suffix && suffix[0]) {
        snprintf(path, sizeof(path), "%s/event_%06u_%s.png",
                 s_output_dir, time_ms, suffix);
    } else {
        snprintf(path, sizeof(path), "%s/frame_%06u.png",
                 s_output_dir, time_ms);
    }

    /* Convert RGB565 to RGB888 */
    uint8_t *rgb = malloc(w * h * 3);
    if (!rgb) return;

    for (int i = 0; i < w * h; i++) {
        uint16_t pixel = framebuffer[i];
        uint8_t r5 = (pixel >> 11) & 0x1F;
        uint8_t g6 = (pixel >> 5) & 0x3F;
        uint8_t b5 = pixel & 0x1F;
        rgb[i * 3 + 0] = (r5 << 3) | (r5 >> 2);
        rgb[i * 3 + 1] = (g6 << 2) | (g6 >> 4);
        rgb[i * 3 + 2] = (b5 << 3) | (b5 >> 2);
    }

    stbi_write_png(path, w, h, 3, rgb, w * 3);
    free(rgb);

    printf("[screenshot] %s\n", path);
}
