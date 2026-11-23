#pragma once
#include <cstdint>
#include <string>
#include <vector>

#include "pmd.h"

struct SensorReport {
    std::string id;
    int hr = -1;
    int rssi = 0;
    int bat = -1;
    const pmd::PpiSample* ppi = nullptr;
    size_t n = 0;
};

std::string buildPayload(const char* gateway, uint32_t seq, uint32_t uptime_s,
                         const std::vector<SensorReport>& sensors, int wifi_rssi = 0);

std::vector<std::string> parseIdList(const std::string& body, size_t max);

std::string deviceIdFromName(const std::string& name);

std::string jsonString(const std::string& body, const char* key);
