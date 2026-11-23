// Reducción de ruido intra-frame: port del prototipo en Python (filtro de Wiener en DCT 8×8 por bloques
// solapados, ruido medido en cada frame). Reproduce la aritmética de NumPy en este Mac: float32 donde el Python usa
// float32, productos de matrices como cadenas FMA en orden (lo que hace Accelerate en np.einsum/np.matmul),
// sumas por parejas de NumPy (pairwise_sum), np.quantile lineal y np.median exactos.
#include "signal_internal.hpp"

#if defined(__APPLE__)
#include <Accelerate/Accelerate.h>
#endif

#include <algorithm>
#include <atomic>
#include <cmath>
#include <cstring>
#include <mutex>
#include <thread>
#include <vector>

namespace cdng {

namespace {

// Hilos dentro de un fotograma. 1 por omisión: al convertir ya hay un fotograma por proceso y repartir además
// por dentro solo haría competir a los hilos entre sí. La vista previa, que es un solo fotograma, lo sube.
std::atomic<int> NoiseThreads{1};

// Reparte [0, count) en tramos contiguos, uno por hilo, y espera. Solo vale para bucles cuyas iteraciones
// escriben en sitios disjuntos: así el resultado es idéntico al del bucle secuencial, bit a bit, porque no
// cambia el orden de ninguna suma. Las fases donde el orden de acumulación importa se quedan en serie.
template <class Fn>
void parallelRange(std::size_t count, Fn &&body) {
    const std::size_t threads = std::min<std::size_t>(std::max(1, NoiseThreads.load()), count);
    if (threads <= 1 || count == 0) { body(std::size_t(0), count); return; }
    std::vector<std::thread> pool;
    pool.reserve(threads - 1);
    const std::size_t chunk = (count + threads - 1) / threads;
    for (std::size_t t = 1; t < threads; ++t) {
        const std::size_t from = std::min(count, t * chunk), to = std::min(count, from + chunk);
        if (from < to) pool.emplace_back([&body, from, to] { body(from, to); });
    }
    body(std::size_t(0), std::min(count, chunk));   // este hilo hace el primer tramo
    for (auto &thread : pool) thread.join();
}

constexpr int N8 = 8;
constexpr double Select = 0.2;
constexpr int Bands = 8;
constexpr double PilotT = 2.7;

const float Dct[64] = {   // DCT 8×8 (float32)
    0x1.6a09e6p-2f, 0x1.6a09e6p-2f, 0x1.6a09e6p-2f, 0x1.6a09e6p-2f, 0x1.6a09e6p-2f, 0x1.6a09e6p-2f, 0x1.6a09e6p-2f, 0x1.6a09e6p-2f,
    0x1.f6297cp-2f, 0x1.a9b662p-2f, 0x1.1c73b4p-2f, 0x1.8f8b84p-4f, -0x1.8f8b84p-4f, -0x1.1c73b4p-2f, -0x1.a9b662p-2f, -0x1.f6297cp-2f,
    0x1.d906bcp-2f, 0x1.87de2ap-3f, -0x1.87de2ap-3f, -0x1.d906bcp-2f, -0x1.d906bcp-2f, -0x1.87de2ap-3f, 0x1.87de2ap-3f, 0x1.d906bcp-2f,
    0x1.a9b662p-2f, -0x1.8f8b84p-4f, -0x1.f6297cp-2f, -0x1.1c73b4p-2f, 0x1.1c73b4p-2f, 0x1.f6297cp-2f, 0x1.8f8b84p-4f, -0x1.a9b662p-2f,
    0x1.6a09e6p-2f, -0x1.6a09e6p-2f, -0x1.6a09e6p-2f, 0x1.6a09e6p-2f, 0x1.6a09e6p-2f, -0x1.6a09e6p-2f, -0x1.6a09e6p-2f, 0x1.6a09e6p-2f,
    0x1.1c73b4p-2f, -0x1.f6297cp-2f, 0x1.8f8b84p-4f, 0x1.a9b662p-2f, -0x1.a9b662p-2f, -0x1.8f8b84p-4f, 0x1.f6297cp-2f, -0x1.1c73b4p-2f,
    0x1.87de2ap-3f, -0x1.d906bcp-2f, 0x1.d906bcp-2f, -0x1.87de2ap-3f, -0x1.87de2ap-3f, 0x1.d906bcp-2f, -0x1.d906bcp-2f, 0x1.87de2ap-3f,
    0x1.8f8b84p-4f, -0x1.1c73b4p-2f, 0x1.a9b662p-2f, -0x1.f6297cp-2f, 0x1.f6297cp-2f, -0x1.a9b662p-2f, 0x1.1c73b4p-2f, -0x1.8f8b84p-4f};

const float Window[64] = {   // Ventana: kaiser(8, 2) ⊗ kaiser(8, 2) (float32)
    0x1.8a1c58p-3f, 0x1.320dcep-2f, 0x1.89e164p-2f, 0x1.bad5fep-2f, 0x1.bad5fep-2f, 0x1.89e164p-2f, 0x1.320dcep-2f, 0x1.8a1c58p-3f,
    0x1.320dcep-2f, 0x1.db57f8p-2f, 0x1.31e006p-1f, 0x1.57e472p-1f, 0x1.57e472p-1f, 0x1.31e006p-1f, 0x1.db57f8p-2f, 0x1.320dcep-2f,
    0x1.89e164p-2f, 0x1.31e006p-1f, 0x1.89a67ap-1f, 0x1.ba93c0p-1f, 0x1.ba93c0p-1f, 0x1.89a67ap-1f, 0x1.31e006p-1f, 0x1.89e164p-2f,
    0x1.bad5fep-2f, 0x1.57e472p-1f, 0x1.ba93c0p-1f, 0x1.f195ccp-1f, 0x1.f195ccp-1f, 0x1.ba93c0p-1f, 0x1.57e472p-1f, 0x1.bad5fep-2f,
    0x1.bad5fep-2f, 0x1.57e472p-1f, 0x1.ba93c0p-1f, 0x1.f195ccp-1f, 0x1.f195ccp-1f, 0x1.ba93c0p-1f, 0x1.57e472p-1f, 0x1.bad5fep-2f,
    0x1.89e164p-2f, 0x1.31e006p-1f, 0x1.89a67ap-1f, 0x1.ba93c0p-1f, 0x1.ba93c0p-1f, 0x1.89a67ap-1f, 0x1.31e006p-1f, 0x1.89e164p-2f,
    0x1.320dcep-2f, 0x1.db57f8p-2f, 0x1.31e006p-1f, 0x1.57e472p-1f, 0x1.57e472p-1f, 0x1.31e006p-1f, 0x1.db57f8p-2f, 0x1.320dcep-2f,
    0x1.8a1c58p-3f, 0x1.320dcep-2f, 0x1.89e164p-2f, 0x1.bad5fep-2f, 0x1.bad5fep-2f, 0x1.89e164p-2f, 0x1.320dcep-2f, 0x1.8a1c58p-3f};

// Constantes de la medida del ruido (D2, DETREND, KEEP, MEASURABLE) y las muestras normales fijas.
struct Constants {
    double d2[64 * 64], detrend[64 * 64], keep[64];
    bool measurable[64];
    int measured[64];   // índices medibles, en orden
    int nMeasured = 0;
    std::vector<double> normals;   // 6000 × 64
};

const Constants &constants() {
    static const Constants c = [] {
        Constants k;
        for (int i = 0; i < 8; ++i)
            for (int kk = 0; kk < 8; ++kk)
                for (int j = 0; j < 8; ++j)
                    for (int l = 0; l < 8; ++l)
                        k.d2[(i * 8 + kk) * 64 + j * 8 + l] = double(Dct[i * 8 + j]) * double(Dct[kk * 8 + l]);
        // DETREND = I − A·solve(AᵀA, Aᵀ); A = [1, x − 3,5, y − 3,5] y AᵀA = diag(64, 336, 336)
        double a[64][3];
        for (int p = 0; p < 64; ++p) { a[p][0] = 1; a[p][1] = p % 8 - 3.5; a[p][2] = p / 8 - 3.5; }
        const double d[3] = {64, 336, 336};
        for (int p = 0; p < 64; ++p)
            for (int q = 0; q < 64; ++q) {
                double s = 0;
                for (int r = 0; r < 3; ++r) s = std::fma(a[p][r], a[q][r] / d[r], s);
                k.detrend[p * 64 + q] = (p == q ? 1.0 : 0.0) - s;
            }
        for (int i = 0; i < 64; ++i) {
            double s = 0;
            for (int j = 0; j < 64; ++j)
                for (int q = 0; q < 64; ++q) s += k.d2[i * 64 + j] * k.detrend[j * 64 + q] * k.d2[i * 64 + q];
            k.keep[i] = s;
            k.measurable[i] = s > 0.5;
            if (k.measurable[i]) k.measured[k.nMeasured++] = i;
        }
        const auto bytes = readBytes(resourceRoot() / "model/nr_normals_6000x64.f32");
        if (bytes.size() != 6000 * 64 * 4) throw Error("The embedded calibration is damaged: nr_normals_6000x64.f32");
        std::vector<float> f(6000 * 64);
        std::memcpy(f.data(), bytes.data(), bytes.size());
        k.normals.assign(f.begin(), f.end());
        return k;
    }();
    return c;
}

// numpy pairwise_sum (float64 y float32): < 8 elementos en secuencia, ≤ 128 con 8 acumuladores, si no por mitades.
template <class T> T pairwise(const T *a, std::size_t n) {
    if (n < 8) {
        T res = 0;
        for (std::size_t i = 0; i < n; ++i) res += a[i];
        return res;
    }
    if (n <= 128) {
        T r[8];
        for (int j = 0; j < 8; ++j) r[j] = a[j];
        std::size_t i = 8;
        for (; i < n - n % 8; i += 8)
            for (int j = 0; j < 8; ++j) r[j] += a[i + j];
        T res = ((r[0] + r[1]) + (r[2] + r[3])) + ((r[4] + r[5]) + (r[6] + r[7]));
        for (; i < n; ++i) res += a[i];
        return res;
    }
    std::size_t n2 = n / 2;
    n2 -= n2 % 8;
    return pairwise(a, n2) + pairwise(a + n2, n - n2);
}

// mean(axis=(…, 8, 8)) de un bloque float32: cada fila por parejas y las filas en secuencia.
float blockMean(const float *p, std::size_t stride) {
    float s = 0;
    for (int u = 0; u < 8; ++u) {
        const float *r = p + u * stride;
        s += ((r[0] + r[1]) + (r[2] + r[3])) + ((r[4] + r[5]) + (r[6] + r[7]));
    }
    return s / 64.0f;
}

// np.quantile(..., method='linear') de valores ya ordenados; q y el resultado en float64.
template <class T> double quantileSorted(const std::vector<T> &sorted, double q) {
    const std::size_t n = sorted.size();
    const double virt = double(n - 1) * q;
    std::size_t prev, next;
    if (virt >= double(n - 1)) prev = next = n - 1;
    else if (virt < 0) prev = next = 0;
    else { prev = std::size_t(std::floor(virt)); next = prev + 1; }
    const double gamma = virt - std::floor(virt);
    const T a = sorted[prev], b = sorted[next];
    const T diff = T(b - a);
    return gamma >= 0.5 ? double(b) - double(diff) * (1 - gamma) : double(a) + double(diff) * gamma;
}

// np.median de un vector (se ordena); media de los dos centrales en el tipo del vector.
template <class T> T median(std::vector<T> &v) {
    const std::size_t n = v.size(), h = n / 2;
    std::nth_element(v.begin(), v.begin() + h, v.end());
    if (n % 2) return v[h];
    const T upper = v[h];
    const T lower = *std::max_element(v.begin(), v.begin() + h);
    return T(lower + upper) / T(2);
}

// rows × 64 por una matriz 64 × 64 (o su traspuesta), como el @ de NumPy. En macOS es la misma llamada a Accelerate
// que hace NumPy, así que el resultado es idéntico bit a bit y mucho más rápido que el bucle; en el resto, cadenas
// FMA en el orden de Accelerate (la paridad allí es la de los criterios del README, no bit a bit).
void matmul64(const double *a, std::size_t rows, const double *b, bool transposeB, double *out) {
#if defined(__APPLE__)
    cblas_dgemm(CblasRowMajor, CblasNoTrans, transposeB ? CblasTrans : CblasNoTrans, int(rows), 64, 64, 1.0, a, 64, b,
                64, 0.0, out, 64);
#else
    // Por filas: cada una escribe su propio tramo de `out` y su cadena FMA no cambia, así que el reparto entre
    // hilos da exactamente los mismos bits que el bucle secuencial.
    parallelRange(rows, [&](std::size_t from, std::size_t to) {
        for (std::size_t r = from; r < to; ++r)
            for (int q = 0; q < 64; ++q) {
                double s = 0;
                for (int i = 0; i < 64; ++i) s = std::fma(a[r * 64 + i], transposeB ? b[q * 64 + i] : b[i * 64 + q], s);
                out[r * 64 + q] = s;
            }
    });
#endif
}

// (B @ DETREND.T) @ D2.T para `rows` bloques de 64 valores (_detrended_coefficients).
void detrendedBatch(const double *blocks, std::size_t rows, double *coeffs) {
    const auto &k = constants();
    std::vector<double> tmp(rows * 64);
    matmul64(blocks, rows, k.detrend, true, tmp.data());
    matmul64(tmp.data(), rows, k.d2, true, coeffs);
}

double measuredEnergy(const double *c) {
    const auto &k = constants();
    double sq[64];
    for (int i = 0; i < k.nMeasured; ++i) { const double v = c[k.measured[i]]; sq[i] = v * v; }
    return pairwise(sq, std::size_t(k.nMeasured));
}

// Mediana por coeficiente de c² en los bloques con energía ≤ cuantil SELECT.
void selectedMedian(const std::vector<double> &coeffs, std::size_t count, double out[64]) {
    std::vector<double> energy(count);
    for (std::size_t b = 0; b < count; ++b) energy[b] = measuredEnergy(&coeffs[64 * b]);
    std::vector<double> sorted = energy;
    std::sort(sorted.begin(), sorted.end());
    const double limit = quantileSorted(sorted, Select);
    std::vector<std::size_t> chosen;
    for (std::size_t b = 0; b < count; ++b) if (energy[b] <= limit) chosen.push_back(b);
    std::vector<double> column(chosen.size());
    for (int i = 0; i < 64; ++i) {
        for (std::size_t r = 0; r < chosen.size(); ++r) { const double v = coeffs[64 * chosen[r] + i]; column[r] = v * v; }
        out[i] = median(column);
    }
}

void fillUnmeasurable(double v[64]) {
    const auto &k = constants();
    for (int i = 0; i < 8; ++i)
        for (int j = 0; j < 8; ++j) {
            if (k.measurable[i * 8 + j]) continue;
            double near[9]; int n = 0;
            for (int a = std::max(0, i - 1); a < std::min(8, i + 2); ++a)
                for (int b = std::max(0, j - 1); b < std::min(8, j + 2); ++b)
                    if (k.measurable[a * 8 + b]) near[n++] = v[a * 8 + b];
            v[i * 8 + j] = pairwise(near, std::size_t(n)) / n;
        }
}

void selectionBias(const double var[64], double out[64]) {
    const auto &k = constants();
    double measured[64];
    for (int i = 0; i < k.nMeasured; ++i) measured[i] = var[k.measured[i]];
    const double mean = pairwise(measured, std::size_t(k.nMeasured)) / k.nMeasured;
    double rel[64], scale[64];
    for (int i = 0; i < 64; ++i) { rel[i] = var[i] / mean; scale[i] = std::sqrt(rel[i]); }
    // (normales × √rel) @ D2: coeficientes simulados → píxeles; luego sin plano y de vuelta a coeficientes.
    std::vector<double> x(6000 * 64), sim(6000 * 64), coeffs(6000 * 64);
    for (int r = 0; r < 6000; ++r)
        for (int i = 0; i < 64; ++i) x[64 * r + i] = k.normals[64 * r + i] * scale[i];
    matmul64(x.data(), 6000, k.d2, false, sim.data());
    detrendedBatch(sim.data(), 6000, coeffs.data());
    selectedMedian(coeffs, 6000, out);
    for (int i = 0; i < 64; ++i) out[i] /= std::max(rel[i], 1e-12);
}

// _noise_for: varianza por coeficiente para un bloque de brillo `level`.
void noiseFor(float level, const NoiseModel &m, float out[64]) {
    const std::size_t n = m.centers.size();
    if (n == 1) { std::memcpy(out, m.variances.data(), 64 * sizeof(float)); return; }
    std::size_t idx = std::lower_bound(m.centers.begin(), m.centers.end(), level) - m.centers.begin();
    idx = std::clamp<std::size_t>(idx, 1, n - 1);
    const float lo = m.centers[idx - 1], hi = m.centers[idx];
    const float f = std::clamp((level - lo) / std::max(hi - lo, 1e-6f), 0.0f, 1.0f);
    const float *a = &m.variances[64 * (idx - 1)], *b = &m.variances[64 * idx];
    for (int i = 0; i < 64; ++i) out[i] = a[i] * (1 - f) + b[i] * f;
}

// einsum('ij,...jk,lk->...il', DCT, B, DCT) y la inversa 'ji,...jk,kl->...il', como dos productos con FMA.
void dct(const float *b, std::size_t stride, float c[64]) {
    float t[64];
    for (int i = 0; i < 8; ++i)
        for (int k = 0; k < 8; ++k) {
            float s = 0;
            for (int j = 0; j < 8; ++j) s = std::fmaf(b[j * stride + k], Dct[i * 8 + j], s);
            t[i * 8 + k] = s;
        }
    for (int i = 0; i < 8; ++i)
        for (int l = 0; l < 8; ++l) {
            float s = 0;
            for (int k = 0; k < 8; ++k) s = std::fmaf(t[i * 8 + k], Dct[l * 8 + k], s);
            c[i * 8 + l] = s;
        }
}

void idct(const float c[64], float out[64]) {
    float t[64];
    for (int i = 0; i < 8; ++i)
        for (int k = 0; k < 8; ++k) {
            float s = 0;
            for (int j = 0; j < 8; ++j) s = std::fmaf(c[j * 8 + k], Dct[j * 8 + i], s);
            t[i * 8 + k] = s;
        }
    for (int i = 0; i < 8; ++i)
        for (int l = 0; l < 8; ++l) {
            float s = 0;
            for (int k = 0; k < 8; ++k) s = std::fmaf(t[i * 8 + k], Dct[k * 8 + l], s);
            out[i * 8 + l] = s;
        }
}

// np.pad(P, 8, mode='reflect')
std::vector<float> reflectPad(const float *p, int w, int h, int pad) {
    if (w <= pad || h <= pad) throw Error("Plane too small for noise reduction");
    const int W = w + 2 * pad, H = h + 2 * pad;
    auto reflect = [](int i, int n) { if (i < 0) return -i; if (i >= n) return 2 * (n - 1) - i; return i; };
    std::vector<float> q(std::size_t(W) * H);
    for (int y = 0; y < H; ++y) {
        const float *row = p + std::size_t(reflect(y - pad, h)) * w;
        for (int x = 0; x < W; ++x) q[std::size_t(y) * W + x] = row[reflect(x - pad, w)];
    }
    return q;
}

void filterPlane(std::vector<float> &plane, int w, int h, double amount, const float *brightness, int stride) {
    if (amount <= 0) return;
    const NoiseModel model = noiseModel(plane.data(), w, h, brightness);
    if (model.centers.empty()) return;
    const int pad = N8, strip = 48;
    const auto Q = reflectPad(plane.data(), w, h, pad);
    const auto L = reflectPad(brightness ? brightness : plane.data(), w, h, pad);
    const int W = w + 2 * pad, H = h + 2 * pad;
    std::vector<float> acc(Q.size(), 0.0f), wsum(Q.size(), 0.0f);
    const int By = (H - N8) / stride + 1, Bx = (W - N8) / stride + 1;
    const float amountF = float(amount), pilot = float(PilotT * PilotT), tiny = float(1e-12);
    std::vector<float> R(std::size_t(strip) * Bx * 64);
    for (int b0 = 0; b0 < By; b0 += strip) {
        const int b1 = std::min(By, b0 + strip), y0 = b0 * stride;
        // Cada bloque se filtra por su cuenta y escribe su propio hueco de 64 valores en R: no hay orden que
        // preservar aquí, así que va en paralelo. La agregación de abajo sí lo tiene y se queda en serie.
        parallelRange(std::size_t(b1 - b0), [&](std::size_t from, std::size_t to) {
            for (int by = b0 + int(from); by < b0 + int(to); ++by)
                for (int bx = 0; bx < Bx; ++bx) {
                    const std::size_t at = std::size_t(by * stride) * W + std::size_t(bx) * stride;
                    float c[64], noise[64], g[64];
                    dct(&Q[at], std::size_t(W), c);
                    noiseFor(blockMean(&L[at], std::size_t(W)), model, noise);
                    for (int i = 0; i < 64; ++i) {
                        const float p = c[i] * c[i] > pilot * noise[i] || i == 0 ? c[i] : 0.0f;
                        const float s = p * p;
                        g[i] = i == 0 ? 1.0f : s / (s + noise[i] + tiny);
                        g[i] = 1.0f - amountF * (1.0f - g[i]);
                        c[i] = c[i] * g[i];
                    }
                    float *r = &R[(std::size_t(by - b0) * Bx + bx) * 64];
                    idct(c, r);
                    for (int i = 0; i < 64; ++i) r[i] = r[i] * Window[i];
                }
        });
        // Misma agregación que el Python: para cada (u, v), todos los bloques de la franja.
        for (int u = 0; u < N8; ++u)
            for (int v = 0; v < N8; ++v)
                for (int by = b0; by < b1; ++by) {
                    const std::size_t row = std::size_t(y0 + u + (by - b0) * stride) * W;
                    for (int bx = 0; bx < Bx; ++bx) {
                        const std::size_t at = row + std::size_t(v + bx * stride);
                        acc[at] += R[(std::size_t(by - b0) * Bx + bx) * 64 + u * 8 + v];
                        wsum[at] += Window[u * 8 + v];
                    }
                }
    }
    for (int y = 0; y < h; ++y)
        for (int x = 0; x < w; ++x) {
            const std::size_t at = std::size_t(y + pad) * W + x + pad;
            plane[std::size_t(y) * w + x] = acc[at] / std::max(wsum[at], 1e-12f);
        }
}

}  // namespace

NoiseModel noiseModel(const float *plane, int width, int height, const float *brightness) {
    const auto &k = constants();
    const int H = height / N8 * N8, W = width / N8 * N8, nby = H / N8, nbx = W / N8;
    const std::size_t nb = std::size_t(nby) * nbx;
    NoiseModel out;
    if (nb == 0) return out;
    std::vector<double> blocks(nb * 64), coeffs(nb * 64);
    std::vector<float> level(nb);
    const float *bright = brightness ? brightness : plane;
    for (int by = 0; by < nby; ++by)
        for (int bx = 0; bx < nbx; ++bx) {
            const std::size_t b = std::size_t(by) * nbx + bx, at = std::size_t(by * N8) * width + bx * N8;
            for (int u = 0; u < 8; ++u)
                for (int v = 0; v < 8; ++v) blocks[64 * b + u * 8 + v] = plane[at + std::size_t(u) * width + v];
            level[b] = blockMean(bright + at, std::size_t(width));
        }
    detrendedBatch(blocks.data(), nb, coeffs.data());
    const int bands = int(std::clamp<std::size_t>(nb / 400, 1, Bands));
    std::vector<float> sortedLevel = level;
    std::sort(sortedLevel.begin(), sortedLevel.end());
    std::vector<double> edges(bands + 1);
    const double step = 1.0 / bands;
    for (int i = 0; i <= bands; ++i) edges[i] = quantileSorted(sortedLevel, i == bands ? 1.0 : i * step);
    std::vector<std::vector<float>> variances;
    std::vector<double> bandCoeffs;
    std::vector<float> bandLevel;
    for (int band = 0; band < bands; ++band) {
        const double lo = edges[band], hi = edges[band + 1];
        bandCoeffs.clear(); bandLevel.clear();
        for (std::size_t b = 0; b < nb; ++b)
            if (level[b] >= lo && level[b] <= hi) {
                bandCoeffs.insert(bandCoeffs.end(), &coeffs[64 * b], &coeffs[64 * b] + 64);
                bandLevel.push_back(level[b]);
            }
        if (bandLevel.size() < std::min<std::size_t>(200, nb)) continue;
        double raw[64], var[64], bias[64];
        selectedMedian(bandCoeffs, bandLevel.size(), raw);
        for (int i = 0; i < 64; ++i) raw[i] = std::max(raw[i], 1e-9);
        for (int i = 0; i < 64; ++i) var[i] = k.measurable[i] ? raw[i] / k.keep[i] : 0.0;
        fillUnmeasurable(var);
        for (int it = 0; it < 2; ++it) {
            selectionBias(var, bias);
            for (int i = 0; i < 64; ++i) var[i] = k.measurable[i] ? raw[i] / std::max(bias[i], 1e-6) : 0.0;
            fillUnmeasurable(var);
        }
        var[0] = 0;
        out.centers.push_back(median(bandLevel));
        variances.emplace_back(var, var + 64);
    }
    const std::size_t n = variances.size();
    out.variances.resize(n * 64);
    for (std::size_t b = 0; b < n; ++b)
        for (int i = 0; i < 64; ++i) {
            if (n >= 3) {   // mediana de la banda y sus vecinas (bordes repetidos)
                float t[3] = {variances[b == 0 ? 0 : b - 1][i], variances[b][i], variances[b + 1 == n ? b : b + 1][i]};
                std::sort(t, t + 3);
                out.variances[64 * b + i] = t[1];
            } else {
                out.variances[64 * b + i] = variances[b][i];
            }
        }
    return out;
}

void setNoiseThreads(int threads) { NoiseThreads.store(std::max(1, threads)); }

void denoisePlanes(Planes &planes, double luma, double chroma, int stride) {
    const int w = planes.width, h = planes.height, hw = w / 2, hh = h / 2;
    if (stride < 1) throw Error("Invalid noise reduction stride");
    std::vector<float> yh;
    if (chroma > 0) {   // brillo de la croma: media 2×2 de la luma sin filtrar
        yh.resize(std::size_t(hw) * hh);
        for (int y = 0; y < hh; ++y)
            for (int x = 0; x < hw; ++x) {
                const float *r0 = &planes.y[std::size_t(2 * y) * w + 2 * x], *r1 = r0 + w;
                yh[std::size_t(y) * hw + x] = ((r0[0] + r0[1]) + (r1[0] + r1[1])) / 4.0f;
            }
    }
    filterPlane(planes.y, w, h, luma, nullptr, stride);
    if (chroma <= 0) return;
    filterPlane(planes.cb, hw, hh, chroma, yh.data(), stride);
    filterPlane(planes.cr, hw, hh, chroma, yh.data(), stride);
}

}  // namespace cdng
