idf_component_register(
    SRCS "main.cpp" "pmd.cpp" "payload.cpp"
    INCLUDE_DIRS "."
    PRIV_REQUIRES nvs_flash esp_wifi esp_netif esp_event esp_http_client esp_timer)

if(DEFINED GATEWAY_ID)
    target_compile_definitions(${COMPONENT_LIB} PRIVATE GATEWAY_ID="${GATEWAY_ID}")
endif()
