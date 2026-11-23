#pragma once
// Tipos comunes del motor de conversión. Sin Qt ni Python: C++17 y la biblioteca estándar.
// Cada bloque (dng, signal, codec, container, worker) incluye este fichero; ver native/engine/README.md.
#include <cstdint>
#include <filesystem>
#include <stdexcept>
#include <string>
#include <vector>

namespace cdng {

// Error con un mensaje en inglés pensado para el usuario. El proceso de trabajo lo traduce con la misma tabla que
// usa la app (app/i18n/es.json), así que los textos deben coincidir con los de esa tabla.
struct Error : std::runtime_error {
    using std::runtime_error::runtime_error;
};

// Imagen RGB lineal entrelazada (R, G, B por píxel, fila a fila). Valores normalizados como en Python:
// cámara sin balance, (cuenta − negro) / (blanco − negro), sin recortar (puede haber negativos cerca del negro).
struct RgbImage {
    int width = 0, height = 0;
    std::vector<float> data;   // width × height × 3
    RgbImage() = default;
    RgbImage(int w, int h) : width(w), height(h), data(std::size_t(w) * h * 3) {}
    float *px(int x, int y) { return data.data() + (std::size_t(y) * width + x) * 3; }
    const float *px(int x, int y) const { return data.data() + (std::size_t(y) * width + x) * 3; }
};

// Planos en el dominio del decodificador, antes del lienzo: Y de width × height y Cb/Cr de (width/2) × (height/2),
// en coma flotante (Y 0…4095, Cb/Cr con signo). Es lo que en Python devuelven image_planes / develop.
struct Planes {
    int width = 0, height = 0;
    std::vector<float> y, cb, cr;
    double reduction = 1;   // píxeles de la fuente por píxel de salida (> 1 si se ha reducido): ajusta el enfoque
    Planes() = default;
    Planes(int w, int h) : width(w), height(h), y(std::size_t(w) * h), cb(std::size_t(w / 2) * (h / 2)), cr(cb.size()) {}
};

// Planos del lienzo almacenado, ya redondeados: Y (hs × ws) en 0…4095 y Cb/Cr (hs/2 × ws/2) en −4096…4095.
// Es la entrada del códec (canvas_planes en Python).
struct CanvasPlanes {
    int ws = 0, hs = 0;
    std::vector<int16_t> y, cb, cr;
};

// Cadencia como fracción reducida (24/1, 24000/1001…).
struct Rate {
    int num = 24, den = 1;
};

// Carpeta que contiene model/ (los mismos ficheros que lista tools/stage_converter.py). Orden: variable de
// entorno CDNG_RESOURCES; si no, junto al ejecutable (../Resources en el .app, o la raíz del repositorio en
// desarrollo). Lo implementa el proceso de trabajo; los bloques reciben rutas, no la buscan.
std::filesystem::path resourceRoot();

std::vector<uint8_t> readFile(const std::filesystem::path &path);

}  // namespace cdng
