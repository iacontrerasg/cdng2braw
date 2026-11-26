#pragma once
// EXP-005 — selector de demosaic LibRaw, solo Pocket OG.
// Ausente ≡ AHD (user_qual=3), bit-idéntico al default histórico. No altera WB/exposición/clip/matrices.
#include "common.hpp"
#include "signal.hpp"

#include <string>

namespace cdng {

struct OgDebayerConfig {
    std::string name = "ahd";
    int userQual = 3;
    bool fromEnv = false;
};

OgDebayerConfig ogDebayerFromEnv();
const char *ogDebayerCanonicalName(int userQual);
void ogDebayerRejectIfNotOg(Route route);

}  // namespace cdng
