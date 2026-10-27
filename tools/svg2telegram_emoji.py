#!/usr/bin/env python3
"""
Convert animated SVGs to Telegram video custom emoji (.webm).

Telegram video emoji requirements (https://core.telegram.org/stickers):
  WebM / VP9, exactly 100x100 px, <= 3 s, <= 30 fps, no audio.
The page says <= 256 KB, but that is the video *sticker* limit: the Bot API
rejects custom emoji over 64 KiB with "file is too big" (measured 2026-10-27:
a 52 KB emoji was accepted, 64.7 KB and 82 KB were rejected).

Each SVG is rendered with Playwright (reuses svg2frames), cropped to the union
bounding box across the whole loop and centered on a transparent 100x100
canvas. The SVG is rasterized directly at the scale that makes that box fit
100 px, so pixel-art edges come from the browser, not from a resample.

Loops longer than 3 s have to be fitted into 3 s. In order of preference:
  1. Loop <= MAX_SPEEDUP x 3 s: play the whole loop, sped up.
  2. A seamless sub-loop (a window whose last frame matches its first) of
     <= MAX_SPEEDUP x 3 s: play just that window, sped up if needed.
  3. Otherwise: play the whole loop, time-compressed into 3 s.

Requires ffmpeg with libvpx-vp9.

Usage:
    python tools/svg2telegram_emoji.py [input_dir] [output_dir]
      --only clawd-happy    Only process this SVG stem
"""

import argparse
import io
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent))
from svg2frames import detect_animation_duration, ensure_playwright, render_frames  # noqa: E402
from svg2slack_emoji import find_union_bbox  # noqa: E402

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

SIZE = 100            # Telegram emoji are exactly 100x100
MAX_SECONDS = 3.0     # Telegram video sticker/emoji limit
FPS = 30              # Telegram max frame rate
MAX_BYTES = 64 * 1024  # custom emoji limit (measured; see module docstring)
MAX_SPEEDUP = 1.5     # faster than this reads as a different animation
BBOX_SCALE = 4.0      # render scale for the bounding-box pass
SEAMLESS_TOLERANCE = 0.002  # mean abs RGBA diff (0..1) for "frames match"


def quiet(fn, *args, **kwargs):
    """Run an svg2frames helper without its per-frame progress output."""
    import contextlib
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*args, **kwargs)


def content_box_units(svg_path, duration, tmp):
    """Union bbox of the whole loop in SVG user units (relative to the viewBox origin)."""
    out = tmp / "bbox"
    saved, _, _ = quiet(render_frames, svg_path, out, 10.0, duration, BBOX_SCALE, "transparent")
    bbox = find_union_bbox(saved)
    shutil.rmtree(out, ignore_errors=True)
    if bbox is None:
        return None
    # Pad by one render pixel each side so anti-aliased edges are not clipped.
    return tuple(v / BBOX_SCALE for v in (bbox[0] - 1, bbox[1] - 1, bbox[2] + 1, bbox[3] + 1))


def frame_diff(a, b):
    return float(np.mean(np.abs(a.astype(np.int16) - b.astype(np.int16)))) / 255.0


def choose_window(frames, duration):
    """
    Pick (start_frame, length_frames, speed) for the output loop, where frames
    cover one full source loop at FPS. Returns a description for logging too.
    """
    n = len(frames)
    if duration <= MAX_SECONDS:
        return 0, n, 1.0, "fits"
    max_len = int(MAX_SECONDS * MAX_SPEEDUP * FPS)
    if n <= max_len:
        return 0, n, duration / MAX_SECONDS, "whole loop, sped up"

    arrs = [np.asarray(f) for f in frames]
    # Longest seamless window first: frame[start] == frame[start + length].
    for length in range(max_len, int(1.0 * FPS) - 1, -1):
        best = None
        for start in range(n):
            d = frame_diff(arrs[start], arrs[(start + length) % n])
            if best is None or d < best[0]:
                best = (d, start)
            if d == 0.0:
                break
        if best[0] <= SEAMLESS_TOLERANCE:
            speed = max(1.0, (length / FPS) / MAX_SECONDS)
            return best[1], length, speed, f"seamless {length / FPS:.2f}s sub-loop"
    return 0, n, duration / MAX_SECONDS, "no seamless sub-loop, whole loop compressed"


def sample_output(frames, start, length, speed):
    """Resample the chosen window to FPS at the given speed, <= MAX_SECONDS."""
    n_out = min(int(MAX_SECONDS * FPS), max(1, round(length / speed)))
    n = len(frames)
    return [frames[(start + int(k * speed)) % n] for k in range(n_out)]


def convert_one(svg_path, output_path):
    svg_text = svg_path.read_text(encoding="utf-8")
    duration = quiet(detect_animation_duration, svg_text)
    tmp = Path(tempfile.mkdtemp(prefix="svg2tg_"))
    try:
        box = content_box_units(svg_path, duration, tmp)
        if box is None:
            print("  WARN: renders fully transparent, skipping")
            return
        bw, bh = box[2] - box[0], box[3] - box[1]
        scale = SIZE / max(bw, bh)

        render_dir = tmp / "frames"
        saved, _, _ = quiet(render_frames, svg_path, render_dir, float(FPS), duration, scale, "transparent")

        crop = tuple(round(v * scale) for v in box)
        frames = []
        for p in saved:
            img = Image.open(p).convert("RGBA").crop(crop)
            canvas = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
            canvas.paste(img.crop((0, 0, min(img.width, SIZE), min(img.height, SIZE))),
                         ((SIZE - min(img.width, SIZE)) // 2, (SIZE - min(img.height, SIZE)) // 2))
            frames.append(canvas)

        start, length, speed, how = choose_window(frames, duration)
        out_frames = sample_output(frames, start, length, speed)

        seq = tmp / "out"
        seq.mkdir()
        for i, f in enumerate(out_frames):
            f.save(seq / f"f_{i:04d}.png")

        # Constant quality first; tighten if the file is over Telegram's limit.
        for crf in (24, 32, 40, 48):
            subprocess.run(
                ["ffmpeg", "-loglevel", "error", "-y", "-framerate", str(FPS),
                 "-i", str(seq / "f_%04d.png"), "-c:v", "libvpx-vp9",
                 "-pix_fmt", "yuva420p", "-b:v", "0", "-crf", str(crf),
                 "-an", str(output_path)],
                check=True,
            )
            if output_path.stat().st_size <= MAX_BYTES:
                break
        size_kb = output_path.stat().st_size / 1024
        secs = len(out_frames) / FPS
        warn = "  (OVER 64KB limit)" if output_path.stat().st_size > MAX_BYTES else ""
        print(f"  -> {output_path.name}: {duration:.1f}s source, {how}, "
              f"x{speed:.2f}, {secs:.2f}s, crf {crf}, {size_kb:.1f} KB{warn}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description="Convert animated SVGs to Telegram video emoji")
    parser.add_argument("input_dir", nargs="?", default="assets/svg-animations")
    parser.add_argument("output_dir", nargs="?", default="assets/telegram-emojis")
    parser.add_argument("--only", help="Only process this SVG stem (e.g. clawd-happy)")
    args = parser.parse_args()

    ensure_playwright()
    if shutil.which("ffmpeg") is None:
        print("Error: ffmpeg (with libvpx-vp9) is required")
        sys.exit(1)

    in_dir, out_dir = Path(args.input_dir), Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    svgs = sorted(in_dir.glob("*.svg"))
    if args.only:
        svgs = [s for s in svgs if s.stem == args.only]
    if not svgs:
        print(f"No SVGs found in {in_dir}")
        sys.exit(1)

    for svg in svgs:
        print(f"[{svg.name}]")
        try:
            convert_one(svg, out_dir / f"{svg.stem}.webm")
        except Exception as e:  # noqa: BLE001
            print(f"  ERROR: {e}")


if __name__ == "__main__":
    main()
