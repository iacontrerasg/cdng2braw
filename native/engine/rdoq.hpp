#pragma once
// EXP-003b — RDOQ: candidatos, distorsión DCT y trellis por bloque AC.
// Desactivado por defecto; activar con CDNG_RDOQ=1 (o λ>0 vía CDNG_RDOQ_LAMBDA).
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

// Lee CDNG_RDOQ / CDNG_RDOQ_LAMBDA. Sin efecto si disabled.
RdoqConfig rdoqConfigFromEnv();

inline double distortionDct(double coef, int64_t level, double step, double weight = 1.0) {
    const double err = coef - double(level) * step;
    return weight * err * err;
}

// Niveles candidatos legales (incl. legacy). Sin cambiar signo arbitrariamente.
std::vector<int64_t> rdoqCandidates(double coef, double step, int64_t legacy,
                                    const RdoqConfig &cfg);

// Optimiza un bloque AC en orden de scan. Escribe en ac_io (tamaño ncoef = 63 o 31).
// Tablas = CdngEntropyTables del codec. A λ=0 minimiza D (legacy ∈ set ⇒ D ≤ D_legacy).
void rdoqOptimizeBlock(const double *coef_scan, double step, int64_t *ac_io, size_t ncoef,
                       const CdngEntropyTables &tables, const RdoqConfig &cfg,
                       DeadzonePlane plane, const int *scan /* ScanY/ScanC */,
                       double *out_D = nullptr, uint64_t *out_R = nullptr);

}  // namespace cdng
