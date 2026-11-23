#include "payload.h"

#include <algorithm>
#include <cctype>
#include <cstdio>

static void appendJsonString(std::string& out, const std::string& s) {
    out += '"';
    for (char c : s) {
        if (c == '"' || c == '\\') out += '\\';
        if (static_cast<unsigned char>(c) >= 0x20) out += c;
    }
    out += '"';
}

std::string buildPayload(const char* gateway, uint32_t seq, uint32_t uptime_s,
                         const std::vector<SensorReport>& sensors, int wifi_rssi) {
    std::string out;
    out.reserve(96 + sensors.size() * 96);
    char num[64];
    out += "{\"gw\":";
    appendJsonString(out, gateway);
    snprintf(num, sizeof num, ",\"seq\":%u,\"uptime\":%u", unsigned(seq), unsigned(uptime_s));
    out += num;
    if (wifi_rssi != 0) { snprintf(num, sizeof num, ",\"wifi\":%d", wifi_rssi); out += num; }
    out += ",\"sensors\":[";
    for (size_t i = 0; i < sensors.size(); ++i) {
        const SensorReport& s = sensors[i];
        if (i) out += ',';
        out += "{\"id\":";
        appendJsonString(out, s.id);
        if (s.hr >= 0) { snprintf(num, sizeof num, ",\"hr\":%d", s.hr); out += num; }
        if (s.rssi != 0) { snprintf(num, sizeof num, ",\"rssi\":%d", s.rssi); out += num; }
        if (s.bat >= 0) { snprintf(num, sizeof num, ",\"bat\":%d", s.bat); out += num; }
        out += ",\"ppi\":[";
        for (size_t k = 0; k < s.n; ++k) {
            const pmd::PpiSample& p = s.ppi[k];
            int contact = p.contact_supported ? int(p.contact) : 1;
            snprintf(num, sizeof num, "%s[%u,%u,%d,%d]", k ? "," : "",
                     unsigned(p.ppi_ms), unsigned(p.err_ms), int(p.blocker), contact);
            out += num;
        }
        out += "]}";
    }
    out += "]}";
    return out;
}

static std::string trimUpper(std::string s) {
    auto notSpace = [](unsigned char c) { return !std::isspace(c); };
    s.erase(s.begin(), std::find_if(s.begin(), s.end(), notSpace));
    s.erase(std::find_if(s.rbegin(), s.rend(), notSpace).base(), s.end());
    for (char& c : s) c = char(std::toupper(static_cast<unsigned char>(c)));
    return s;
}

std::vector<std::string> parseIdList(const std::string& body, size_t max) {
    std::vector<std::string> ids;
    size_t start = 0;
    while (start <= body.size() && ids.size() < max) {
        size_t end = body.find('\n', start);
        if (end == std::string::npos) end = body.size();
        std::string id = trimUpper(body.substr(start, end - start));
        if (!id.empty() && std::find(ids.begin(), ids.end(), id) == ids.end()) ids.push_back(id);
        start = end + 1;
    }
    return ids;
}

std::string deviceIdFromName(const std::string& name) {
    static const std::string prefix = "Polar Sense ";
    if (name.compare(0, prefix.size(), prefix) != 0) return "";
    return trimUpper(name.substr(prefix.size()));
}

std::string jsonString(const std::string& body, const char* key) {
    std::string quoted = std::string("\"") + key + "\"";
    size_t p = body.find(quoted);
    if (p == std::string::npos) return "";
    p = body.find_first_not_of(" \t", p + quoted.size());
    if (p == std::string::npos || body[p] != ':') return "";
    p = body.find_first_not_of(" \t", p + 1);
    if (p == std::string::npos || body[p] != '"') return "";
    size_t end = body.find('"', p + 1);
    return end == std::string::npos ? "" : body.substr(p + 1, end - p - 1);
}
