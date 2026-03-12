# macOS Menu Bar App — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a macOS status bar app that wraps the Clawd Tank daemon, exposing device configuration (brightness, sleep timeout) and connection/notification status via a menu bar icon.

**Architecture:** The daemon becomes a reusable library with two entry points (headless CLI, menu bar GUI). A new BLE GATT config characteristic (read+write) enables device settings. Config is persisted to NVS on the ESP32. The menu bar app runs rumps on the main thread with the daemon's asyncio loop in a background thread.

**Tech Stack:** ESP-IDF 5.3.2 (C), NimBLE, NVS, LEDC, Python 3.11, asyncio, bleak, rumps, PyObjC

**Spec:** `docs/superpowers/specs/2026-03-12-macos-menubar-app-design.md`

---

## File Map

### New Files (Firmware)
- `firmware/main/config_store.h` — Config store public API (init, getters, setters, JSON serialize)
- `firmware/main/config_store.c` — NVS-backed config store implementation
- `firmware/test/test_config_store.c` — Unit tests for config store (with NVS mock)

### Modified Files (Firmware)
- `firmware/main/display.h` — Add `display_set_brightness()` declaration
- `firmware/main/display.c` — Add `display_set_brightness()`, use config store for initial brightness
- `firmware/main/ui_manager.h` — Add `ui_manager_set_sleep_timeout()` declaration
- `firmware/main/ui_manager.c` — Replace hardcoded sleep timeout with config store, add runtime setter
- `firmware/main/ble_service.c` — Add config characteristic with read/write callbacks
- `firmware/main/main.c` — Call `config_store_init()` before `display_init()`
- `firmware/test/Makefile` — Add `test_config_store` target

### New Files (Host)
- `host/clawd_tank_menubar/__init__.py` — Package marker
- `host/clawd_tank_menubar/app.py` — rumps.App subclass, daemon thread, menu UI
- `host/clawd_tank_menubar/launchd.py` — Launch-at-login plist management
- `host/clawd_tank_menubar/slider.py` — Custom NSSlider menu item via PyObjC
- `host/clawd_tank_menubar/icons/` — Status bar icon images (3 states, @1x and @2x)
- `host/tests/test_ble_config.py` — Tests for BLE config read/write methods
- `host/tests/test_observer.py` — Tests for daemon observer callbacks
- `host/tests/test_menubar.py` — Tests for menu bar app state transitions

### Modified Files (Host)
- `host/clawd_tank_daemon/ble_client.py` — Add `read_config()`, `write_config()`, disconnect callback
- `host/clawd_tank_daemon/daemon.py` — Add `DaemonObserver` protocol, observer param, callback calls
- `host/requirements.txt` — Add `rumps`

### Modified Files (Simulator)
- `simulator/shims/ble_service.h` — Add config store shim declarations
- `simulator/sim_events.c` — Add `config` event command

---

## Chunk 1: Firmware Config Store

### Task 1: Create config_store header

**Files:**
- Create: `firmware/main/config_store.h`

- [ ] **Step 1: Write config_store.h**

```c
// firmware/main/config_store.h
#ifndef CONFIG_STORE_H
#define CONFIG_STORE_H

#include <stdint.h>

#define CONFIG_DEFAULT_BRIGHTNESS    102
#define CONFIG_DEFAULT_SLEEP_TIMEOUT 300  /* seconds */

// Initialize config store — loads from NVS, or uses defaults.
// Must be called before display_init().
void config_store_init(void);

// Getters — return current in-memory values.
uint8_t  config_store_get_brightness(void);
uint32_t config_store_get_sleep_timeout_ms(void);  /* returns milliseconds */

// Setters — update in-memory value AND persist to NVS.
void config_store_set_brightness(uint8_t duty);
void config_store_set_sleep_timeout(uint16_t seconds);

// Serialize full config to JSON. Returns number of bytes written (excluding null).
// Output is null-terminated.
uint16_t config_store_serialize_json(char *buf, uint16_t buf_sz);

#endif // CONFIG_STORE_H
```

- [ ] **Step 2: Commit**

```bash
git add firmware/main/config_store.h
git commit -m "feat(config): add config_store header with public API"
```

### Task 2: Write config_store unit tests

**Files:**
- Create: `firmware/test/test_config_store.c`
- Modify: `firmware/test/Makefile`

- [ ] **Step 1: Write test_config_store.c with NVS mock**

The test file must mock NVS functions since we're compiling natively (not on ESP-IDF). The mock stores values in static variables.

```c
// firmware/test/test_config_store.c
#include <stdio.h>
#include <string.h>
#include <assert.h>
#include <stdlib.h>

// --- NVS mock ---
// config_store.c calls nvs_open, nvs_get_u8, nvs_get_u16, nvs_set_u8, nvs_set_u16, nvs_commit, nvs_close.
// We mock these with a simple key-value store.

typedef int esp_err_t;
typedef uint32_t nvs_handle_t;
#define ESP_OK 0
#define ESP_ERR_NVS_NOT_FOUND 0x1102

static uint8_t  mock_brightness = 0;
static uint16_t mock_sleep_timeout = 0;
static int mock_brightness_set = 0;
static int mock_sleep_timeout_set = 0;

static void mock_nvs_reset(void) {
    mock_brightness = 0;
    mock_sleep_timeout = 0;
    mock_brightness_set = 0;
    mock_sleep_timeout_set = 0;
}

esp_err_t nvs_open(const char *ns, int mode, nvs_handle_t *handle) {
    (void)ns; (void)mode;
    *handle = 1;
    return ESP_OK;
}

esp_err_t nvs_get_u8(nvs_handle_t h, const char *key, uint8_t *val) {
    (void)h;
    if (strcmp(key, "brightness") == 0 && mock_brightness_set) {
        *val = mock_brightness;
        return ESP_OK;
    }
    return ESP_ERR_NVS_NOT_FOUND;
}

esp_err_t nvs_get_u16(nvs_handle_t h, const char *key, uint16_t *val) {
    (void)h;
    if (strcmp(key, "sleep_tmout") == 0 && mock_sleep_timeout_set) {
        *val = mock_sleep_timeout;
        return ESP_OK;
    }
    return ESP_ERR_NVS_NOT_FOUND;
}

esp_err_t nvs_set_u8(nvs_handle_t h, const char *key, uint8_t val) {
    (void)h;
    if (strcmp(key, "brightness") == 0) {
        mock_brightness = val;
        mock_brightness_set = 1;
    }
    return ESP_OK;
}

esp_err_t nvs_set_u16(nvs_handle_t h, const char *key, uint16_t val) {
    (void)h;
    if (strcmp(key, "sleep_tmout") == 0) {
        mock_sleep_timeout = val;
        mock_sleep_timeout_set = 1;
    }
    return ESP_OK;
}

esp_err_t nvs_commit(nvs_handle_t h) { (void)h; return ESP_OK; }
void nvs_close(nvs_handle_t h) { (void)h; }

// Provide ESP_LOG stubs
#define ESP_LOGI(tag, fmt, ...) (void)0
#define ESP_LOGW(tag, fmt, ...) (void)0
#define ESP_LOGE(tag, fmt, ...) (void)0

// Now include the implementation directly (compiles with our mocks)
#include "../main/config_store.c"

// --- Tests ---

static void test_defaults_when_nvs_empty(void) {
    mock_nvs_reset();
    config_store_init();
    assert(config_store_get_brightness() == CONFIG_DEFAULT_BRIGHTNESS);
    assert(config_store_get_sleep_timeout_ms() == CONFIG_DEFAULT_SLEEP_TIMEOUT * 1000);
    printf("  PASS: test_defaults_when_nvs_empty\n");
}

static void test_loads_from_nvs(void) {
    mock_nvs_reset();
    mock_brightness = 200;
    mock_brightness_set = 1;
    mock_sleep_timeout = 60;
    mock_sleep_timeout_set = 1;
    config_store_init();
    assert(config_store_get_brightness() == 200);
    assert(config_store_get_sleep_timeout_ms() == 60000);
    printf("  PASS: test_loads_from_nvs\n");
}

static void test_set_brightness_persists(void) {
    mock_nvs_reset();
    config_store_init();
    config_store_set_brightness(255);
    assert(config_store_get_brightness() == 255);
    assert(mock_brightness == 255);
    assert(mock_brightness_set == 1);
    printf("  PASS: test_set_brightness_persists\n");
}

static void test_set_sleep_timeout_persists(void) {
    mock_nvs_reset();
    config_store_init();
    config_store_set_sleep_timeout(120);
    assert(config_store_get_sleep_timeout_ms() == 120000);
    assert(mock_sleep_timeout == 120);
    assert(mock_sleep_timeout_set == 1);
    printf("  PASS: test_set_sleep_timeout_persists\n");
}

static void test_serialize_json(void) {
    mock_nvs_reset();
    config_store_init();
    config_store_set_brightness(128);
    config_store_set_sleep_timeout(600);

    char buf[256];
    uint16_t len = config_store_serialize_json(buf, sizeof(buf));
    assert(len > 0);
    assert(len == strlen(buf));

    // Verify it contains expected fields
    assert(strstr(buf, "\"brightness\":128") != NULL);
    assert(strstr(buf, "\"sleep_timeout\":600") != NULL);
    printf("  PASS: test_serialize_json\n");
}

static void test_serialize_json_default_values(void) {
    mock_nvs_reset();
    config_store_init();

    char buf[256];
    uint16_t len = config_store_serialize_json(buf, sizeof(buf));
    assert(len > 0);
    assert(strstr(buf, "\"brightness\":102") != NULL);
    assert(strstr(buf, "\"sleep_timeout\":300") != NULL);
    printf("  PASS: test_serialize_json_default_values\n");
}

static void test_serialize_json_small_buffer(void) {
    mock_nvs_reset();
    config_store_init();

    char buf[10];  // Too small for full JSON
    uint16_t len = config_store_serialize_json(buf, sizeof(buf));
    // Should return 0 or truncated safely
    assert(len < sizeof(buf));
    printf("  PASS: test_serialize_json_small_buffer\n");
}

int main(void) {
    printf("Running config store tests...\n");
    test_defaults_when_nvs_empty();
    test_loads_from_nvs();
    test_set_brightness_persists();
    test_set_sleep_timeout_persists();
    test_serialize_json();
    test_serialize_json_default_values();
    test_serialize_json_small_buffer();
    printf("All config store tests passed!\n");
    return 0;
}
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd firmware/test && gcc -Wall -Wextra -I../main -o test_config_store test_config_store.c 2>&1
```

Expected: Compilation error — `config_store.c` does not exist yet.

- [ ] **Step 3: Implement config_store.c**

```c
// firmware/main/config_store.c
#include "config_store.h"
#include "esp_log.h"
#include "nvs_flash.h"
#include "nvs.h"
#include <stdio.h>
#include <string.h>

static const char *TAG = "config";
static const char *NVS_NAMESPACE = "clawd_cfg";

static uint8_t  s_brightness;
static uint16_t s_sleep_timeout_secs;

void config_store_init(void)
{
    s_brightness = CONFIG_DEFAULT_BRIGHTNESS;
    s_sleep_timeout_secs = CONFIG_DEFAULT_SLEEP_TIMEOUT;

    nvs_handle_t handle;
    esp_err_t err = nvs_open(NVS_NAMESPACE, 1 /* NVS_READWRITE */, &handle);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "NVS open failed (%d), using defaults", err);
        return;
    }

    uint8_t b;
    if (nvs_get_u8(handle, "brightness", &b) == ESP_OK) {
        s_brightness = b;
        ESP_LOGI(TAG, "Loaded brightness=%u from NVS", b);
    }

    uint16_t t;
    if (nvs_get_u16(handle, "sleep_tmout", &t) == ESP_OK) {
        s_sleep_timeout_secs = t;
        ESP_LOGI(TAG, "Loaded sleep_timeout=%u from NVS", t);
    }

    nvs_close(handle);
}

uint8_t config_store_get_brightness(void)
{
    return s_brightness;
}

uint32_t config_store_get_sleep_timeout_ms(void)
{
    return (uint32_t)s_sleep_timeout_secs * 1000;
}

void config_store_set_brightness(uint8_t duty)
{
    s_brightness = duty;

    nvs_handle_t handle;
    if (nvs_open(NVS_NAMESPACE, 1, &handle) == ESP_OK) {
        nvs_set_u8(handle, "brightness", duty);
        nvs_commit(handle);
        nvs_close(handle);
    }
}

void config_store_set_sleep_timeout(uint16_t seconds)
{
    s_sleep_timeout_secs = seconds;

    nvs_handle_t handle;
    if (nvs_open(NVS_NAMESPACE, 1, &handle) == ESP_OK) {
        nvs_set_u16(handle, "sleep_tmout", seconds);
        nvs_commit(handle);
        nvs_close(handle);
    }
}

uint16_t config_store_serialize_json(char *buf, uint16_t buf_sz)
{
    int n = snprintf(buf, buf_sz,
        "{\"brightness\":%u,\"sleep_timeout\":%u}",
        s_brightness, s_sleep_timeout_secs);
    if (n < 0 || (uint16_t)n >= buf_sz) {
        if (buf_sz > 0) buf[0] = '\0';
        return 0;
    }
    return (uint16_t)n;
}
```

- [ ] **Step 4: Run tests and verify they pass**

```bash
cd firmware/test && gcc -Wall -Wextra -I../main -o test_config_store test_config_store.c && ./test_config_store
```

Expected: All 7 tests pass.

- [ ] **Step 5: Add test_config_store to Makefile**

Add the new target to the existing Makefile so `make test` runs both test suites:

```makefile
# In firmware/test/Makefile, update:
test: test_notification test_config_store
	./test_notification
	./test_config_store

test_config_store: test_config_store.c
	$(CC) $(CFLAGS) -o $@ $<

clean:
	rm -f test_notification test_config_store
```

Note: `test_config_store.c` includes `config_store.c` directly (with mocks), so no additional source files needed on the command line.

- [ ] **Step 6: Run full test suite**

```bash
cd firmware/test && make clean && make test
```

Expected: Both `test_notification` and `test_config_store` pass.

- [ ] **Step 7: Commit**

```bash
git add firmware/main/config_store.c firmware/test/test_config_store.c firmware/test/Makefile
git commit -m "feat(config): add NVS-backed config store with unit tests"
```

---

## Chunk 2: Firmware Integration — Display, UI Manager, BLE, Main

### Task 3: Add display_set_brightness()

**Files:**
- Modify: `firmware/main/display.h`
- Modify: `firmware/main/display.c`

- [ ] **Step 1: Add declaration to display.h**

Add after the `display_init()` declaration:

```c
// Set backlight brightness (0-255 PWM duty cycle).
void display_set_brightness(uint8_t duty);
```

- [ ] **Step 2: Add implementation to display.c**

Add at the end of `display.c`, before the closing of the file:

```c
void display_set_brightness(uint8_t duty)
{
    ledc_set_duty(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_0, duty);
    ledc_update_duty(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_0);
    ESP_LOGI(TAG, "Brightness set to %u", duty);
}
```

- [ ] **Step 3: Use config store for initial brightness**

In `display_init()`, replace the hardcoded duty value `102` with the config store getter. Both the `ledc_set_duty()` and `ledc_update_duty()` lines must remain:

```c
// Replace:
//   ledc_set_duty(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_0, 102);
//   ledc_update_duty(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_0);
// With:
    ledc_set_duty(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_0, config_store_get_brightness());
    ledc_update_duty(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_0);
```

Add `#include "config_store.h"` at the top of `display.c`.

- [ ] **Step 4: Commit**

```bash
git add firmware/main/display.h firmware/main/display.c
git commit -m "feat(display): add display_set_brightness() and load initial from config store"
```

### Task 4: Add ui_manager_set_sleep_timeout()

**Files:**
- Modify: `firmware/main/ui_manager.h`
- Modify: `firmware/main/ui_manager.c`

- [ ] **Step 1: Add declaration to ui_manager.h**

Add after the `ui_manager_tick()` declaration:

```c
// Set sleep timeout in milliseconds. 0 = never sleep.
// Takes effect immediately (resets idle timer).
void ui_manager_set_sleep_timeout(uint32_t ms);
```

- [ ] **Step 2: Modify ui_manager.c — replace macro with variable**

Replace `#define SLEEP_TIMEOUT_MS (5 * 60 * 1000)` with:

```c
static uint32_t s_sleep_timeout_ms = 5 * 60 * 1000;  /* updated by config store */
```

In `ui_manager_init()`, add after `_lock_init(&s_lock)`:

```c
    s_sleep_timeout_ms = config_store_get_sleep_timeout_ms();
```

Add `#include "config_store.h"` at the top.

- [ ] **Step 3: Update sleep timeout check in ui_manager_tick()**

In `ui_manager_tick()`, replace `SLEEP_TIMEOUT_MS` with `s_sleep_timeout_ms` in the comparison:

```c
    // Replace:  if (elapsed >= SLEEP_TIMEOUT_MS) {
    // With:
    if (s_sleep_timeout_ms > 0 && elapsed >= s_sleep_timeout_ms) {
```

Note the `s_sleep_timeout_ms > 0` check — 0 means "never sleep" per spec.

- [ ] **Step 4: Add the runtime setter**

```c
void ui_manager_set_sleep_timeout(uint32_t ms)
{
    _lock_acquire(&s_lock);
    s_sleep_timeout_ms = ms;
    s_last_activity_tick = lv_tick_get();  /* reset idle timer */
    if (s_sleeping) {
        s_sleeping = false;
        if (s_state == UI_STATE_FULL_IDLE) {
            scene_set_clawd_anim(s_scene, CLAWD_ANIM_IDLE);
        }
    }
    _lock_release(&s_lock);
    ESP_LOGI(TAG, "Sleep timeout set to %lu ms", (unsigned long)ms);
}
```

- [ ] **Step 5: Commit**

```bash
git add firmware/main/ui_manager.h firmware/main/ui_manager.c
git commit -m "feat(ui): add runtime sleep timeout setter, load from config store"
```

### Task 5: Add BLE config characteristic

**Files:**
- Modify: `firmware/main/ble_service.c`

- [ ] **Step 1: Add config characteristic UUID (after the notification UUID)**

```c
// Config Characteristic: E9F6E626-5FCA-4201-B80C-4D2B51C40F51
static const ble_uuid128_t config_chr_uuid = BLE_UUID128_INIT(
    0x51, 0x0f, 0xc4, 0x51, 0x2b, 0x4d, 0x0c, 0xb8,
    0x01, 0x42, 0xca, 0x5f, 0x26, 0xe6, 0xf6, 0xe9
);
```

- [ ] **Step 2: Add includes for config_store, display, and ui_manager**

```c
#include "config_store.h"
#include "display.h"
#include "ui_manager.h"
```

- [ ] **Step 3: Implement config read/write callback**

NimBLE handles Long Read fragmentation internally — the access callback is called once per read, and the stack slices the response at the correct offset. The callback just appends the full value to `ctxt->om`.

```c
static int config_access_cb(uint16_t conn_handle, uint16_t attr_handle,
                             struct ble_gatt_access_ctxt *ctxt, void *arg)
{
    if (ctxt->op == BLE_GATT_ACCESS_OP_READ_CHR) {
        char buf[512];
        uint16_t len = config_store_serialize_json(buf, sizeof(buf));
        int rc = os_mbuf_append(ctxt->om, buf, len);
        return rc == 0 ? 0 : BLE_ATT_ERR_INSUFFICIENT_RES;
    }

    if (ctxt->op == BLE_GATT_ACCESS_OP_WRITE_CHR) {
        uint16_t len = OS_MBUF_PKTLEN(ctxt->om);
        if (len == 0 || len > 512) {
            return BLE_ATT_ERR_INVALID_ATTR_VALUE_LEN;
        }

        char wbuf[513];
        uint16_t copied;
        int rc = ble_hs_mbuf_to_flat(ctxt->om, wbuf, len, &copied);
        if (rc != 0) {
            return BLE_ATT_ERR_UNLIKELY;
        }
        wbuf[copied] = '\0';

        cJSON *json = cJSON_ParseWithLength(wbuf, copied);
        if (!json) {
            ESP_LOGW(TAG, "Config: malformed JSON");
            return BLE_ATT_ERR_UNLIKELY;
        }

        cJSON *brightness = cJSON_GetObjectItem(json, "brightness");
        if (brightness && cJSON_IsNumber(brightness)) {
            int val = brightness->valueint;
            if (val >= 0 && val <= 255) {
                config_store_set_brightness((uint8_t)val);
                display_set_brightness((uint8_t)val);
                ESP_LOGI(TAG, "Config: brightness=%d", val);
            }
        }

        cJSON *sleep_timeout = cJSON_GetObjectItem(json, "sleep_timeout");
        if (sleep_timeout && cJSON_IsNumber(sleep_timeout)) {
            int val = sleep_timeout->valueint;
            if (val >= 0 && val <= 3600) {
                config_store_set_sleep_timeout((uint16_t)val);
                ui_manager_set_sleep_timeout((uint32_t)val * 1000);
                ESP_LOGI(TAG, "Config: sleep_timeout=%d", val);
            }
        }

        cJSON_Delete(json);
        return 0;
    }

    return 0;
}
```

- [ ] **Step 4: Add config characteristic to the GATT service definition**

Update the `gatt_svcs` array to add the config characteristic after the notification one:

```c
static const struct ble_gatt_svc_def gatt_svcs[] = {
    {
        .type = BLE_GATT_SVC_TYPE_PRIMARY,
        .uuid = &clawd_svc_uuid.u,
        .characteristics = (struct ble_gatt_chr_def[]) {
            {
                .uuid = &notif_chr_uuid.u,
                .access_cb = notification_write_cb,
                .flags = BLE_GATT_CHR_F_WRITE | BLE_GATT_CHR_F_WRITE_NO_RSP,
            },
            {
                .uuid = &config_chr_uuid.u,
                .access_cb = config_access_cb,
                .flags = BLE_GATT_CHR_F_READ | BLE_GATT_CHR_F_WRITE | BLE_GATT_CHR_F_WRITE_NO_RSP,
            },
            { 0 },
        },
    },
    { 0 },
};
```

- [ ] **Step 5: Commit**

```bash
git add firmware/main/ble_service.c
git commit -m "feat(ble): add config characteristic with read/write and NVS persistence"
```

### Task 6: Update main.c init order

**Files:**
- Modify: `firmware/main/main.c`

- [ ] **Step 1: Move nvs_flash_init() to main.c and add config_store_init()**

Currently `nvs_flash_init()` is called inside `ble_service_init()` (ble_service.c:205). Since `config_store_init()` needs NVS before BLE init, move the NVS init to `main.c`.

Add includes at the top of `main.c`:

```c
#include "config_store.h"
#include "nvs_flash.h"
```

In `app_main()`, add before `display_init()`:

```c
    // Init NVS (moved from ble_service_init — needed by config_store before BLE)
    ESP_ERROR_CHECK(nvs_flash_init());

    // Init config store — must be before display_init for brightness
    config_store_init();
```

Remove `ESP_ERROR_CHECK(nvs_flash_init())` from `ble_service_init()` in `ble_service.c` (it's now called from `main.c` before `ble_service_init`). Also remove the `#include "nvs_flash.h"` from `ble_service.c` if no other NVS calls remain there.

- [ ] **Step 2: Build firmware to verify compilation**

```bash
cd firmware && idf.py build
```

Expected: Build succeeds with no errors.

- [ ] **Step 3: Commit**

```bash
git add firmware/main/main.c
git commit -m "feat(main): init config store before display for NVS-backed brightness"
```

---

## Chunk 3: Host — BLE Config Methods and Daemon Observer

### Task 7: Add BLE config methods to ClawdBleClient

**Files:**
- Modify: `host/clawd_tank_daemon/ble_client.py`
- Create: `host/tests/test_ble_config.py`

- [ ] **Step 1: Write failing tests**

```python
# host/tests/test_ble_config.py
import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from clawd_tank_daemon.ble_client import ClawdBleClient, CONFIG_CHR_UUID


@pytest.mark.asyncio
async def test_read_config_returns_dict():
    client = ClawdBleClient()
    client._client = MagicMock()
    client._client.is_connected = True
    client._client.read_gatt_char = AsyncMock(
        return_value=b'{"brightness":102,"sleep_timeout":300}'
    )
    result = await client.read_config()
    assert result == {"brightness": 102, "sleep_timeout": 300}
    client._client.read_gatt_char.assert_called_once_with(CONFIG_CHR_UUID)


@pytest.mark.asyncio
async def test_read_config_not_connected():
    client = ClawdBleClient()
    client._client = None
    result = await client.read_config()
    assert result == {}


@pytest.mark.asyncio
async def test_write_config_success():
    client = ClawdBleClient()
    client._client = MagicMock()
    client._client.is_connected = True
    client._client.write_gatt_char = AsyncMock()
    result = await client.write_config('{"brightness":200}')
    assert result is True
    client._client.write_gatt_char.assert_called_once_with(
        CONFIG_CHR_UUID, b'{"brightness":200}', response=False
    )


@pytest.mark.asyncio
async def test_write_config_not_connected():
    client = ClawdBleClient()
    client._client = None
    result = await client.write_config('{"brightness":200}')
    assert result is False


@pytest.mark.asyncio
async def test_write_config_ble_error():
    client = ClawdBleClient()
    client._client = MagicMock()
    client._client.is_connected = True
    client._client.write_gatt_char = AsyncMock(side_effect=Exception("BLE error"))
    result = await client.write_config('{"brightness":200}')
    assert result is False
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd host && python -m pytest tests/test_ble_config.py -v
```

Expected: ImportError — `CONFIG_CHR_UUID` does not exist yet.

- [ ] **Step 3: Implement the methods**

In `host/clawd_tank_daemon/ble_client.py`, add `import json` at the top with the other imports, and add the UUID constant after the existing ones:

```python
import json
```

```python
CONFIG_CHR_UUID = "e9f6e626-5fca-4201-b80c-4d2b51c40f51"
```

Add these methods to `ClawdBleClient`:

```python
    async def read_config(self) -> dict:
        """Read full device config from the config characteristic.

        Returns empty dict if not connected or on error.
        """
        async with self._lock:
            if not self.is_connected:
                logger.warning("Not connected, cannot read config")
                return {}
            try:
                data = await self._client.read_gatt_char(CONFIG_CHR_UUID)
                return json.loads(data.decode("utf-8"))
            except Exception as e:
                logger.error("Config read failed: %s", e)
                return {}

    async def write_config(self, payload: str) -> bool:
        """Write a partial config JSON to the config characteristic.

        Returns True on success, False on failure.
        """
        async with self._lock:
            if not self.is_connected:
                logger.warning("Not connected, cannot write config")
                return False
            try:
                data = payload.encode("utf-8")
                await self._client.write_gatt_char(
                    CONFIG_CHR_UUID, data, response=False
                )
                logger.debug("Config write: %s", payload)
                return True
            except Exception as e:
                logger.error("Config write failed: %s", e)
                return False
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd host && python -m pytest tests/test_ble_config.py -v
```

Expected: All 5 tests pass.

- [ ] **Step 5: Commit**

```bash
git add host/clawd_tank_daemon/ble_client.py host/tests/test_ble_config.py
git commit -m "feat(ble): add read_config/write_config to BLE client"
```

### Task 8: Add DaemonObserver and disconnect callback

**Files:**
- Modify: `host/clawd_tank_daemon/daemon.py`
- Modify: `host/clawd_tank_daemon/ble_client.py`
- Create: `host/tests/test_observer.py`

- [ ] **Step 1: Write failing tests**

```python
# host/tests/test_observer.py
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from clawd_tank_daemon.daemon import ClawdDaemon, DaemonObserver


class MockObserver:
    def __init__(self):
        self.connection_changes = []
        self.notification_changes = []

    def on_connection_change(self, connected: bool) -> None:
        self.connection_changes.append(connected)

    def on_notification_change(self, count: int) -> None:
        self.notification_changes.append(count)


@pytest.mark.asyncio
async def test_observer_notification_add():
    observer = MockObserver()
    daemon = ClawdDaemon(observer=observer)
    await daemon._handle_message(
        {"event": "add", "session_id": "s1", "project": "p", "message": "m"}
    )
    assert observer.notification_changes == [1]


@pytest.mark.asyncio
async def test_observer_notification_dismiss():
    observer = MockObserver()
    daemon = ClawdDaemon(observer=observer)
    await daemon._handle_message(
        {"event": "add", "session_id": "s1", "project": "p", "message": "m"}
    )
    await daemon._handle_message({"event": "dismiss", "session_id": "s1"})
    assert observer.notification_changes == [1, 0]


@pytest.mark.asyncio
async def test_observer_notification_add_multiple():
    observer = MockObserver()
    daemon = ClawdDaemon(observer=observer)
    await daemon._handle_message(
        {"event": "add", "session_id": "s1", "project": "p", "message": "m"}
    )
    await daemon._handle_message(
        {"event": "add", "session_id": "s2", "project": "p", "message": "m"}
    )
    assert observer.notification_changes == [1, 2]


@pytest.mark.asyncio
async def test_no_observer_does_not_crash():
    """ClawdDaemon without observer must work exactly as before."""
    daemon = ClawdDaemon()
    await daemon._handle_message(
        {"event": "add", "session_id": "s1", "project": "p", "message": "m"}
    )
    assert "s1" in daemon._active_notifications


@pytest.mark.asyncio
async def test_observer_connection_via_disconnect_callback():
    """BLE client disconnect callback triggers observer."""
    observer = MockObserver()
    daemon = ClawdDaemon(observer=observer)
    # Simulate the disconnect callback the daemon registers with BLE client
    daemon._on_ble_disconnect()
    assert observer.connection_changes == [False]


@pytest.mark.asyncio
async def test_observer_connection_true_on_ble_sender_connect():
    """_ble_sender fires on_connection_change(True) after reconnect."""
    observer = MockObserver()
    daemon = ClawdDaemon(observer=observer)
    daemon._ble = AsyncMock()
    daemon._ble.is_connected = False  # starts disconnected

    async def fake_ensure():
        daemon._ble.is_connected = True  # becomes connected

    daemon._ble.ensure_connected = AsyncMock(side_effect=fake_ensure)
    daemon._ble.write_notification = AsyncMock(return_value=True)

    await daemon._pending_queue.put(
        {"event": "dismiss", "session_id": "s1"}
    )

    sender = asyncio.create_task(daemon._ble_sender())
    await asyncio.sleep(0.1)
    daemon._running = False
    sender.cancel()
    try:
        await sender
    except asyncio.CancelledError:
        pass

    assert True in observer.connection_changes
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd host && python -m pytest tests/test_observer.py -v
```

Expected: TypeError or ImportError — `DaemonObserver` and `observer` param don't exist yet.

- [ ] **Step 3: Add DaemonObserver protocol and update ClawdDaemon**

In `host/clawd_tank_daemon/daemon.py`, add after the imports:

```python
from typing import Protocol, Optional, runtime_checkable


@runtime_checkable
class DaemonObserver(Protocol):
    def on_connection_change(self, connected: bool) -> None: ...
    def on_notification_change(self, count: int) -> None: ...
```

Update `ClawdDaemon.__init__`:

```python
    def __init__(self, observer: Optional[DaemonObserver] = None, headless: bool = True):
        self._ble = ClawdBleClient(on_disconnect_cb=self._on_ble_disconnect)
        self._socket = SocketServer(on_message=self._handle_message)
        self._active_notifications: dict[str, dict] = {}
        self._pending_queue: asyncio.Queue[dict] = asyncio.Queue()
        self._running = True
        self._shutdown_event = asyncio.Event()
        self._lock_fd: int | None = None
        self._observer = observer
        self._headless = headless  # False when embedded in menu bar app
```

Update `_handle_message` — add observer call at the end:

```python
    async def _handle_message(self, msg: dict) -> None:
        event = msg.get("event")
        session_id = msg.get("session_id", "")

        if event == "add":
            self._active_notifications[session_id] = msg
        elif event == "dismiss":
            self._active_notifications.pop(session_id, None)

        await self._pending_queue.put(msg)

        if self._observer:
            self._observer.on_notification_change(len(self._active_notifications))
```

Add a disconnect callback method:

```python
    def _on_ble_disconnect(self) -> None:
        """Called by BLE client on disconnect."""
        if self._observer:
            self._observer.on_connection_change(False)
```

Update `run()` — conditionally register signal handlers (they only work on the main thread; skip when embedded in menu bar app):

In `daemon.py`'s `run()` method, wrap the signal handler registration:

```python
        if self._headless:
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(sig, lambda: asyncio.create_task(self._shutdown()))
```

(The existing code registers signal handlers unconditionally. Wrap it in `if self._headless:` so it's skipped when running in a background thread from the menu bar app, where `set_wakeup_fd` would raise `ValueError`.)

Update `_ble_sender` — add observer call after successful connect:

```python
    async def _ble_sender(self) -> None:
        while self._running:
            try:
                msg = await asyncio.wait_for(self._pending_queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            try:
                payload = daemon_message_to_ble_payload(msg)
            except ValueError:
                logger.error("Skipping unknown event: %s", msg.get("event"))
                continue

            was_connected = self._ble.is_connected
            await self._ble.ensure_connected()
            if not was_connected and self._ble.is_connected and self._observer:
                self._observer.on_connection_change(True)

            success = await self._ble.write_notification(payload)

            if not success:
                was_connected = self._ble.is_connected
                await self._ble.ensure_connected()
                if not was_connected and self._ble.is_connected and self._observer:
                    self._observer.on_connection_change(True)
                await self._replay_active()
```

- [ ] **Step 4: Add disconnect callback registration to BLE client**

In `host/clawd_tank_daemon/ble_client.py`, update `__init__` to accept a disconnect callback:

```python
    def __init__(self, on_disconnect_cb=None):
        self._client: BleakClient | None = None
        self._lock = asyncio.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._on_disconnect_cb = on_disconnect_cb
```

Update `_on_disconnect` to call the callback:

```python
    def _on_disconnect(self, client: BleakClient) -> None:
        logger.warning("Disconnected from Clawd Tank")
        if self._loop is not None and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._clear_client)
        else:
            self._clear_client()
        if self._on_disconnect_cb:
            if self._loop is not None and self._loop.is_running():
                self._loop.call_soon_threadsafe(self._on_disconnect_cb)
            else:
                self._on_disconnect_cb()
```

Note: `ClawdBleClient(on_disconnect_cb=self._on_ble_disconnect)` is already in the updated `__init__` above — no additional change needed here.

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd host && python -m pytest tests/test_observer.py tests/test_daemon.py -v
```

Expected: All tests pass (both new observer tests and existing daemon tests).

- [ ] **Step 6: Commit**

```bash
git add host/clawd_tank_daemon/daemon.py host/clawd_tank_daemon/ble_client.py host/tests/test_observer.py
git commit -m "feat(daemon): add DaemonObserver protocol and disconnect callback"
```

---

## Chunk 4: Menu Bar App

### Task 9: Create menu bar app package and entry point

**Files:**
- Create: `host/clawd_tank_menubar/__init__.py`
- Create: `host/clawd_tank_menubar/app.py`
- Create: `host/clawd_tank_menubar/slider.py`
- Create: `host/clawd_tank_menubar/launchd.py`
- Modify: `host/requirements.txt`

- [ ] **Step 1: Add rumps to requirements.txt**

```
rumps>=0.4.0
```

- [ ] **Step 2: Install dependencies**

```bash
cd host && pip install rumps
```

- [ ] **Step 3: Create package __init__.py**

```python
# host/clawd_tank_menubar/__init__.py
```

(Empty file — package marker only.)

- [ ] **Step 4: Create slider.py — custom NSSlider menu item**

SliderMenuItem must subclass `AppKit.NSObject` (via PyObjC) so that it's a valid ObjC target for the slider's action. Pure Python objects can't be targets for NSControl actions.

```python
# host/clawd_tank_menubar/slider.py
"""Custom NSSlider embedded in an NSMenuItem for the status bar menu."""

import time
import AppKit
import objc

# Debounce interval in seconds
DEBOUNCE_INTERVAL = 0.2


class SliderMenuItem(AppKit.NSObject):
    """Wraps an NSSlider inside an NSMenuItem for use in a rumps menu.

    Subclasses NSObject so it can be the target of the NSSlider action.
    """

    @classmethod
    def create(cls, label: str, min_val: int = 0, max_val: int = 255,
               initial: int = 102, on_change=None):
        """Factory method — NSObject subclasses use alloc().init(), not __init__."""
        instance = cls.alloc().init()
        instance._on_change = on_change
        instance._last_send_time = 0.0

        # Create container view
        width = 250
        height = 40
        view = AppKit.NSView.alloc().initWithFrame_(
            AppKit.NSMakeRect(0, 0, width, height)
        )

        # Label
        label_field = AppKit.NSTextField.labelWithString_(label)
        label_field.setFrame_(AppKit.NSMakeRect(16, 20, 120, 16))
        label_field.setFont_(AppKit.NSFont.systemFontOfSize_(13))
        view.addSubview_(label_field)

        # Value label
        instance._value_label = AppKit.NSTextField.labelWithString_(
            f"{int(initial / 255 * 100)}%"
        )
        instance._value_label.setFrame_(AppKit.NSMakeRect(width - 50, 20, 34, 16))
        instance._value_label.setFont_(AppKit.NSFont.systemFontOfSize_(11))
        instance._value_label.setAlignment_(AppKit.NSTextAlignmentRight)
        view.addSubview_(instance._value_label)

        # Slider
        instance._slider = AppKit.NSSlider.alloc().initWithFrame_(
            AppKit.NSMakeRect(16, 2, width - 32, 20)
        )
        instance._slider.setMinValue_(min_val)
        instance._slider.setMaxValue_(max_val)
        instance._slider.setIntegerValue_(initial)
        instance._slider.setContinuous_(True)
        instance._slider.setTarget_(instance)
        instance._slider.setAction_(objc.selector(
            instance.sliderChanged_, signature=b'v@:@'
        ))
        view.addSubview_(instance._slider)

        # Menu item
        instance.item = AppKit.NSMenuItem.alloc().init()
        instance.item.setView_(view)

        return instance

    @objc.python_method
    def set_value(self, value: int):
        """Set slider value programmatically (e.g., on config read)."""
        self._slider.setIntegerValue_(value)
        self._value_label.setStringValue_(f"{int(value / 255 * 100)}%")

    @objc.python_method
    def set_enabled(self, enabled: bool):
        """Enable or disable the slider."""
        self._slider.setEnabled_(enabled)
        if not enabled:
            self._value_label.setStringValue_("--")

    def sliderChanged_(self, sender):
        value = int(sender.integerValue())
        self._value_label.setStringValue_(f"{int(value / 255 * 100)}%")

        now = time.monotonic()
        if now - self._last_send_time >= DEBOUNCE_INTERVAL:
            self._last_send_time = now
            if self._on_change:
                self._on_change(value)
```

- [ ] **Step 5: Create launchd.py — Launch at Login management**

```python
# host/clawd_tank_menubar/launchd.py
"""Manage Launch at Login via launchd user agent plist."""

import os
import plistlib
import subprocess
import sys
from pathlib import Path

PLIST_LABEL = "com.clawd-tank.menubar"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{PLIST_LABEL}.plist"


def is_enabled() -> bool:
    """Check if the launch agent plist exists."""
    return PLIST_PATH.exists()


def enable() -> None:
    """Write the launchd plist and load the agent."""
    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)

    # Find the executable path
    executable = sys.executable
    module_path = "clawd_tank_menubar.app"

    plist = {
        "Label": PLIST_LABEL,
        "ProgramArguments": [executable, "-m", module_path],
        "RunAtLoad": True,
        "KeepAlive": False,
    }

    with open(PLIST_PATH, "wb") as f:
        plistlib.dump(plist, f)

    uid = os.getuid()
    subprocess.run(
        ["launchctl", "bootstrap", f"gui/{uid}", str(PLIST_PATH)],
        capture_output=True,
    )


def disable() -> None:
    """Unload and remove the launchd plist."""
    if not PLIST_PATH.exists():
        return

    uid = os.getuid()
    subprocess.run(
        ["launchctl", "bootout", f"gui/{uid}", str(PLIST_PATH)],
        capture_output=True,
    )

    PLIST_PATH.unlink(missing_ok=True)
```

- [ ] **Step 6: Create app.py — main menu bar application**

```python
# host/clawd_tank_menubar/app.py
"""Clawd Tank macOS status bar application."""

import asyncio
import json
import logging
import threading
from typing import Optional

import rumps

from clawd_tank_daemon.daemon import ClawdDaemon, DaemonObserver
from . import launchd
from .slider import SliderMenuItem

logger = logging.getLogger("clawd-tank.menubar")

SLEEP_TIMEOUT_OPTIONS = [
    ("1 minute", 60),
    ("2 minutes", 120),
    ("5 minutes", 300),
    ("10 minutes", 600),
    ("30 minutes", 1800),
    ("Never", 0),
]


class ClawdTankApp(rumps.App, DaemonObserver):
    def __init__(self):
        super().__init__("Clawd Tank", quit_button=None)

        self._daemon: Optional[ClawdDaemon] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._loop_ready = threading.Event()  # set when asyncio loop is running
        self._connected = False
        self._notification_count = 0
        self._current_config: dict = {}

        # Build menu
        self._status_item = rumps.MenuItem("Disconnected", callback=None)
        self._status_item.set_callback(None)
        self._subtitle_item = rumps.MenuItem("Scanning for device...", callback=None)
        self._subtitle_item.set_callback(None)

        # Brightness slider (factory method — SliderMenuItem subclasses NSObject)
        self._brightness_slider = SliderMenuItem.create(
            "Brightness", min_val=0, max_val=255, initial=102,
            on_change=self._on_brightness_change,
        )

        # Sleep timeout submenu
        self._sleep_menu = rumps.MenuItem("Sleep Timeout")
        self._sleep_timeout_value = 300
        for label, seconds in SLEEP_TIMEOUT_OPTIONS:
            item = rumps.MenuItem(label, callback=self._on_sleep_timeout_select)
            item._seconds = seconds
            if seconds == 300:
                item.state = True
            self._sleep_menu.add(item)

        # Launch at login
        self._login_item = rumps.MenuItem(
            "Launch at Login",
            callback=self._on_toggle_login,
        )
        self._login_item.state = launchd.is_enabled()

        # Reconnect
        self._reconnect_item = rumps.MenuItem("Reconnect", callback=self._on_reconnect)

        # Quit
        self._quit_item = rumps.MenuItem("Quit Clawd Tank", callback=self._on_quit)

        # Assemble menu
        self.menu = [
            self._status_item,
            self._subtitle_item,
            None,  # separator
            self._brightness_slider.item,
            None,
            self._sleep_menu,
            None,
            self._login_item,
            None,
            self._reconnect_item,
            None,
            self._quit_item,
        ]

        self._update_menu_state()

    # --- Lifecycle ---

    def _start_daemon_thread(self):
        """Start the daemon's asyncio event loop in a background thread."""
        self._daemon = ClawdDaemon(observer=self, headless=False)

        def run_loop():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._loop_ready.set()  # signal main thread that loop is available
            self._loop.run_until_complete(self._daemon.run())

        thread = threading.Thread(target=run_loop, daemon=True)
        thread.start()
        self._loop_ready.wait(timeout=5)  # wait for loop to be ready

    # --- DaemonObserver callbacks (called from asyncio thread) ---

    def on_connection_change(self, connected: bool) -> None:
        self._connected = connected
        if connected and self._loop:
            # Read config on connect
            asyncio.run_coroutine_threadsafe(
                self._read_device_config(), self._loop
            )
        self._schedule_menu_update()

    def on_notification_change(self, count: int) -> None:
        self._notification_count = count
        self._schedule_menu_update()

    # --- Config ---

    async def _read_device_config(self):
        """Read config from device and update menu."""
        if self._daemon:
            config = await self._daemon._ble.read_config()
            if config:
                self._current_config = config
                self._schedule_menu_update()

    def _schedule_menu_update(self):
        """Thread-safe menu update via PyObjC main thread dispatch."""
        try:
            from PyObjCTools.AppHelper import callAfter
            callAfter(self._update_menu_state)
        except ImportError:
            # Fallback: direct call (may not be thread-safe)
            self._update_menu_state()

    def _update_menu_state(self):
        """Update all menu items based on current state. Must run on main thread."""
        if self._connected:
            self._status_item.title = "Connected"
            if self._notification_count > 0:
                self._subtitle_item.title = (
                    f"{self._notification_count} active notification"
                    f"{'s' if self._notification_count != 1 else ''}"
                )
                self.icon = self._icon_path("crab-notifications")
            else:
                self._subtitle_item.title = "No active notifications"
                self.icon = self._icon_path("crab-connected")

            # Update brightness from config
            brightness = self._current_config.get("brightness", 102)
            self._brightness_slider.set_value(brightness)
            self._brightness_slider.set_enabled(True)

            # Update sleep timeout from config
            timeout = self._current_config.get("sleep_timeout", 300)
            self._sleep_timeout_value = timeout
            for key, item in self._sleep_menu.items():
                item.state = (item._seconds == timeout)
            self._sleep_menu.title = f"Sleep Timeout"

            self._reconnect_item.set_callback(self._on_reconnect)
        else:
            self._status_item.title = "Disconnected"
            self._subtitle_item.title = "Scanning for device..."
            self.icon = self._icon_path("crab-disconnected")
            self._brightness_slider.set_enabled(False)
            self._reconnect_item.set_callback(None)

    def _icon_path(self, name: str) -> Optional[str]:
        """Return path to icon file, or None if not found."""
        import importlib.resources
        try:
            icons_dir = importlib.resources.files("clawd_tank_menubar") / "icons"
            path = icons_dir / f"{name}.png"
            if hasattr(path, '__fspath__'):
                return str(path)
        except Exception:
            pass
        return None

    # --- Menu callbacks ---

    def _on_brightness_change(self, value: int):
        """Called from slider on main thread. Send config write via asyncio."""
        if self._loop and self._connected:
            payload = json.dumps({"brightness": value})
            asyncio.run_coroutine_threadsafe(
                self._daemon._ble.write_config(payload), self._loop
            )

    def _on_sleep_timeout_select(self, sender):
        seconds = sender._seconds
        self._sleep_timeout_value = seconds

        # Update checkmarks
        for key, item in self._sleep_menu.items():
            item.state = (item._seconds == seconds)

        if self._loop and self._connected:
            payload = json.dumps({"sleep_timeout": seconds})
            asyncio.run_coroutine_threadsafe(
                self._daemon._ble.write_config(payload), self._loop
            )

    def _on_toggle_login(self, sender):
        if launchd.is_enabled():
            launchd.disable()
        else:
            launchd.enable()
        sender.state = launchd.is_enabled()

    def _on_reconnect(self, _):
        """Trigger a reconnect attempt. Uses ensure_connected which is non-blocking
        (unlike connect() which loops forever). The daemon's _ble_sender will
        also call ensure_connected on its next pending message."""
        if self._loop:
            asyncio.run_coroutine_threadsafe(
                self._daemon._ble.ensure_connected(), self._loop
            )

    def _on_quit(self, _):
        if self._loop and self._daemon:
            future = asyncio.run_coroutine_threadsafe(
                self._daemon._shutdown(), self._loop
            )
            future.result(timeout=5)
        rumps.quit_application()


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    app = ClawdTankApp()
    app._start_daemon_thread()
    app.run()


if __name__ == "__main__":
    main()
```

- [ ] **Step 7: Create __main__.py for `python -m` invocation**

```python
# host/clawd_tank_menubar/__main__.py
from .app import main

main()
```

This allows running via `python -m clawd_tank_menubar` (or `cd host && python -m clawd_tank_menubar`). Formal packaging with `pyproject.toml` entry points is out of scope for this plan — the existing project doesn't use packaging (it uses `requirements.txt` + direct imports).

- [ ] **Step 8: Commit**

```bash
git add host/clawd_tank_menubar/ host/requirements.txt
git commit -m "feat(menubar): add macOS status bar app with daemon integration"
```

### Task 10: Write menu bar app tests

**Files:**
- Create: `host/tests/test_menubar.py`

- [ ] **Step 1: Write tests for menu bar state logic**

```python
# host/tests/test_menubar.py
"""Tests for menu bar app state transitions.

These test the observer-driven state updates without launching
the actual rumps app (which requires macOS AppKit).
"""
import pytest
from unittest.mock import MagicMock, patch, AsyncMock

from clawd_tank_daemon.daemon import ClawdDaemon


class FakeObserver:
    """Minimal observer for testing daemon integration."""
    def __init__(self):
        self.connection_changes = []
        self.notification_changes = []

    def on_connection_change(self, connected: bool) -> None:
        self.connection_changes.append(connected)

    def on_notification_change(self, count: int) -> None:
        self.notification_changes.append(count)


@pytest.mark.asyncio
async def test_add_then_dismiss_observer_sequence():
    """Observer sees correct count sequence: 1, 2, 1, 0."""
    obs = FakeObserver()
    daemon = ClawdDaemon(observer=obs)

    await daemon._handle_message(
        {"event": "add", "session_id": "s1", "project": "p", "message": "m"}
    )
    await daemon._handle_message(
        {"event": "add", "session_id": "s2", "project": "p", "message": "m"}
    )
    await daemon._handle_message({"event": "dismiss", "session_id": "s1"})
    await daemon._handle_message({"event": "dismiss", "session_id": "s2"})

    assert obs.notification_changes == [1, 2, 1, 0]


@pytest.mark.asyncio
async def test_disconnect_callback_fires_observer():
    obs = FakeObserver()
    daemon = ClawdDaemon(observer=obs)
    daemon._on_ble_disconnect()
    assert obs.connection_changes == [False]


def test_launchd_is_enabled_checks_plist():
    """launchd.is_enabled returns True iff the plist file exists."""
    from clawd_tank_menubar import launchd
    with patch.object(launchd, "PLIST_PATH") as mock_path:
        mock_path.exists.return_value = True
        assert launchd.is_enabled() is True
        mock_path.exists.return_value = False
        assert launchd.is_enabled() is False
```

- [ ] **Step 2: Run all tests**

```bash
cd host && python -m pytest tests/ -v
```

Expected: All tests pass (existing + new).

- [ ] **Step 3: Commit**

```bash
git add host/tests/test_menubar.py
git commit -m "test(menubar): add tests for observer sequence and launchd check"
```

---

## Chunk 5: Simulator Extension and Final Integration

### Task 11: Extend simulator BLE shim for config

**Files:**
- Modify: `simulator/shims/ble_service.h`
- Modify: `simulator/sim_events.c`

- [ ] **Step 1: Add config store shim to BLE service header**

The simulator compiles the same `config_store.c` as firmware but needs NVS stubs. Add to `simulator/shims/ble_service.h` (or create a dedicated `simulator/shims/config_store_shim.h`):

Since the simulator already stubs ESP-IDF APIs, add NVS stubs to the shim layer. The simplest approach: create `simulator/shims/nvs_flash.h` and `simulator/shims/nvs.h` with in-memory mock implementations (same approach as the test mock, but as shim headers).

Create `simulator/shims/nvs_flash.h`:

```c
#ifndef NVS_FLASH_H_SHIM
#define NVS_FLASH_H_SHIM

#include <stdint.h>

#ifndef ESP_OK
#define ESP_OK 0
#endif
typedef int esp_err_t;

static inline esp_err_t nvs_flash_init(void) { return ESP_OK; }

#endif
```

Create `simulator/shims/nvs.h`:

```c
#ifndef NVS_H_SHIM
#define NVS_H_SHIM

#include <stdint.h>
#include <string.h>

typedef uint32_t nvs_handle_t;
#ifndef ESP_OK
#define ESP_OK 0
#endif
#ifndef ESP_ERR_NVS_NOT_FOUND
#define ESP_ERR_NVS_NOT_FOUND 0x1102
#endif

/* Simple in-memory KV for simulator config persistence */
static uint8_t  _shim_brightness = 0;
static uint16_t _shim_sleep_timeout = 0;
static int _shim_brightness_set = 0;
static int _shim_sleep_timeout_set = 0;

static inline int nvs_open(const char *ns, int mode, nvs_handle_t *h) {
    (void)ns; (void)mode; *h = 1; return 0;
}
static inline int nvs_get_u8(nvs_handle_t h, const char *key, uint8_t *v) {
    (void)h;
    if (strcmp(key, "brightness") == 0 && _shim_brightness_set) { *v = _shim_brightness; return 0; }
    return ESP_ERR_NVS_NOT_FOUND;
}
static inline int nvs_get_u16(nvs_handle_t h, const char *key, uint16_t *v) {
    (void)h;
    if (strcmp(key, "sleep_tmout") == 0 && _shim_sleep_timeout_set) { *v = _shim_sleep_timeout; return 0; }
    return ESP_ERR_NVS_NOT_FOUND;
}
static inline int nvs_set_u8(nvs_handle_t h, const char *key, uint8_t v) {
    (void)h;
    if (strcmp(key, "brightness") == 0) { _shim_brightness = v; _shim_brightness_set = 1; }
    return 0;
}
static inline int nvs_set_u16(nvs_handle_t h, const char *key, uint16_t v) {
    (void)h;
    if (strcmp(key, "sleep_tmout") == 0) { _shim_sleep_timeout = v; _shim_sleep_timeout_set = 1; }
    return 0;
}
static inline int nvs_commit(nvs_handle_t h) { (void)h; return 0; }
static inline void nvs_close(nvs_handle_t h) { (void)h; }

#endif
```

- [ ] **Step 2: Add `config` command to sim_events.c**

In the inline event parser (after the `clear` handler), add:

```c
        else if (strncmp(p, "config", 6) == 0) {
            p += 6;
            char json_str[256];
            p = parse_quoted(p, json_str, sizeof(json_str));

            /* Parse and apply config directly */
            cJSON *json = cJSON_Parse(json_str);
            if (json) {
                cJSON *brightness = cJSON_GetObjectItem(json, "brightness");
                if (brightness && cJSON_IsNumber(brightness)) {
                    config_store_set_brightness((uint8_t)brightness->valueint);
                    printf("[sim] Config: brightness=%d\n", brightness->valueint);
                }
                cJSON *sleep_t = cJSON_GetObjectItem(json, "sleep_timeout");
                if (sleep_t && cJSON_IsNumber(sleep_t)) {
                    config_store_set_sleep_timeout((uint16_t)sleep_t->valueint);
                    ui_manager_set_sleep_timeout((uint32_t)sleep_t->valueint * 1000);
                    printf("[sim] Config: sleep_timeout=%d\n", sleep_t->valueint);
                }
                cJSON_Delete(json);
            }
        }
```

Add `#include "config_store.h"` at the top of `sim_events.c`.

- [ ] **Step 3: Update simulator CMakeLists.txt to compile config_store.c**

In `simulator/CMakeLists.txt`, add `${FIRMWARE_DIR}/config_store.c` to the `add_executable(...)` source list alongside the other `${FIRMWARE_DIR}/*.c` entries (e.g., after `${FIRMWARE_DIR}/notification.c`).

- [ ] **Step 4: Build and test simulator**

```bash
cd simulator && cmake -B build && cmake --build build
```

Expected: Builds successfully.

```bash
./simulator/build/clawd-tank-sim --headless \
  --events 'connect; wait 500; config "{\"brightness\":200}"; wait 1000' \
  --run-ms 2000
```

Expected: Outputs `[sim] Config: brightness=200`.

- [ ] **Step 5: Commit**

```bash
git add simulator/
git commit -m "feat(sim): add NVS shims and config event command to simulator"
```

### Task 12: Create status bar icons

**Files:**
- Create: `host/clawd_tank_menubar/icons/crab-connected.png`
- Create: `host/clawd_tank_menubar/icons/crab-notifications.png`
- Create: `host/clawd_tank_menubar/icons/crab-disconnected.png`

- [ ] **Step 1: Install Pillow and create placeholder icon PNGs**

```bash
pip install Pillow
```

Create 16x16 template images for each state. These are placeholder icons — final pixel art can be refined later. macOS template images use black with alpha for automatic dark/light mode adaptation.

```python
# Run this once to generate placeholder icons:
from PIL import Image, ImageDraw

def make_icon(path, color_dot=None, grayscale=False):
    img = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    # Simple crab shape: body
    draw.ellipse([3, 4, 12, 12], fill=(0, 0, 0, 200 if not grayscale else 80))
    # Eyes
    draw.ellipse([5, 2, 7, 4], fill=(0, 0, 0, 200 if not grayscale else 80))
    draw.ellipse([9, 2, 11, 4], fill=(0, 0, 0, 200 if not grayscale else 80))
    # Claws
    draw.ellipse([0, 6, 3, 9], fill=(0, 0, 0, 200 if not grayscale else 80))
    draw.ellipse([12, 6, 15, 9], fill=(0, 0, 0, 200 if not grayscale else 80))
    # Status dot
    if color_dot:
        draw.ellipse([11, 10, 15, 14], fill=color_dot)
    img.save(path)

import os
os.makedirs("host/clawd_tank_menubar/icons", exist_ok=True)
make_icon("host/clawd_tank_menubar/icons/crab-connected.png", color_dot=(34, 197, 94, 255))
make_icon("host/clawd_tank_menubar/icons/crab-notifications.png", color_dot=(245, 158, 11, 255))
make_icon("host/clawd_tank_menubar/icons/crab-disconnected.png", grayscale=True)
```

- [ ] **Step 2: Commit**

```bash
git add host/clawd_tank_menubar/icons/
git commit -m "feat(menubar): add placeholder status bar icon PNGs"
```

### Task 13: Run all tests and verify

**Files:** None (verification only)

- [ ] **Step 1: Run C tests**

```bash
cd firmware/test && make clean && make test
```

Expected: All tests pass (notification + config store).

- [ ] **Step 2: Run Python tests**

```bash
cd host && python -m pytest tests/ -v
```

Expected: All tests pass.

- [ ] **Step 3: Build firmware**

```bash
cd firmware && idf.py build
```

Expected: Build succeeds.

- [ ] **Step 4: Build simulator**

```bash
cd simulator && cmake -B build && cmake --build build
```

Expected: Build succeeds.

- [ ] **Step 5: Update TODO.md**

Add completed items and new section for the menu bar app.
