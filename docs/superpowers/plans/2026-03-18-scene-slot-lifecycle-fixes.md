# Scene Slot Lifecycle Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix three multi-slot rendering edge cases in scene.c: narrow mode empty scenery, disconnect extra clawds, and connect slot adoption.

**Architecture:** Three independent, surgical fixes in `firmware/main/scene.c`. No new files, no new abstractions. Each fix modifies a specific function at a precise insertion point. Testing via simulator TCP `query_state` introspection.

**Tech Stack:** C (ESP-IDF/LVGL 9), simulator (SDL2), TCP JSON protocol

**Spec:** `docs/superpowers/specs/2026-03-18-scene-slot-lifecycle-fixes-design.md`

---

### Task 1: Fix 2 — Deactivate extra slots in `scene_set_clawd_anim`

This is the simplest fix and establishes the cleanup pattern used conceptually by the other fixes.

**Files:**
- Modify: `firmware/main/scene.c:706-739` (the `scene_set_clawd_anim` function)

- [ ] **Step 1: Add slot cleanup loop and move early return**

In `scene_set_clawd_anim`, after the `if (!slot->active) return;` guard (line 710) and BEFORE the existing `if (anim == slot->cur_anim) return;` (line 711), insert the cleanup loop. Then move the `anim == cur_anim` early return to after the cleanup so extra slots are always cleaned regardless:

```c
void scene_set_clawd_anim(scene_t *scene, clawd_anim_id_t anim)
{
    if (!scene) return;
    clawd_slot_t *slot = &scene->slots[0];
    if (!slot->active) return;

    /* Deactivate all extra slots — v1 path = single-clawd mode */
    for (int i = 1; i < MAX_SLOTS; i++) {
        if (scene->slots[i].active) {
            if (scene->slots[i].sprite_img) {
                lv_anim_delete(scene->slots[i].sprite_img,
                               (lv_anim_exec_xcb_t)lv_obj_set_x);
                lv_obj_delete(scene->slots[i].sprite_img);
                scene->slots[i].sprite_img = NULL;
            }
            free(scene->slots[i].frame_buf);
            scene->slots[i].frame_buf = NULL;
            scene->slots[i].frame_buf_size = 0;
            scene->slots[i].active = false;
            scene->slots[i].departing = false;
        }
    }
    scene->active_slot_count = 1;
    scene->pending_reposition = false;
    scene_update_hud(scene, 0, 0, 1);

    if (anim == slot->cur_anim) return;

    /* ... rest of function unchanged from line 713 onward ... */
```

The key changes:
- Remove the original `if (anim == slot->cur_anim) return;` at line 711
- Insert the cleanup loop after `if (!slot->active) return;`
- Add `scene_update_hud(scene, 0, 0, 1)` to reset the HUD badge
- Re-add the `anim == cur_anim` early return AFTER the cleanup

- [ ] **Step 2: Build simulator to verify compilation**

Run: `cd simulator && cmake -B build && cmake --build build 2>&1 | tail -5`
Expected: Build succeeds with no errors.

- [ ] **Step 3: Test via simulator — disconnect with 2 sessions**

Run the simulator with TCP listener, then send commands to verify:

```bash
# Terminal 1: start simulator
./simulator/build/clawd-tank-sim --headless --listen &
SIM_PID=$!
sleep 1

# Terminal 2: send test commands
# Connect and set 2 sessions
echo '{"action":"set_sessions","anims":["typing","idle"],"ids":[1,2]}' | nc -w1 localhost 19872
sleep 0.5

# Query state — should show 2 active slots
echo '{"action":"query_state"}' | nc -w1 localhost 19872

# Disconnect (via set_status which calls scene_set_clawd_anim)
echo '{"action":"set_status","status":"disconnected"}' | nc -w1 localhost 19872
sleep 0.5

# Query state — should show ONLY slot 0 active with "disconnected" anim
echo '{"action":"query_state"}' | nc -w1 localhost 19872

kill $SIM_PID
```

Expected: After disconnect, `query_state` shows `active_slot_count: 1`, only slot 0 active with anim "disconnected", slots 1+ all inactive.

- [ ] **Step 4: Commit**

```bash
git add firmware/main/scene.c
git commit -m "fix: deactivate extra slots on v1 animation switch (disconnect)

scene_set_clawd_anim now cleans up all slots except 0 before
switching animation. Fixes disconnect showing stale session clawds
alongside the disconnected animation."
```

---

### Task 2: Fix 3 — Adopt unclaimed slot 0 on connect

**Files:**
- Modify: `firmware/main/scene.c:1107-1115` (pre-scan) and `firmware/main/scene.c:1123-1125` (matching loop)

- [ ] **Step 1: Exclude `display_id == 0` from pre-scan**

At line 1110, add `old_ids[i] != 0` to the condition so unclaimed slots don't trigger deferred walks:

Replace:
```c
            if (old_slots[i].active && find_id_in(ids, count, old_ids[i]) < 0) {
```
With:
```c
            if (old_slots[i].active && old_ids[i] != 0
                && find_id_in(ids, count, old_ids[i]) < 0) {
```

- [ ] **Step 2: Add adoption logic in matching loop**

After line 1124 (`int old_i = find_id_in(old_ids, old_count, ids[new_i]);`), insert the adoption fallback:

```c
        int old_i = find_id_in(old_ids, old_count, ids[new_i]);

        /* Adopt unclaimed slot (display_id==0) for the first new session.
         * This happens on first set_sessions after connect — the disconnect
         * animation slot has no real ID, so adopt it rather than departing. */
        if (old_i < 0 && new_i == 0) {
            for (int j = 0; j < old_count; j++) {
                if (old_slots[j].active && old_ids[j] == 0 && old_slots[j].sprite_img) {
                    old_i = j;
                    break;
                }
            }
        }
```

This goes right after the `find_id_in` call, before the `int x_off = ...` line. No other code changes needed — the existing transfer path handles the rest (sprite stays in place, walks to new position).

- [ ] **Step 3: Build simulator**

Run: `cd simulator && cmake --build build 2>&1 | tail -5`
Expected: Build succeeds.

- [ ] **Step 4: Test via simulator — connect with existing sessions**

```bash
./simulator/build/clawd-tank-sim --headless --listen &
SIM_PID=$!
sleep 1

# Start disconnected, then connect and send 2 sessions
echo '{"action":"set_sessions","anims":["typing","idle"],"ids":[1,2]}' | nc -w1 localhost 19872
sleep 0.5

# Query state — slot 0 should be active with display_id=1, NOT departing.
# No slot should have "going_away" animation.
echo '{"action":"query_state"}' | nc -w1 localhost 19872

kill $SIM_PID
```

Expected: `query_state` shows 2 active slots. Slot 0 has `display_id: 1` and `walking_in: true` (walking from center to its multi-session position). No slot has `anim: "going_away"`.

- [ ] **Step 5: Commit**

```bash
git add firmware/main/scene.c
git commit -m "fix: adopt unclaimed slot 0 on first set_sessions after connect

When connecting with active sessions, the disconnect animation clawd
(display_id=0) is adopted as slot 0 instead of playing going-away.
Fixes the visual glitch where the main clawd appeared between session
clawds and then burrowed away."
```

---

### Task 3: Fix 1 — Unhide transferred sprite in narrow mode

**Files:**
- Modify: `firmware/main/scene.c:1262-1273` (narrow guard in `scene_set_sessions`)

- [ ] **Step 1: Add unhide and walk-cancel in narrow guard**

Replace the existing narrow guard block (lines 1262-1273):

```c
    /* In narrow mode, hide all slots except 0 and re-center slot 0 */
    if (s->narrow) {
        for (int i = 1; i < count; i++) {
            if (s->slots[i].sprite_img) {
                lv_obj_add_flag(s->slots[i].sprite_img, LV_OBJ_FLAG_HIDDEN);
            }
        }
        if (s->slots[0].active && s->slots[0].sprite_img) {
            s->slots[0].x_off = 0;
            const anim_def_t *def = &anim_defs[s->slots[0].cur_anim];
            lv_obj_align(s->slots[0].sprite_img, LV_ALIGN_BOTTOM_MID, 0, def->y_offset);
        }
    }
```

With:

```c
    /* In narrow mode, hide all slots except 0 and re-center slot 0 */
    if (s->narrow) {
        for (int i = 1; i < count; i++) {
            if (s->slots[i].sprite_img) {
                lv_obj_add_flag(s->slots[i].sprite_img, LV_OBJ_FLAG_HIDDEN);
            }
        }
        if (s->slots[0].active && s->slots[0].sprite_img) {
            lv_obj_clear_flag(s->slots[0].sprite_img, LV_OBJ_FLAG_HIDDEN);
            if (s->slots[0].walking_in) {
                lv_anim_delete(s->slots[0].sprite_img,
                               (lv_anim_exec_xcb_t)lv_obj_set_x);
                s->slots[0].walking_in = false;
                s->slots[0].cur_anim = s->slots[0].fallback_anim;
                s->slots[0].frame_idx = 0;
                s->slots[0].last_frame_tick = lv_tick_get();
                decode_and_apply_frame(&s->slots[0]);
            }
            s->slots[0].x_off = 0;
            const anim_def_t *def = &anim_defs[s->slots[0].cur_anim];
            lv_obj_set_size(s->slots[0].sprite_img, def->width, def->height);
            lv_obj_align(s->slots[0].sprite_img, LV_ALIGN_BOTTOM_MID, 0, def->y_offset);
        }
    }
```

Changes:
- `lv_obj_clear_flag(LV_OBJ_FLAG_HIDDEN)` — ensures slot 0 is visible even if transferred from a hidden slot
- Walk-in cancel block — stops any walk animation targeting the wide-mode position, switches to fallback animation
- Added `lv_obj_set_size` before `lv_obj_align` — ensures widget dimensions match after potential animation switch

- [ ] **Step 2: Build simulator**

Run: `cd simulator && cmake --build build 2>&1 | tail -5`
Expected: Build succeeds.

- [ ] **Step 3: Test via simulator — narrow mode slot 0 departure**

This test requires the notification panel to be open (narrow mode). The `--events` system supports `notify` to trigger narrow mode, then `sessions` to change session state:

```bash
./simulator/build/clawd-tank-sim --headless --events \
  'connect; sessions typing 1 idle 2; wait 500; notify "test" "hello"; wait 500; sessions idle 2; wait 500' \
  --listen &
SIM_PID=$!
sleep 3

# Query state — slot 0 should be active with display_id=2, anim "idle"
# Scene should be narrow=true
echo '{"action":"query_state"}' | nc -w1 localhost 19872

kill $SIM_PID
```

Expected: `query_state` shows `narrow: true`, `active_slot_count: 1`, slot 0 active with `display_id: 2` and `anim: "idle"`. Slot 0 should NOT have `walking_in: true` (walk was cancelled by narrow guard).

- [ ] **Step 4: Commit**

```bash
git add firmware/main/scene.c
git commit -m "fix: unhide transferred sprite at slot 0 in narrow mode

When slot 0's session departs in narrow mode, the replacement sprite
transferred from a hidden slot retained LV_OBJ_FLAG_HIDDEN, leaving
an empty scene. Now the narrow guard clears the hidden flag, cancels
any walk-in animation, and properly re-centers the sprite."
```

---

### Task 4: Build firmware and final verification

**Files:**
- Build: `firmware/` (ESP-IDF build to verify no firmware compilation issues)

- [ ] **Step 1: Build firmware**

Run: `cd firmware && idf.py build 2>&1 | tail -10`
Expected: Build succeeds. The same `scene.c` compiles for both simulator and firmware.

- [ ] **Step 2: Interactive simulator verification (optional)**

Run the simulator interactively to visually verify all three fixes:

```bash
./simulator/build/clawd-tank-sim --pinned --listen
```

Test scenarios using keyboard shortcuts and TCP commands:
1. **Fix 2**: Press `c` (connect), send `set_sessions` with 2 sessions via TCP, press `d` (disconnect). Verify only one disconnected clawd shows.
2. **Fix 3**: Start fresh, send `set_sessions` with 2 sessions. Verify the main clawd slides to slot 0 position (no burrowing).
3. **Fix 1**: Press `c`, send 2 sessions, press `n` (notify to trigger narrow mode), send sessions removing slot 0. Verify a clawd remains visible.

- [ ] **Step 3: Final commit (if any tweaks needed)**

Only if previous tasks required adjustments during final testing.
