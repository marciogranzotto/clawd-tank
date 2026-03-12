#!/usr/bin/env python3
"""
SVG Animation to PNG Frame Sequence Converter.

Renders animated SVGs (CSS @keyframes + SVG animate elements) frame-by-frame
using headless Chromium via Playwright, producing PNG sequences ready for
the png2rgb565.py pipeline.

Usage:
    python tools/svg2frames.py <input.svg> <output_dir/> [options]
      --fps 8          Frame rate (default: 8)
      --duration auto  Animation duration in seconds, or 'auto' to detect from SVG (default: auto)
      --scale 6        Scale multiplier from SVG units to pixels (default: 6)
      --background transparent  Background color or 'transparent' (default: transparent)

Example:
    python tools/svg2frames.py assets/svg-animations/clawd-happy.svg /tmp/happy-frames/ --fps 10 --scale 6
"""

import argparse
import math
import os
import re
import sys
from pathlib import Path


def ensure_playwright():
    """Check that playwright is installed, give helpful error if not."""
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError:
        print("Error: playwright is required. Install it with:")
        print("  pip3 install playwright")
        print("  python3 -m playwright install chromium")
        sys.exit(1)


def parse_css_duration(value: str) -> float:
    """Parse a CSS time value like '1s', '500ms', '1.5s' to seconds."""
    value = value.strip()
    if value.endswith("ms"):
        return float(value[:-2]) / 1000.0
    elif value.endswith("s"):
        return float(value[:-1])
    return 0.0


def detect_animation_duration(svg_text: str) -> float:
    """
    Auto-detect the animation cycle length from SVG content.

    Looks at:
    - CSS animation shorthand: animation: name Xs ...
    - CSS animation-duration property: animation-duration: Xs
    - SVG animate/animateTransform dur attribute: dur="Xs"

    Returns the maximum duration found, or 1.0s as a fallback.
    """
    durations = []

    # CSS animation shorthand: animation: <name> <duration> ...
    # The duration is the first time value after the name token.
    # Pattern: animation: <anything> <time>
    for match in re.finditer(
        r"animation\s*:\s*[^;{]+?(\d+(?:\.\d+)?(?:ms|s))", svg_text
    ):
        try:
            durations.append(parse_css_duration(match.group(1)))
        except ValueError:
            pass

    # CSS animation-duration property
    for match in re.finditer(
        r"animation-duration\s*:\s*(\d+(?:\.\d+)?(?:ms|s))", svg_text
    ):
        try:
            durations.append(parse_css_duration(match.group(1)))
        except ValueError:
            pass

    # SVG animate/animateTransform dur attribute
    for match in re.finditer(r'\bdur\s*=\s*["\'](\d+(?:\.\d+)?(?:ms|s))["\']', svg_text):
        try:
            durations.append(parse_css_duration(match.group(1)))
        except ValueError:
            pass

    if not durations:
        print("Warning: No animation durations detected; defaulting to 1.0s.")
        return 1.0

    max_dur = max(durations)
    print(f"Detected animation durations: {sorted(set(durations))}s → using {max_dur}s")
    return max_dur


def build_html_wrapper(svg_path: Path, scale: float, background: str) -> str:
    """
    Build an HTML page that renders the SVG at the exact target pixel size.

    The SVG viewBox is preserved so the browser maps SVG units to screen pixels
    at the requested scale (e.g., scale=3 → 1 SVG unit = 3px). shape-rendering:
    crispEdges ensures rectangles snap to pixel boundaries.
    """
    svg_text = svg_path.read_text(encoding="utf-8")

    # Parse viewBox to compute target canvas dimensions
    vb_match = re.search(r'viewBox\s*=\s*["\']([^"\']+)["\']', svg_text)
    if vb_match:
        parts = vb_match.group(1).split()
        vb_w = float(parts[2])
        vb_h = float(parts[3])
        canvas_w = math.ceil(vb_w * scale)
        canvas_h = math.ceil(vb_h * scale)
    else:
        w_match = re.search(r'\bwidth\s*=\s*["\'](\d+(?:\.\d+)?)["\']', svg_text)
        h_match = re.search(r'\bheight\s*=\s*["\'](\d+(?:\.\d+)?)["\']', svg_text)
        canvas_w = math.ceil(float(w_match.group(1)) * scale) if w_match else 300
        canvas_h = math.ceil(float(h_match.group(1)) * scale) if h_match else 300

    bg_css = background if background != "transparent" else "transparent"

    # Strip the SVG's own width/height — we control sizing via CSS
    svg_text = re.sub(
        r'(<svg[^>]*?)\s+width\s*=\s*["\'][^"\']*["\']', r"\1", svg_text
    )
    svg_text = re.sub(
        r'(<svg[^>]*?)\s+height\s*=\s*["\'][^"\']*["\']', r"\1", svg_text
    )

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8"/>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  html, body {{
    width: {canvas_w}px;
    height: {canvas_h}px;
    overflow: hidden;
    background: {bg_css};
  }}
  #svg-container {{
    width: {canvas_w}px;
    height: {canvas_h}px;
  }}
  #svg-container svg {{
    width: {canvas_w}px;
    height: {canvas_h}px;
    shape-rendering: crispEdges;
  }}
  #svg-container svg * {{
    shape-rendering: crispEdges;
  }}
</style>
</head>
<body>
<div id="svg-container">
{svg_text}
</div>
<script>
function pauseAll() {{
  document.getAnimations().forEach(function(anim) {{
    anim.pause();
  }});
}}

window.seekAnimations = function(timeMs) {{
  document.getAnimations().forEach(function(anim) {{
    anim.currentTime = timeMs;
  }});
}};

requestAnimationFrame(function() {{
  requestAnimationFrame(function() {{
    pauseAll();
  }});
}});
</script>
</body>
</html>"""
    return html, canvas_w, canvas_h


def snap_pixel_art(frame_path: Path, num_colors: int = 12):
    """
    Post-process a rendered frame to remove anti-aliasing artifacts.

    CSS transforms (translate, scale, rotate) cause the browser to anti-alias
    pixel-art edges even with shape-rendering: crispEdges. This snaps every
    pixel to the nearest color in an auto-detected palette, eliminating
    intermediate anti-aliased colors.
    """
    from PIL import Image
    import numpy as np

    img = Image.open(frame_path).convert("RGBA")
    data = np.array(img)

    # Separate alpha and RGB
    alpha = data[:, :, 3]
    rgb = data[:, :, :3]

    # Three alpha tiers:
    #   alpha > 200  → pixel art: make fully opaque + snap RGB to palette
    #   16 < alpha ≤ 200 → intentional semi-transparent (aura, shadows): preserve as-is
    #   alpha ≤ 16   → noise: make fully transparent
    solid_mask = alpha > 200
    noise_mask = alpha <= 16

    # Kill noise
    alpha_out = alpha.copy()
    alpha_out[noise_mask] = 0
    alpha_out[solid_mask] = 255

    # Build palette from solid pixels (the "true" sprite colors)
    if not np.any(solid_mask):
        data[:, :, 3] = alpha_out
        Image.fromarray(data, "RGBA").save(frame_path)
        return

    solid_pixels = rgb[solid_mask]

    # Quantize solid pixels to find the dominant colors:
    # Round RGB to nearest 16 to cluster similar anti-aliased shades
    quantized = (solid_pixels // 16 * 16).astype(np.uint8)
    unique_flat = np.unique(
        quantized.reshape(-1, 3).view(np.dtype([('r', 'u1'), ('g', 'u1'), ('b', 'u1')])),
        return_counts=True
    )
    colors_structured = unique_flat[0]
    counts = unique_flat[1]

    # Take the top N most common colors as the palette
    top_idx = np.argsort(-counts)[:num_colors]
    palette = np.array(
        [[c['r'], c['g'], c['b']] for c in colors_structured[top_idx]],
        dtype=np.float32
    )

    # Snap only solid pixels to nearest palette color (Euclidean distance)
    solid_rgb = rgb[solid_mask].astype(np.float32)
    dists = np.sum((solid_rgb[:, None, :] - palette[None, :, :]) ** 2, axis=2)
    nearest_idx = np.argmin(dists, axis=1)
    snapped = palette[nearest_idx].astype(np.uint8)

    # Write back — only solid pixels get RGB-snapped; semi-transparent kept as-is
    rgb[solid_mask] = snapped
    data[:, :, :3] = rgb
    data[:, :, 3] = alpha_out

    Image.fromarray(data, "RGBA").save(frame_path)


def render_frames(
    svg_path: Path,
    output_dir: Path,
    fps: float,
    duration: float,
    scale: float,
    background: str,
):
    """Render SVG animation frames at the exact target size using Playwright."""
    from playwright.sync_api import sync_playwright

    output_dir.mkdir(parents=True, exist_ok=True)

    html_content, canvas_w, canvas_h = build_html_wrapper(svg_path, scale, background)

    tmp_html = output_dir / "_svg2frames_tmp.html"
    tmp_html.write_text(html_content, encoding="utf-8")

    frame_interval = 1.0 / fps
    n_frames = max(1, round(duration * fps))
    timestamps = [frame_interval * i for i in range(n_frames)]

    print(f"Canvas size: {canvas_w}x{canvas_h}px (scale {scale}x)")
    print(f"Rendering {n_frames} frames at {fps} fps (duration={duration:.3f}s)...")

    saved = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": canvas_w, "height": canvas_h},
            device_scale_factor=1,
        )
        page = context.new_page()

        page.goto(tmp_html.as_uri())
        page.wait_for_load_state("networkidle")
        page.evaluate("() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")

        container = page.query_selector("#svg-container")

        for i, ts in enumerate(timestamps):
            ts_ms = ts * 1000.0
            page.evaluate(f"window.seekAnimations({ts_ms})")
            page.evaluate("() => new Promise(r => requestAnimationFrame(r))")

            frame_name = f"frame_{i:02d}.png"
            out_path = output_dir / frame_name

            omit = background == "transparent"
            container.screenshot(path=str(out_path), omit_background=omit)

            # Snap anti-aliased pixels to nearest palette color (pixel art cleanup)
            snap_pixel_art(out_path)

            saved.append(out_path)
            print(f"  Saved {frame_name}  (t={ts:.3f}s)")

        browser.close()

    tmp_html.unlink(missing_ok=True)

    return saved, canvas_w, canvas_h


def main():
    parser = argparse.ArgumentParser(
        description="Convert animated SVG to PNG frame sequence for png2rgb565.py pipeline"
    )
    parser.add_argument("input_svg", help="Input animated SVG file")
    parser.add_argument("output_dir", help="Output directory for PNG frames")
    parser.add_argument(
        "--fps", type=float, default=8.0, help="Frames per second (default: 8)"
    )
    parser.add_argument(
        "--duration",
        default="auto",
        help="Animation duration in seconds, or 'auto' to detect from SVG (default: auto)",
    )
    parser.add_argument(
        "--scale",
        type=float,
        default=6.0,
        help="Scale multiplier from SVG units to pixels (default: 6)",
    )
    parser.add_argument(
        "--background",
        default="transparent",
        help="Background color or 'transparent' (default: transparent)",
    )
    args = parser.parse_args()

    ensure_playwright()

    svg_path = Path(args.input_svg)
    if not svg_path.is_file():
        print(f"Error: {svg_path} is not a file")
        sys.exit(1)

    output_dir = Path(args.output_dir)

    # Determine duration
    if args.duration == "auto":
        svg_text = svg_path.read_text(encoding="utf-8")
        duration = detect_animation_duration(svg_text)
    else:
        try:
            duration = float(args.duration)
        except ValueError:
            print(f"Error: --duration must be a number or 'auto', got: {args.duration!r}")
            sys.exit(1)

    if duration <= 0:
        print(f"Error: animation duration must be > 0, got {duration}")
        sys.exit(1)

    saved_frames, canvas_w, canvas_h = render_frames(
        svg_path=svg_path,
        output_dir=output_dir,
        fps=args.fps,
        duration=duration,
        scale=args.scale,
        background=args.background,
    )

    print()
    print("Done.")
    print(f"  Input:      {svg_path}")
    print(f"  Output dir: {output_dir}")
    print(f"  Frames:     {len(saved_frames)}")
    print(f"  Size:       {canvas_w}x{canvas_h}px")
    print(f"  Duration:   {duration:.3f}s @ {args.fps} fps")
    print()
    print("Next step — convert to RGB565 header:")
    name_hint = svg_path.stem.replace("-", "_")
    print(
        f"  python3 tools/png2rgb565.py {output_dir} firmware/main/{name_hint}_frames.h --name {name_hint}"
    )


if __name__ == "__main__":
    main()
