#!/usr/bin/env python3
"""Generate animated SVGs for Clawd Tank using the Gemini CLI.

Constructs a detailed prompt with the base SVG, design conventions, and an
example animation, then calls `gemini -p` in headless mode to generate a new
animated SVG.

Usage:
    python tools/gemini_animate.py working-wizard "Wearing a wizard hat, waving a wand with magical sparkles"
    python tools/gemini_animate.py working-wizard "..." --model gemini-2.5-pro
    python tools/gemini_animate.py working-wizard "..." --example assets/svg-animations/clawd-working-typing.svg
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BASE_SVG_PATH = PROJECT_ROOT / "assets" / "svg-animations" / "clawd-static-base.svg"
DEFAULT_EXAMPLE = PROJECT_ROOT / "assets" / "svg-animations" / "clawd-working-confused.svg"
OUTPUT_DIR = PROJECT_ROOT / "assets" / "svg-animations"
PLANS_PATH = PROJECT_ROOT / "assets" / "svg-animations" / "PLANS.md"

DEFAULT_MODEL = "gemini-3.1-pro-preview"
FALLBACK_MODEL = "gemini-2.5-pro"


def build_prompt(name: str, description: str, base_svg: str, example_svg: str,
                 plan_text: str | None, fps: int) -> str:
    """Build the full prompt for Gemini."""
    prompt_parts = [
        "Create an animated SVG for the Clawd Tank project.",
        "",
        "## Character Base SVG",
        "",
        "This is the canonical Clawd character geometry. Your animation MUST use",
        "these exact same elements (torso, arms, legs, eyes) with the same IDs,",
        "coordinates, and colors. Animate by applying CSS transforms and keyframes",
        "to these elements — do NOT redraw the character.",
        "",
        "```svg",
        base_svg.strip(),
        "```",
        "",
        "## Design Constraints",
        "",
        "- **Body color:** `#DE886D` (salmon-orange). All body parts use this.",
        "- **Eyes:** `#000000`, 1x2 unit rectangles at positions (4,8) and (10,8).",
        "- **ViewBox:** Use `\"-15 -25 45 45\"` for working animations (gives room for effects above/around the character). The character itself lives at coordinates (0,0)-(15,16).",
        "- **Output size:** `width=\"500\" height=\"500\"` on the root `<svg>` element.",
        "- **Animation method:** Pure CSS `@keyframes` with transforms (translate, rotate, scale, opacity). No JavaScript, no SMIL `<animate>` elements.",
        "- **Pixel-art aesthetic:** The character is made of rectangles. Keep all effects (sparkles, particles, icons) as small rectangles too — no circles, no curves, no gradients.",
        "- **Transparency key:** Never use the exact color `#18C428` (RGB565 `0x18C5`) in visible artwork — it becomes transparent in firmware.",
        "- **Looping:** The animation should loop seamlessly (`infinite`). First and last frames must match.",
        f"- **Target framerate:** {fps} FPS. Design timing so keyframes look good at this rate.",
        "- **Duration:** Keep total animation duration between 3s and 8s for most working animations. Idle/sleeping can be longer (up to 16s).",
        "- **Ground shadow:** Keep `<rect id=\"ground-shadow\" x=\"3\" y=\"15\" width=\"9\" height=\"1\" fill=\"#000000\" opacity=\"0.5\"/>` as a static element (not animated).",
        "- **Legs:** Usually static (same position as base SVG), unless the animation specifically involves leg movement.",
        "- **Structure:** Separate static legs from animated upper body (torso + arms + eyes) using `<g>` groups with CSS classes.",
        "",
        "## Example Animation",
        "",
        "Here is a complete working animation for reference. Study the structure,",
        "CSS pattern, and how it keeps the base character geometry intact while",
        "adding movement and effects:",
        "",
        "```svg",
        example_svg.strip(),
        "```",
        "",
    ]

    if plan_text:
        # Find the specific plan entry for this animation name
        plan_section = _find_plan_section(plan_text, name)
        if plan_section:
            prompt_parts.extend([
                "## Animation Plan (from PLANS.md)",
                "",
                plan_section,
                "",
            ])

    prompt_parts.extend([
        "## Your Task",
        "",
        f"Create an animated SVG named `clawd-{name}.svg` with this behavior:",
        "",
        f"**{description}**",
        "",
        "Output ONLY the complete SVG markup — no explanation, no markdown fences,",
        "no commentary. Start with `<svg` and end with `</svg>`.",
    ])

    return "\n".join(prompt_parts)


def _find_plan_section(plans_text: str, name: str) -> str | None:
    """Try to find a relevant section in PLANS.md for the given animation name."""
    # Convert name like "working-wizard" to search terms like "wizard"
    search_terms = name.replace("working-", "").replace("-", " ").lower().split()

    best_match = None
    best_score = 0

    # Split by ## headings
    sections = re.split(r'(?=^## )', plans_text, flags=re.MULTILINE)
    for section in sections:
        section_lower = section.lower()
        score = sum(1 for term in search_terms if term in section_lower)
        if score > best_score:
            best_score = score
            best_match = section.strip()

    return best_match if best_score > 0 else None


def extract_svg(text: str) -> str | None:
    """Extract SVG content from Gemini's response."""
    # Try to find SVG inside markdown code fences first
    fenced = re.search(r'```(?:svg|xml)?\s*\n(<svg[^>]*>.*?</svg>)\s*\n```', text, re.DOTALL)
    if fenced:
        return fenced.group(1)

    # Try bare SVG
    bare = re.search(r'<svg[^>]*>.*?</svg>', text, re.DOTALL)
    if bare:
        return bare.group(0)

    return None


def call_gemini(prompt: str, model: str) -> str:
    """Call the Gemini CLI in headless mode."""
    cmd = ["gemini", "-p", prompt, "-m", model, "-o", "text"]

    print(f"Calling Gemini CLI with model {model}...")
    print(f"(this may take 30-60 seconds for complex animations)")

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=300,  # 5 minute timeout
    )

    if result.returncode != 0:
        stderr = result.stderr.strip()
        # If the primary model fails (e.g., rate limit), suggest fallback
        if "429" in stderr or "No capacity" in stderr:
            if model != FALLBACK_MODEL:
                print(f"Model {model} unavailable (rate limited). Trying {FALLBACK_MODEL}...")
                return call_gemini(prompt, FALLBACK_MODEL)
        print(f"Gemini CLI error (exit {result.returncode}):", file=sys.stderr)
        print(stderr, file=sys.stderr)
        sys.exit(1)

    return result.stdout


def main():
    parser = argparse.ArgumentParser(
        description="Generate Clawd Tank animated SVGs using Gemini CLI",
        epilog="Example: %(prog)s working-wizard 'Wearing wizard hat, waving wand with sparkles'",
    )
    parser.add_argument("name", help="Animation name (e.g., 'working-wizard', 'sleeping')")
    parser.add_argument("description", help="Description of the animation behavior")
    parser.add_argument("--model", "-m", default=DEFAULT_MODEL,
                        help=f"Gemini model (default: {DEFAULT_MODEL})")
    parser.add_argument("--fps", type=int, default=8,
                        help="Target framerate for pipeline hint (default: 8)")
    parser.add_argument("--example", "-e", type=Path, default=DEFAULT_EXAMPLE,
                        help="Path to example SVG for reference")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the prompt without calling Gemini")
    parser.add_argument("--output", "-o", type=Path, default=None,
                        help="Output path (default: assets/svg-animations/clawd-{name}.svg)")

    args = parser.parse_args()

    # Read inputs
    if not BASE_SVG_PATH.exists():
        print(f"Error: base SVG not found at {BASE_SVG_PATH}", file=sys.stderr)
        sys.exit(1)

    base_svg = BASE_SVG_PATH.read_text()

    if not args.example.exists():
        print(f"Error: example SVG not found at {args.example}", file=sys.stderr)
        sys.exit(1)

    example_svg = args.example.read_text()

    # Try to load animation plans
    plan_text = PLANS_PATH.read_text() if PLANS_PATH.exists() else None

    # Build prompt
    prompt = build_prompt(args.name, args.description, base_svg, example_svg,
                          plan_text, args.fps)

    if args.dry_run:
        print(prompt)
        return

    # Call Gemini
    response = call_gemini(prompt, args.model)

    # Extract SVG
    svg_content = extract_svg(response)
    if not svg_content:
        print("Error: Could not extract SVG from Gemini response.", file=sys.stderr)
        print("--- Raw response ---", file=sys.stderr)
        print(response[:2000], file=sys.stderr)
        sys.exit(1)

    # Save
    output_path = args.output or (OUTPUT_DIR / f"clawd-{args.name}.svg")
    output_path.write_text(svg_content + "\n")
    print(f"Saved: {output_path}")

    # Print next pipeline steps
    sprite_name = args.name.replace("-", "_")
    frames_dir = f"/tmp/clawd-{args.name}-frames"
    print(f"\nNext steps:")
    print(f"  # 1. Preview the SVG in a browser first")
    print(f"  open {output_path}")
    print(f"  # 2. Render to PNG frames")
    print(f"  python tools/svg2frames.py {output_path} {frames_dir}/ --fps {args.fps} --scale 6")
    print(f"  # 3. Convert to firmware header")
    print(f"  python tools/png2rgb565.py {frames_dir}/ firmware/main/assets/sprite_{sprite_name}.h --name {sprite_name}")


if __name__ == "__main__":
    main()
