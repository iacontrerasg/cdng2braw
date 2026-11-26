#pragma once
// EXP-004 — volcado diagnóstico de etapas (opt-in). No altera el cálculo si CDNG_DUMP_STAGES está ausente.
// CDNG_INJECT_RGB sí sustituye el RGB de cámara: entra en OUTPUT_ENV / identidad de caché.
#include "common.hpp"
#include "dng.hpp"
#include "geometry.hpp"

#include <cstddef>
#include <optional>
#include <string>

namespace cdng {

bool stageDumpEnabled();
void stageDumpSetFrame(int index);
bool stageDumpThisFrame();

// CDNG_INJECT_RGB = BRWD float32 RGB. CDNG_INJECT_AT = camera (defecto) | pre_map.
std::optional<RgbImage> stageInjectRgb();
std::string stageInjectAt();  // "camera" | "pre_map" | ""

void stageDumpBayer(const BayerImage &bayer, const char *note);
void stageDumpRgb(const char *stage, const RgbImage &rgb, const char *domain, const char *note);
void stageDumpYccFull(const char *stage, int w, int h, const float *y, const float *cb, const float *cr,
                      const char *note);
void stageDumpPlanes(const char *stage, const Planes &planes, const char *note);
void stageDumpCanvas(const char *stage, const CanvasPlanes &canvas, const Geometry &g, const char *note);
void stageDumpWriteIndex(int q, int width, int height, const char *chroma_mode,
                         const char *kernel, const char *phase);

}  // namespace cdng
