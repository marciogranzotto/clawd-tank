#ifndef DISPLAY_H
#define DISPLAY_H

#include "lvgl.h"
#include <stdint.h>

/* Stub declarations — simulator handles display via SDL2.
 * Implementations are in sim_display_shim.c. */
lv_display_t *display_init(void);
void display_set_brightness(uint8_t duty);

#endif /* DISPLAY_H */
