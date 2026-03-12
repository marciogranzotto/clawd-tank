#!/usr/bin/env python3
"""
Generate PNG frames for Clawd alert and happy animations.

Uses the same pixel coordinates as the HTML designers for consistency.
Output PNGs use #1a1a2e background which png2rgb565.py treats as transparent.

Usage:
    python tools/sprite-designer/generate_sprite_pngs.py
"""

import os
import sys

try:
    from PIL import Image, ImageDraw
except ImportError:
    print("Error: Pillow is required. Install with: pip install Pillow")
    sys.exit(1)

W, H = 64, 64
BG = (0x1A, 0x1A, 0x2E)
BODY_COLOR = (0xFF, 0x6B, 0x2B)
EYE_COLOR = (0x00, 0x00, 0x00)
ALERT_COLOR = (0xFF, 0xDD, 0x57)
SPARKLE_COLOR = (0xFF, 0xDD, 0x57)

# Clawd base dimensions (matches overlay in index.html)
BODY = {"x": 18, "y": 23, "w": 28, "h": 18}
EYES_LEFT = {"x": 25, "y": 28}
EYES_RIGHT = {"x": 37, "y": 28}
EYE_SIZE = 2
LEG_POSITIONS = [21, 26, 36, 41]
LEG_Y = 41
LEG_W, LEG_H = 2, 5
CLAW_LEFT = {"x": 14, "y": 27}
CLAW_RIGHT = {"x": 46, "y": 27}
CLAW_W, CLAW_H = 4, 4


def new_frame():
    """Create a new 64x64 image with background color."""
    img = Image.new("RGB", (W, H), BG)
    return img, ImageDraw.Draw(img)


def draw_rect(draw, x, y, w, h, color):
    """Draw a filled rectangle (x, y, width, height)."""
    draw.rectangle([x, y, x + w - 1, y + h - 1], fill=color)


def draw_clawd(draw, dx=0, dy=0, eye_dx=0, leg_h=LEG_H):
    """Draw the base Clawd sprite with optional offsets."""
    # Body
    draw_rect(draw, BODY["x"] + dx, BODY["y"] + dy, BODY["w"], BODY["h"], BODY_COLOR)

    # Left claw
    draw_rect(draw, CLAW_LEFT["x"] + dx, CLAW_LEFT["y"] + dy, CLAW_W, CLAW_H, BODY_COLOR)

    # Right claw
    draw_rect(draw, CLAW_RIGHT["x"] + dx, CLAW_RIGHT["y"] + dy, CLAW_W, CLAW_H, BODY_COLOR)

    # Legs
    for lx in LEG_POSITIONS:
        draw_rect(draw, lx + dx, LEG_Y + dy, LEG_W, leg_h, BODY_COLOR)

    # Eyes
    draw_rect(
        draw,
        EYES_LEFT["x"] + dx + eye_dx,
        EYES_LEFT["y"] + dy,
        EYE_SIZE,
        EYE_SIZE,
        EYE_COLOR,
    )
    draw_rect(
        draw,
        EYES_RIGHT["x"] + dx + eye_dx,
        EYES_RIGHT["y"] + dy,
        EYE_SIZE,
        EYE_SIZE,
        EYE_COLOR,
    )


def draw_exclamation(draw, dx=0):
    """Draw '!' alert mark above Clawd's head."""
    ex, ey = 32, 16
    # Stick (2x2)
    draw_rect(draw, ex, ey, 2, 2, ALERT_COLOR)
    # Dot (2x1, with 1px gap)
    draw_rect(draw, ex, ey + 3, 2, 1, ALERT_COLOR)


def draw_sparkles(draw, dy=0):
    """Draw sparkle crosses at body corners."""
    bx = BODY["x"]
    by = BODY["y"] + dy
    bx2 = bx + BODY["w"]
    by2 = by + BODY["h"]

    # Each sparkle is a small cross pattern
    sparkle_points = [
        # Top-left
        [(bx - 3, by - 1), (bx - 2, by - 2), (bx - 2, by), (bx - 1, by - 1)],
        # Top-right
        [(bx2 + 1, by - 1), (bx2 + 2, by - 2), (bx2 + 2, by), (bx2 + 3, by - 1)],
        # Bottom-left
        [(bx - 3, by2 + 1), (bx - 2, by2), (bx - 2, by2 + 2), (bx - 1, by2 + 1)],
        # Bottom-right
        [(bx2 + 1, by2 + 1), (bx2 + 2, by2), (bx2 + 2, by2 + 2), (bx2 + 3, by2 + 1)],
    ]

    for points in sparkle_points:
        for px, py in points:
            if 0 <= px < W and 0 <= py < H:
                draw.point((px, py), fill=SPARKLE_COLOR)


def generate_alert_frames():
    """Generate 6 frames for the alert animation."""
    frames = []

    # Frame 0: Neutral pose
    img, draw = new_frame()
    draw_clawd(draw)
    frames.append(img)

    # Frame 1: Eyes shift right 1px
    img, draw = new_frame()
    draw_clawd(draw, eye_dx=1)
    frames.append(img)

    # Frame 2: Body leans right 1px, eyes shifted
    img, draw = new_frame()
    draw_clawd(draw, dx=1, eye_dx=1)
    frames.append(img)

    # Frame 3: "!" appears above head
    img, draw = new_frame()
    draw_clawd(draw, dx=1, eye_dx=1)
    draw_exclamation(draw, dx=1)
    frames.append(img)

    # Frame 4: Hold alert pose
    img, draw = new_frame()
    draw_clawd(draw, dx=1, eye_dx=1)
    draw_exclamation(draw, dx=1)
    frames.append(img)

    # Frame 5: "!" fades, body still leaning
    img, draw = new_frame()
    draw_clawd(draw, dx=1, eye_dx=1)
    frames.append(img)

    return frames


def generate_happy_frames():
    """Generate 6 frames for the happy animation."""
    frames = []

    # Frame 0: Neutral pose
    img, draw = new_frame()
    draw_clawd(draw)
    frames.append(img)

    # Frame 1: Crouch (legs shorten by 2px)
    img, draw = new_frame()
    draw_clawd(draw, leg_h=3)
    frames.append(img)

    # Frame 2: Jump up 4px, legs extend
    img, draw = new_frame()
    draw_clawd(draw, dy=-4)
    frames.append(img)

    # Frame 3: Peak with sparkles
    img, draw = new_frame()
    draw_clawd(draw, dy=-4)
    draw_sparkles(draw, dy=-4)
    frames.append(img)

    # Frame 4: Coming down (body at +2px from normal = -2 offset)
    img, draw = new_frame()
    draw_clawd(draw, dy=-2)
    frames.append(img)

    # Frame 5: Landing (back to neutral)
    img, draw = new_frame()
    draw_clawd(draw)
    frames.append(img)

    return frames


def save_frames(frames, output_dir):
    """Save frames as numbered PNG files."""
    os.makedirs(output_dir, exist_ok=True)
    for i, img in enumerate(frames):
        path = os.path.join(output_dir, f"frame_{i:02d}.png")
        img.save(path, "PNG")
        print(f"  Saved: {path}")


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))

    # Alert animation
    print("Generating alert animation frames...")
    alert_frames = generate_alert_frames()
    alert_dir = os.path.join(script_dir, "exports", "alert")
    save_frames(alert_frames, alert_dir)
    print(f"  {len(alert_frames)} frames saved to {alert_dir}\n")

    # Happy animation
    print("Generating happy animation frames...")
    happy_frames = generate_happy_frames()
    happy_dir = os.path.join(script_dir, "exports", "happy")
    save_frames(happy_frames, happy_dir)
    print(f"  {len(happy_frames)} frames saved to {happy_dir}\n")

    print("Done! Run png2rgb565.py to convert to C headers.")


if __name__ == "__main__":
    main()
