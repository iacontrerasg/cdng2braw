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

OgExposure ogExposure(const OgModel &model, double baselineExposure, std::optional<int> exifIso) {
    if (!model.impl) throw Error("Missing calibration");
    const auto &m = *model.impl;
    if (!std::isfinite(baselineExposure)) throw Error("Invalid source BaselineExposure");
    const OgModel::Impl::IsoStop *found = nullptr;
    for (const auto &stop : m.isoTable)
        if (std::fabs(baselineExposure - stop.baselineExposure) <= m.baselineTolerance) found = &stop;
    if (!found) throw Error("BaselineExposure is not represented by the Pocket Cinema Camera ISO table (ISO 200, 400, 800, 1600)");
    if (exifIso && *exifIso != found->iso) throw Error("source ISO does not match its BaselineExposure");
    return {found->iso, found->postGain};
}

OgShot ogSourceInfo(const Dng &dng, const OgModel &model) {
    if (!model.impl) throw Error("Missing calibration");
    const auto &m = *model.impl;
    const DngColor color = dng.color();
    if (!(allclose(color.cm1, m.cm1) && allclose(color.cm2, m.cm2) && color.k1 == m.k1 && color.k2 == m.k2))
        throw Error("source matrices differ from calibrated DNG");
    const auto baseline = dng.scalar(50730);
    if (!baseline || !std::isfinite(*baseline)) throw Error("Invalid source BaselineExposure");
    std::optional<int> exifIso;
    if (const auto iso = dng.numeric(34855); iso && !iso->empty() && (*iso)[0] != 0) exifIso = int((*iso)[0]);
    const OgExposure exposure = ogExposure(model, *baseline, exifIso);
    OgShot shot;
    // La exposición viaja como ISO del clip (post_gain del perfil, al decodificar): multiplicar aquí recortaría las
    // altas luces en el techo de la curva del sensor.
    shot.gain = 1.0;
    shot.iso = exposure.iso;
    shot.baselineExposure = *baseline;
    shot.postGain = exposure.postGain;
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
