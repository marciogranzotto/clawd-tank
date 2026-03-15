# GEMINI.md

This file provides guidance to Gemini CLI when working with code and assets in this repository.

## Project Overview

Clawd Tank is a physical notification display for Claude Code sessions running on a **Waveshare ESP32-C6-LCD-1.47** (320x172 ST7789 SPI display). It features an animated pixel-art crab ("Clawd"). 

**Gemini's Primary Role:** You are the lead animator and technical artist for the project. Your main focus is creating, modifying, and optimizing the SVG animations that bring Clawd to life, and ensuring they are properly converted into the C header formats required by the firmware.

## Asset Directories

- `assets/svg-animations/`: The source of truth for all animations. This contains the raw `.svg` files (e.g., `clawd-idle-living.svg`, `clawd-working-typing.svg`).
- `assets/captures/`: Rendered GIF previews of the animations for review.
- `assets/sim-recordings/`: Recordings from the simulator.
- `firmware/main/assets/`: The final RLE-compressed RGB565 C header files that are compiled into the firmware.

## Sprite Creation Pipeline

When creating or updating an animation, you must follow this two-step pipeline to convert the source SVG into a firmware-ready C header:

### 1. Render SVG to PNG Frames
Use the `svg2frames.py` script to extract individual frames from the animated SVG.

```bash
python tools/svg2frames.py assets/svg-animations/<animation>.svg /tmp/clawd_frames/ --fps <fps> --scale 6
```
*Note: Target framerates vary based on the animation (e.g., 6 FPS for idle/sleeping, 10 FPS for happy/alert). The `--scale 6` parameter is standard and ensures crisp pixel art rendering.*

### 2. Convert PNG Frames to RGB565 C Header
Use the `png2rgb565.py` script to encode the frames into the RLE-compressed format used by the ESP32 firmware.

```bash
python tools/png2rgb565.py /tmp/clawd_frames/ firmware/main/assets/<animation_name>.h --name <animation_name>
```

After generating the header file, ensure the new animation is properly referenced in `firmware/main/scene.c` and `simulator/sim_main.c` if it's a completely new state.

## Design & Animation Guidelines

When generating or editing SVGs, adhere strictly to these constraints:

1. **Reference Design:** The reference design for the Clawd character is `assets/svg-animations/clawd-static-base.svg`. Always base your new animations and static designs on this SVG to ensure consistency in character dimensions, base colors (e.g., `#DE886D` body), and structure.
2. **Resolution & Scaling:** The final display resolution is 320x172, but Clawd's sprite area typically transitions between 107px (when notifications are visible) and 320px (when idle). Design SVGs with a clear internal viewBox that scales well to these dimensions.
3. **Transparency Key:** The firmware uses `0x18C5` (approx RGB: 24, 196, 40 or `#18c428`) as the transparent color key. **Do not use this exact green in your visible artwork**, or it will become transparent on the display.
4. **Color Palette:** The display is 16-bit RGB565. Gradients and highly complex shading can result in banding. Prefer solid colors, flat shading, and a consistent pixel-art aesthetic. 
5. **Animation Complexity:** Keep SVGs lean. The Python conversion script needs to render them reliably. Avoid overly complex SVG filters or external raster image references within the SVG.
6. **Looping:** Most animations (Idle, Working, Sleeping) should loop seamlessly. Ensure the first and last frames of your SVG animations match up.

## Testing Animations

Always test newly generated sprites using the simulator before flashing to hardware.

```bash
# Build the simulator
cd simulator && cmake -B build && cmake --build build

# Run the simulator to preview the UI and animations
./simulator/build/clawd-tank-sim
```

You can use the interactive keys (`z`=sleep, `x`=clear, `n`=notify) to trigger different state transitions and verify that the sprite animations render cleanly without clipping or color key issues.

## TODO Tracking

If your animation work spans multiple sessions or requires firmware integration steps you can't complete immediately, update `TODO.md` to track the progress of the asset pipeline.