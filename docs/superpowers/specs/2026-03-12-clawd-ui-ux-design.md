# Clawd Notification Display — UI/UX Design Spec

## Overview

Visual and interaction design for the Clawd notification display on a 320x172 pixel LCD (ST7789, RGB565). The aesthetic is **playful pixel art** — Clawd lives in a tiny pixel world with a starry sky, grass ground, and colorful notification cards. The display should feel like a scene from a pixel art game, not a sterile dashboard.

## Hardware Constraints

- **Screen:** 320x172 pixels, landscape, 16-bit color (RGB565)
- **Sprite storage:** Flash primary, SD card (128GB available) as fallback if flash budget is tight
- **Rendering:** LVGL v9.x with double-buffered rendering
- **Target frame rate:** 30fps for animations

## Clawd Sprite Design

### Size and Placement

- **Sprite canvas:** ~64x64 pixels
- **Position:** Left ~1/3 of screen (~107px wide), vertically centered
- **Prominence:** Medium — large enough to see expressions and animations, not so large it crowds notifications

### Animation States

Full animation set with 8+ frames per state, transitions between states, and particle effects.

| State | Description | Frames | Details |
|-------|-------------|--------|---------|
| **Idle / Living** | Clawd wanders around in a living pixel scene | 8-12 | Breathing, blinking, occasionally scratching, yawning, looking around. Subtle ambient movement. |
| **Alert** | New notification arrived | 6-8 | Glances toward the right side of the screen. Future: random reaction pool (startled jump, wave, curious lean). |
| **Happy** | All notifications cleared | 6-8 | Celebratory — bouncing, waving claws, little sparkle particles. |
| **Sleeping** | UI-layer inactivity timer (no notifications for ~5 minutes while BLE connected). Purely a UI decision — no firmware event needed. | 6-8 | Curled up, eyes closed (dashes), slow breathing, Zzz bubbles float up. Dimmer scene. |
| **Disconnected** | No BLE connection (triggered by `ui_set_connection_state(false)`) | 6-8 | Looking around confused, staring at a large floating BLE icon. Side-to-side head movement. |

Note: The original system spec mapped "sleeping" to the disconnected state. This spec separates them into two distinct states. The sleeping state is triggered purely by a UI-layer inactivity timer (no new firmware contract needed), while the disconnected state maps to the existing `ui_set_connection_state(false)` call.

### Sprite Format

- RGB565 bitmaps with transparency key color
- Stored as C arrays in flash (or raw files on SD card if flash runs low)
- Sprites are read directly from flash via memory-mapped access (ESP32-C6 XIP). Only one frame is active in RAM at a time — frames are NOT pre-loaded into RAM
- Estimated total: ~40-50 sprites across all states

## Screen Layout

### General Structure

```
┌──────────────────────────────────────────────┐
│  Clawd Scene  │   Notification Area          │
│  (~107px)     │   (~213px)                   │
│               │                              │
│  ┌─────────┐  │                              │
│  │  Clawd  │  │                              │
│  │  64x64  │  │                              │
│  └─────────┘  │                              │
│  ▓▓▓▓▓▓▓▓▓▓  │                              │
│  (grass)      │                              │
└──────────────────────────────────────────────┘
```

The left panel is Clawd's pixel world (starry sky gradient, grass ground, stars). The right panel shows notification content.

## Idle Screen (No Notifications)

**Style: Clawd Living — ambient wandering scene with time display.**

### Scene Elements

- **Sky:** Dark gradient background (`#0a0e1a` → `#16213e` → `#1a1a2e`)
- **Stars:** 5-6 small colored stars (`✦`) twinkling at random intervals. Colors: `#ffff88`, `#88ccff`, `#ffaa88`, `#aaccff`, `#ffdd88`, `#88ffcc`
- **Clawd:** Centered in the scene, performing idle animations (wandering, breathing, blinking)
- **Ground:** Grass strip at the bottom (`#2d4a2d` → `#1a331a`) with small tuft details
- **Grass tufts:** Small `▔` characters in muted greens scattered along the ground line

### Time Display

- **Position:** Top-right corner of the screen
- **Font:** Monospace, bold
- **Size:** ~16-18px (larger than original mockup — user requested bigger clock)
- **Color:** Muted blue (`#4466aa`) with subtle glow (`text-shadow: 0 0 6px #4466aa44`)
- **Format:** 24-hour (`HH:MM`)
- **Integration:** Blends into the sky as a subtle ambient element, not a dominant UI component

### Full-Screen Idle

When no notifications are active, the living scene extends across the full 320px width. Clawd wanders freely across the wider space. Stars and ground span the full screen. The time sits in the top-right corner.

## Notification States

### Single Notification

When one notification arrives:

1. The scene contracts — Clawd's world shrinks to the left ~1/3
2. Notification card slides in from the right edge
3. Clawd glances toward the notification

### Featured + Compact List Layout (Multiple Notifications)

The newest notification gets a large "featured" card with full details. Older notifications collapse to one-line compact entries.

```
┌──────────────┬───────────────────────────────┐
│              │  ▸ 4 WAITING!                 │
│   Clawd      │  ┌─────────────────────────┐  │
│   Scene      │  │ espc6-lcd-display       │  │
│              │  │ Needs approval to run    │  │
│              │  │ ● NEWEST                 │  │
│              │  └─────────────────────────┘  │
│              │  ● bond-core2                 │
│              │  ● telegram-bot               │
│              │  ● pluggy-sync                │
└──────────────┴───────────────────────────────┘
```

#### Featured Card

- **Background:** Dark purple tint (`#2a1a3e`)
- **Border:** 2px solid, color matches notification priority/project accent
- **Project name:** Bold monospace, warm yellow (`#ffdd57`), 8-9px
- **Message:** Light purple/muted (`#cc99ff`), 7px monospace
- **Badge:** Shows "NEWEST" when displaying the most recent notification, or the notification's relative age (e.g., "2m ago") when auto-rotation cycles to older entries

#### Compact List Entries

- **Format:** Colored dot (`●`) + project name, single line
- **Each entry gets a unique accent color** from a rotating palette
- **Background:** Subtle tinted row (`#1a2a3e55`, `#2a2a1e55`, etc.)
- **Font:** 8px monospace (LVGL built-in Montserrat 8)
- **Row height:** ~12px per entry including padding

#### Overflow Handling (5+ Notifications)

The right panel is 172px tall. Space budget:
- Counter header: ~14px
- Featured card: ~50px
- Available for compact list: ~108px → fits ~9 rows at 12px each

With the 8-notification firmware cap, the compact list shows at most 7 entries (1 featured + 7 compact). At 12px per row, 7 entries = 84px — fits within the 108px budget with room to spare. No scrolling mechanism is needed.

#### Counter Header

- **Text:** `▸ N WAITING!`
- **Color:** Bright yellow (`#ffdd57`), bold monospace, 8-9px
- **Position:** Top of the notification area

### Notification Color Palette

Each notification gets an accent color from this rotating set:

| Index | Color | Hex | Use |
|-------|-------|-----|-----|
| 1 | Amber/Orange | `#ff6b2b` | Featured border, primary accent |
| 2 | Blue | `#4488ff` | Secondary notifications |
| 3 | Yellow-Green | `#aaaa33` | Tertiary |
| 4 | Green | `#44aa44` | Quaternary |
| 5 | Purple | `#7b68ee` | Quinary |
| 6 | Cyan | `#44cccc` | Additional |
| 7 | Pink | `#cc4488` | Additional |
| 8 | Warm Yellow | `#ccaa22` | Additional |

The firmware enforces a hard cap of 8 active notifications. The UI will never receive a 9th `ui_show_notification` call while 8 are already active. Colors are assigned 1:1 — no cycling needed.

### Auto-Rotation

The featured card auto-rotates through notifications on a timer (~8 seconds per card). The currently featured card is always the newest by default, but the rotation cycles through all active notifications so the user sees each one's full message.

## Disconnected State

When BLE connection is lost:

- **Scene:** Same living pixel world but with Clawd looking confused
- **BLE Icon:** Large floating BLE icon (Bluetooth symbol) rendered in pixel art, positioned in the scene
- **Clawd behavior:** Staring at the BLE icon, occasional side-to-side head movement
- **Color shift:** Scene slightly desaturated or blue-shifted to indicate something is off
- **Minimal text:** Small "No connection" label (8px, muted color) at the bottom of the scene for clarity on first use. The visual is primary, the text is secondary.

When BLE reconnects, Clawd does a happy reaction and the scene returns to normal idle.

## Animations and Transitions

### New Notification Arrival

1. **Slide-in:** New notification card slides in from the right edge of the screen (~300ms ease-out)
2. **Shift down:** Existing cards animate downward to make room (~200ms)
3. **Clawd reaction:** Glances toward the right side (head turn animation)
4. Future: Pool of random Clawd reactions (startled jump, curious lean, wave) — start with the glance

### Notification Dismissed

1. **Slide-out:** Dismissed card slides out to the right (~250ms)
2. **Collapse:** Remaining cards shift up to fill the gap (~200ms)
3. **If last notification:** Scene expands back to full-screen idle, Clawd does happy animation

### Idle → Notification Transition

1. **Scene contracts:** Left panel shrinks from full-width to ~107px (~400ms)
2. **Stars/ground:** Clip to the smaller area
3. **Notification slides in:** After scene contracts

### Scene Particles

- **Star twinkle:** Random stars brighten/dim on 2-4 second cycles
- **Grass sway:** Subtle tuft animation (optional, low priority)
- **Zzz bubbles:** Float upward during sleeping state, fade out

## Typography

### Font Selection

LVGL v9 includes Montserrat at 8, 10, 12, 14, 16, 18, 20+ px. All font sizes below map to available built-in sizes to avoid custom font generation.

- **Primary:** Montserrat (LVGL built-in), monospace pixel font as stretch goal
- **Sizes (mapped to LVGL built-ins):**
  - Time display: Montserrat 18px bold
  - Counter header (`N WAITING!`): Montserrat 10px bold
  - Project name (featured): Montserrat 10px bold
  - Message text (featured): Montserrat 8px regular
  - Compact list entries: Montserrat 8px regular

If a custom pixel-art font (e.g., Unscii, Press Start 2P) is desired for aesthetic reasons, it can be generated via the LVGL Font Converter tool and added as a firmware asset. This is a stretch goal — the built-in Montserrat works out of the box.

### Text Handling

- **Project names:** Truncate with ellipsis at ~18 characters
- **Messages:** Truncate with ellipsis at ~30 characters (featured card), not shown in compact list
- **Font rendering:** Anti-aliased where LVGL supports it, crisp pixel edges preferred

## Color System

### Background Palette

| Element | Color | Hex |
|---------|-------|-----|
| Sky (top) | Deep navy | `#0a0e1a` |
| Sky (mid) | Dark blue | `#16213e` |
| Sky (low) | Dark indigo | `#1a1a2e` |
| Ground (top) | Dark green | `#2d4a2d` |
| Ground (bottom) | Deeper green | `#1a331a` |

### Clawd Colors

| Part | Color | Hex |
|------|-------|-----|
| Shell (primary) | Deep orange | `#ff6b2b` |
| Shell (highlight) | Bright orange | `#ff8844` |
| Eyes | White | `#ffffff` |
| Shell (shadow) | Dark orange | `#993d1a` |

### UI Colors

| Element | Color | Hex |
|---------|-------|-----|
| Time text | Muted blue | `#4466aa` |
| Counter text | Bright yellow | `#ffdd57` |
| Featured card bg | Dark purple | `#2a1a3e` |
| Compact entry bg | Semi-transparent tints | Various `55` alpha |
| "All clear" text | Green | `#44aa44` |
| Star colors | Warm/cool mix | See star list above |

## State Machine (UI Layer)

```
FULL_IDLE (full-screen living scene + time)
  ├── [notification arrives] → CONTRACT_SCENE → NOTIFICATION_VIEW
  └── [BLE disconnect] → DISCONNECTED

NOTIFICATION_VIEW (Clawd left, notifications right)
  ├── [notification arrives] → ANIMATE_NEW (slide in, shift down)
  ├── [notification dismissed] → ANIMATE_REMOVE (slide out, shift up)
  │     └── [last dismissed] → EXPAND_SCENE → FULL_IDLE
  └── [BLE disconnect] → DISCONNECTED

DISCONNECTED (Clawd staring at BLE icon)
  └── [BLE reconnect] → FULL_IDLE or NOTIFICATION_VIEW (depending on active notifications)
```

## Asset Requirements Summary

| Asset | Format | Estimated Count | Storage |
|-------|--------|-----------------|---------|
| Clawd idle frames | RGB565 bitmap | 8-12 | Flash |
| Clawd alert frames | RGB565 bitmap | 6-8 | Flash |
| Clawd happy frames | RGB565 bitmap | 6-8 | Flash |
| Clawd sleeping frames | RGB565 bitmap | 6-8 | Flash |
| Clawd disconnected frames | RGB565 bitmap | 6-8 | Flash |
| BLE icon | RGB565 bitmap | 1 | Flash |
| Star particles | Procedural (LVGL) | 0 | N/A |
| Grass tufts | Procedural (LVGL) | 0 | N/A |
| Pixel font | LVGL bitmap font | 1-2 | Flash |

**Estimated sprite storage:** ~40-50 frames × 64×64 × 2 bytes = ~320-400 KB. Well within flash budget. SD card not needed for initial implementation.

## RAM Budget

The ESP32-C6 has 512 KB SRAM. Key allocations:

| Consumer | Estimated RAM |
|----------|---------------|
| LVGL display buffers (2× partial, 320×34 each) | ~44 KB |
| LVGL heap (widgets, styles, animations) | ~30 KB |
| NimBLE stack | ~40 KB |
| FreeRTOS tasks (3 × 4KB stacks) | ~12 KB |
| Notification store + JSON parse buffer | ~4 KB |
| Active sprite decode buffer (1 frame) | ~8 KB |
| System overhead | ~50 KB |
| **Total estimated** | **~188 KB** |
| **Available headroom** | **~324 KB** |

Key constraint: LVGL uses **partial rendering** with two smaller buffers (320×34 = ~21 KB each) rather than full frame buffers. This is standard for ESP32-C6 with LVGL and keeps RAM usage manageable.

Sprite frames are stored in flash and accessed via memory-mapped XIP — only one 64×64 frame (~8 KB) needs to be in RAM at any time for the active animation frame.

## Implementation Notes

- All rendering via LVGL — sprites as `lv_image` sourced from flash (XIP), animations via `lv_anim`, text via `lv_label`
- Sprite frames remain in flash; LVGL reads them directly via memory-mapped access. No frame pre-loading into RAM
- LVGL partial rendering: two 320×34 line buffers (~21 KB each), not full frame buffers
- Star twinkle and scene particles use LVGL timer callbacks
- Notification cards are LVGL container objects with styled borders and backgrounds
- Scene contraction uses LVGL animation on the container width
- Auto-rotation timer cycles the featured card every ~8 seconds
- Sleeping state triggered by a UI-layer `lv_timer` (5-minute inactivity timeout), no firmware integration needed
