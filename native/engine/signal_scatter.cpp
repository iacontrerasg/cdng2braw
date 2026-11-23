// Interpolador de la ruta pocket: la triangulación de Delaunay de SciPy (LinearNDInterpolator) recorrida con un
// índice en rejilla regular y el vecino más cercano fuera de la envolvente (NearestNDInterpolator). No es una tabla
// regular porque una tabla N³ no bajaba de decenas de códigos de error.
#include "signal_internal.hpp"

#include <algorithm>
#include <cfloat>
#include <cmath>
#include <cstring>
#include <limits>
#include <numeric>
#include <unordered_map>

namespace cdng {

namespace {

constexpr double Eps = 100 * DBL_EPSILON;           // scipy.interpolate._interpnd: eps
const double EpsBroad = std::sqrt(DBL_EPSILON);     // eps_broad

template <class T> void take(const std::vector<uint8_t> &bytes, std::size_t &at, T *out, std::size_t count) {
    if (at + count * sizeof(T) > bytes.size()) throw Error("The embedded calibration is damaged: pocket table");
    std::memcpy(out, bytes.data() + at, count * sizeof(T));
    at += count * sizeof(T);
}

// Transformada baricéntrica como scipy.spatial._qhull._get_barycentric_transforms: T[i][j] = p_j[i] − p_3[i],
// inversa por LU con pivote parcial; NaN si es singular o rcond < 1000·DBL_EPSILON.
void barycentricTransform(const double *p0, const double *p1, const double *p2, const double *p3, double *out) {
    double a[3][3];
    const double *p[3] = {p0, p1, p2};
    for (int i = 0; i < 3; ++i)
        for (int j = 0; j < 3; ++j) a[i][j] = p[j][i] - p3[i];
    double norm = 0;
    for (int j = 0; j < 3; ++j) norm = std::max(norm, std::fabs(a[0][j]) + std::fabs(a[1][j]) + std::fabs(a[2][j]));
    double lu[3][3];
    std::memcpy(lu, a, sizeof lu);
    int perm[3] = {0, 1, 2};
    bool singular = false;
    for (int k = 0; k < 3 && !singular; ++k) {
        int piv = k;
        for (int i = k + 1; i < 3; ++i) if (std::fabs(lu[i][k]) > std::fabs(lu[piv][k])) piv = i;
        if (lu[piv][k] == 0) { singular = true; break; }
        if (piv != k) { std::swap(lu[piv], lu[k]); std::swap(perm[piv], perm[k]); }
        for (int i = k + 1; i < 3; ++i) {
            lu[i][k] /= lu[k][k];
            for (int j = k + 1; j < 3; ++j) lu[i][j] -= lu[i][k] * lu[k][j];
        }
    }
    double inv[3][3];
    if (!singular) {
        for (int col = 0; col < 3; ++col) {   // resuelve A x = e_col
            double y[3];
            for (int i = 0; i < 3; ++i) {
                y[i] = perm[i] == col ? 1.0 : 0.0;
                for (int j = 0; j < i; ++j) y[i] -= lu[i][j] * y[j];
            }
            for (int i = 2; i >= 0; --i) {
                for (int j = i + 1; j < 3; ++j) y[i] -= lu[i][j] * y[j];
                y[i] /= lu[i][i];
            }
            for (int i = 0; i < 3; ++i) inv[i][col] = y[i];
        }
        double inorm = 0;
        for (int j = 0; j < 3; ++j) inorm = std::max(inorm, std::fabs(inv[0][j]) + std::fabs(inv[1][j]) + std::fabs(inv[2][j]));
        const double rcond = 1.0 / (norm * inorm);
        if (!(rcond >= 1000 * DBL_EPSILON)) singular = true;
    }
    for (int i = 0; i < 3; ++i)
        for (int j = 0; j < 3; ++j) out[3 * i + j] = singular ? NAN : inv[i][j];
    for (int j = 0; j < 3; ++j) out[9 + j] = singular ? NAN : p3[j];
}

}  // namespace

Scattered::Scattered(const std::filesystem::path &path) {
    const auto bytes = readBytes(path);
    if (bytes.size() < 8 || std::memcmp(bytes.data(), "CDNGTRI1", 8) != 0)
        throw Error("The embedded calibration is damaged: " + path.filename().string());
    std::size_t at = 8;
    uint32_t n = 0, m = 0, flags = 0;
    take(bytes, at, &n, 1); take(bytes, at, &m, 1); take(bytes, at, &flags, 1);
    take(bytes, at, &c_, 1);
    take(bytes, at, matrix_.data(), 9);
    take(bytes, at, lo_, 3); take(bytes, at, hi_, 3);
    warp_ = flags & 1;
    sqrtC_ = std::sqrt(c_);
    points_.resize(std::size_t(n) * 3); values_.resize(std::size_t(n) * 3);
    simplices_.resize(std::size_t(m) * 4); transform_.resize(std::size_t(m) * 12);
    take(bytes, at, points_.data(), points_.size());
    take(bytes, at, values_.data(), values_.size());
    take(bytes, at, simplices_.data(), simplices_.size());
    for (int32_t v : simplices_) if (v < 0 || uint32_t(v) >= n) throw Error("The embedded calibration is damaged: " + path.filename().string());
    if (flags & 2) {
        take(bytes, at, transform_.data(), transform_.size());
    } else {
        for (uint32_t s = 0; s < m; ++s) {
            const int32_t *v = &simplices_[4 * s];
            barycentricTransform(&points_[3 * v[0]], &points_[3 * v[1]], &points_[3 * v[2]], &points_[3 * v[3]],
                                 &transform_[12 * s]);
        }
    }
    order_.resize(n);
    std::iota(order_.begin(), order_.end(), 0);
    if (flags & 4) {   // puntos del vecino más cercano (un representante por grupo de puntos repetidos)
        uint32_t k = 0;
        take(bytes, at, &k, 1);
        order_.resize(k);
        take(bytes, at, order_.data(), k);
        for (int32_t v : order_) if (v < 0 || uint32_t(v) >= n) throw Error("The embedded calibration is damaged: " + path.filename().string());
    }
    if (at != bytes.size()) throw Error("The embedded calibration is damaged: " + path.filename().string());
    valid_.resize(m);
    for (uint32_t s = 0; s < m; ++s) valid_[s] = transform_[12 * s] == transform_[12 * s];

    // Vecinos: neighbors[s][k] es el símplice al otro lado de la cara opuesta al vértice k (como Qhull).
    neighbors_.assign(std::size_t(m) * 4, -1);
    std::unordered_map<uint64_t, std::pair<int32_t, int32_t>> faces;
    faces.reserve(std::size_t(m) * 3);
    for (uint32_t s = 0; s < m; ++s) {
        for (int k = 0; k < 4; ++k) {
            uint32_t f[3]; int q = 0;
            for (int j = 0; j < 4; ++j) if (j != k) f[q++] = uint32_t(simplices_[4 * s + j]);
            std::sort(f, f + 3);
            const uint64_t key = uint64_t(f[0]) << 42 | uint64_t(f[1]) << 21 | f[2];
            auto it = faces.find(key);
            if (it == faces.end()) {
                faces.emplace(key, std::make_pair(int32_t(s), int32_t(k)));
            } else {
                neighbors_[4 * s + k] = it->second.first;
                neighbors_[4 * it->second.first + it->second.second] = int32_t(s);
            }
        }
    }

    // Índice en rejilla regular: cada celda lista (en orden) los símplices cuya caja, algo ampliada, la toca.
    const int g = std::clamp(int(std::cbrt(double(m))), 8, 64);
    for (int a = 0; a < 3; ++a) { grid_[a] = g; cell_[a] = std::max(hi_[a] - lo_[a], 1e-300) / g; }
    auto cellRange = [&](uint32_t s, int lo[3], int hi[3]) {
        for (int a = 0; a < 3; ++a) {
            double mn = INFINITY, mx = -INFINITY;
            for (int k = 0; k < 4; ++k) {
                const double v = points_[3 * simplices_[4 * s + k] + a];
                mn = std::min(mn, v); mx = std::max(mx, v);
            }
            const double pad = 1e-7 * (hi_[a] - lo_[a]) + 1e-12;
            lo[a] = std::clamp(int(std::floor((mn - pad - lo_[a]) / cell_[a])), 0, grid_[a] - 1);
            hi[a] = std::clamp(int(std::floor((mx + pad - lo_[a]) / cell_[a])), 0, grid_[a] - 1);
        }
    };
    const std::size_t cells = std::size_t(g) * g * g;
    std::vector<int32_t> count(cells + 1, 0);
    for (int pass = 0; pass < 2; ++pass) {
        if (pass == 1) {
            cellStart_.assign(cells + 1, 0);
            for (std::size_t i = 0; i < cells; ++i) cellStart_[i + 1] = cellStart_[i] + count[i];
            cellItems_.resize(cellStart_[cells]);
            std::fill(count.begin(), count.end(), 0);
        }
        for (uint32_t s = 0; s < m; ++s) {
            int lo[3], hi[3];
            cellRange(s, lo, hi);
            for (int z = lo[0]; z <= hi[0]; ++z)
                for (int y = lo[1]; y <= hi[1]; ++y)
                    for (int x = lo[2]; x <= hi[2]; ++x) {
                        const std::size_t cell = (std::size_t(z) * g + y) * g + x;
                        if (pass == 0) ++count[cell];
                        else cellItems_[cellStart_[cell] + count[cell]++] = int32_t(s);
                    }
        }
    }

    nodes_.reserve(2 * order_.size() / 8 + 16);
    build(0, int(order_.size()));
}

int Scattered::build(int lo, int hi) {
    const int id = int(nodes_.size());
    nodes_.push_back({lo, hi, -1, -1, -1, 0});
    if (hi - lo <= 8) return id;
    double mn[3] = {INFINITY, INFINITY, INFINITY}, mx[3] = {-INFINITY, -INFINITY, -INFINITY};
    for (int i = lo; i < hi; ++i)
        for (int a = 0; a < 3; ++a) { mn[a] = std::min(mn[a], points_[3 * order_[i] + a]); mx[a] = std::max(mx[a], points_[3 * order_[i] + a]); }
    int axis = 0;
    for (int a = 1; a < 3; ++a) if (mx[a] - mn[a] > mx[axis] - mn[axis]) axis = a;
    const int mid = (lo + hi) / 2;
    std::nth_element(order_.begin() + lo, order_.begin() + mid, order_.begin() + hi,
                     [&](int a, int b) { return points_[3 * a + axis] < points_[3 * b + axis]; });
    const double split = points_[3 * order_[mid] + axis];
    const int left = build(lo, mid), right = build(mid, hi);
    nodes_[id].axis = axis; nodes_[id].split = split; nodes_[id].left = left; nodes_[id].right = right;
    return id;
}

void Scattered::nearestIn(int node, const double x[3], int &best, double &bestD) const {
    const Node &nd = nodes_[node];
    if (nd.axis < 0) {
        for (int i = nd.lo; i < nd.hi; ++i) {
            const int p = order_[i];
            const double dx = x[0] - points_[3 * p], dy = x[1] - points_[3 * p + 1], dz = x[2] - points_[3 * p + 2];
            const double d = dx * dx + dy * dy + dz * dz;
            if (d < bestD || (d == bestD && p < best)) { bestD = d; best = p; }
        }
        return;
    }
    const double diff = x[nd.axis] - nd.split;
    const int nearSide = diff < 0 ? nd.left : nd.right, farSide = diff < 0 ? nd.right : nd.left;
    nearestIn(nearSide, x, best, bestD);
    if (diff * diff <= bestD) nearestIn(farSide, x, best, bestD);
}

int Scattered::nearest(const double x[3]) const {
    int best = -1; double bestD = INFINITY;
    nearestIn(0, x, best, bestD);
    return best;
}

bool Scattered::inside(int s, const double x[3], double c[4]) const {
    const double *t = &transform_[12 * std::size_t(s)];
    c[3] = 1.0;
    for (int i = 0; i < 3; ++i) {
        c[i] = 0;
        for (int j = 0; j < 3; ++j) c[i] = std::fma(t[3 * i + j], x[j] - t[9 + j], c[i]);
        c[3] -= c[i];
        if (!(-Eps <= c[i] && c[i] <= 1 + Eps)) return false;
    }
    return -Eps <= c[3] && c[3] <= 1 + Eps;
}

// _find_simplex_bruteforce para un símplice degenerado: se prueba en sus vecinos con margen eps_broad hacia él.
bool Scattered::insideDegenerate(int s, const double x[3], double c[4], int &found) const {
    for (int k = 0; k < 4; ++k) {
        const int nb = neighbors_[4 * std::size_t(s) + k];
        if (nb < 0 || !valid_[nb]) continue;
        const double *t = &transform_[12 * std::size_t(nb)];
        c[3] = 1.0;
        for (int i = 0; i < 3; ++i) {
            c[i] = 0;
            for (int j = 0; j < 3; ++j) c[i] = std::fma(t[3 * i + j], x[j] - t[9 + j], c[i]);
            c[3] -= c[i];
        }
        bool in = true;
        for (int q = 0; q < 4 && in; ++q) {
            const double lo = neighbors_[4 * std::size_t(nb) + q] == s ? -EpsBroad : -Eps;
            in = lo <= c[q] && c[q] <= 1 + Eps;
        }
        if (in) { found = nb; return true; }
    }
    return false;
}

int Scattered::locate(const double x[3], double c[4], int &hint) const {
    if (hint >= 0 && hint < int(valid_.size()) && valid_[hint] && inside(hint, x, c)) return hint;
    int idx[3];
    for (int a = 0; a < 3; ++a) idx[a] = std::clamp(int(std::floor((x[a] - lo_[a]) / cell_[a])), 0, grid_[a] - 1);
    const std::size_t cell = (std::size_t(idx[0]) * grid_[1] + idx[1]) * grid_[2] + idx[2];
    for (int32_t i = cellStart_[cell]; i < cellStart_[cell + 1]; ++i) {
        const int s = cellItems_[i];
        if (valid_[s] && inside(s, x, c)) return hint = s;
    }
    for (int32_t i = cellStart_[cell]; i < cellStart_[cell + 1]; ++i) {
        const int s = cellItems_[i];
        int found = -1;
        if (!valid_[s] && insideDegenerate(s, x, c, found)) return hint = found;
    }
    return -1;
}

bool Scattered::outside(const double x[3]) const {
    for (int a = 0; a < 3; ++a) if (x[a] < lo_[a] - Eps || x[a] > hi_[a] + Eps) return true;
    double c[4]; int hint = -1;
    return locate(x, c, hint) < 0;
}

void Scattered::eval(const double x[3], double out[3], int &hint) const {
    if (!(std::isfinite(x[0]) && std::isfinite(x[1]) && std::isfinite(x[2]))) {
        out[0] = out[1] = out[2] = NAN;
        return;
    }
    bool in = true;
    for (int a = 0; a < 3; ++a) if (x[a] < lo_[a] - Eps || x[a] > hi_[a] + Eps) in = false;
    double c[4];
    const int s = in ? locate(x, c, hint) : -1;
    if (s < 0) {
        const int p = nearest(x);
        for (int j = 0; j < 3; ++j) out[j] = values_[3 * p + j];
        return;
    }
    for (int j = 0; j < 3; ++j) out[j] = 0;
    for (int k = 0; k < 4; ++k) {
        const double *v = &values_[3 * std::size_t(simplices_[4 * std::size_t(s) + k])];
        for (int j = 0; j < 3; ++j) out[j] = std::fma(c[k], v[j], out[j]);
    }
}

void Scattered::input(const double rgb[3], double t[3]) const {
    for (int i = 0; i < 3; ++i) {
        const double raw = std::fma(rgb[2], matrix_[3 * i + 2], std::fma(rgb[1], matrix_[3 * i + 1], rgb[0] * matrix_[3 * i]));   // matmul de Accelerate
        t[i] = warp_ ? std::sqrt(std::max(raw + c_, 0.0)) - sqrtC_ : raw;
    }
}

}  // namespace cdng
