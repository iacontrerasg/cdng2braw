#include "rdoq.hpp"
#include "cdng_ac_cost.h"

#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <limits>
#include <string>

namespace cdng {
namespace {

constexpr int64_t kMaxLevel = 4095;

bool env_on(const char *name) {
    const char *v = std::getenv(name);
    return v && *v && std::string(v) != "0";
}

double env_double(const char *name, double fallback) {
    const char *v = std::getenv(name);
    if (!v || !*v) return fallback;
    char *end = nullptr;
    const double x = std::strtod(v, &end);
    return (end && end != v) ? x : fallback;
}

double weight_at(const RdoqConfig &cfg, DeadzonePlane plane, size_t scan_i) {
    double w = (plane == DeadzonePlane::Y) ? cfg.weights.planeY : cfg.weights.planeC;
    const auto &band = (plane == DeadzonePlane::Y) ? cfg.weights.bandY : cfg.weights.bandC;
    if (scan_i < band.size()) w *= band[scan_i];
    return w;
}

void push_unique(std::vector<int64_t> &out, int64_t v) {
    if (v < -kMaxLevel || v > kMaxLevel) return;
    if (std::find(out.begin(), out.end(), v) == out.end()) out.push_back(v);
}

}  // namespace

RdoqConfig rdoqConfigFromEnv() {
    RdoqConfig c;
    c.enabled = env_on("CDNG_RDOQ");
    c.lambda = env_double("CDNG_RDOQ_LAMBDA", 0.0);
    if (c.enabled && c.lambda < 0) c.lambda = 0;
    return c;
}

std::vector<int64_t> rdoqCandidates(double coef, double step, int64_t legacy, const RdoqConfig &cfg) {
    std::vector<int64_t> out;
    push_unique(out, legacy);
    push_unique(out, 0);
    if (legacy != 0) {
        const int64_t sign = legacy > 0 ? 1 : -1;
        const int64_t mag = legacy > 0 ? legacy : -legacy;
        for (int d = 1; d <= cfg.neighbors_toward_zero; ++d) {
            const int64_t m = mag - d;
            if (m <= 0) {
                push_unique(out, 0);
                break;
            }
            push_unique(out, sign * m);
        }
        if (cfg.allow_away_from_zero && mag < kMaxLevel) {
            // Solo si reduce |c − k s| respecto a legacy o puede acortar VLC (mag±1).
            const int64_t away = sign * (mag + 1);
            const double d0 = std::fabs(coef - double(legacy) * step);
            const double d1 = std::fabs(coef - double(away) * step);
            if (d1 <= d0) push_unique(out, away);
        }
    } else if (std::fabs(coef) >= 0.5 * step) {
        // Legacy 0 pero coef cerca del umbral: probar ±1.
        const int64_t s = coef >= 0 ? 1 : -1;
        push_unique(out, s);
    }
    // Deadzone de control (z=0.5) como candidato adicional cuando difiere.
    if (step > 0) {
        const double v = coef / step;
        const int64_t dz = int64_t(std::copysign(std::floor(std::fabs(v) + 0.5 - 0.5), v));
        push_unique(out, dz);
    }
    return out;
}

void rdoqOptimizeBlock(const double *coef_scan, double step, int64_t *ac_io, size_t ncoef,
                       const CdngEntropyTables &tables, const RdoqConfig &cfg,
                       DeadzonePlane plane, const int * /*scan*/,
                       double *out_D, uint64_t *out_R) {
    if (!cfg.enabled || !ac_io || !coef_scan || (ncoef != 63 && ncoef != 31)) return;

    // Candidatos por posición (incl. legacy ac_io[i]).
    std::vector<std::vector<int64_t>> cands(ncoef);
    for (size_t i = 0; i < ncoef; ++i)
        cands[i] = rdoqCandidates(coef_scan[i], step, ac_io[i], cfg);

    // DP: estado = último índice no-cero (−1 = ninguno). Coste J acumulado.
    // prev_nz_state indexado como last+1 (0 = ninguno).
    const size_t S = ncoef + 1;
    const double inf = std::numeric_limits<double>::infinity();
    std::vector<double> J(S, inf), J2(S, inf);
    std::vector<int64_t> choice(ncoef * S, 0);
    std::vector<int> back(ncoef * S, -2);  // estado previo
    J[0] = 0;  // sin NZ previos, coste 0 antes del primer coef

    for (size_t i = 0; i < ncoef; ++i) {
        std::fill(J2.begin(), J2.end(), inf);
        for (size_t sp = 0; sp < S; ++sp) {
            if (!(J[sp] < inf)) continue;
            const int prev = int(sp) - 1;  // -1 si sp==0
            for (int64_t lev : cands[i]) {
                const double w = weight_at(cfg, plane, i);
                const double D = distortionDct(coef_scan[i], lev, step, w);
                double R = 0;
                size_t sn;
                if (lev == 0) {
                    sn = sp;  // no cambia último NZ
                } else {
                    const size_t run = (prev < 0) ? i : size_t(int(i) - prev - 1);
                    const uint64_t bits = cdng_ac_symbol_bits(run, lev, &tables);
                    if (!bits) continue;  // ilegal / drop
                    R = double(bits);
                    sn = i + 1;
                }
                const double j = J[sp] + D + cfg.lambda * R;
                if (j < J2[sn]) {
                    J2[sn] = j;
                    choice[i * S + sn] = lev;
                    back[i * S + sn] = int(sp);
                }
            }
        }
        J.swap(J2);
    }

    // Cerrar con EOB (coste fijo; no depende del camino salvo que siempre se emite).
    const double eob = cfg.lambda * double(cdng_ac_eob_bits(&tables));
    double bestJ = inf;
    size_t bestS = 0;
    for (size_t s = 0; s < S; ++s) {
        if (!(J[s] < inf)) continue;
        const double j = J[s] + eob;
        if (j < bestJ) { bestJ = j; bestS = s; }
    }
    if (!(bestJ < inf)) return;  // no tocar

    // Reconstruir niveles.
    std::vector<int64_t> levels(ncoef, 0);
    size_t s = bestS;
    for (size_t i = ncoef; i-- > 0;) {
        const int psp = back[i * S + s];
        if (psp < -1) {
            // Estado no alcanzado de forma coherente: abortar (conservar legacy).
            return;
        }
        levels[i] = choice[i * S + s];
        s = size_t(psp);
    }
    for (size_t i = 0; i < ncoef; ++i) ac_io[i] = levels[i];

    if (out_D || out_R) {
        double D = 0;
        for (size_t i = 0; i < ncoef; ++i)
            D += distortionDct(coef_scan[i], ac_io[i], step, weight_at(cfg, plane, i));
        CdngAcBlockCost cost{};
        cdng_ac_block_cost(ac_io, ncoef, &tables, &cost, nullptr);
        if (out_D) *out_D = D;
        if (out_R) *out_R = cost.total_bits;
    }
}

}  // namespace cdng
