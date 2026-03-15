/* pixel_font.c — 5x5 pixel-art bitmap font for HUD counters
 *
 * Each glyph is 5 rows of 5 bits, stored as uint8_t[5].
 * Bit layout per row: bit4=leftmost pixel, bit0=rightmost pixel.
 * Example: 0x1F = 11111 = all 5 pixels lit.
 */
#include "pixel_font.h"

/* ----- Glyph bitmaps (5x5) -----
 *
 * Visual key: # = pixel on, . = pixel off
 * Bits are MSB-left: bit4 bit3 bit2 bit1 bit0
 */

static const uint8_t glyph_0[5] = {
    /* .###. */ 0x0E,
    /* #...# */ 0x11,
    /* #...# */ 0x11,
    /* #...# */ 0x11,
    /* .###. */ 0x0E,
};

static const uint8_t glyph_1[5] = {
    /* ..#.. */ 0x04,
    /* .##.. */ 0x0C,
    /* ..#.. */ 0x04,
    /* ..#.. */ 0x04,
    /* .###. */ 0x0E,
};

static const uint8_t glyph_2[5] = {
    /* .###. */ 0x0E,
    /* #...# */ 0x11,
    /* ..##. */ 0x06,
    /* .#... */ 0x08,
    /* ##### */ 0x1F,
};

static const uint8_t glyph_3[5] = {
    /* ####. */ 0x1E,
    /* ....# */ 0x01,
    /* .###. */ 0x0E,
    /* ....# */ 0x01,
    /* ####. */ 0x1E,
};

static const uint8_t glyph_4[5] = {
    /* #..#. */ 0x12,
    /* #..#. */ 0x12,
    /* ##### */ 0x1F,
    /* ...#. */ 0x02,
    /* ...#. */ 0x02,
};

static const uint8_t glyph_5[5] = {
    /* ##### */ 0x1F,
    /* #.... */ 0x10,
    /* ####. */ 0x1E,
    /* ....# */ 0x01,
    /* ####. */ 0x1E,
};

static const uint8_t glyph_6[5] = {
    /* .###. */ 0x0E,
    /* #.... */ 0x10,
    /* ####. */ 0x1E,
    /* #...# */ 0x11,
    /* .###. */ 0x0E,
};

static const uint8_t glyph_7[5] = {
    /* ##### */ 0x1F,
    /* ....# */ 0x01,
    /* ...#. */ 0x02,
    /* ..#.. */ 0x04,
    /* ..#.. */ 0x04,
};

static const uint8_t glyph_8[5] = {
    /* .###. */ 0x0E,
    /* #...# */ 0x11,
    /* .###. */ 0x0E,
    /* #...# */ 0x11,
    /* .###. */ 0x0E,
};

static const uint8_t glyph_9[5] = {
    /* .###. */ 0x0E,
    /* #...# */ 0x11,
    /* .#### */ 0x0F,
    /* ....# */ 0x01,
    /* .###. */ 0x0E,
};

static const uint8_t glyph_x[5] = {
    /* ..... */ 0x00,
    /* #...# */ 0x11,
    /* .#.#. */ 0x0A,
    /* #...# */ 0x11,
    /* ..... */ 0x00,
};

static const uint8_t glyph_plus[5] = {
    /* ..... */ 0x00,
    /* ..#.. */ 0x04,
    /* .###. */ 0x0E,
    /* ..#.. */ 0x04,
    /* ..... */ 0x00,
};

static const uint8_t *get_glyph(char c)
{
    switch (c) {
        case '0': return glyph_0;
        case '1': return glyph_1;
        case '2': return glyph_2;
        case '3': return glyph_3;
        case '4': return glyph_4;
        case '5': return glyph_5;
        case '6': return glyph_6;
        case '7': return glyph_7;
        case '8': return glyph_8;
        case '9': return glyph_9;
        case 'x': case 'X': return glyph_x;
        case '+':            return glyph_plus;
        default:             return NULL;
    }
}

void pixel_font_draw(lv_obj_t *canvas, const char *text,
                     int x, int y, int px_size, lv_color_t color)
{
    for (const char *p = text; *p; p++) {
        const uint8_t *glyph = get_glyph(*p);
        if (!glyph) {
            /* Unknown character — advance by a small space */
            x += 3 * px_size;
            continue;
        }

        for (int row = 0; row < 5; row++) {
            for (int col = 0; col < 5; col++) {
                if (glyph[row] & (0x10 >> col)) {
                    for (int dy = 0; dy < px_size; dy++) {
                        for (int dx = 0; dx < px_size; dx++) {
                            lv_canvas_set_px(canvas,
                                             x + col * px_size + dx,
                                             y + row * px_size + dy,
                                             color, LV_OPA_COVER);
                        }
                    }
                }
            }
        }
        x += 6 * px_size; /* 5 pixels + 1 pixel gap */
    }
}
