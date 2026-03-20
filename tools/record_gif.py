#!/usr/bin/env python3
"""Record seamlessly-looping GIFs of Clawd Tank animations.

Uses the simulator's --capture-anim mode to capture exactly one animation
cycle with perfect frame sync, then stitches into a GIF with Pillow.

Usage:
    python tools/record_gif.py thinking output.gif
    python tools/record_gif.py --all gifs/
    python tools/record_gif.py --list
"""

import argparse
import glob
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    print("Error: Pillow is required. Install with: pip install Pillow", file=sys.stderr)
    sys.exit(1)

# Animation names accepted by --capture-anim
# V1 status names use STATUS_MAP, v2 names pass through directly
ANIMATIONS = [
    "idle", "sleeping",
    "thinking", "typing", "juggling", "building", "confused", "sweeping",
    "debugger", "wizard", "conducting", "beacon",
]

# Map friendly names to set_status values (v1 intensity tiers)
STATUS_MAP = {
    "typing": "working_1",
    "juggling": "working_2",
    "building": "working_3",
}

PROJECT_ROOT = Path(__file__).parent.parent
SIMULATOR = PROJECT_ROOT / "simulator" / "build" / "clawd-tank-sim"


def find_simulator():
    if SIMULATOR.exists():
        return str(SIMULATOR)
    print(f"Error: Simulator not found at {SIMULATOR}", file=sys.stderr)
    print("Build it first: cd simulator && cmake -B build && cmake --build build", file=sys.stderr)
    sys.exit(1)


def record_animation(name: str, output_path: str, scale: int = 2):
    if name not in ANIMATIONS:
        print(f"Error: Unknown animation '{name}'", file=sys.stderr)
        print(f"Available: {', '.join(ANIMATIONS)}", file=sys.stderr)
        sys.exit(1)

    sim_bin = find_simulator()

    with tempfile.TemporaryDirectory() as tmpdir:
        status_name = STATUS_MAP.get(name, name)
        cmd = [sim_bin, "--capture-anim", status_name, tmpdir]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            print(f"Simulator error: {result.stderr}", file=sys.stderr)
            sys.exit(1)

        # Parse frame_ms from simulator output
        frame_ms = 125  # default
        for line in result.stdout.splitlines():
            if line.startswith("frame_ms="):
                frame_ms = int(line.split("=")[1])

        # Collect frame PNGs (sorted by suffix number)
        frames_glob = sorted(glob.glob(os.path.join(tmpdir, "event_*.png")))
        if not frames_glob:
            print("Error: No frames captured", file=sys.stderr)
            print(f"Simulator output: {result.stdout}", file=sys.stderr)
            sys.exit(1)

        # Load frames
        images = [Image.open(f) for f in frames_glob]

        # Scale up with nearest-neighbor for crisp pixels
        if scale != 1:
            images = [
                img.resize(
                    (img.width * scale, img.height * scale),
                    Image.Resampling.NEAREST,
                )
                for img in images
            ]

        # Save as GIF
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        images[0].save(
            output_path,
            save_all=True,
            append_images=images[1:],
            duration=frame_ms,
            loop=0,
        )

        duration_ms = len(images) * frame_ms
        print(f"  {name}: {len(images)} frames, {frame_ms}ms/frame, "
              f"{duration_ms}ms total → {output_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Record seamlessly-looping GIFs of Clawd Tank animations",
    )
    parser.add_argument("animation", nargs="?",
                        help="Animation name (e.g., thinking, typing, idle)")
    parser.add_argument("output", nargs="?",
                        help="Output GIF path (or directory with --all)")
    parser.add_argument("--all", action="store_true",
                        help="Record all animations to the output directory")
    parser.add_argument("--scale", type=int, default=2,
                        help="Scale factor for GIF (default: 2)")
    parser.add_argument("--list", action="store_true",
                        help="List available animations")

    args = parser.parse_args()

    if args.list:
        print("Available animations:")
        for name in ANIMATIONS:
            print(f"  {name}")
        return

    if args.all:
        # With --all, the first positional arg is the output directory
        out_dir = args.animation or args.output
        if not out_dir:
            print("Error: specify output directory with --all", file=sys.stderr)
            sys.exit(1)
        os.makedirs(out_dir, exist_ok=True)
        print(f"Recording all animations to {out_dir}/")
        for name in ANIMATIONS:
            out = os.path.join(out_dir, f"clawd-{name}.gif")
            record_animation(name, out, args.scale)
        print("Done!")
        return

    if not args.animation or not args.output:
        parser.print_help()
        sys.exit(1)

    record_animation(args.animation, args.output, args.scale)


if __name__ == "__main__":
    main()
