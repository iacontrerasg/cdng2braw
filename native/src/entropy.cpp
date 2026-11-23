#include "cdng_native.h"

#include <algorithm>
#include <array>
#include <cstring>
#include <limits>
#include <memory>
#include <new>
#include <stdexcept>
#include <vector>

namespace {
class BitWriter {
    std::vector<uint8_t> bytes_;
    unsigned used_ = 0;
public:
    void put(uint64_t value, unsigned length) {
        if (length > 64) throw std::invalid_argument("code length exceeds 64");
        while (length) {
            if (used_ == 0) bytes_.push_back(0);
            const unsigned take = std::min(8 - used_, length);
            const unsigned shift = length - take; // 0..63, including full-width input
            const auto part = static_cast<uint8_t>((value >> shift) & ((1u << take) - 1u));
            bytes_.back() |= static_cast<uint8_t>(part << (8 - used_ - take));
            used_ = (used_ + take) % 8;
            length -= take;
        }
    }
    void finish(CdngBuffer &out) {
        bytes_.resize(bytes_.size() + ((8 - bytes_.size() % 8) % 8), 0);
        if (bytes_.empty()) return;
        auto data = std::make_unique<uint8_t[]>(bytes_.size());
        std::memcpy(data.get(), bytes_.data(), bytes_.size());
        out.size = bytes_.size();
        out.data = data.release();
    }
};

constexpr unsigned kClasses = 12;   // classes 0..11: |value| <= 4095 (JPEG K.5 + BRAW codes, verified 2026-11-09)
constexpr int64_t kMax = 4095;

unsigned klass(int64_t value) {
    // Only used after clamping to [-kMax, kMax].
    auto magnitude = static_cast<unsigned>(value < 0 ? -value : value);
    unsigned k = 0;
    while (magnitude >>= 1) ++k;
    return k;
}

void block(BitWriter &writer, const CdngPlane &plane, size_t index,
           const CdngEntropyTables &t, CdngEntropyStats &stats) {
    writer.put(plane.dc_values[index], plane.dc_lengths[index]);
    std::array<int64_t, 63> ac{};
    const auto *source = plane.ac + index * plane.coefficients;
    for (size_t i = 0; i < plane.coefficients; ++i) {
        auto value = source[i];
        if (value > kMax || value < -kMax) {
            ++stats.clamped;
            value = value > 0 ? kMax : -kMax;
        }
        const auto adjustment = t.adjust[klass(value)];
        if (value && adjustment) value = value > 0 ? adjustment : -adjustment;
        if (value > kMax || value < -kMax) throw std::invalid_argument("invalid adjustment");
        ac[i] = value;
    }
    // Match the reference's two simultaneous drop passes, including the old
    // predecessor when it is itself dropped in the current pass.
    for (unsigned pass = 0; pass < 2; ++pass) {
        int previous = -1;
        bool changed = false;
        for (size_t i = 0; i < plane.coefficients; ++i) {
            if (!ac[i]) continue;
            const auto run = static_cast<size_t>(static_cast<int>(i) - previous - 1);
            previous = static_cast<int>(i);
            if (t.drop[run * kClasses + klass(ac[i])]) {
                ac[i] = 0;
                ++stats.dropped;
                changed = true;
            }
        }
        if (!changed) break;
    }
    int previous = -1;
    for (size_t i = 0; i < plane.coefficients; ++i) {
        auto value = ac[i];
        if (!value) continue;
        const auto run = static_cast<size_t>(static_cast<int>(i) - previous - 1);
        previous = static_cast<int>(i);
        const unsigned k = klass(value), positive = value > 0;
        const auto entry = (positive * 63 + run) * kClasses + k;
        bool fallback = false;
        for (unsigned p = 0; p < 4; ++p) {
            const auto pos = p * 2 * 63 * kClasses + entry;
            const auto length = t.prefix_lengths[pos];
            if (length) {
                fallback = true;
                writer.put(t.prefix_values[pos], length);
            }
        }
        stats.fallback += fallback;
        const auto magnitude = static_cast<unsigned>(value > 0 ? value : -value);
        const auto mantissa = positive ? magnitude - (1u << k) : (1u << (k + 1)) - 1u - magnitude;
        // Separate writes also avoid shifting a 64-bit code past its width.
        if (!t.final_lengths[entry]) throw std::invalid_argument("missing AC code");
        writer.put(t.final_values[entry], t.final_lengths[entry]);
        writer.put((positive << k) | mantissa, k + 1);
    }
    writer.put(t.eob_value, t.eob_length);
}

bool valid_plane(const CdngPlane *p, size_t coefficients) {
    return p && p->ac && p->dc_values && p->dc_lengths && p->blocks &&
           p->coefficients == coefficients &&
           p->blocks <= std::numeric_limits<size_t>::max() / coefficients;
}

template<class Function> int guarded(CdngBuffer *output, Function function) noexcept {
    if (!output || output->data || output->size) return 1;
    try {
        BitWriter writer;
        function(writer);
        writer.finish(*output);
        return 0;
    } catch (const std::bad_alloc &) { return 2; }
      catch (...) { return 1; }
}
}

uint32_t cdng_native_abi(void) { return 3; }

int cdng_pack_bits(const uint64_t *values, const uint8_t *lengths,
                   size_t count, CdngBuffer *output) {
    if (count && (!values || !lengths)) return 1;
    return guarded(output, [&](BitWriter &writer) {
        for (size_t i = 0; i < count; ++i) writer.put(values[i], lengths[i]);
    });
}

int cdng_encode_slice(const CdngPlane *y, const CdngPlane *cb, const CdngPlane *cr,
                      const CdngEntropyTables *tables, CdngBuffer *output,
                      CdngEntropyStats *stats) {
    if (stats) *stats = {};
    if (!stats || !valid_plane(y, 63) || !valid_plane(cb, 31) || !valid_plane(cr, 31) ||
        cb->blocks != cr->blocks || cb->blocks > std::numeric_limits<size_t>::max() / 2 ||
        y->blocks != cb->blocks * 2 || !tables || !tables->prefix_values ||
        !tables->prefix_lengths || !tables->final_values || !tables->final_lengths ||
        !tables->drop || !tables->adjust || !tables->eob_length || tables->eob_length > 64)
        return 1;
    CdngEntropyStats local{};
    const auto status = guarded(output, [&](BitWriter &writer) {
        for (size_t m = 0; m < cb->blocks; ++m) {
            block(writer, *y, m * 2, *tables, local);
            block(writer, *y, m * 2 + 1, *tables, local);
            block(writer, *cb, m, *tables, local);
            block(writer, *cr, m, *tables, local);
            writer.put(0xff, 8);
        }
    });
    if (status == 0) *stats = local;
    return status;
}

void cdng_buffer_free(CdngBuffer *buffer) {
    if (!buffer) return;
    delete[] buffer->data;
    *buffer = {};
}
