#!/usr/bin/env python3
"""
Crop existing RLE-compressed sprite headers in-place.

Reads each sprite_*.h in firmware/main/assets/, decodes all frames,
finds the tight bounding box across all frames, applies symmetric
horizontal cropping (keeps Clawd centered) and free vertical cropping,
re-encodes to RLE, and writes the header back.

Also prints the y_offset adjustment needed for scene.c (to keep sprites
bottom-aligned at the same position after cropping).

Usage:
    python tools/crop_sprites.py [--dry-run]
"""

import argparse
import re
import sys
from pathlib import Path

ASSETS_DIR = Path(__file__).parent.parent / "firmware" / "main" / "assets"
TRANSPARENT_KEY = 0x18C5


def parse_header(path: Path):
    """Parse a sprite header. Returns dict with all parsed data."""
    text = path.read_text()

    m = re.search(r'#define\s+(\w+)_WIDTH\s+(\d+)', text)
    if not m:
        raise ValueError(f"Cannot find WIDTH define in {path.name}")
    prefix_upper = m.group(1)
    prefix = prefix_upper.lower()
    width = int(m.group(2))

    def get_define(pat):
        mm = re.search(pat, text)
        return int(mm.group(1)) if mm else None

    height = get_define(rf'#define\s+{prefix_upper}_HEIGHT\s+(\d+)')
    frame_count = get_define(rf'#define\s+{prefix_upper}_FRAME_COUNT\s+(\d+)')
    if height is None or frame_count is None:
        raise ValueError(f"Missing HEIGHT or FRAME_COUNT in {path.name}")

    # Check for frame_ms define
    frame_ms = get_define(rf'#define\s+{prefix_upper}_FRAME_MS\s+(\d+)')

    # Parse frame_offsets
    fo_pat = rf'static\s+const\s+uint32_t\s+{prefix}_frame_offsets\s*\[.*?\]\s*=\s*\{{([^}}]+)\}}'
    fo_m = re.search(fo_pat, text, re.DOTALL)
    if not fo_m:
        return None  # Skip non-RLE (legacy flat) headers

    frame_offsets = [int(x) for x in re.findall(r'\d+', fo_m.group(1))]

    # Parse rle_data
    rle_pat = rf'static\s+const\s+uint16_t\s+{prefix}_rle_data\s*\[\s*\]\s*=\s*\{{([^}}]+)\}}'
    rle_m = re.search(rle_pat, text, re.DOTALL)
    if not rle_m:
        raise ValueError(f"Found frame_offsets but no rle_data in {path.name}")

    tokens = re.findall(r'0x[0-9A-Fa-f]+|\d+', rle_m.group(1))
    rle_data = [int(t, 16) if t.startswith('0x') else int(t) for t in tokens]

    return {
        'prefix': prefix,
        'prefix_upper': prefix_upper,
        'width': width,
        'height': height,
        'frame_count': frame_count,
        'frame_ms': frame_ms,
        'frame_offsets': frame_offsets,
        'rle_data': rle_data,
    }


def decode_frame(rle_data, frame_offsets, frame_idx, width, height):
    """Decode a single frame from RLE data to flat pixel list."""
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


def find_bbox(all_frames, width, height):
    """Find tight bounding box across all frames."""
    min_x, min_y = width, height
    max_x, max_y = -1, -1
    for pixels in all_frames:
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
    return min_x, min_y, max_x, max_y


def compute_symmetric_crop(bbox, width, height):
    """
    Compute crop region with symmetric horizontal padding.
    Returns (crop_x, crop_y, crop_w, crop_h).
    """
    min_x, min_y, max_x, max_y = bbox
    center_x = width / 2.0

    # Symmetric: max distance from center on either side
    dist_left = center_x - min_x
    dist_right = max_x - center_x + 1
    half_w = max(dist_left, dist_right)

    crop_x = int(center_x - half_w)
    crop_w = int(half_w * 2)

    # Clamp
    if crop_x < 0:
        crop_w += crop_x
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

    crop_y = min_y
    crop_h = max_y - min_y + 1

    return crop_x, crop_y, crop_w, crop_h


def crop_frame(pixels, orig_w, orig_h, crop_x, crop_y, crop_w, crop_h):
    """Extract a crop region from a flat pixel list."""
    cropped = []
    for y in range(crop_y, crop_y + crop_h):
        row_start = y * orig_w + crop_x
        cropped.extend(pixels[row_start:row_start + crop_w])
    return cropped


def rle_encode(pixels):
    """RLE-encode a pixel list into (value, count) pairs."""
    if not pixels:
        return []
    runs = []
    current_val = pixels[0]
    current_count = 1
    for pixel in pixels[1:]:
        if pixel == current_val and current_count < 65535:
            current_count += 1
        else:
            runs.append((current_val, current_count))
            current_val = pixel
            current_count = 1
    runs.append((current_val, current_count))
    return runs


def format_rle_array(runs, indent="    "):
    """Format RLE pairs as C array content."""
    lines = []
    for i in range(0, len(runs), 8):
        chunk = runs[i:i + 8]
        pairs = ", ".join(f"0x{v:04X},{c}" for v, c in chunk)
        lines.append(f"{indent}{pairs},")
    return "\n".join(lines)


def generate_header(prefix, prefix_upper, width, height, frame_count,
                    frame_ms, frames_data):
    """Generate a C header with RLE-compressed sprite data."""
    guard = f"{prefix.upper()}_FRAMES_H"

    all_rle_runs = []
    frame_offsets = []
    word_offset = 0
    for pixels in frames_data:
        frame_offsets.append(word_offset)
        runs = rle_encode(pixels)
        all_rle_runs.extend(runs)
        word_offset += len(runs) * 2
    frame_offsets.append(word_offset)

    raw_size = frame_count * width * height * 2
    rle_size = word_offset * 2
    ratio = raw_size / rle_size if rle_size > 0 else 0

    lines = []
    lines.append(f"#ifndef {guard}")
    lines.append(f"#define {guard}")
    lines.append("")
    lines.append("/**")
    lines.append(f" * Auto-generated by png2rgb565.py (RLE compressed, auto-cropped)")
    lines.append(f" * {frame_count} frame(s), {width}x{height} pixels")
    lines.append(f" * Raw: {raw_size:,} bytes, RLE: {rle_size:,} bytes ({ratio:.0f}x compression)")
    lines.append(f" * Transparent key: 0x{TRANSPARENT_KEY:04X}")
    lines.append(" */")
    lines.append("")
    lines.append("#include <stdint.h>")
    lines.append("")
    lines.append(f"#define {prefix_upper}_WIDTH  {width}")
    lines.append(f"#define {prefix_upper}_HEIGHT {height}")
    lines.append(f"#define {prefix_upper}_FRAME_COUNT {frame_count}")
    if frame_ms is not None:
        lines.append(f"#define {prefix_upper}_FRAME_MS {frame_ms}")
    lines.append(f"#define {prefix_upper}_TRANSPARENT_KEY 0x{TRANSPARENT_KEY:04X}")
    lines.append("")

    lines.append(f"static const uint32_t {prefix}_frame_offsets[{frame_count + 1}] = {{")
    for i in range(0, len(frame_offsets), 8):
        chunk = frame_offsets[i:i + 8]
        vals = ", ".join(str(o) for o in chunk)
        lines.append(f"    {vals},")
    lines.append("};")
    lines.append("")

    lines.append(f"static const uint16_t {prefix}_rle_data[] = {{")
    lines.append(format_rle_array(all_rle_runs))
    lines.append("};")
    lines.append("")
    lines.append(f"#endif // {guard}")
    lines.append("")

    return "\n".join(lines)


def process_sprite(path, dry_run=False):
    """Process a single sprite header. Returns result dict or None."""
    parsed = parse_header(path)
    if parsed is None:
        return None  # Skip non-RLE headers

    prefix = parsed['prefix']
    prefix_upper = parsed['prefix_upper']
    width = parsed['width']
    height = parsed['height']
    frame_count = parsed['frame_count']
    frame_ms = parsed['frame_ms']

    # Decode all frames
    all_frames = []
    for f in range(frame_count):
        pixels = decode_frame(parsed['rle_data'], parsed['frame_offsets'],
                              f, width, height)
        all_frames.append(pixels)

    # Find bounding box
    bbox = find_bbox(all_frames, width, height)
    if bbox is None:
        return {'name': prefix, 'skipped': True, 'reason': 'all transparent'}

    min_x, min_y, max_x, max_y = bbox

    # Compute symmetric crop
    crop_x, crop_y, crop_w, crop_h = compute_symmetric_crop(bbox, width, height)

    # Skip if crop doesn't save anything meaningful
    if crop_w >= width and crop_h >= height:
        return {'name': prefix, 'skipped': True, 'reason': 'no savings'}

    # Compute y_offset adjustment
    bottom_rows_removed = height - (max_y + 1)
    y_offset_delta = bottom_rows_removed

    # Crop all frames
    cropped_frames = []
    for pixels in all_frames:
        cropped = crop_frame(pixels, width, height, crop_x, crop_y, crop_w, crop_h)
        cropped_frames.append(cropped)

    # Compute sizes for reporting
    old_buf = width * height * 3  # RGB565A8
    new_buf = crop_w * crop_h * 3
    old_rle_size = len(parsed['rle_data']) * 2

    # Re-encode
    all_rle_runs = []
    for pixels in cropped_frames:
        runs = rle_encode(pixels)
        all_rle_runs.extend(runs)
    new_rle_words = sum(len(rle_encode(f)) * 2 for f in cropped_frames)
    new_rle_size = new_rle_words * 2

    if not dry_run:
        header = generate_header(prefix, prefix_upper, crop_w, crop_h,
                                 frame_count, frame_ms, cropped_frames)
        path.write_text(header)

    return {
        'name': prefix,
        'skipped': False,
        'old_w': width, 'old_h': height,
        'crop_x': crop_x, 'crop_y': crop_y,
        'new_w': crop_w, 'new_h': crop_h,
        'bbox': bbox,
        'bottom_removed': bottom_rows_removed,
        'y_offset_delta': y_offset_delta,
        'old_buf': old_buf, 'new_buf': new_buf,
        'old_rle': old_rle_size, 'new_rle': new_rle_size,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Crop existing sprite headers with symmetric horizontal padding"
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Analyze only, don't modify files")
    args = parser.parse_args()

    headers = sorted(ASSETS_DIR.glob("sprite_*.h"))
    if not headers:
        print(f"Error: No sprite_*.h files found in {ASSETS_DIR}", file=sys.stderr)
        sys.exit(1)

    mode = "DRY RUN" if args.dry_run else "CROPPING"
    print(f"\n{'='*90}")
    print(f"  SPRITE AUTO-CROP ({mode})")
    print(f"  Symmetric horizontal, free vertical. RGB565A8 (3 B/px) buffer sizes.")
    print(f"{'='*90}\n")

    results = []
    for h in headers:
        print(f"  Processing {h.name}...", end="", flush=True)
        try:
            result = process_sprite(h, dry_run=args.dry_run)
            if result is None:
                print(" skipped (non-RLE format)")
                continue
            results.append(result)
            if result['skipped']:
                print(f" skipped ({result['reason']})")
            else:
                r = result
                pct = 100 * (r['old_buf'] - r['new_buf']) / r['old_buf']
                print(f" {r['old_w']}x{r['old_h']} -> {r['new_w']}x{r['new_h']}"
                      f" ({pct:.0f}% buf savings, y_offset +={r['y_offset_delta']})")
        except Exception as e:
            print(f" ERROR: {e}", file=sys.stderr)

    # Summary table
    print(f"\n{'='*90}")
    print(f"  SUMMARY — y_offset adjustments for scene.c anim_defs")
    print(f"{'='*90}\n")
    print(f"  {'Name':<16} {'Old WxH':>10} {'New WxH':>10} {'Buf':>8} {'y_off +=':>8} {'Flash':>12}")
    print(f"  {'-'*74}")

    total_old_buf = 0
    total_new_buf = 0
    total_old_rle = 0
    total_new_rle = 0

    for r in results:
        if r['skipped']:
            continue
        total_old_buf += r['old_buf']
        total_new_buf += r['new_buf']
        total_old_rle += r['old_rle']
        total_new_rle += r['new_rle']
        flash_saved = r['old_rle'] - r['new_rle']
        print(f"  {r['name']:<16} {r['old_w']:>3}x{r['old_h']:<4}"
              f"  {r['new_w']:>3}x{r['new_h']:<4}"
              f"  {r['new_buf']/1024:>5.1f}KB"
              f"  +{r['y_offset_delta']:<6}"
              f"  {flash_saved/1024:>+7.1f} KB")

    buf_saved = total_old_buf - total_new_buf
    rle_saved = total_old_rle - total_new_rle
    print(f"  {'-'*74}")
    print(f"  {'TOTAL':<16} {'':>10} {'':>10}"
          f"  {total_new_buf/1024:>5.0f}KB"
          f"  {'':>8}"
          f"  {rle_saved/1024:>+7.1f} KB")
    print(f"\n  Frame buffer savings: {total_old_buf/1024:.0f} KB -> {total_new_buf/1024:.0f} KB"
          f" ({buf_saved/1024:.0f} KB saved, {100*buf_saved/total_old_buf:.0f}%)")
    print(f"  Flash (RLE data) savings: {total_old_rle/1024:.0f} KB -> {total_new_rle/1024:.0f} KB"
          f" ({rle_saved/1024:.0f} KB saved)")
    print()

    if args.dry_run:
        print("  (dry run — no files modified)\n")


if __name__ == "__main__":
    main()
