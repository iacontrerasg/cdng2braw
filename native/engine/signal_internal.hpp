#pragma once
// Piezas internas del bloque B (señal) que comparten signal*.cpp, nr.cpp y las pruebas. No es parte del contrato.
#include "signal.hpp"

#include <array>
#include <string>
#include <vector>

namespace cdng {

// Curva del sensor del perfil.
struct SensorCurve {
    double linearA = 0, linearB = 0, quadX = 0, quadY = 0, quadZ = 0, knee = 0, kneeRaw = 0;
    double encode(double raw) const;
    double decode(double code) const;
};

struct OgModel::Impl {
    // NativeSensorMap
    bool sqrtKind = false;
    Vec3 offset{};            // camera_from_ycc[:, 3] (cero en la variante sqrt)
    Mat3 inverse{};           // inv(code_from_ycc) o inv(camera_from_ycc[:, :3])
    Vec3 scale{1, 1, 1};      // input_channel_scale
    double encodingMax = 4095, blackOffset = 0;
    bool hasCurve = false;
    SensorCurve curve;
    // Corrección para casar con el DNG en Resolve con gestión de color (rcm_tone): en espacio
    // de cámara antes de codificar, una matriz que deja quieto el neutro de captura y la curva por canal
    // T_k(x) = s_k · exp(a + b·ln x + c·ln² x), lineal por debajo de linearBelow y con hombro tanh por encima de shoulder.
    bool hasTone = false;
    double toneA = 0, toneB = 1, toneC = 0, toneShoulder = 1, toneLinearBelow = 1e-3;
    Vec3 toneScale{1, 1, 1};
    Mat3 toneMatrix{1, 0, 0, 0, 1, 0, 0, 0, 1}, toneInverse{1, 0, 0, 0, 1, 0, 0, 0, 1};
    double tone(double x, int channel) const;
    double untone(double y, int channel) const;
    void correct(const double *in, double *out) const;     // matriz y curva
    void uncorrect(const double *in, double *out) const;   // su inversa, para la vista previa
    bool hasCodeFromYcc = false;
    Mat3 codeFromYcc{};       // vista previa (planes_crop_camera, ruta og)
    // source_info
    Mat3 cm1{}, cm2{};
    double k1 = 0, k2 = 0, baselineGain = 1;
    int sourceIso = 0;
    // infer_as_shot: tabla del perfil en mired creciente (filas) × tinte (columnas)
    std::vector<double> mired, tints, red, blue;
};

// infer_as_shot: (kelvin, tint) enteros; lanza Error('AsShotNeutral is outside the measured profile domain').
std::pair<int, int> inferAsShot(const Vec3 &neutral, const OgModel::Impl &profile);

std::string sha256Hex(const std::vector<uint8_t> &data);
std::vector<uint8_t> readBytes(const std::filesystem::path &path);
Mat3 inverse3(const Mat3 &m);

// Interpolación lineal sobre la triangulación de Delaunay de SciPy (LinearNDInterpolator) con el vecino más cercano
// fuera de la envolvente (NearestNDInterpolator).
class Scattered {
public:
    explicit Scattered(const std::filesystem::path &path);
    // x en el dominio del interpolador (ya con warp); hint: último símplice encontrado (por hilo/llamada).
    void eval(const double x[3], double out[3], int &hint) const;
    // Entrada del mapa: matriz (Rec.709 → raw) y warp de PocketMap.
    void input(const double rgb[3], double t[3]) const;
    bool outside(const double x[3]) const;   // fuera de la envolvente (vecino más cercano)

private:
    struct Node { int lo, hi, axis, left, right; double split; };
    bool inside(int s, const double x[3], double c[4]) const;
    bool insideDegenerate(int s, const double x[3], double c[4], int &found) const;
    int locate(const double x[3], double c[4], int &hint) const;
    int nearest(const double x[3]) const;
    void nearestIn(int node, const double x[3], int &best, double &bestD) const;
    int build(int lo, int hi);

    bool warp_ = false;
    double c_ = 0, sqrtC_ = 0;
    Mat3 matrix_{};
    double lo_[3]{}, hi_[3]{};
    std::vector<double> points_, values_, transform_;
    std::vector<int32_t> simplices_, neighbors_;
    std::vector<uint8_t> valid_;
    int grid_[3]{};
    double cell_[3]{};
    std::vector<int32_t> cellStart_, cellItems_;
    std::vector<int32_t> order_;   // permutación del árbol k-d
    std::vector<Node> nodes_;
};

// Partes de la reducción de ruido expuestas para las pruebas.
struct NoiseModel {
    std::vector<float> centers;
    std::vector<float> variances;   // bandas × 64
};
NoiseModel noiseModel(const float *plane, int width, int height, const float *brightness);

}  // namespace cdng
