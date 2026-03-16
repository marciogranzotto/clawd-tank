#!/bin/bash
# Build the Clawd Tank menu bar .app bundle with bundled simulator.
# Usage: cd host && ./build.sh [--install]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SIM_DIR="$SCRIPT_DIR/../simulator"
SIM_BINARY="$SIM_DIR/build-static/clawd-tank-sim"

# Always rebuild static simulator — cmake handles incremental builds
echo "==> Building static simulator..."
cd "$SIM_DIR"
cmake -B build-static -DSTATIC_SDL2=ON
cmake --build build-static
cd "$SCRIPT_DIR"

# Build .app with py2app
echo "==> Building .app bundle..."
cd "$SCRIPT_DIR"
rm -rf build dist
.venv/bin/python setup.py py2app 2>&1 | tail -3

# Bundle simulator binary
echo "==> Bundling simulator binary..."
cp "$SIM_BINARY" "dist/Clawd Tank.app/Contents/MacOS/clawd-tank-sim"

echo "==> Built: dist/Clawd Tank.app"

# Install if requested
if [ "${1:-}" = "--install" ]; then
    echo "==> Installing to /Applications..."
    rm -rf "/Applications/Clawd Tank.app"
    cp -R "dist/Clawd Tank.app" "/Applications/Clawd Tank.app"
    echo "==> Installed to /Applications/Clawd Tank.app"
fi
