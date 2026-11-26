#include "cdng_ac_cost.h"
#include "cdng_native.h"
#include "cdng_bit_budget.h"

#include <array>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <string>
#include <vector>

// Prueba del oráculo AC sin tablas reales del modelo: tablas sintéticas mínimas
// (código final corto para run/clase usados) + reconciliación con BitWriter vía encode.

void require(bool v, const char *msg) {
    if (!v) { std::cerr << "ac_cost_test failed: " << msg << "\n"; std::exit(1); }
}

int main() {
    require(cdng_native_abi() == 3, "abi");

    // Tablas vacías salvo EOB y códigos (run,k) para clase 0..2 signo ±.
    std::array<uint64_t, 4 * 2 * 63 * 12> pre{};
    std::array<uint8_t, 4 * 2 * 63 * 12> prelen{};
    std::array<uint64_t, 2 * 63 * 12> fin{};
    std::array<uint8_t, 2 * 63 * 12> finlen{};
    std::array<uint8_t, 63 * 12> drop{};
    std::array<int64_t, 12> adjust{};
    for (size_t run = 0; run < 63; ++run)
        for (int k = 0; k < 12; ++k)
            for (int sg = 0; sg < 2; ++sg) {
                const auto entry = (sg * 63 + run) * 12 + k;
                fin[entry] = 1;
                finlen[entry] = 2;
            }
    // Drop ±1 en runs largos sin ZRL (como encoder real).
    for (size_t run = 16; run < 63; ++run) drop[run * 12 + 0] = 1;

    CdngEntropyTables tables{pre.data(), prelen.data(), fin.data(), finlen.data(),
                             drop.data(), adjust.data(), 0b1010, 4};

    // Todos ceros → solo EOB = 4 bits.
    {
        std::array<int64_t, 63> ac{};
        CdngAcBlockCost cost{};
        require(cdng_ac_block_cost(ac.data(), 63, &tables, &cost, nullptr) == 0, "zeros");
        require(cost.total_bits == 4 && cost.eob_bits == 4 && cost.ac_coded == 0, "zeros bits");
        require(cdng_ac_eob_bits(&tables) == 4, "eob api");
    }

    // Un solo no-cero en i=0, nivel +1 (clase 0).
    {
        std::array<int64_t, 63> ac{};
        ac[0] = 1;
        CdngAcBlockCost cost{};
        require(cdng_ac_block_cost(ac.data(), 63, &tables, &cost, nullptr) == 0, "one");
        // final 2 + mag (k+1=1) + eob 4 = 7
        require(cost.total_bits == 7 && cost.ac_coded == 1, "one bits");
        require(cdng_ac_symbol_bits(0, 1, &tables) == 3, "sym bits");
    }

    // Run largo de ceros + ±1 → drop → EOB only.
    {
        std::array<int64_t, 63> ac{};
        ac[20] = 1;
        int64_t eff[63];
        CdngAcBlockCost cost{};
        require(cdng_ac_block_cost(ac.data(), 63, &tables, &cost, eff) == 0, "drop");
        require(eff[20] == 0 && cost.dropped == 1 && cost.total_bits == 4, "drop effect");
    }

    // Consecutivos + signos.
    {
        std::array<int64_t, 63> ac{};
        ac[0] = 1; ac[1] = -2; ac[2] = 3;
        CdngAcBlockCost a{}, b{};
        require(cdng_ac_block_cost(ac.data(), 63, &tables, &a, nullptr) == 0, "seq");
        // Misma secuencia vía prepare + symbol sum.
        int64_t eff[63];
        CdngEntropyStats st{};
        require(cdng_ac_prepare(ac.data(), 63, &tables, eff, &st) == 0, "prep");
        uint64_t bits = cdng_ac_eob_bits(&tables);
        int prev = -1;
        for (int i = 0; i < 63; ++i) {
            if (!eff[i]) continue;
            const size_t run = size_t(i - prev - 1);
            prev = i;
            bits += cdng_ac_symbol_bits(run, eff[i], &tables);
        }
        require(bits == a.total_bits, "symbol sum == block");
        require(cdng_ac_block_cost(eff, 63, &tables, &b, nullptr) == 0, "eff");
        require(a.total_bits == b.total_bits, "idempotent");
    }

    // Aleatorio reproducible: coste bloque == suma símbolos + EOB.
    uint64_t rng = 42;
    for (int trial = 0; trial < 200; ++trial) {
        std::array<int64_t, 63> ac{};
        for (auto &v : ac) {
            rng = rng * 6364136223846793005ULL + 1;
            const int mode = int(rng % 5);
            if (mode == 0) v = 0;
            else if (mode == 1) v = int64_t(rng % 7) - 3;
            else if (mode == 2) v = int64_t(rng % 31) - 15;
            else v = 0;
        }
        CdngAcBlockCost cost{};
        int64_t eff[63];
        require(cdng_ac_block_cost(ac.data(), 63, &tables, &cost, eff) == 0, "rand");
        uint64_t sum = cdng_ac_eob_bits(&tables);
        int prev = -1;
        for (int i = 0; i < 63; ++i) {
            if (!eff[i]) continue;
            const size_t run = size_t(i - prev - 1);
            prev = i;
            const uint64_t sb = cdng_ac_symbol_bits(run, eff[i], &tables);
            require(sb > 0, "rand sym");
            sum += sb;
        }
        require(sum == cost.total_bits, "rand reconcile");
    }

    // Chroma 31 coefs.
    {
        std::array<int64_t, 31> ac{};
        ac[5] = 2;
        CdngAcBlockCost cost{};
        require(cdng_ac_block_cost(ac.data(), 31, &tables, &cost, nullptr) == 0, "chroma");
        require(cost.ac_coded == 1 && cost.eob_bits == 4, "chroma coded");
    }

    std::cout << "ac_cost_test passed\n";
    return 0;
}
