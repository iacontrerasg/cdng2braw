// Datos de la fuente en la ruta og. Fichero aparte porque es lo único del bloque B
// que necesita el lector DNG del bloque A: así el resto se enlaza y se prueba sin él.
#include "signal_internal.hpp"

#include <cmath>

namespace cdng {

namespace {

bool allclose(const Mat3 &a, const Mat3 &b) {
    for (int i = 0; i < 9; ++i)
        if (!(std::fabs(a[i] - b[i]) <= 1e-7)) return false;   // np.allclose(atol=1e-7, rtol=0)
    return true;
}

}  // namespace

OgShot ogSourceInfo(const Dng &dng, const OgModel &model) {
    if (!model.impl) throw Error("Missing calibration");
    const auto &m = *model.impl;
    const DngColor color = dng.color();
    if (!(allclose(color.cm1, m.cm1) && allclose(color.cm2, m.cm2) && color.k1 == m.k1 && color.k2 == m.k2))
        throw Error("source matrices differ from calibrated DNG");
    const auto baseline = dng.scalar(50730);
    if (!baseline || !std::isfinite(*baseline)) throw Error("Invalid source BaselineExposure");
    const double gain = std::pow(2.0, *baseline);
    OgShot shot;
    // Experimental: normalize exposure to the calibrated sensor response.
    // Keep the reference ISO and camera profile unchanged.
    shot.gain = gain / m.baselineGain;
    if (!std::isfinite(shot.gain) || shot.gain <= 0.0)
        throw Error("Invalid source BaselineExposure");
    // Preserve exact unity for the original calibrated input.
    if (std::fabs(gain - m.baselineGain) <= 1e-6) shot.gain = 1.0;
    const auto iso = dng.numeric(34855);
    if (iso && !iso->empty() && (*iso)[0] != 0) {
        shot.iso = int((*iso)[0]);
        if (shot.iso != m.sourceIso) throw Error("source ISO is not represented by this experimental profile");
    } else if (m.sourceIso == 1250) {
        shot.iso = 1250;   // cámaras sin ISO en EXIF (Pocket original): el perfil usa su ISO nativo de referencia
    } else {
        throw Error("source must declare capture ISO in EXIF");
    }
    if (!dng.numeric(50719) || !dng.numeric(50720)) throw Error("source must declare DefaultCropOrigin and DefaultCropSize");
    // Con rcm_tone el perfil recibe la cámara corregida: el neutro se pasa por la misma corrección, a un gris medio
    // (0,18 de la escena, antes del BaselineExposure), para buscarlo en las tablas de ganancias del perfil.
    Vec3 neutral = color.asShotNeutral;
    if (m.hasTone) {
        const double level = 0.18 / m.baselineGain;
        const double in[3] = {neutral[0] * level, neutral[1] * level, neutral[2] * level};
        double out[3];
        m.correct(in, out);
        for (int k = 0; k < 3; ++k) neutral[k] = out[k] / out[1];
    }
    const auto [kelvin, tint] = inferAsShot(neutral, m);
    shot.kelvin = kelvin;
    shot.tint = tint;
    return shot;
}

}  // namespace cdng
