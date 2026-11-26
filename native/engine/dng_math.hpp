#pragma once
// Álgebra 3×3 del bloque A con la misma aritmética que numpy: producto con la suma en orden (a0b0 + a1b1) + a2b2 e
// inversa/solución por LU con pivoteo parcial (lo que hace LAPACK dgesv dentro de np.linalg.inv/solve).
#include "dng.hpp"

#include <cmath>
#include <utility>

namespace cdng::math {

inline constexpr Mat3 XyzTo709{3.2406, -1.5372, -0.4986, -0.9689, 1.8758, 0.0415, 0.0557, -0.2040, 1.0570};
// Inverse of tools/fidelity/metrics.py P3_TO_XYZ (DCI-P3 D65). Same LU as inv().
inline constexpr Mat3 XyzToP3{2.493496912315877, -0.9313836180667149, -0.4027107844233256,
                              -0.8294889697233035, 1.762664060254474, 0.02362468583965987,
                              0.03584583028346169, -0.07617238933474427, 0.9568845240077503};
inline constexpr Mat3 Bradford{0.8951, 0.2664, -0.1614, -0.7502, 1.7135, 0.0367, 0.0389, -0.0685, 1.0296};
inline constexpr Vec3 D65{0.95047, 1.0, 1.08883};

inline Vec3 mul(const Mat3 &a, const Vec3 &v) {
    Vec3 r;
    for (int i = 0; i < 3; ++i) r[i] = a[i * 3] * v[0] + a[i * 3 + 1] * v[1] + a[i * 3 + 2] * v[2];
    return r;
}

inline Mat3 mul(const Mat3 &a, const Mat3 &b) {
    Mat3 r;
    for (int i = 0; i < 3; ++i)
        for (int j = 0; j < 3; ++j) r[i * 3 + j] = a[i * 3] * b[j] + a[i * 3 + 1] * b[3 + j] + a[i * 3 + 2] * b[6 + j];
    return r;
}

// LU con pivoteo parcial (dgetrf) y resolución de A·X = B para nrhs columnas (dgetrs). B fila a fila, 3 × nrhs.
template <int N>
inline std::array<double, 3 * N> luSolve(Mat3 a, std::array<double, 3 * N> b) {
    int piv[3] = {0, 1, 2};
    for (int k = 0; k < 3; ++k) {
        int p = k;
        for (int i = k + 1; i < 3; ++i) if (std::fabs(a[i * 3 + k]) > std::fabs(a[p * 3 + k])) p = i;
        if (a[p * 3 + k] == 0) throw Error("Singular matrix");
        if (p != k) {
            for (int j = 0; j < 3; ++j) std::swap(a[k * 3 + j], a[p * 3 + j]);
            for (int j = 0; j < N; ++j) std::swap(b[k * N + j], b[p * N + j]);
            std::swap(piv[k], piv[p]);
        }
        const double r = 1.0 / a[k * 3 + k];   // dgetf2 escala la columna por el recíproco del pivote
        for (int i = k + 1; i < 3; ++i) {
            a[i * 3 + k] *= r;
            for (int j = k + 1; j < 3; ++j) a[i * 3 + j] -= a[i * 3 + k] * a[k * 3 + j];
        }
    }
    for (int c = 0; c < N; ++c) {
        for (int i = 1; i < 3; ++i)
            for (int k = 0; k < i; ++k) b[i * N + c] -= a[i * 3 + k] * b[k * N + c];
        for (int i = 2; i >= 0; --i) {
            for (int k = i + 1; k < 3; ++k) b[i * N + c] -= a[i * 3 + k] * b[k * N + c];
            b[i * N + c] /= a[i * 3 + i];
        }
    }
    return b;
}

inline Mat3 inv(const Mat3 &a) { return luSolve<3>(a, Mat3{1, 0, 0, 0, 1, 0, 0, 0, 1}); }
inline Vec3 solve(const Mat3 &a, const Vec3 &b) { return luSolve<1>(a, b); }
inline double hypot(double a, double b) { return std::hypot(a, b); }

}  // namespace cdng::math
