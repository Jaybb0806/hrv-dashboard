#include "pmd.h"

namespace pmd {

static uint16_t u16(const uint8_t* p) { return uint16_t(p[0] | (p[1] << 8)); }

size_t buildStartPpi(uint8_t* out) {
    out[0] = OP_START;
    out[1] = TYPE_PPI;
    return 2;
}

size_t buildStopPpi(uint8_t* out) {
    out[0] = OP_STOP;
    out[1] = TYPE_PPI;
    return 2;
}

ControlResponse parseControlResponse(const uint8_t* d, size_t n) {
    if (n < 4 || d[0] != CONTROL_RESPONSE) return {false, 0, 0, 0};
    return {true, d[1], d[2], d[3]};
}

const char* statusText(uint8_t status) {
    static const char* const names[] = {
        "success", "invalid op code", "invalid measurement type", "not supported",
        "invalid length", "invalid parameter", "already in state", "invalid resolution",
        "invalid sample rate", "invalid range", "invalid MTU", "invalid number of channels",
        "invalid state", "device in charger",
    };
    return status < sizeof(names) / sizeof(names[0]) ? names[status] : "unknown";
}

int parsePpiFrame(const uint8_t* d, size_t n, PpiSample* out, size_t max) {
    constexpr size_t HEADER = 10, SAMPLE = 6;
    if (n < HEADER || d[0] != TYPE_PPI || d[9] != 0x00) return -1;
    size_t count = 0;
    for (size_t i = HEADER; i + SAMPLE <= n && count < max; i += SAMPLE) {
        uint16_t ppi = u16(d + i + 1);
        if (ppi == 0) continue;
        uint8_t flags = d[i + 5];
        out[count++] = PpiSample{
            ppi, u16(d + i + 3), d[i],
            (flags & 0x01) != 0, (flags & 0x02) != 0, (flags & 0x04) != 0,
        };
    }
    return int(count);
}

int parseHeartRate(const uint8_t* d, size_t n) {
    if (n < 2) return -1;
    if ((d[0] & 0x06) == 0x04) return 0;
    if (d[0] & 0x01) return n >= 3 ? u16(d + 1) : -1;
    return d[1];
}

}
