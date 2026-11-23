// Bloque C — compresión de un frame. Pasos del cuantizador, tablas, DCT por bloques,
// cuantización, cadena y códigos DC, slices, control de tasa y calidad fija.
// Es un port del prototipo en Python, que no se publica. La entropía es la de
// native/src/entropy.cpp (cdng_encode_slice), la misma que usaba el prototipo.
//
// Paridad con el prototipo (2 nov 2026, entropía numpy de referencia): 55 frames codificados idénticos byte a
// byte (6 frames reales a tamaño nativo y 1080 + 4 sintéticos, q 0/2/4/8/13/24), los mismos errores de desborde, el
// mismo q y tamaño en el control de tasa y el mismo chunk de frame_chunk. La DCT es una multiplicación de matrices
// en double, no la de pocketfft: la diferencia es de ~1e-12 y el valor cuantizado más cercano a un empate .5 en los
// frames reales está a ≥ 6e-8, así que no hay empates de redondeo que cambien el resultado.
// Tiempo de encode (un hilo, M3 Pro, Release, q 8): UHD 0,11–0,16 s frente a 0,79–1,07 s del Python (≈ ×7);
// 1080 0,016–0,040 s frente a 0,12–0,26 s.
#include "codec.hpp"
#include "codec_tables.hpp"

#include "cdng_native.h"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <limits>
#include <optional>
#include <string>
#include <tuple>

#include <simdjson.h>

namespace cdng {

namespace {

constexpr int DcLimit = 4095;   // clase 12

// Zona muerta del cuantizador AC (CDNG_DEADZONE, 0 … 0,5): ℓ = signo·floor(|c|/paso + 0,5 − z).
// Con 0 es nearbyint, la cuantización verificada con el prototipo. Los coeficientes pequeños (ruido) se
// anulan antes y no se escriben como ±1 paso.
double deadZone() {
    static const double zone = [] {
        const char *value = std::getenv("CDNG_DEADZONE");
        return std::clamp(value ? std::atof(value) : 0.0, 0.0, 0.5);
    }();
    return zone;
}

int64_t quantize(double coef, double step) {
    const double v = coef / step, z = deadZone();
    if (z <= 0) return int64_t(std::nearbyint(v));
    return int64_t(std::copysign(std::floor(std::fabs(v) + 0.5 - z), v));
}

// Síntesis de ruido del decodificador, modo adaptativo (por defecto se escribe la tabla del Video Assist, más abajo;
// con model/noise_synthesis.json se usa la tabla calibrada de más abajo; esto es
// la estimación anterior, que queda si falta la tabla). Medido el 7 nov 2026 con fotogramas planos inyectados en un contenedor real:
// el decodificador añade ruido de σ ≈ valor/287 códigos a Y (u16 en 20–21 de la cabecera) y ≈ valor/600 a cada plano
// de croma (u16 en 22–23), independiente de q y del contenido (778/421, los valores de la plantilla que se copiaban
// siempre, dan 2,73 / 0,71). La cámara y el Video Assist escriben valores propios por q (Pocket: 778/421 a q8,
// 683/404 a q4; VA: 266/280 a q0). Aquí se escribe lo que la cuantización ha quitado al ruido de las zonas lisas:
// en el 30 % de bloques con menos energía AC (Parseval, DCT ortonormal), la raíz de la diferencia entre la energía
// que llega al decodificador sin códec y la que reconstruye de los coeficientes cuantizados. Las dos se derivan de
// la energía AC del codificador con factores medidos en la carta Sigma (DNG068, zonas lisas, q 1 y 4): la Y lleva la
// precompensación antibloques (×1,19 de energía) y el filtro del decodificador la deshace (decodificado = 0,84·E_in
// y 0,77·E_out); en croma no hay filtro (0,91·E_in y 1,04·E_out). Con ello a q 1–4 no se repone nada y a q 24 la
// Y decodificada queda en 2,0 códigos rms frente a 1,9 de entrada. CDNG_NOISE_SYNTH="y,c" fija los dos valores
// (p. ej. "0,0" o "778,421").
constexpr double NoiseUnitY = 287, NoiseUnitC = 600;
constexpr double DecodedInY = 0.84, DecodedOutY = 0.77, DecodedInC = 0.91, DecodedOutC = 1.04;

std::optional<std::pair<uint16_t, uint16_t>> fixedNoiseSynthesis() {
    static const auto value = []() -> std::optional<std::pair<uint16_t, uint16_t>> {
        const char *s = std::getenv("CDNG_NOISE_SYNTH");
        int y = 0, c = 0;
        if (!s || std::sscanf(s, "%d,%d", &y, &c) != 2) return std::nullopt;
        return std::make_pair(uint16_t(std::clamp(y, 0, 65535)), uint16_t(std::clamp(c, 0, 65535)));
    }();
    return value;
}
// CDNG_NOISE_SYNTH=adaptativo: el ruido que ha quitado la cuantización a esta imagen (la tabla calibrada o las
// energías), en lugar de la tabla del Video Assist.
bool adaptiveNoiseSynthesis() {
    static const bool value = [] { const char *s = std::getenv("CDNG_NOISE_SYNTH"); return s && std::string(s) == "adaptativo"; }();
    return value;
}

// Lo que piden las cámaras de Blackmagic (13 nov 2026). La Pocket 4K (firmware 7.9.1) escribe un valor fijo para cada
// q, el mismo en todos los fotogramas y en todas las escenas: clips Q0/Q1/Q3/Q5 (q 0, 2, 9 y 20) y
// los de 3:1 (q 0–5 y 8, que cambian de un fotograma a otro). El único clip del Video Assist
// (Sigma fp, Q0) pide 266/280 a q 0, en todos sus fotogramas, frente a 450/404 de la Pocket. Suponemos que el Video
// Assist sigue la misma tabla a otra escala: la de la Pocket por 266/450 en luma y 280/404 en croma. Entre q medidos
// se interpola; por encima de q 20 se queda el de q 20. La tabla no es monótona (q 1 > q 2; croma de q 5 > q 8): es
// lo que escribe la cámara.
// Sobre esa tabla, una atenuación que va del 60 % en q 0 a nada en q 24, en línea recta: en la pared del Fender
// (SIGMA fp, 13 nov 2026) los q bajos ya salen algo más ruidosos que el original sin ruido del descodificador, y con
// él se notaba de más; en los q altos tapa los bloques y se deja como está.
struct CameraNoise { int q; double y, c; };
constexpr CameraNoise PocketNoise[] = {{0, 450, 404}, {1, 652, 404}, {2, 616, 404}, {3, 634, 404}, {4, 683, 404},
                                       {5, 730, 567}, {8, 778, 421}, {9, 824, 578}, {20, 1039, 707}};
constexpr double VideoAssistScaleY = 266.0 / 450, VideoAssistScaleC = 280.0 / 404;
constexpr double LowQNoiseFactor = 0.6;   // en q 0; sube en línea recta hasta 1 en QMax

// Bloques lisos: energía AC de entrada ≤ la del bloque en el percentil 30 (umbral = elemento k = 3n/10 del orden).
// Energía AC por píxel de esos bloques, antes y después de cuantizar, en las unidades del plano descodificado.
std::pair<double, double> smoothEnergy(const std::vector<double> &in, const std::vector<double> &out, int pixels, double kIn, double kOut) {
    if (in.empty()) return {0, 0};
    std::vector<double> sorted(in);
    const auto k = sorted.begin() + (sorted.size() * 3) / 10;
    std::nth_element(sorted.begin(), k, sorted.end());
    const double threshold = *k;
    double sumIn = 0, sumOut = 0, n = 0;
    for (std::size_t i = 0; i < in.size(); ++i)
        if (in[i] <= threshold) { sumIn += in[i]; sumOut += out[i]; n += 1; }
    return {kIn * sumIn / (n * pixels), kOut * sumOut / (n * pixels)};
}
uint16_t noiseField(const std::pair<double, double> &e, double unit) {
    return uint16_t(std::clamp(std::nearbyint(unit * std::sqrt(std::max(e.first - e.second, 0.0))), 0.0, 65535.0));
}

// Ruido que quita la compresión, calibrado (model/noise_synthesis.json): para cada
// nivel de ruido de entrada (σ estimado por el codificador en los bloques lisos, `noiseLevel`) y cada q, la σ que hay
// que reponer para que el plano descodificado por el SDK tenga el ruido que tenía a q 0. Entre niveles se interpola;
// por debajo del primero se escala con el nivel y por encima del último se usa el último.
struct NoiseCurve {
    std::vector<double> level;
    std::vector<std::array<double, QMax + 1>> removed;
    double at(int q, double sigma) const {
        if (level.empty()) return 0;
        q = std::clamp(q, 0, QMax);
        if (sigma <= level.front()) return removed.front()[q] * std::max(sigma, 0.0) / level.front();
        if (sigma >= level.back()) return removed.back()[q];
        const auto hi = std::size_t(std::upper_bound(level.begin(), level.end(), sigma) - level.begin()), lo = hi - 1;
        const double t = (sigma - level[lo]) / (level[hi] - level[lo]);
        return removed[lo][q] * (1 - t) + removed[hi][q] * t;
    }
};
struct NoiseTable { NoiseCurve y, c; };

std::optional<NoiseTable> loadNoiseTable(const std::filesystem::path &path) {
    if (!std::filesystem::is_regular_file(path)) return std::nullopt;
    NoiseTable table;
    try {
        simdjson::dom::parser parser;
        simdjson::padded_string json = simdjson::padded_string::load(path.string());
        const simdjson::dom::element doc = parser.parse(json);
        for (auto [curve, key] : {std::pair{&table.y, "y"}, std::pair{&table.c, "c"}}) {
            const simdjson::dom::element plane = doc[key];
            for (double v : plane["level"].get_array()) curve->level.push_back(v);
            for (simdjson::dom::element row : plane["removed"].get_array()) {
                std::array<double, QMax + 1> r{};
                std::size_t q = 0;
                for (double v : row.get_array()) { if (q > std::size_t(QMax)) throw Error("bad row"); r[q++] = v; }
                if (q != std::size_t(QMax) + 1) throw Error("bad row");
                curve->removed.push_back(r);
            }
            if (curve->level.empty() || curve->level.size() != curve->removed.size() ||
                !std::is_sorted(curve->level.begin(), curve->level.end()))
                throw Error("bad table");
        }
    } catch (const std::exception &) {
        throw Error("The embedded calibration is damaged: noise_synthesis.json");
    }
    return table;
}
const char *const DcOverflowMessage = "DC difference exceeds verified VLC range; use a higher --q";

// Buffer de la ABI C liberado siempre.
struct NativeBuffer {
    CdngBuffer b{nullptr, 0};
    ~NativeBuffer() { cdng_buffer_free(&b); }
};

// Coeficientes cuantizados y códigos DC de un plano de un slice, en el formato de CdngPlane.
struct PlaneCodes {
    std::vector<int64_t> ac;
    std::vector<uint64_t> dcValues;
    std::vector<uint8_t> dcLengths;
    CdngPlane view(std::size_t coefficients) const {
        return CdngPlane{ac.data(), dcValues.data(), dcLengths.data(), dcLengths.size(), coefficients};
    }
};

// _dc_chain (estricto) + _dc_codes: D = rint(target), d = D − anterior (el primero respecto a 0).
// Diferencias de DC. Estricto: una que exceda la clase 12 lanza CodecRangeError. Si no, se recorta y la cadena sigue
// desde el valor recortado, que es el que reconstruye el decodificador.
void dcChain(const std::vector<double> &target, const CodecTables &t, PlaneCodes &out, bool strict, uint64_t &clamped) {
    out.dcValues.resize(target.size());
    out.dcLengths.resize(target.size());
    int64_t prev = 0;
    std::vector<int64_t> d(target.size());
    for (std::size_t i = 0; i < target.size(); ++i) {
        const auto D = int64_t(std::nearbyint(target[i]));
        d[i] = D - prev;
        if (d[i] > DcLimit || d[i] < -DcLimit) {
            if (strict) throw CodecRangeError(DcOverflowMessage);
            d[i] = std::clamp<int64_t>(d[i], -DcLimit, DcLimit);
            ++clamped;
        }
        prev += d[i];
    }
    for (std::size_t i = 0; i < d.size(); ++i) {
        const auto code = t.dcCode(d[i]);
        out.dcValues[i] = code.value;
        out.dcLengths[i] = uint8_t(code.length);
    }
}

}  // namespace

std::pair<uint16_t, uint16_t> videoAssistNoise(int q) {
    q = std::clamp(q, QMin, QMax);
    const auto *hi = std::begin(PocketNoise);
    while (hi + 1 != std::end(PocketNoise) && hi->q < q) ++hi;
    double y = hi->y, c = hi->c;
    if (hi->q > q) {
        const auto *lo = hi - 1;
        const double t = double(q - lo->q) / (hi->q - lo->q);
        y = lo->y + (hi->y - lo->y) * t; c = lo->c + (hi->c - lo->c) * t;
    }
    const double f = LowQNoiseFactor + (1 - LowQNoiseFactor) * (q - QMin) / double(QMax - QMin);
    return {uint16_t(std::nearbyint(y * VideoAssistScaleY * f)), uint16_t(std::nearbyint(c * VideoAssistScaleC * f))};
}

// Respuesta del descodificador (SDK) al campo de síntesis de ruido, medida el 14 nov 2026: el mismo fotograma con el
// campo Y (o C) a cada valor, revelado, menos el revelado con 0, en la pared del Fender (tono ~55 %); σ en códigos de
// 10 bits gamma 709 (en croma, la media de Cb y Cr). No es proporcional: por debajo de 25–50 no pone nada (probablemente
// lo redondea a su precisión), luego salta a ~0,3 y crece casi en línea recta. Depende del bloque: en los que se han
// quedado lisos (aquí, a q 20) pone un 40–50 % más que en los que conservan textura (q 0), probablemente porque
// rellena lo que falta. Esta es la de los lisos, donde el ruido sintético se ve: la media de 4K, Full HD y 720p, que
// difieren menos de un 5 %.
namespace {
constexpr double NoiseFields[] = {0, 25, 50, 75, 100, 150, 200, 300, 400, 600, 800, 1200, 1600};
constexpr double NoiseSigmaY[] = {0.000, 0.000, 0.317, 0.411, 0.478, 0.575, 0.690, 0.934, 1.192, 1.736, 2.285, 3.405, 4.512};
constexpr double NoiseSigmaC[] = {0.000, 0.000, 0.000, 0.258, 0.358, 0.438, 0.510, 0.603, 0.720, 0.988, 1.250, 1.791, 2.347};
}  // namespace

double decoderNoise(double field, bool chroma) {
    constexpr int n = 13;
    const double *row = chroma ? NoiseSigmaC : NoiseSigmaY;
    if (field <= 0) return 0;
    int i = 1;
    while (i < n - 1 && NoiseFields[i] < field) ++i;   // el último tramo se prolonga
    const double t = (field - NoiseFields[i - 1]) / (NoiseFields[i] - NoiseFields[i - 1]);
    return std::max(0.0, row[i - 1] + (row[i] - row[i - 1]) * t);
}

uint16_t decoderNoiseField(double sigma, bool chroma) {
    if (!(sigma > 0)) return 0;
    int lo = 0, hi = 65535;   // el campo más pequeño que da al menos sigma (la respuesta no baja)
    if (decoderNoise(hi, chroma) < sigma) return uint16_t(hi);
    while (lo < hi) { const int mid = (lo + hi) / 2; if (decoderNoise(mid, chroma) < sigma) lo = mid + 1; else hi = mid; }
    // el más cercano de los dos vecinos
    if (lo > 0 && sigma - decoderNoise(lo - 1, chroma) < decoderNoise(lo, chroma) - sigma) --lo;
    return uint16_t(lo);
}

struct Codec::Impl {
    CodecTables tables;
    CdngEntropyTables native{};
    std::optional<NoiseTable> noise;   // sin la tabla, la estimación por energías de cada frame (smoothEnergy)
};

Codec::Codec(const std::filesystem::path &resources) : impl_(std::make_unique<Impl>()) {
    impl_->tables = CodecTables::load(resources / "model" / "vlc-table.json", resources / "model" / "quant_steps.json");
    impl_->noise = loadNoiseTable(resources / "model" / "noise_synthesis.json");
    const auto &t = impl_->tables;
    impl_->native = CdngEntropyTables{t.preVal.data(), t.preLen.data(), t.finVal.data(), t.finLen.data(),
                                      t.drop.data(), t.adjust.data(), t.eobValue, uint8_t(t.eobLength)};
}

Codec::~Codec() = default;
Codec::Codec(Codec &&) noexcept = default;

EncodedFrame Codec::encode(const CanvasPlanes &planes, int q, const Geometry &g, bool strict) const {
    const auto &t = impl_->tables;
    const auto &st = t.steps(q);
    if (planes.ws != g.ws || planes.hs != g.hs || planes.y.size() != std::size_t(g.ws) * g.hs ||
        planes.cb.size() != std::size_t(g.ws / 2) * (g.hs / 2) || planes.cr.size() != planes.cb.size())
        throw Error("Unsupported frame geometry.");
    EncodedFrame frame;
    frame.q = q;
    frame.slices.reserve(std::size_t(g.n));
    const int wc = g.ws / 2;
    std::vector<double> target;
    PlaneCodes y, cb, cr;
    double block[64], coef[64];
    std::vector<double> inY, outY, inC, outC;   // energía AC por bloque antes y después de cuantizar
    inY.reserve(std::size_t(g.ws / 8) * (g.hs / 8)); outY.reserve(inY.capacity()); inC.reserve(inY.capacity()); outC.reserve(inY.capacity());
    for (std::size_t p = 0; p < g.bounds.size(); ++p) {
        const int x0 = g.bounds[p].first, x1 = g.bounds[p].second;
        for (int k = 0; k < g.slices; ++k) {
            const int y0 = k * g.rows, y1 = std::min(y0 + g.rows, g.hs);
            const int rows = (y1 - y0) / 8, cols = (x1 - x0) / 8, nmb = cols / 2;
            const std::size_t nb = std::size_t(rows) * cols, nc = std::size_t(rows) * nmb;
            if (!nb || cols % 2) throw Error("Unsupported frame geometry.");
            // ---- Y: bloques 8×8 en orden raster ----
            target.resize(nb);
            y.ac.resize(nb * 63);
            for (int r = 0; r < rows; ++r)
                for (int c = 0; c < cols; ++c) {
                    const std::size_t b = std::size_t(r) * cols + c;
                    for (int i = 0; i < 8; ++i) {
                        const int16_t *src = planes.y.data() + std::size_t(y0 + r * 8 + i) * g.ws + x0 + c * 8;
                        for (int j = 0; j < 8; ++j) block[i * 8 + j] = src[j];
                    }
                    dct8x8(block, coef);
                    target[b] = (coef[0] / 8.0 - 2048.0) / st.yUnit;
                    int64_t *ac = y.ac.data() + b * 63;
                    double ein = 0, eout = 0;
                    for (int i = 0; i < 63; ++i) {
                        const double c = coef[ScanY[i]];
                        ac[i] = quantize(c, st.qY);
                        const double r = double(ac[i]) * st.qY;
                        ein += c * c; eout += r * r;
                    }
                    inY.push_back(ein); outY.push_back(eout);
                }
            dcChain(target, t, y, strict, frame.stats.clamped);
            // ---- croma: bloques 4×8 en orden raster ----
            for (int s = 0; s < 2; ++s) {
                const auto &plane = s ? planes.cr : planes.cb;
                auto &out = s ? cr : cb;
                target.resize(nc);
                out.ac.resize(nc * 31);
                for (int r = 0; r < rows; ++r)
                    for (int c = 0; c < nmb; ++c) {
                        const std::size_t b = std::size_t(r) * nmb + c;
                        for (int i = 0; i < 4; ++i) {
                            const int16_t *src = plane.data() + std::size_t(y0 / 2 + r * 4 + i) * wc + x0 / 2 + c * 8;
                            for (int j = 0; j < 8; ++j) block[i * 8 + j] = src[j];
                        }
                        dct4x8(block, coef);
                        target[b] = coef[0] / st.cUnit;
                        int64_t *ac = out.ac.data() + b * 31;
                        double ein = 0, eout = 0;
                        for (int i = 0; i < 31; ++i) {
                            const double c = coef[ScanC[i]];
                            ac[i] = quantize(c, st.qC);
                            const double r = double(ac[i]) * st.qC;
                            ein += c * c; eout += r * r;
                        }
                        inC.push_back(ein); outC.push_back(eout);
                    }
                dcChain(target, t, out, strict, frame.stats.clamped);
            }
            const auto vy = y.view(63), vb = cb.view(31), vr = cr.view(31);
            NativeBuffer buffer;
            CdngEntropyStats stats{};
            const int status = cdng_encode_slice(&vy, &vb, &vr, &impl_->native, &buffer.b, &stats);
            if (status == 2) throw std::bad_alloc();
            if (status) throw Error("Entropy coder rejected the slice.");
            frame.stats.fallback += stats.fallback;
            frame.stats.clamped += stats.clamped;
            frame.stats.dropped += stats.dropped;
            frame.slices.emplace_back(buffer.b.data, buffer.b.data + buffer.b.size);
            frame.size += buffer.b.size;
        }
    }
    if (frame.stats.clamped && strict)
        throw CodecRangeError(std::to_string(frame.stats.clamped) + " AC coefficients exceed the verified VLC range at q " +
                              std::to_string(q));
    const auto eY = smoothEnergy(inY, outY, 64, DecodedInY, DecodedOutY), eC = smoothEnergy(inC, outC, 32, DecodedInC, DecodedOutC);
    frame.noiseLevelY = std::sqrt(eY.first); frame.noiseLevelC = std::sqrt(eC.first);
    if (const auto fixed = fixedNoiseSynthesis()) { frame.noiseY = fixed->first; frame.noiseC = fixed->second; }
    else if (!adaptiveNoiseSynthesis()) std::tie(frame.noiseY, frame.noiseC) = videoAssistNoise(q);
    else if (impl_->noise) {
        const auto field = [](double sigma, double unit) { return uint16_t(std::clamp(std::nearbyint(unit * sigma), 0.0, 65535.0)); };
        frame.noiseY = field(impl_->noise->y.at(q, frame.noiseLevelY), NoiseUnitY);
        frame.noiseC = field(impl_->noise->c.at(q, frame.noiseLevelC), NoiseUnitC);
    } else {
        frame.noiseY = noiseField(eY, NoiseUnitY);
        frame.noiseC = noiseField(eC, NoiseUnitC);
    }
    frame.size += FirstSlice;
    return frame;
}

EncodedFrame Codec::encodeRateControlled(const CanvasPlanes &planes, const Geometry &g, std::size_t target, int q0,
                                         int qMin) const {
    constexpr double Inf = std::numeric_limits<double>::infinity();
    struct Try {
        bool ok = false;
        EncodedFrame frame;
        double size = Inf;
    };
    int attempts = 0;
    auto enc = [&](int q) {
        Try r;
        ++attempts;
        try {
            r.frame = encode(planes, q, g);
            r.ok = true;
            r.size = double(r.frame.size);
        } catch (const CodecRangeError &) {
        }
        return r;
    };
    const double limit = double(target);
    // Se busca el q **más fino que cabe**, sondeando primero el extremo que toque y bisecando después. Antes se
    // recorría de uno en uno (o de dos en dos): con material que ni a QMax cabe —lo normal en tomas con mucho
    // ruido, el caso D10— cada fotograma de arranque subía de q 8 a q 24 escalón a escalón, 13 codificaciones
    // enteras para acabar recortando igual. Sondeando el extremo eso se resuelve en 2.
    // Bisecar además da el mínimo exacto: subiendo de dos en dos, la búsqueda anterior podía saltárselo y dejar
    // un q más grueso del necesario, o sea peor calidad de la pedida.
    int lo = qMin - 1, hi = QMax + 1;   // lo: el mayor que NO cabe; hi: el menor que SÍ cabe
    Try best;
    const int start = std::clamp(q0, qMin, QMax);
    Try cur = enc(start);
    const bool fits = cur.ok && cur.size <= limit;
    if (fits) { hi = start; best = std::move(cur); } else lo = start;
    // Extremo: si cabe al primer intento se prueba el más fino de todos; si no cabe, el más grueso.
    if (hi - lo > 1) {
        const int edge = fits ? qMin : QMax;
        Try t = enc(edge);
        if (t.ok && t.size <= limit) { hi = edge; best = std::move(t); } else lo = edge;
    }
    while (hi - lo > 1) {   // bisección en el hueco que quede: como mucho log2(QMax) codificaciones más
        const int mid = lo + (hi - lo) / 2;
        Try t = enc(mid);
        if (t.ok && t.size <= limit) { hi = mid; best = std::move(t); }
        else lo = mid;
    }
    if (!best.ok) { auto last = encode(planes, QMax, g, false); last.stats.attempts = attempts + 1; return last; }
    best.frame.stats.attempts = attempts;
    return std::move(best.frame);
}

EncodedFrame Codec::encodeFixed(const CanvasPlanes &planes, const Geometry &g, int q) const {
    int attempts = 0;
    while (true) {   // un frame que desborda a ese q se codifica al siguiente q seguro; si ninguno, QMax recortando
        ++attempts;
        try {
            auto frame = encode(planes, q, g);
            frame.stats.attempts = attempts;
            return frame;
        } catch (const CodecRangeError &) {
            if (q >= QMax) { auto frame = encode(planes, QMax, g, false); frame.stats.attempts = attempts + 1; return frame; }
            ++q;
        }
    }
}

int Codec::nextQuantiser(int q, std::size_t size, std::size_t target, int qMin) {
    if (!target || !size) return q;
    const double ratio = double(size) / double(target);
    // Dentro de la banda muerta no se toca: evita que el q oscile con las variaciones normales entre fotogramas.
    if (ratio <= 1.05 && ratio >= 0.85) return q;
    // Paso proporcional a lo lejos que esté: el tamaño cae de forma aproximadamente geométrica con q, así que
    // la distancia en q va con el logaritmo de la relación. Sin esto el ajuste es de uno en uno y, con un q
    // inicial lejano y N procesos (cada uno con su q), tarda N fotogramas por paso en converger: se desperdicia
    // calidad durante todo ese tramo. Se limita a 4 para no pasarse de largo en un corte de escena.
    const double steps = std::log2(ratio > 1 ? ratio : 1 / ratio) * 3;
    const int step = std::clamp(int(std::lround(std::max(1.0, steps))), 1, 4);
    return ratio > 1 ? std::min(q + step, QMax) : std::max(q - step, qMin);
}

CanvasPlanes Codec::reconstruct(const CanvasPlanes &planes, int q, const Geometry &g) const {
    const auto &st = impl_->tables.steps(q);
    if (planes.ws != g.ws || planes.hs != g.hs) throw Error("Unsupported frame geometry.");
    CanvasPlanes out = planes;
    const int wc = g.ws / 2;
    double block[64], coef[64];
    for (std::size_t p = 0; p < g.bounds.size(); ++p) {
        const int x0 = g.bounds[p].first, x1 = g.bounds[p].second;
        for (int k = 0; k < g.slices; ++k) {
            const int y0 = k * g.rows, y1 = std::min(y0 + g.rows, g.hs);
            const int rows = (y1 - y0) / 8, cols = (x1 - x0) / 8, nmb = cols / 2;
            for (int r = 0; r < rows; ++r)
                for (int c = 0; c < cols; ++c) {
                    for (int i = 0; i < 8; ++i)
                        for (int j = 0; j < 8; ++j) block[i * 8 + j] = planes.y[std::size_t(y0 + r * 8 + i) * g.ws + x0 + c * 8 + j];
                    dct8x8(block, coef);
                    double rec[64] = {};
                    rec[0] = (std::nearbyint((coef[0] / 8.0 - 2048.0) / st.yUnit) * st.yUnit + 2048.0) * 8.0;
                    for (int i = 0; i < 63; ++i) rec[ScanY[i]] = double(quantize(coef[ScanY[i]], st.qY)) * st.qY;
                    idct8x8(rec, block);
                    for (int i = 0; i < 8; ++i)
                        for (int j = 0; j < 8; ++j)
                            out.y[std::size_t(y0 + r * 8 + i) * g.ws + x0 + c * 8 + j] = int16_t(std::clamp(std::nearbyint(block[i * 8 + j]), 0., 4095.));
                }
            for (int s = 0; s < 2; ++s) {
                const auto &plane = s ? planes.cr : planes.cb;
                auto &dst = s ? out.cr : out.cb;
                for (int r = 0; r < rows; ++r)
                    for (int c = 0; c < nmb; ++c) {
                        for (int i = 0; i < 4; ++i)
                            for (int j = 0; j < 8; ++j) block[i * 8 + j] = plane[std::size_t(y0 / 2 + r * 4 + i) * wc + x0 / 2 + c * 8 + j];
                        dct4x8(block, coef);
                        double rec[64] = {};
                        rec[0] = std::nearbyint(coef[0] / st.cUnit) * st.cUnit;
                        for (int i = 0; i < 31; ++i) rec[ScanC[i]] = double(quantize(coef[ScanC[i]], st.qC)) * st.qC;
                        idct4x8(rec, block);
                        for (int i = 0; i < 4; ++i)
                            for (int j = 0; j < 8; ++j)
                                dst[std::size_t(y0 / 2 + r * 4 + i) * wc + x0 / 2 + c * 8 + j] = int16_t(std::clamp(std::nearbyint(block[i * 8 + j]), -4096., 4095.));
                    }
            }
        }
    }
    return out;
}

std::vector<uint8_t> frameChunk(const std::vector<uint8_t> &geometryHeader, const std::vector<uint8_t> &geometryGap,
                                const EncodedFrame &frame) {
    std::vector<uint8_t> hdr(geometryHeader);
    if (hdr.size() < 24) throw Error("Unsupported frame geometry.");
    hdr[1] = uint8_t(frame.q);
    hdr[20] = uint8_t(frame.noiseY >> 8); hdr[21] = uint8_t(frame.noiseY);   // síntesis de ruido del decodificador
    hdr[22] = uint8_t(frame.noiseC >> 8); hdr[23] = uint8_t(frame.noiseC);
    return buildFileChunk(hdr, geometryGap, frame.slices);
}

}  // namespace cdng
