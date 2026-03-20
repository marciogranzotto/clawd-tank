# firmware/main/CMakeLists.txt
idf_component_register(
    SRCS "main.c" "display.c" "ble_service.c" "notification.c" "ui_manager.c" "scene.c" "notification_ui.c" "config_store.c" "rgb_led.c" "pixel_font.c" "button.c"
    INCLUDE_DIRS "."
    REQUIRES esp_lcd driver esp_timer bt nvs_flash freertos json
)
