#pragma once

#if __has_include("config_local.h")
#include "config_local.h"
#endif

#ifndef WIFI_SSID
#define WIFI_SSID "HRV-Lab"
#define WIFI_PASS "change-me"
#endif

#ifndef SERVER_URL
#define SERVER_URL "http://192.168.0.10:8000"
#endif

#ifndef GATEWAY_ID
#define GATEWAY_ID "GW-01"
#endif

static const char* const FALLBACK_SENSORS[] = {
    nullptr,
};
constexpr bool AUTO_CLAIM = false;

constexpr int STATUS_LED_GPIO = 8;

constexpr int MAX_SENSORS = 7;
constexpr int POST_INTERVAL_MS = 1000;
constexpr int MAX_SAMPLES_PER_POST = 120;
constexpr int BUFFER_SAMPLES = 600;
constexpr int PPI_START_GRACE_MS = 40000;
constexpr int PPI_RESTART_AFTER_MS = 20000;
constexpr int PPI_DISCONNECT_AFTER_MS = 60000;
constexpr int HR_SILENT_MS = 20000;
