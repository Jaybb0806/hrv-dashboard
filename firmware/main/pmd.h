#pragma once
#include <cstddef>
#include <cstdint>

namespace pmd {

constexpr const char* SERVICE_UUID = "FB005C80-02E7-F387-1CAD-8ACD2D8DF0C8";
constexpr const char* CONTROL_UUID = "FB005C81-02E7-F387-1CAD-8ACD2D8DF0C8";
constexpr const char* DATA_UUID = "FB005C82-02E7-F387-1CAD-8ACD2D8DF0C8";

constexpr uint8_t TYPE_PPI = 0x03;
constexpr uint8_t OP_GET_SETTINGS = 0x01;
constexpr uint8_t OP_START = 0x02;
constexpr uint8_t OP_STOP = 0x03;
constexpr uint8_t CONTROL_RESPONSE = 0xF0;

constexpr uint8_t STATUS_SUCCESS = 0;
constexpr uint8_t STATUS_ALREADY_IN_STATE = 6;

struct PpiSample {
    uint16_t ppi_ms;
    uint16_t err_ms;
    uint8_t hr;
    bool blocker;
    bool contact;
    bool contact_supported;
};

struct ControlResponse {
    bool valid;
    uint8_t op;
    uint8_t type;
    uint8_t status;
};

size_t buildStartPpi(uint8_t* out);
size_t buildStopPpi(uint8_t* out);

ControlResponse parseControlResponse(const uint8_t* d, size_t n);
const char* statusText(uint8_t status);

int parsePpiFrame(const uint8_t* d, size_t n, PpiSample* out, size_t max);

int parseHeartRate(const uint8_t* d, size_t n);

}
