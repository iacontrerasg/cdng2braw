#pragma once
// EXP-003b — RDOQ: candidatos, distorsión DCT y trellis por bloque AC.
// Desactivado por defecto; activar solo con CDNG_RDOQ=1|on|true|yes.
// CDNG_RDOQ_LAMBDA exige RDOQ activo; valores inválidos se rechazan (no se normalizan).
#include "codec_tables.hpp"
#include "deadzone_policy.hpp"
#include "cdng_native.h"

#include <cstdint>
#include <cstddef>
#include <vector>

namespace cdng {

struct RdoqWeights {
    double planeY = 1.0, planeC = 1.0;
    // Banda por índice de scan (misma longitud que el bloque AC); vacío → 1.0.
    std::vector<double> bandY, bandC;
};

struct RdoqConfig {
    bool enabled = false;
    double lambda = 0.0;          // J = D + lambda * R_bits
    RdoqWeights weights;
    int neighbors_toward_zero = 2;
    bool allow_away_from_zero = true;
};

// Lee CDNG_RDOQ / CDNG_RDOQ_LAMBDA.
// Ausente → disabled. Flag desconocido, λ inválida o λ sin RDOQ → Error.
RdoqConfig rdoqConfigFromEnv();

inline double distortionDct(double coef, int64_t level, double step, double weight = 1.0) {
    const double err = coef - double(level) * step;
    return weight * err * err;
}

// Niveles candidatos legales (incl. legacy). Sin cambiar signo arbitrariamente.
std::vector<int64_t> rdoqCandidates(double coef, double step, int64_t legacy,
                                    const RdoqConfig &cfg);

// Costes y post-modificaciones del entropy (Fase E).
struct RdoqBlockReport {
    double D = 0;                 // distorsión del modelo sobre niveles elegidos (pre-prepare)
    double J = 0;                 // D + λ·R_estimated
    uint64_t R_estimated = 0;     // suma de costes de símbolo del trellis + EOB (sin prepare)
    uint64_t R_emitted = 0;       // cdng_ac_block_cost tras clamp/adjust/drop
    uint64_t R_optimized = 0;     // alias documentado = R_estimated (objetivo del DP)
    bool levels_modified = false; // prepare cambió algún nivel
    uint64_t n_modified = 0;
    uint64_t clamped = 0;
    uint64_t dropped = 0;
    uint64_t fallback = 0;
};

// J = D + λ·R_estimated para una secuencia (misma regla de runs que el trellis).
// Devuelve +inf si algún no-cero es ilegal (drop / sin código).
double rdoqEvalJ(const double *coef_scan, const int64_t *levels, size_t ncoef, double step,
                 double lambda, const CdngEntropyTables &tables, DeadzonePlane plane,
                 const RdoqConfig &cfg, uint64_t *out_R_estimated = nullptr, double *out_D = nullptr);

// Optimiza un bloque AC en orden de scan. Escribe en ac_io (tamaño ncoef = 63 o 31).
// Tablas = CdngEntropyTables del codec. A λ=0 minimiza D (legacy ∈ set ⇒ D ≤ D_legacy).
// Empates: menor |nivel|, luego nivel más cercano a 0 (determinista).
void rdoqOptimizeBlock(const double *coef_scan, double step, int64_t *ac_io, size_t ncoef,
                       const CdngEntropyTables &tables, const RdoqConfig &cfg,
                       DeadzonePlane plane, const int *scan /* ScanY/ScanC */,
                       double *out_D = nullptr, uint64_t *out_R = nullptr,
                       RdoqBlockReport *out_report = nullptr);

}  // namespace cdng
