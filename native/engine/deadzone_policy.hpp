#pragma once
// EXP-003: política experimental de deadzone AC por plano / frecuencia.
// Desactivada por defecto. No altera DC ni las tablas del decoder.
#include "common.hpp"

#include <array>
#include <cstdint>
#include <string>

namespace cdng {

enum class DeadzonePlane { Y = 0, Cb = 1, Cr = 2 };

// z ∈ [0, 0.5] para el coeficiente en índice de frecuencia dctIndex = u*8+v.
// Sin política (legacy): CDNG_DEADZONE global o 0.
struct DeadzonePolicy {
    bool active = false;
    std::string name;
    int schema = 0;
    std::string sha256;   // SHA del JSON leído (vacío si legacy)
    std::string path;     // ruta efectiva, si aplica
    // Tablas precalculadas: Y 8×8 (índice u*8+v), croma 4×8 (mismo layout, u∈[0,3]).
    std::array<double, 64> zY{};
    std::array<double, 64> zCb{};
    std::array<double, 64> zCr{};

    double at(DeadzonePlane plane, int dctIndex) const;
};

// Carga una sola vez (antes de workers). Tras ensure, lecturas sin mutex por coeficiente.
// Conflicto CDNG_DEADZONE ∩ CDNG_DEADZONE_POLICY → Error.
void deadzonePolicyEnsureLoaded();
const DeadzonePolicy &deadzonePolicy();
// Solo tests: invalida el puntero atómico y vuelve a permitir carga.
void deadzonePolicyResetForTests();

// Clasificación de banda AC (DC excluido): 0=low, 1=mid, 2=high.
int deadzoneBand(DeadzonePlane plane, int u, int v);
double deadzoneNormalizedFreq(DeadzonePlane plane, int u, int v);

// Cuantización AC con z por coeficiente. z=0 → nearbyint (paridad histórica).
// Usa el puntero atómico de la política ya cargada (sin adquirir mutex).
int64_t quantizeAc(double coef, double step, DeadzonePlane plane, int dctIndex);

}  // namespace cdng
