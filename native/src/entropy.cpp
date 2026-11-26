#include "cdng_native.h"
#include "cdng_bit_budget.h"

#include <algorithm>
#include <array>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <limits>
#include <memory>
#include <new>
#include <stdexcept>
#include <string>
#include <vector>

// g_budget es thread_local: el worker puede codificar fotogramas en paralelo.

namespace {
class BitWriter {
    std::vector<uint8_t> bytes_;
    unsigned used_ = 0;
    uint64_t bits_written_ = 0;
public:
    void put(uint64_t value, unsigned length) {
        if (length > 64) throw std::invalid_argument("code length exceeds 64");
        bits_written_ += length;
        while (length) {
            if (used_ == 0) bytes_.push_back(0);
            const unsigned take = std::min(8 - used_, length);
            const unsigned shift = length - take;
            const auto part = static_cast<uint8_t>((value >> shift) & ((1u << take) - 1u));
            bytes_.back() |= static_cast<uint8_t>(part << (8 - used_ - take));
            used_ = (used_ + take) % 8;
            length -= take;
        }
    }
    uint64_t bits_written() const { return bits_written_; }
    void finish(CdngBuffer &out, uint64_t *padding_bits) {
        bytes_.resize(bytes_.size() + ((8 - bytes_.size() % 8) % 8), 0);
        if (bytes_.empty()) {
            if (padding_bits) *padding_bits = 0;
            return;
        }
        auto data = std::make_unique<uint8_t[]>(bytes_.size());
        std::memcpy(data.get(), bytes_.data(), bytes_.size());
        out.size = bytes_.size();
        out.data = data.release();
        // Reconciliación exacta: bits del buffer − bits emitidos por put() = padding
        // (resto del último byte parcial + bytes de alineación a 8).
        if (padding_bits) *padding_bits = out.size * 8ull - bits_written_;
    }
};

constexpr unsigned kClasses = 12;
constexpr int64_t kMax = 4095;

bool budget_enabled() {
    static const bool on = [] {
        const char *v = std::getenv("CDNG_BIT_BUDGET");
        return v && *v && std::string(v) != "0";
    }();
    return on;
}

thread_local CdngBitBudget g_budget{};

unsigned klass(int64_t value) {
    auto magnitude = static_cast<unsigned>(value < 0 ? -value : value);
    unsigned k = 0;
    while (magnitude >>= 1) ++k;
    return k;
}

void block(BitWriter &writer, const CdngPlane &plane, size_t index,
           const CdngEntropyTables &t, CdngEntropyStats &stats, int plane_id, CdngBitBudget *budget) {
    const uint64_t before_dc = writer.bits_written();
    writer.put(plane.dc_values[index], plane.dc_lengths[index]);
    if (budget) budget->dc_bits[plane_id] += writer.bits_written() - before_dc;

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
        if (budget && value) ++budget->ac_nonzero[plane_id];
    }
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
                const uint64_t b0 = writer.bits_written();
                writer.put(t.prefix_values[pos], length);
                if (budget) budget->ac_prefix_bits[plane_id] += writer.bits_written() - b0;
            }
        }
        stats.fallback += fallback;
        const auto magnitude = static_cast<unsigned>(value > 0 ? value : -value);
        const auto mantissa = positive ? magnitude - (1u << k) : (1u << (k + 1)) - 1u - magnitude;
        if (!t.final_lengths[entry]) throw std::invalid_argument("missing AC code");
        {
            const uint64_t b0 = writer.bits_written();
            writer.put(t.final_values[entry], t.final_lengths[entry]);
            if (budget) budget->ac_final_bits[plane_id] += writer.bits_written() - b0;
        }
        {
            const uint64_t b0 = writer.bits_written();
            writer.put((positive << k) | mantissa, k + 1);
            if (budget) budget->ac_magnitude_bits[plane_id] += writer.bits_written() - b0;
        }
        if (budget) ++budget->ac_coded[plane_id];
    }
    {
        const uint64_t b0 = writer.bits_written();
        writer.put(t.eob_value, t.eob_length);
        if (budget) {
            budget->eob_bits[plane_id] += writer.bits_written() - b0;
            ++budget->eob_count[plane_id];
        }
    }
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
        return 0;
    } catch (const std::bad_alloc &) { return 2; }
      catch (...) { return 1; }
}
}  // namespace

uint32_t cdng_native_abi(void) { return 3; }

int cdng_pack_bits(const uint64_t *values, const uint8_t *lengths,
                   size_t count, CdngBuffer *output) {
    if (count && (!values || !lengths)) return 1;
    return guarded(output, [&](BitWriter &writer) {
        for (size_t i = 0; i < count; ++i) writer.put(values[i], lengths[i]);
        writer.finish(*output, nullptr);
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
    CdngBitBudget slice{};
    CdngBitBudget *budget = budget_enabled() ? &slice : nullptr;
    if (budget) {
        budget->blocks_y = y->blocks;
        budget->blocks_c = cb->blocks;
    }
    const auto status = guarded(output, [&](BitWriter &writer) {
        for (size_t m = 0; m < cb->blocks; ++m) {
            block(writer, *y, m * 2, *tables, local, CDNG_PLANE_Y, budget);
            block(writer, *y, m * 2 + 1, *tables, local, CDNG_PLANE_Y, budget);
            block(writer, *cb, m, *tables, local, CDNG_PLANE_CB, budget);
            block(writer, *cr, m, *tables, local, CDNG_PLANE_CR, budget);
            {
                const uint64_t b0 = writer.bits_written();
                writer.put(0xff, 8);
                if (budget) {
                    budget->marker_bits += writer.bits_written() - b0;
                    ++budget->marker_count;
                }
            }
        }
        if (budget) budget->coded_bits = writer.bits_written();
        uint64_t pad = 0;
        writer.finish(*output, budget ? &pad : nullptr);
        if (budget) {
            budget->padding_bits = pad;
            budget->payload_bytes = output->size;
        }
    });
    if (status == 0) {
        *stats = local;
        if (budget) cdng_bit_budget_add(budget);
    }
    return status;
}

void cdng_buffer_free(CdngBuffer *buffer) {
    if (!buffer) return;
    delete[] buffer->data;
    *buffer = {};
}

int cdng_bit_budget_enabled(void) { return budget_enabled() ? 1 : 0; }

void cdng_bit_budget_reset(void) { g_budget = {}; }

void cdng_bit_budget_add(const CdngBitBudget *slice) {
    if (!slice) return;
    for (int p = 0; p < CDNG_PLANES; ++p) {
        g_budget.dc_bits[p] += slice->dc_bits[p];
        g_budget.ac_prefix_bits[p] += slice->ac_prefix_bits[p];
        g_budget.ac_final_bits[p] += slice->ac_final_bits[p];
        g_budget.ac_magnitude_bits[p] += slice->ac_magnitude_bits[p];
        g_budget.eob_bits[p] += slice->eob_bits[p];
        g_budget.ac_nonzero[p] += slice->ac_nonzero[p];
        g_budget.ac_coded[p] += slice->ac_coded[p];
        g_budget.eob_count[p] += slice->eob_count[p];
    }
    g_budget.marker_bits += slice->marker_bits;
    g_budget.padding_bits += slice->padding_bits;
    g_budget.coded_bits += slice->coded_bits;
    g_budget.payload_bytes += slice->payload_bytes;
    g_budget.blocks_y += slice->blocks_y;
    g_budget.blocks_c += slice->blocks_c;
    g_budget.marker_count += slice->marker_count;
}

void cdng_bit_budget_get(CdngBitBudget *out) {
    if (out) *out = g_budget;
}

int cdng_bit_budget_json(char *buf, size_t cap) {
    if (!buf || !cap) return -1;
    const CdngBitBudget &b = g_budget;
    const auto sum_plane = [&](int p) {
        return b.dc_bits[p] + b.ac_prefix_bits[p] + b.ac_final_bits[p] +
               b.ac_magnitude_bits[p] + b.eob_bits[p];
    };
    const uint64_t attributed = sum_plane(0) + sum_plane(1) + sum_plane(2) + b.marker_bits + b.padding_bits;
    int n = std::snprintf(buf, cap,
        "{"
        "\"dc_bits\":[%llu,%llu,%llu],"
        "\"ac_prefix_bits\":[%llu,%llu,%llu],"
        "\"ac_final_bits\":[%llu,%llu,%llu],"
        "\"ac_magnitude_bits\":[%llu,%llu,%llu],"
        "\"eob_bits\":[%llu,%llu,%llu],"
        "\"marker_bits\":%llu,"
        "\"padding_bits\":%llu,"
        "\"coded_bits\":%llu,"
        "\"payload_bytes\":%llu,"
        "\"attributed_bits\":%llu,"
        "\"blocks_y\":%llu,\"blocks_c\":%llu,"
        "\"ac_nonzero\":[%llu,%llu,%llu],"
        "\"ac_coded\":[%llu,%llu,%llu],"
        "\"eob_count\":[%llu,%llu,%llu],"
        "\"marker_count\":%llu,"
        "\"planes\":[\"Y\",\"Cb\",\"Cr\"]"
        "}",
        (unsigned long long)b.dc_bits[0], (unsigned long long)b.dc_bits[1], (unsigned long long)b.dc_bits[2],
        (unsigned long long)b.ac_prefix_bits[0], (unsigned long long)b.ac_prefix_bits[1], (unsigned long long)b.ac_prefix_bits[2],
        (unsigned long long)b.ac_final_bits[0], (unsigned long long)b.ac_final_bits[1], (unsigned long long)b.ac_final_bits[2],
        (unsigned long long)b.ac_magnitude_bits[0], (unsigned long long)b.ac_magnitude_bits[1], (unsigned long long)b.ac_magnitude_bits[2],
        (unsigned long long)b.eob_bits[0], (unsigned long long)b.eob_bits[1], (unsigned long long)b.eob_bits[2],
        (unsigned long long)b.marker_bits, (unsigned long long)b.padding_bits,
        (unsigned long long)b.coded_bits, (unsigned long long)b.payload_bytes,
        (unsigned long long)attributed,
        (unsigned long long)b.blocks_y, (unsigned long long)b.blocks_c,
        (unsigned long long)b.ac_nonzero[0], (unsigned long long)b.ac_nonzero[1], (unsigned long long)b.ac_nonzero[2],
        (unsigned long long)b.ac_coded[0], (unsigned long long)b.ac_coded[1], (unsigned long long)b.ac_coded[2],
        (unsigned long long)b.eob_count[0], (unsigned long long)b.eob_count[1], (unsigned long long)b.eob_count[2],
        (unsigned long long)b.marker_count);
    if (n < 0 || size_t(n) >= cap) return -1;
    return n;
}
