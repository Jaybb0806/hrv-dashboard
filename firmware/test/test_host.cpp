#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

#include "payload.h"
#include "pmd.h"
#include "sample_buffer.h"

static int failures = 0;
#define CHECK(cond)                                                         \
    do {                                                                    \
        if (!(cond)) {                                                      \
            std::printf("FAIL %s:%d  %s\n", __FILE__, __LINE__, #cond);     \
            ++failures;                                                     \
        }                                                                   \
    } while (0)

static std::vector<uint8_t> ppiFrame(const std::vector<std::vector<int>>& samples) {
    std::vector<uint8_t> f = {pmd::TYPE_PPI, 1, 2, 3, 4, 5, 6, 7, 8, 0x00};
    for (auto& s : samples) {
        f.push_back(uint8_t(s[0]));
        f.push_back(uint8_t(s[1] & 0xFF)); f.push_back(uint8_t(s[1] >> 8));
        f.push_back(uint8_t(s[2] & 0xFF)); f.push_back(uint8_t(s[2] >> 8));
        f.push_back(uint8_t(s[3]));
    }
    return f;
}

static void testCommands() {
    uint8_t cmd[2];
    CHECK(pmd::buildStartPpi(cmd) == 2 && cmd[0] == 0x02 && cmd[1] == 0x03);
    CHECK(pmd::buildStopPpi(cmd) == 2 && cmd[0] == 0x03 && cmd[1] == 0x03);
    const uint8_t ok[] = {0xF0, 0x02, 0x03, 0x00, 0x00};
    auto r = pmd::parseControlResponse(ok, sizeof ok);
    CHECK(r.valid && r.op == pmd::OP_START && r.type == pmd::TYPE_PPI && r.status == 0);
    const uint8_t already[] = {0xF0, 0x02, 0x03, 0x06};
    CHECK(pmd::parseControlResponse(already, 4).status == pmd::STATUS_ALREADY_IN_STATE);
    const uint8_t junk[] = {0x01, 0x02};
    CHECK(!pmd::parseControlResponse(junk, 2).valid);
    CHECK(std::strcmp(pmd::statusText(13), "device in charger") == 0);
    CHECK(std::strcmp(pmd::statusText(200), "unknown") == 0);
}

static void testPpiFrame() {
    auto f = ppiFrame({{72, 812, 6, 0x06}, {73, 0, 0, 0x06}, {74, 1290, 25, 0x05}});
    pmd::PpiSample out[8];
    int n = pmd::parsePpiFrame(f.data(), f.size(), out, 8);
    CHECK(n == 2);
    CHECK(out[0].ppi_ms == 812 && out[0].err_ms == 6 && out[0].hr == 72);
    CHECK(!out[0].blocker && out[0].contact && out[0].contact_supported);
    CHECK(out[1].ppi_ms == 1290 && out[1].err_ms == 25 && out[1].blocker && !out[1].contact);
    CHECK(pmd::parsePpiFrame(f.data(), f.size(), out, 1) == 1);

    auto partial = f;
    partial.resize(partial.size() - 3);
    CHECK(pmd::parsePpiFrame(partial.data(), partial.size(), out, 8) == 1);

    auto wrongType = f;
    wrongType[0] = 0x01;
    CHECK(pmd::parsePpiFrame(wrongType.data(), wrongType.size(), out, 8) == -1);
    auto compressed = f;
    compressed[9] = 0x80;
    CHECK(pmd::parsePpiFrame(compressed.data(), compressed.size(), out, 8) == -1);
}

static void testHeartRate() {
    const uint8_t hr8[] = {0x00, 72};
    const uint8_t hr16[] = {0x01, 0x2C, 0x01};
    CHECK(pmd::parseHeartRate(hr8, 2) == 72);
    CHECK(pmd::parseHeartRate(hr16, 3) == 300);
    CHECK(pmd::parseHeartRate(hr16, 2) == -1);
    const uint8_t off[] = {0x04, 65}, on[] = {0x06, 65};
    CHECK(pmd::parseHeartRate(off, 2) == 0);
    CHECK(pmd::parseHeartRate(on, 2) == 65);
}

static void testBuffer() {
    SampleBuffer b(3);
    for (uint16_t i = 1; i <= 2; ++i) b.push({uint16_t(800 + i), 5, 70, false, true, true});
    std::vector<pmd::PpiSample> out;
    uint64_t upto = b.peek(out, 10);
    CHECK(out.size() == 2 && upto == 2);
    b.push({803, 5, 70, false, true, true});
    b.ack(upto);
    CHECK(b.size() == 1);
    b.peek(out, 10);
    CHECK(out[0].ppi_ms == 803);

    SampleBuffer c(2);
    c.push({1, 0, 0, false, true, true});
    c.push({2, 0, 0, false, true, true});
    upto = c.peek(out, 10);
    c.push({3, 0, 0, false, true, true});
    c.push({4, 0, 0, false, true, true});
    c.ack(upto);
    CHECK(c.size() == 2 && c.dropped() == 2);
    c.peek(out, 10);
    CHECK(out[0].ppi_ms == 3 && out[1].ppi_ms == 4);
}

static void testPayload() {
    pmd::PpiSample s[] = {{812, 6, 72, false, true, true}, {798, 5, 72, true, false, false}};
    std::vector<SensorReport> r(2);
    r[0].id = "A1B2C3D4"; r[0].hr = 72; r[0].rssi = -61; r[0].bat = 88; r[0].ppi = s; r[0].n = 2;
    r[1].id = "B\"X";
    std::string json = buildPayload("GW-01", 7, 30, r);
    CHECK(json ==
          "{\"gw\":\"GW-01\",\"seq\":7,\"uptime\":30,\"sensors\":["
          "{\"id\":\"A1B2C3D4\",\"hr\":72,\"rssi\":-61,\"bat\":88,\"ppi\":[[812,6,0,1],[798,5,1,1]]},"
          "{\"id\":\"B\\\"X\",\"ppi\":[]}]}");
    CHECK(buildPayload("GW-02", 0, 0, {}) == "{\"gw\":\"GW-02\",\"seq\":0,\"uptime\":0,\"sensors\":[]}");
    CHECK(buildPayload("GW-03", 1, 2, {}, -58) == "{\"gw\":\"GW-03\",\"seq\":1,\"uptime\":2,\"wifi\":-58,\"sensors\":[]}");
}

static void testIds() {
    auto ids = parseIdList(" a1b2c3d4 \r\nB2C3D4E5\n\nA1B2C3D4\nC3D4E5F6\n", 2);
    CHECK(ids.size() == 2 && ids[0] == "A1B2C3D4" && ids[1] == "B2C3D4E5");
    CHECK(parseIdList("", 5).empty());
    CHECK(deviceIdFromName("Polar Sense 0a1b2c3d") == "0A1B2C3D");
    CHECK(deviceIdFromName("Polar H10 12345678").empty());
}

static void testReply() {
    std::string r = "{\"ok\": true, \"beats\": 3, \"phase\": \"music\", \"mode\": \"hr\"}";
    CHECK(jsonString(r, "phase") == "music" && jsonString(r, "mode") == "hr");
    CHECK(jsonString("{\"mode\":\"ppi\"}", "mode") == "ppi");
    CHECK(jsonString(r, "beats").empty() && jsonString(r, "missing").empty());
}

int main(int argc, char** argv) {
    if (argc == 3 && std::strcmp(argv[1], "--payload") == 0) {
        std::vector<std::vector<pmd::PpiSample>> beats(5);
        std::vector<SensorReport> r(5);
        for (int i = 0; i < 5; ++i) {
            for (int k = 0; k < 2; ++k)
                beats[i].push_back({uint16_t(800 + 20 * (k % 2) + i), 5, 74, false, true, true});
            char id[16];
            std::snprintf(id, sizeof id, "HOST%04d", i + 1);
            r[i].id = id; r[i].hr = 74; r[i].rssi = -60; r[i].bat = 90;
            r[i].ppi = beats[i].data(); r[i].n = beats[i].size();
        }
        std::puts(buildPayload(argv[2], 1, 1, r, -55).c_str());
        return 0;
    }
    testCommands();
    testPpiFrame();
    testHeartRate();
    testBuffer();
    testPayload();
    testIds();
    testReply();
    std::printf(failures ? "%d FAILED\n" : "all host tests passed\n", failures);
    return failures ? 1 : 0;
}
