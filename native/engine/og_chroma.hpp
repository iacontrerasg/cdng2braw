#pragma once
// EXP-004 — subsample de croma Pocket OG (C0–C4). Default C0 ≡ imagePlanes histórico.
// Solo Cb/Cr; Y no se toca. Flags: CDNG_OG_CHROMA_MODE / KERNEL / PHASE.
#include "common.hpp"

#include <string>
#include <vector>

namespace cdng {

enum class OgChromaMode { C0, C1, C2, C3, C4 };
enum class OgChromaKernel { None, B3, B5, B7 };
enum class OgChromaPhase { Center, Even, Odd };

struct OgChromaConfig {
    OgChromaMode mode = OgChromaMode::C0;
    OgChromaKernel kernel = OgChromaKernel::None;
    OgChromaPhase phase = OgChromaPhase::Center;
};

OgChromaConfig ogChromaConfigFromEnv();
const char *ogChromaModeName(OgChromaMode m);
const char *ogChromaKernelName(OgChromaKernel k);
const char *ogChromaPhaseName(OgChromaPhase p);

// Rellena planes.cb/cr (w/2 × h/2) desde Cb/Cr a resolución completa. No escribe Y.
void ogChromaDownsample(const float *cb_full, const float *cr_full, int w, int h,
                        Planes &planes, const OgChromaConfig &cfg);

}  // namespace cdng
