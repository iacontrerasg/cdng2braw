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

bool better_tie(int64_t a, int64_t b) {
    // Preferir menor magnitud; si empatan, el más cercano a 0 ya está; luego el menor valor con signo.
    const int64_t aa = a < 0 ? -a : a, bb = b < 0 ? -b : b;
    if (aa != bb) return aa < bb;
    return a < b;
}

bool better_j(double j, int64_t lev, double jBest, int64_t levBest) {
    if (j < jBest) return true;
    if (j > jBest) return false;
    return better_tie(lev, levBest);
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
            const int64_t away = sign * (mag + 1);
            const double d0 = std::fabs(coef - double(legacy) * step);
            const double d1 = std::fabs(coef - double(away) * step);
            if (d1 <= d0) push_unique(out, away);
        }
    } else if (std::fabs(coef) >= 0.5 * step) {
        const int64_t s = coef >= 0 ? 1 : -1;
        push_unique(out, s);
    }
    if (step > 0) {
        const double v = coef / step;
        const int64_t dz = int64_t(std::copysign(std::floor(std::fabs(v) + 0.5 - 0.5), v));
        push_unique(out, dz);
    }
    return out;
}

double rdoqEvalJ(const double *coef_scan, const int64_t *levels, size_t ncoef, double step,
                 double lambda, const CdngEntropyTables &tables, DeadzonePlane plane,
                 const RdoqConfig &cfg, uint64_t *out_R_estimated, double *out_D) {
    const double inf = std::numeric_limits<double>::infinity();
    if (!coef_scan || !levels || (ncoef != 63 && ncoef != 31 && ncoef > 63)) return inf;
    double D = 0;
    uint64_t R = 0;
    int prev = -1;
    for (size_t i = 0; i < ncoef; ++i) {
        D += distortionDct(coef_scan[i], levels[i], step, weight_at(cfg, plane, i));
        if (!levels[i]) continue;
        const size_t run = (prev < 0) ? i : size_t(int(i) - prev - 1);
        prev = int(i);
        const uint64_t bits = cdng_ac_symbol_bits(run, levels[i], &tables);
        if (!bits) return inf;
        R += bits;
    }
    R += cdng_ac_eob_bits(&tables);
    if (out_R_estimated) *out_R_estimated = R;
    if (out_D) *out_D = D;
    return D + lambda * double(R);
}

void rdoqOptimizeBlock(const double *coef_scan, double step, int64_t *ac_io, size_t ncoef,
                       const CdngEntropyTables &tables, const RdoqConfig &cfg,
                       DeadzonePlane plane, const int * /*scan*/,
                       double *out_D, uint64_t *out_R, RdoqBlockReport *out_report) {
    if (!cfg.enabled || !ac_io || !coef_scan || (ncoef != 63 && ncoef != 31)) return;

    std::vector<std::vector<int64_t>> cands(ncoef);
    for (size_t i = 0; i < ncoef; ++i)
        cands[i] = rdoqCandidates(coef_scan[i], step, ac_io[i], cfg);

    const size_t S = ncoef + 1;
    const double inf = std::numeric_limits<double>::infinity();
    std::vector<double> J(S, inf), J2(S, inf);
    std::vector<uint64_t> Rest(S, 0), Rest2(S, 0);
    std::vector<int64_t> choice(ncoef * S, 0);
    std::vector<int> back(ncoef * S, -2);
    J[0] = 0;
    Rest[0] = 0;

    for (size_t i = 0; i < ncoef; ++i) {
        std::fill(J2.begin(), J2.end(), inf);
        std::fill(Rest2.begin(), Rest2.end(), 0);
        for (size_t sp = 0; sp < S; ++sp) {
            if (!(J[sp] < inf)) continue;
            const int prev = int(sp) - 1;
            for (int64_t lev : cands[i]) {
                const double w = weight_at(cfg, plane, i);
                const double D = distortionDct(coef_scan[i], lev, step, w);
                uint64_t dR = 0;
                size_t sn;
                if (lev == 0) {
                    sn = sp;
                } else {
                    const size_t run = (prev < 0) ? i : size_t(int(i) - prev - 1);
                    dR = cdng_ac_symbol_bits(run, lev, &tables);
                    if (!dR) continue;
                    sn = i + 1;
                }
                const double j = J[sp] + D + cfg.lambda * double(dR);
                const uint64_t rNew = Rest[sp] + dR;
                if (!(J2[sn] < inf) || better_j(j, lev, J2[sn], choice[i * S + sn])) {
                    J2[sn] = j;
                    Rest2[sn] = rNew;
                    choice[i * S + sn] = lev;
                    back[i * S + sn] = int(sp);
                }
            }
        }
        J.swap(J2);
        Rest.swap(Rest2);
    }

    const uint64_t eobBits = cdng_ac_eob_bits(&tables);
    const double eob = cfg.lambda * double(eobBits);
    double bestJ = inf;
    size_t bestS = 0;
    int64_t bestLastLev = 0;
    for (size_t s = 0; s < S; ++s) {
        if (!(J[s] < inf)) continue;
        const double j = J[s] + eob;
        // Desempate final: menor R estimado, luego estado con último NZ más temprano.
        if (j < bestJ || (j == bestJ && (Rest[s] < Rest[bestS] ||
                (Rest[s] == Rest[bestS] && s < bestS)))) {
            bestJ = j;
            bestS = s;
            bestLastLev = (s == 0) ? 0 : choice[(ncoef - 1) * S + s];
            (void)bestLastLev;
        }
    }
    if (!(bestJ < inf)) return;

    std::vector<int64_t> levels(ncoef, 0);
    size_t s = bestS;
    for (size_t i = ncoef; i-- > 0;) {
        const int psp = back[i * S + s];
        if (psp < -1) return;
        levels[i] = choice[i * S + s];
        s = size_t(psp);
    }
    for (size_t i = 0; i < ncoef; ++i) ac_io[i] = levels[i];

    uint64_t R_est = 0;
    double D = 0;
    const double Jchk = rdoqEvalJ(coef_scan, ac_io, ncoef, step, cfg.lambda, tables, plane, cfg, &R_est, &D);
    (void)Jchk;

    CdngAcBlockCost emitted{};
    int64_t effective[63];
    cdng_ac_block_cost(ac_io, ncoef, &tables, &emitted, effective);

    uint64_t n_mod = 0;
    for (size_t i = 0; i < ncoef; ++i)
        if (effective[i] != ac_io[i]) ++n_mod;

    if (out_D) *out_D = D;
    if (out_R) *out_R = emitted.total_bits;
    if (out_report) {
        out_report->D = D;
        out_report->R_estimated = R_est;
        out_report->R_optimized = R_est;
        out_report->R_emitted = emitted.total_bits;
        out_report->J = D + cfg.lambda * double(R_est);
        out_report->levels_modified = n_mod > 0;
        out_report->n_modified = n_mod;
        out_report->clamped = emitted.clamped;
        out_report->dropped = emitted.dropped;
        out_report->fallback = emitted.fallback;
    }
}

}  // namespace cdng
