#pragma once
// Bloque C — tablas del códec (privadas del bloque; las pruebas las usan directamente). Pasos del
// cuantizador, tabla AC del JSON y tablas fijas (DC, EOB, orden de lectura).
#include "common.hpp"

#include <array>
#include <map>
#include <string>
#include <utility>

namespace cdng {

constexpr int AcRuns = 63, AcClasses = 12, AcPieces = 4;   // MAXK = 11 (|ℓ| ≤ 4095), NPRE = 4

// Orden de lectura de los AC como índice u·8 + v del bloque.
extern const std::array<int, 63> ScanY;
extern const std::array<int, 31> ScanC;

// DCT-II ortonormal 2D (como scipy.fft.dctn(norm="ortho")) de un bloque 8×8 y de uno 4×8 (filas × columnas,
// fila a fila); el resultado tiene el mismo orden.
void dct8x8(const double *in, double *out);
void dct4x8(const double *in, double *out);
// Inversas (la DCT es ortonormal: la traspuesta). Para la vista previa de la compresión.
void idct8x8(const double *in, double *out);
void idct4x8(const double *in, double *out);

struct QuantSteps {
    double yUnit = 0, cUnit = 0;   // unidades del DC de luma y de croma
    double qY = 0, qC = 0;         // paso AC plano de luma y de croma (FLAT_STEPS)
};

struct Code {
    uint64_t value = 0;
    int length = 0;
};

struct CodecTables {
    std::map<std::pair<int, int>, std::string> ac;   // (run, clase) → prefijo
    std::string zrl = "11111111001";
    std::vector<QuantSteps> quant;                    // índice = q
    // Tablas de entropía con la disposición de CdngEntropyTables.
    std::vector<uint64_t> preVal, finVal;             // [4][2][63][10], [2][63][10]
    std::vector<uint8_t> preLen, finLen, drop;        // ídem, drop [63][10]
    std::vector<int64_t> adjust;                      // [10]
    std::array<uint64_t, 13> dcVal{};
    std::array<int, 13> dcLen{};
    uint64_t eobValue = 0;
    int eobLength = 0;

    static CodecTables load(const std::filesystem::path &vlcTable, const std::filesystem::path &quantSteps);
    // Construye las tablas de entropía a partir de `ac` y `zrl` (lo hace load; público para las pruebas).
    void build();
    const QuantSteps &steps(int q) const;
    // Código DC diferencial (_dc_codes) para |d| ≤ 4095.
    Code dcCode(int64_t d) const;
    // (prefijo, código final) de un (run, clase) sin código propio (_fallback_bits).
    std::pair<std::string, std::string> fallbackBits(int run, int k, char signBit) const;
};

}  // namespace cdng
