// As Shot de la ruta og: (kelvin, tinte) cuya ganancia R/B de la tabla del perfil reproduce
// el AsShotNeutral. El Python lo resuelve con scipy least_squares acotado (TRF, tolerancias 1e-12) sobre la
// interpolación bilineal de la tabla en (mired, tinte). Aquí: en cada celda las dos ecuaciones bilineales se
// reducen a una cuadrática (solución exacta, pulida con Newton); si no hay raíz dentro del dominio, el mínimo
// acotado de ‖residuo‖² está en el borde y se busca por sección áurea en cada tramo de cada lado.
#include "signal_internal.hpp"

#include <algorithm>
#include <cmath>

namespace cdng {

namespace {

struct Gains {
    const OgModel::Impl &p;
    double tr, tb;
    std::size_t nm() const { return p.mired.size(); }
    std::size_t nt() const { return p.tints.size(); }
    static std::size_t cell(const std::vector<double> &axis, double x) {
        std::size_t i = std::upper_bound(axis.begin(), axis.end(), x) - axis.begin();
        return std::min(std::max<std::size_t>(i, 1), axis.size() - 1) - 1;
    }
    // residuo (log R/tr, log B/tb) y, si se pide, su jacobiano en (m, t) dentro de la celda (i, j)
    void eval(double m, double t, std::size_t i, std::size_t j, double r[2], double J[4] = nullptr) const {
        const double dm = p.mired[i + 1] - p.mired[i], dt = p.tints[j + 1] - p.tints[j];
        const double u = (m - p.mired[i]) / dm, v = (t - p.tints[j]) / dt;
        const std::size_t n = nt();
        const std::vector<double> *tables[2] = {&p.red, &p.blue};
        const double target[2] = {tr, tb};
        for (int k = 0; k < 2; ++k) {
            const auto &g = *tables[k];
            const double g00 = g[i * n + j], g10 = g[(i + 1) * n + j], g01 = g[i * n + j + 1], g11 = g[(i + 1) * n + j + 1];
            const double value = g00 * (1 - u) * (1 - v) + g10 * u * (1 - v) + g01 * (1 - u) * v + g11 * u * v;
            r[k] = std::log(value / target[k]);
            if (J) {
                J[2 * k] = ((g10 - g00) * (1 - v) + (g11 - g01) * v) / dm / value;
                J[2 * k + 1] = ((g01 - g00) * (1 - u) + (g11 - g10) * u) / dt / value;
            }
        }
    }
    void eval(double m, double t, double r[2]) const { eval(m, t, cell(p.mired, m), cell(p.tints, t), r); }
    double cost(double m, double t) const { double r[2]; eval(m, t, r); return r[0] * r[0] + r[1] * r[1]; }
};

}  // namespace

std::pair<int, int> inferAsShot(const Vec3 &neutral, const OgModel::Impl &p) {
    const Gains g{p, neutral[1] / neutral[0], neutral[1] / neutral[2]};
    const auto &M = p.mired, &T = p.tints;
    const std::size_t nm = M.size(), nt = T.size();
    const double m0 = std::clamp(1e6 / 5600, M.front(), M.back()), t0 = 10;
    const double spanM = M.back() - M.front(), spanT = T.back() - T.front();
    double bestM = NAN, bestT = NAN, bestD = INFINITY;
    const double tol = 1e-9;
    for (std::size_t i = 0; i + 1 < nm; ++i)
        for (std::size_t j = 0; j + 1 < nt; ++j) {
            double a[4], b[4];
            const double target[2] = {g.tr, g.tb};
            const std::vector<double> *tables[2] = {&p.red, &p.blue};
            for (int k = 0; k < 2; ++k) {
                const auto &tab = *tables[k];
                const double g00 = tab[i * nt + j], g10 = tab[(i + 1) * nt + j], g01 = tab[i * nt + j + 1], g11 = tab[(i + 1) * nt + j + 1];
                double *c = k == 0 ? a : b;
                c[0] = g00 - target[k]; c[1] = g10 - g00; c[2] = g01 - g00; c[3] = g11 - g10 - g01 + g00;
            }
            // (a0 + a1 u) + (a2 + a3 u) v = 0 y (b0 + b1 u) + (b2 + b3 u) v = 0 → A u² + B u + C = 0
            const double A = a[1] * b[3] - b[1] * a[3];
            const double B = a[0] * b[3] + a[1] * b[2] - b[0] * a[3] - b[1] * a[2];
            const double C = a[0] * b[2] - b[0] * a[2];
            double roots[2]; int count = 0;
            const double scale = std::max({std::fabs(A), std::fabs(B), std::fabs(C)});
            if (scale == 0) continue;
            if (std::fabs(A) <= 1e-14 * scale) {
                if (B != 0) roots[count++] = -C / B;
            } else {
                const double disc = B * B - 4 * A * C;
                if (disc < 0) continue;
                const double q = -0.5 * (B + std::copysign(std::sqrt(disc), B));
                if (q != 0) roots[count++] = C / q;
                roots[count++] = q / A;
            }
            for (int r = 0; r < count; ++r) {
                const double u = roots[r];
                if (!(u >= -tol && u <= 1 + tol)) continue;
                const double da = a[2] + a[3] * u, db = b[2] + b[3] * u;
                const double v = std::fabs(da) >= std::fabs(db) ? -(a[0] + a[1] * u) / da : -(b[0] + b[1] * u) / db;
                if (!(v >= -tol && v <= 1 + tol)) continue;
                double m = M[i] + u * (M[i + 1] - M[i]), t = T[j] + v * (T[j + 1] - T[j]);
                for (int it = 0; it < 4; ++it) {   // Newton sobre el residuo en logaritmos
                    double res[2], J[4];
                    g.eval(m, t, Gains::cell(M, m), Gains::cell(T, t), res, J);
                    const double det = J[0] * J[3] - J[1] * J[2];
                    if (det == 0) break;
                    m -= (J[3] * res[0] - J[1] * res[1]) / det;
                    t -= (-J[2] * res[0] + J[0] * res[1]) / det;
                    m = std::clamp(m, M.front(), M.back()); t = std::clamp(t, T.front(), T.back());
                }
                const double d = std::hypot((m - m0) / spanM, (t - t0) / spanT);
                if (d < bestD) { bestD = d; bestM = m; bestT = t; }
            }
        }
    if (!(bestD < INFINITY)) {
        // Sin raíz en el dominio: mínimo de ‖r‖² en el borde, tramo a tramo (la función es suave en cada tramo).
        double best = INFINITY;
        auto segment = [&](double ma, double ta, double mb, double tb) {
            const double phi = (std::sqrt(5.0) - 1) / 2;
            double lo = 0, hi = 1;
            double x1 = hi - phi * (hi - lo), x2 = lo + phi * (hi - lo);
            auto at = [&](double s, double &m, double &t) { m = ma + s * (mb - ma); t = ta + s * (tb - ta); };
            double m, t;
            at(x1, m, t); double f1 = g.cost(m, t);
            at(x2, m, t); double f2 = g.cost(m, t);
            for (int it = 0; it < 200 && hi - lo > 1e-15; ++it) {
                if (f1 <= f2) { hi = x2; x2 = x1; f2 = f1; x1 = hi - phi * (hi - lo); at(x1, m, t); f1 = g.cost(m, t); }
                else { lo = x1; x1 = x2; f1 = f2; x2 = lo + phi * (hi - lo); at(x2, m, t); f2 = g.cost(m, t); }
            }
            for (double s : {0.0, 1.0, (lo + hi) / 2}) {
                at(s, m, t);
                const double f = g.cost(m, t);
                if (f < best) { best = f; bestM = m; bestT = t; }
            }
        };
        for (std::size_t i = 0; i + 1 < nm; ++i) {
            segment(M[i], T.front(), M[i + 1], T.front());
            segment(M[i], T.back(), M[i + 1], T.back());
        }
        for (std::size_t j = 0; j + 1 < nt; ++j) {
            segment(M.front(), T[j], M.front(), T[j + 1]);
            segment(M.back(), T[j], M.back(), T[j + 1]);
        }
    }
    double r[2];
    g.eval(bestM, bestT, r);
    if (!(std::max(std::fabs(r[0]), std::fabs(r[1])) <= .005))
        throw Error("AsShotNeutral is outside the measured profile domain");
    return {int(std::nearbyint(1e6 / bestM)), int(std::nearbyint(bestT))};
}

}  // namespace cdng
