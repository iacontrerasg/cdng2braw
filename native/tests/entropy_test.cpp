#include "cdng_native.h"
#include "cdng_bit_budget.h"
#include <array>
#include <cstdlib>
#include <iostream>
#include <limits>
#include <string>

void require(bool value) {
    if (!value) { std::cerr << "native entropy test failed\n"; std::exit(1); }
}
int main() {
    // Antes de cualquier encode: el flag se memoíza en el primer getenv.
    setenv("CDNG_BIT_BUDGET", "1", 1);
    require(cdng_native_abi() == 3);
    CdngBuffer buffer{};
    const uint64_t values[] = {5, 0, 3, 0x123456789abcdef0ULL};
    const uint8_t lengths[] = {3, 0, 2, 64};
    require(cdng_pack_bits(values, lengths, 4, &buffer) == 0);
    // Independently specified concatenation: 101|11|0x123456789abcdef0.
    const uint8_t expected[] = {0xb8, 0x91, 0xa2, 0xb3, 0xc4, 0xd5, 0xe6, 0xf7,
                                0x80, 0, 0, 0, 0, 0, 0, 0};
    require(buffer.size == sizeof(expected));
    for (size_t i = 0; i < buffer.size; ++i) require(buffer.data[i] == expected[i]);
    require(cdng_pack_bits(values, lengths, 4, &buffer) == 1); // ownership stays intact
    cdng_buffer_free(&buffer);
    cdng_buffer_free(&buffer);
    require(buffer.data == nullptr && buffer.size == 0);
    require(cdng_pack_bits(nullptr, nullptr, 0, &buffer) == 0);
    require(buffer.size == 0);
    const uint8_t invalid[] = {65};
    require(cdng_pack_bits(values, invalid, 1, &buffer) == 1);
    require(buffer.size == 0);
    require(cdng_pack_bits(nullptr, lengths, 1, &buffer) == 1);

    std::array<int64_t, 126> yac{};
    std::array<int64_t, 31> cac{};
    const uint64_t dc[] = {1, 1};
    const uint8_t dl[] = {1, 1};
    CdngPlane y{yac.data(), dc, dl, 2, 63}, cb{cac.data(), dc, dl, 1, 31};
    std::array<uint64_t, 4 * 2 * 63 * 12> pre{};
    std::array<uint8_t, 4 * 2 * 63 * 12> prelen{};
    std::array<uint64_t, 2 * 63 * 12> fin{};
    std::array<uint8_t, 2 * 63 * 12> finlen{};
    std::array<uint8_t, 63 * 12> drop{};
    std::array<int64_t, 12> adjust{};
    CdngEntropyTables tables{pre.data(), prelen.data(), fin.data(), finlen.data(),
                              drop.data(), adjust.data(), 0, 1};
    CdngEntropyStats stats{};
    require(cdng_encode_slice(&y, &cb, &cb, &tables, &buffer, &stats) == 0);
    require(buffer.size == 8 && buffer.data[0] == 0xaa && buffer.data[1] == 0xff);
    require(stats.fallback == 0 && stats.clamped == 0 && stats.dropped == 0);
    cdng_buffer_free(&buffer);
    // INT64_MIN must clamp before taking an absolute value (no signed overflow).
    yac[0] = std::numeric_limits<int64_t>::min();
    require(cdng_encode_slice(&y, &cb, &cb, &tables, &buffer, &stats) == 1); // missing AC code
    require(buffer.data == nullptr && stats.clamped == 0);
    // Exercise the full AC writer under sanitizers, including all classes,
    // signs, prefixes, clamping and sparse coefficients.
    fin.fill(2); finlen.fill(2); pre.fill(5); prelen.fill(3);
    for (size_t run = 16; run < 63; ++run) drop[run * 12] = 1;
    uint64_t random = 1701;
    for (unsigned trial = 0; trial < 100; ++trial) {
        for (auto &value : yac) {
            random = random * 6364136223846793005ULL + 1;
            value = trial % 3 == 1 ? static_cast<int64_t>(random % 511) - 255 :
                    trial % 3 == 2 ? static_cast<int64_t>(random % 9001) - 4500 :   // classes 9..11 and clamping
                                     (random % 30 == 0 ? 1 : 0);
        }
        for (size_t i = 0; i < cac.size(); ++i) cac[i] = yac[i];
        require(cdng_encode_slice(&y, &cb, &cb, &tables, &buffer, &stats) == 0);
        require(buffer.size > 0 && buffer.size % 8 == 0);
        cdng_buffer_free(&buffer);
    }
    y.blocks = 1;
    require(cdng_encode_slice(&y, &cb, &cb, &tables, &buffer, &stats) == 1);

    // Presupuesto de bits: reconciliación payload_bytes×8 == attributed.
    require(cdng_bit_budget_enabled() == 1);
    cdng_bit_budget_reset();
    y.blocks = 2;
    yac.fill(0); cac.fill(0);
    require(cdng_encode_slice(&y, &cb, &cb, &tables, &buffer, &stats) == 0);
    CdngBitBudget bud{};
    cdng_bit_budget_get(&bud);
    require(bud.payload_bytes == buffer.size);
    require(bud.payload_bytes % 8 == 0);
    const uint64_t attributed = bud.dc_bits[0] + bud.dc_bits[1] + bud.dc_bits[2] +
        bud.ac_prefix_bits[0] + bud.ac_prefix_bits[1] + bud.ac_prefix_bits[2] +
        bud.ac_final_bits[0] + bud.ac_final_bits[1] + bud.ac_final_bits[2] +
        bud.ac_magnitude_bits[0] + bud.ac_magnitude_bits[1] + bud.ac_magnitude_bits[2] +
        bud.eob_bits[0] + bud.eob_bits[1] + bud.eob_bits[2] +
        bud.marker_bits + bud.padding_bits;
    require(attributed == bud.payload_bytes * 8ull);
    require(bud.coded_bits + bud.padding_bits == bud.payload_bytes * 8ull);
    char json[2048];
    require(cdng_bit_budget_json(json, sizeof json) > 0);
    require(std::string(json).find("\"dc_bits\"") != std::string::npos);
    cdng_buffer_free(&buffer);

    std::cout << "native entropy tests passed\n";
}
