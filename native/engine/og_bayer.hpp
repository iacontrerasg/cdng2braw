#pragma once
// EXP-005c — reconstrucción lineal del mosaico Pocket OG, sin LibRaw AHD y sin ChannelCodec VA.
// Ausente/0/off ≡ ruta RGB AHD (default, bit-idéntica). 1/on ≡ cameraBayer + interpolación experimental.
#include "dng.hpp"
#include "signal.hpp"

#include <string>

namespace cdng {

struct OgBayerConfig {
    bool enabled = false;
    bool fromEnv = false;
};

OgBayerConfig ogBayerFromEnv();
void ogBayerRejectIfNotOg(Route route);
// RGB cámara lineal del mosaico recortado (DefaultCrop ya aplicado en cameraBayer).
// Verde: cúbico direccional como bayerFields (muestras lineales, no códigos).
// R/B: bilinear de sitios del mismo canal. No es el procesamiento parcial de Blackmagic.
RgbImage bayerReconstructLinear(const BayerImage &bayer);

}  // namespace cdng
