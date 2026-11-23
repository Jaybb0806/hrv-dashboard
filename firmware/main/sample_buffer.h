#pragma once
#include <cstdint>
#include <deque>
#include <vector>

#include "pmd.h"

class SampleBuffer {
public:
    explicit SampleBuffer(size_t capacity) : cap_(capacity) {}

    void push(const pmd::PpiSample& s) {
        if (q_.size() == cap_) {
            q_.pop_front();
            ++head_;
            ++dropped_;
        }
        q_.push_back(s);
    }

    uint64_t peek(std::vector<pmd::PpiSample>& out, size_t max) const {
        size_t n = q_.size() < max ? q_.size() : max;
        out.assign(q_.begin(), q_.begin() + n);
        return head_ + n;
    }

    void ack(uint64_t upto) {
        while (head_ < upto && !q_.empty()) {
            q_.pop_front();
            ++head_;
        }
    }

    size_t size() const { return q_.size(); }
    uint32_t dropped() const { return dropped_; }

private:
    std::deque<pmd::PpiSample> q_;
    uint64_t head_ = 0;
    size_t cap_;
    uint32_t dropped_ = 0;
};
