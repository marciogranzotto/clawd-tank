# Scene Slot Lifecycle Fixes

**Date**: 2026-03-18
**Status**: Approved
**Scope**: `firmware/main/scene.c` — three targeted fixes for multi-slot rendering edge cases

## Problem

Three related bugs in the scene's multi-slot rendering system:

1. **Narrow mode empty scenery**: When the notification panel is open (narrow mode, only slot 0 visible) and the session at slot 0 closes, the replacement clawd transferred from a hidden slot retains `LV_OBJ_FLAG_HIDDEN`, leaving an empty scene despite other sessions being active.

2. **Disconnect shows extra clawds**: `scene_set_clawd_anim(DISCONNECTED)` only updates slot 0. Slots 1+ from a previous multi-session state remain active and visible, overlapping with the disconnect animation.

3. **Main clawd goes away on connect**: After connecting, slot 0 has `display_id=0`. When `set_sessions` arrives with real session IDs, the diff can't match `display_id=0`, so old slot 0 plays going-away while all new sessions walk in from off-screen. The main clawd should be adopted as slot 0 instead.

## Approach

Three independent, surgical fixes in `scene.c`. No new abstractions or helpers needed.

## Fix 1: Narrow mode — unhide transferred sprite at slot 0

**Location**: `scene_set_sessions()`, narrow guard (~line 1262).

**Root cause**: The multi-session diff transfers a sprite from slot 1+ (hidden in narrow mode via `LV_OBJ_FLAG_HIDDEN`) to slot 0. The narrow guard re-centers slot 0 but never clears the hidden flag.

**Change**: In the narrow guard block, after confirming slot 0 is active:
- Clear `LV_OBJ_FLAG_HIDDEN` on slot 0's sprite
- Cancel any in-progress walk-in animation (`lv_anim_delete` + clear `walking_in`) since the walk targets the wide-mode position, not center

```c
if (s->narrow) {
    for (int i = 1; i < count; i++) {
        if (s->slots[i].sprite_img) {
            lv_obj_add_flag(s->slots[i].sprite_img, LV_OBJ_FLAG_HIDDEN);
        }
    }
    if (s->slots[0].active && s->slots[0].sprite_img) {
        lv_obj_clear_flag(s->slots[0].sprite_img, LV_OBJ_FLAG_HIDDEN);  // NEW
        if (s->slots[0].walking_in) {                                     // NEW
            lv_anim_delete(s->slots[0].sprite_img,                        // NEW
                           (lv_anim_exec_xcb_t)lv_obj_set_x);             // NEW
            s->slots[0].walking_in = false;                                // NEW
            s->slots[0].cur_anim = s->slots[0].fallback_anim;             // NEW
            s->slots[0].frame_idx = 0;                                     // NEW
            s->slots[0].last_frame_tick = lv_tick_get();                   // NEW
            decode_and_apply_frame(&s->slots[0]);                          // NEW
        }                                                                  // NEW
        s->slots[0].x_off = 0;
        const anim_def_t *def = &anim_defs[s->slots[0].cur_anim];
        lv_obj_align(s->slots[0].sprite_img, LV_ALIGN_BOTTOM_MID, 0, def->y_offset);
    }
}
```

## Fix 2: Disconnect — deactivate all extra slots

**Location**: `scene_set_clawd_anim()` (~line 706).

**Root cause**: This function only touches slot 0. When called for disconnect (or any v1 animation), slots 1+ from a previous multi-session state remain active and visible.

**Change**: At the top of `scene_set_clawd_anim`, before the `anim == cur_anim` early return, deactivate all slots except 0. This function is the v1 path — it means single-clawd mode. Move the early return after the cleanup so extra slots are always cleaned even if slot 0's animation isn't changing.

```c
void scene_set_clawd_anim(scene_t *scene, clawd_anim_id_t anim)
{
    if (!scene) return;
    clawd_slot_t *slot = &scene->slots[0];
    if (!slot->active) return;

    /* NEW: Deactivate all extra slots — v1 path = single-clawd mode */
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

    scene_update_hud(scene, 0, 0, 1);  /* reset HUD — no subagents/overflow */

    if (anim == slot->cur_anim) return;  /* moved after cleanup */

    /* ... rest unchanged ... */
}
```

## Fix 3: Connect — adopt unclaimed slot 0

**Location**: `scene_set_sessions()`, multi-session diff matching loop (~line 1123).

**Root cause**: After connecting, slot 0 has `display_id=0` (from disconnect/boot state). The diff's `find_id_in` can't match it to any new session ID, so slot 0 becomes a departing slot with going-away animation. All new sessions walk in from off-screen, causing the "main clawd appears between new clawds then burrows away" visual.

**Change**: Two parts:

**Part A — Pre-scan exclusion**: The `will_have_departing` pre-scan (~line 1107) runs before the matching loop. It would incorrectly flag `display_id=0` as departing (since it's not in the new IDs), which causes the adopted slot to enter the deferred-walk branch instead of the immediate-walk branch. Exclude `display_id == 0` from the pre-scan:

```c
bool will_have_departing = false;
if (!s->narrow) {
    for (int i = 0; i < old_count; i++) {
        if (old_slots[i].active && old_ids[i] != 0  /* NEW: skip unclaimed */
            && find_id_in(ids, count, old_ids[i]) < 0) {
            will_have_departing = true;
            break;
        }
    }
}
```

**Part B — Adoption in matching loop**: When `find_id_in` returns -1 for `new_i == 0`, check for an unclaimed old slot with `display_id == 0` and adopt it:

```c
for (int new_i = 0; new_i < count; new_i++) {
    int old_i = find_id_in(old_ids, old_count, ids[new_i]);

    /* NEW: Adopt unclaimed slot 0 for the first new session */
    if (old_i < 0 && new_i == 0) {
        for (int j = 0; j < old_count; j++) {
            if (old_slots[j].active && old_ids[j] == 0 && old_slots[j].sprite_img) {
                old_i = j;
                break;
            }
        }
    }

    int x_off = x_centers[count - 1][new_i] - 160;
    if (old_i >= 0 && old_slots[old_i].active) {
        /* existing transfer path — sprite stays, walks to new position */
    } else {
        /* existing walk-in from off-screen path */
    }
}
```

With the pre-scan fixed (Part A), `will_have_departing` is only true when real sessions depart. The adopted slot correctly enters the immediate-walk branch and walks from center (x_off=0) to its multi-session position.

This only triggers when:
- `new_i == 0` (first slot in the new set)
- No ID match was found
- An old slot exists with `display_id == 0` (unclaimed from disconnect/boot)

Result: the disconnect clawd becomes session slot 0 and walks to its multi-session position. Other new sessions walk in from off-screen normally.

## Testing

All three fixes can be tested via the simulator with `--headless --events`:

1. **Fix 1**: Connect, send 2 sessions, open notifications (narrow), remove session at slot 0. Verify slot 0 shows the surviving session (not empty).
2. **Fix 2**: Connect with 2+ sessions, then disconnect. Verify only the disconnect animation shows (no extra clawds).
3. **Fix 3**: Start disconnected, connect, send 2 sessions. Verify the main clawd slides to slot 0 position (no going-away animation).

Interactive testing with the `--listen` TCP server covers all scenarios with live session manipulation.

## Files Changed

- `firmware/main/scene.c` — all three fixes
