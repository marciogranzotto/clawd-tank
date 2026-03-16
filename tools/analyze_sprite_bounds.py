#!/usr/bin/env python3
"""
Analyze sprite bounding boxes in RLE-compressed RGB565 C header files.

For each animation sprite header, decodes all frames and finds the tightest
bounding box containing ALL non-transparent pixels across ALL frames.

Uses SYMMETRIC HORIZONTAL cropping: the crop region is centered on the
sprite's horizontal center so that Clawd stays centered after cropping.
Vertical cropping is asymmetric (crop top freely, keep bottom alignment).

Reports per-animation and summary tables with RGB565A8 (3 bytes/pixel)
buffer sizes for the ESP32-C6 firmware.

The RLE format (from png2rgb565.py):
  - flat uint16_t array: [value0, count0, value1, count1, ...]
  - frame_offsets[i] is a *word* offset (index into uint16_t array)
  - decode: repeat each value by its count to fill width*height pixels
  - transparent key: 0x18C5
"""

import re
import sys
from pathlib import Path

ASSETS_DIR = Path(__file__).parent.parent / "firmware" / "main" / "assets"
TRANSPARENT_KEY = 0x18C5
BYTES_PER_PIXEL = 3  # RGB565A8


def parse_header(path: Path):
    """
    Parse a sprite header file.

    Supports two formats:
      1. RLE format (modern): frame_offsets + rle_data arrays.
      2. Flat format (legacy BLE icon): per-frame uint16_t arrays.

    Returns (prefix, width, height, frame_count, data_a, data_b, fmt).
    """
    text = path.read_text()

    def get_define(pattern):
        m = re.search(pattern, text)
        if m:
            return int(m.group(1))
        return None

    m = re.search(r'#define\s+(\w+)_WIDTH\s+(\d+)', text)
    if not m:
        raise ValueError(f"Cannot find WIDTH define in {path.name}")
    prefix_upper = m.group(1)
    prefix = prefix_upper.lower()
    width = int(m.group(2))

    height = get_define(rf'#define\s+{prefix_upper}_HEIGHT\s+(\d+)')
    frame_count = get_define(rf'#define\s+{prefix_upper}_FRAME_COUNT\s+(\d+)')

    if height is None or frame_count is None:
        raise ValueError(f"Missing HEIGHT or FRAME_COUNT in {path.name}")

    fo_pattern = rf'static\s+const\s+uint32_t\s+{prefix}_frame_offsets\s*\[.*?\]\s*=\s*\{{([^}}]+)\}}'
    fo_m = re.search(fo_pattern, text, re.DOTALL)

    if fo_m:
        frame_offsets = [int(x) for x in re.findall(r'\d+', fo_m.group(1))]
        rle_pattern = rf'static\s+const\s+uint16_t\s+{prefix}_rle_data\s*\[\s*\]\s*=\s*\{{([^}}]+)\}}'
        rle_m = re.search(rle_pattern, text, re.DOTALL)
        if not rle_m:
            raise ValueError(f"Found frame_offsets but no rle_data in {path.name}")
        rle_body = rle_m.group(1)
        tokens = re.findall(r'0x[0-9A-Fa-f]+|\d+', rle_body)
        rle_data = [int(t, 16) if t.startswith('0x') else int(t) for t in tokens]
        return prefix, width, height, frame_count, frame_offsets, rle_data, 'rle'
    else:
        flat_frames = []
        frame_array_pat = rf'static\s+const\s+uint16_t\s+{prefix}_frame_\w+\s*\[\d*\]\s*=\s*\{{([^}}]+)\}}'
        for fm in re.finditer(frame_array_pat, text, re.DOTALL):
            tokens = re.findall(r'0x[0-9A-Fa-f]+|\d+', fm.group(1))
            pixels = [int(t, 16) if t.startswith('0x') else int(t) for t in tokens]
            flat_frames.append(pixels)
        if not flat_frames:
            raise ValueError(f"Cannot find frame_offsets or flat frame arrays in {path.name}")
        return prefix, width, height, frame_count, flat_frames, None, 'flat'


def decode_frame(rle_data, frame_offsets, frame_idx, width, height):
    """Decode a single frame from RLE data."""
    start = frame_offsets[frame_idx]
    end = frame_offsets[frame_idx + 1]
    pixels = []
    i = start
    while i < end:
        value = rle_data[i]
        count = rle_data[i + 1]
        pixels.extend([value] * count)
        i += 2
    expected = width * height
    if len(pixels) < expected:
        pixels.extend([TRANSPARENT_KEY] * (expected - len(pixels)))
    elif len(pixels) > expected:
        pixels = pixels[:expected]
    return pixels


def find_tight_bbox(all_frame_pixels, width, height):
    """
    Find the tightest bounding box across all frames.
    Returns (min_x, min_y, max_x, max_y) inclusive, or None.
    """
    min_x = width
    min_y = height
    max_x = -1
    max_y = -1
    for pixels in all_frame_pixels:
        for idx, val in enumerate(pixels):
            if val != TRANSPARENT_KEY:
                x = idx % width
                y = idx // width
                min_x = min(min_x, x)
                max_x = max(max_x, x)
                min_y = min(min_y, y)
                max_y = max(max_y, y)
    if max_x < 0:
        return None
    return (min_x, min_y, max_x, max_y)


def symmetric_crop(bbox, width, height):
    """
    Compute crop region with SYMMETRIC horizontal padding (keeps Clawd centered)
    and asymmetric vertical cropping (crop top freely, bottom freely).

    Returns (crop_x, crop_y, crop_w, crop_h) where crop_x/crop_y is the
    top-left corner of the crop region in the original sprite coordinates.
    """
    min_x, min_y, max_x, max_y = bbox
    center_x = width / 2.0

    # Symmetric horizontal: find max distance from center on either side
    dist_left = center_x - min_x
    dist_right = max_x - center_x + 1  # +1 because max_x is inclusive
    half_w = max(dist_left, dist_right)

    # Ensure even width for clean centering
    crop_x = int(center_x - half_w)
    crop_w = int(half_w * 2)

    # Clamp to sprite bounds
    if crop_x < 0:
        crop_w += crop_x  # reduce width by overshoot
        crop_x = 0
    if crop_x + crop_w > width:
        crop_w = width - crop_x

    # Ensure even width
    if crop_w % 2 != 0:
        if crop_x + crop_w < width:
            crop_w += 1
        elif crop_x > 0:
            crop_x -= 1
            crop_w += 1

    # Vertical: crop freely
    crop_y = min_y
    crop_h = max_y - min_y + 1

    return crop_x, crop_y, crop_w, crop_h


def analyze_sprite(path: Path):
    """Analyze a single sprite header. Returns a result dict."""
    prefix, width, height, frame_count, data_a, data_b, fmt = parse_header(path)

    all_frame_pixels = []
    if fmt == 'rle':
        frame_offsets, rle_data = data_a, data_b
        for f in range(frame_count):
            pixels = decode_frame(rle_data, frame_offsets, f, width, height)
            all_frame_pixels.append(pixels)
    else:
        all_frame_pixels = data_a

    bbox = find_tight_bbox(all_frame_pixels, width, height)

    if bbox is None:
        return {
            'name': prefix, 'width': width, 'height': height,
            'frame_count': frame_count, 'bbox': None,
            'crop_x': 0, 'crop_y': 0, 'crop_w': 0, 'crop_h': 0,
        }

    crop_x, crop_y, crop_w, crop_h = symmetric_crop(bbox, width, height)

    return {
        'name': prefix, 'width': width, 'height': height,
        'frame_count': frame_count, 'bbox': bbox,
        'crop_x': crop_x, 'crop_y': crop_y,
        'crop_w': crop_w, 'crop_h': crop_h,
    }


def format_table(results):
    """Print formatted analysis tables."""

    print()
    print("=" * 120)
    print("SPRITE BOUNDING BOX ANALYSIS (symmetric horizontal crop)")
    print(f"Transparent key: 0x{TRANSPARENT_KEY:04X}")
    print(f"Buffer size = W * H * {BYTES_PER_PIXEL} bytes (RGB565A8 per frame)")
    print("=" * 120)
    print()

    col_hdr = (
        f"{'Name':<16} {'Curr WxH':>10} {'Tight bbox':>22} "
        f"{'Sym crop':>14} {'Crop WxH':>10} "
        f"{'Curr buf':>10} {'Crop buf':>10} {'Saved':>8} {'%':>6}"
    )
    print(col_hdr)
    print("-" * 120)

    total_curr = 0
    total_crop = 0

    for r in sorted(results, key=lambda x: x['name']):
        name = r['name']
        w, h = r['width'], r['height']
        curr_buf = w * h * BYTES_PER_PIXEL

        if r['bbox'] is None:
            print(f"  {name:<14} {w:>3}x{h:<4}  {'(all transparent)':>22}")
            total_curr += curr_buf
            continue

        min_x, min_y, max_x, max_y = r['bbox']
        cx, cy = r['crop_x'], r['crop_y']
        cw, ch = r['crop_w'], r['crop_h']
        crop_buf = cw * ch * BYTES_PER_PIXEL
        saved = curr_buf - crop_buf
        pct = 100.0 * saved / curr_buf if curr_buf > 0 else 0

        bbox_str = f"({min_x},{min_y})->({max_x},{max_y})"
        crop_region = f"@({cx},{cy})"

        print(
            f"  {name:<14} {w:>3}x{h:<4}  {bbox_str:>22} "
            f" {crop_region:>14}  {cw:>3}x{ch:<4} "
            f"  {curr_buf:>8}B {crop_buf:>8}B {saved:>7}B {pct:5.1f}%"
        )

        total_curr += curr_buf
        total_crop += crop_buf

    print("-" * 120)
    total_saved = total_curr - total_crop
    total_pct = 100.0 * total_saved / total_curr if total_curr > 0 else 0
    print(f"  {'TOTAL':<14} {'':>10} {'':>22}  {'':>14} {'':>10}"
          f"  {total_curr:>8}B {total_crop:>8}B {total_saved:>7}B {total_pct:5.1f}%")
    print()

    # Worst-case multi-session memory analysis
    print("=" * 120)
    print("WORST-CASE MULTI-SESSION MEMORY (RGB565A8, cropped)")
    print("=" * 120)
    print()

    # Collect session-relevant sprites (the ones the daemon sends via set_sessions)
    session_sprites = ['idle', 'typing', 'thinking', 'building', 'confused', 'sweeping']
    transition_sprites = ['walking', 'going_away']

    by_name = {r['name']: r for r in results}

    session_bufs = []
    for name in session_sprites:
        if name in by_name and by_name[name]['bbox']:
            r = by_name[name]
            buf = r['crop_w'] * r['crop_h'] * BYTES_PER_PIXEL
            session_bufs.append((name, buf))

    session_bufs.sort(key=lambda x: -x[1])  # largest first

    transition_bufs = []
    for name in transition_sprites:
        if name in by_name and by_name[name]['bbox']:
            r = by_name[name]
            buf = r['crop_w'] * r['crop_h'] * BYTES_PER_PIXEL
            transition_bufs.append((name, buf))

    print("  Session animation buffers (largest first):")
    for name, buf in session_bufs:
        r = by_name[name]
        print(f"    {name:<14} {r['crop_w']:>3}x{r['crop_h']:<4} = {buf:>8} B ({buf/1024:.1f} KB)")

    print()
    print("  Transition animation buffers:")
    for name, buf in transition_bufs:
        r = by_name[name]
        print(f"    {name:<14} {r['crop_w']:>3}x{r['crop_h']:<4} = {buf:>8} B ({buf/1024:.1f} KB)")

    largest_session = session_bufs[0][1] if session_bufs else 0
    largest_transition = max(b for _, b in transition_bufs) if transition_bufs else 0

    print()
    for n_slots in [2, 3, 4, 6, 8]:
        # Worst case: all slots use the largest session sprite
        worst = n_slots * largest_session
        # Realistic: mix of session + transition
        n_session = min(n_slots, 4)
        n_trans = min(n_slots - n_session, len(transition_bufs)) if n_slots > 4 else 0
        realistic = sum(b for _, b in session_bufs[:n_session])
        realistic += sum(b for _, b in transition_bufs[:n_trans])
        print(f"  {n_slots} slots: worst={worst/1024:.0f} KB  realistic={realistic/1024:.0f} KB")

    print()


def main():
    headers = sorted(ASSETS_DIR.glob("sprite_*.h"))
    if not headers:
        print(f"Error: No sprite_*.h files found in {ASSETS_DIR}", file=sys.stderr)
        sys.exit(1)

    print(f"Found {len(headers)} sprite headers in {ASSETS_DIR}")
    print("Decoding RLE data for all frames (symmetric horizontal crop)...")

    results = []
    for h in headers:
        name = h.stem
        print(f"  Analyzing {name}...", end="", flush=True)
        try:
            result = analyze_sprite(h)
            results.append(result)
            bbox = result['bbox']
            if bbox:
                print(f" {result['width']}x{result['height']} -> "
                      f"{result['crop_w']}x{result['crop_h']}  "
                      f"({result['frame_count']} frames)")
            else:
                print(f" (all transparent)")
        except Exception as e:
            print(f" ERROR: {e}", file=sys.stderr)

    format_table(results)


if __name__ == "__main__":
    main()
