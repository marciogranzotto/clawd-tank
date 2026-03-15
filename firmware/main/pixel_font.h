/* pixel_font.h — 5x5 pixel-art bitmap digits */
#pragma once
#include "lvgl.h"

/* Draw a pixel-art string at (x, y) with given pixel size and color.
 * Supported characters: 0-9, x (multiply), + */
void pixel_font_draw(lv_obj_t *canvas, const char *text,
                     int x, int y, int px_size, lv_color_t color);
