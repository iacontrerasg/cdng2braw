// Bloque B — de la imagen de cámara a los planos del decodificador. Ver signal.hpp para el origen Python de cada
// función. La aritmética sigue el orden de las operaciones de NumPy (sin contracción FMA, ver native/CMakeLists.txt)
// para que image_planes y canvas_planes den los mismos valores.
#include "signal_internal.hpp"
#include "stage_dump.hpp"

#include <simdjson.h>

#include <algorithm>
#include <cctype>
#include <cmath>
#include <cstdlib>
#include <string>
#include <map>
#include <mutex>

namespace cdng {

namespace {

using simdjson::dom::element;

std::string damaged(const std::filesystem::path &path) {
    return "The embedded calibration is damaged: " + path.filename().string();
}

element parseJson(simdjson::dom::parser &parser, simdjson::padded_string &text, const std::filesystem::path &path) {
    const auto bytes = readBytes(path);
    text = simdjson::padded_string(reinterpret_cast<const char *>(bytes.data()), bytes.size());
    element doc;
    if (parser.parse(text).get(doc)) throw Error(damaged(path));
    return doc;
}

bool has(const element &obj, const char *key) {
    element value;
    return !obj[key].get(value) && !value.is_null();
}

double number(const element &obj, const char *key) {
    double value;
    if (obj[key].get_double().get(value)) throw Error(std::string("Calibration field missing: ") + key);
    return value;
}

std::vector<double> numbers(const element &array) {
    std::vector<double> out;
    simdjson::dom::array items;
    if (array.get_array().get(items)) throw Error("Calibration field is not an array");
    for (element item : items) {
        double value;
        if (item.get_double().get(value)) throw Error("Calibration field is not numeric");
        out.push_back(value);
    }
    return out;
}

// Matriz de filas × columnas; devuelve las columnas [0, cols) de cada fila, fila a fila.
std::vector<double> matrix(const element &obj, const char *key, std::size_t rows, std::size_t cols) {
    element value;
    if (obj[key].get(value)) throw Error(std::string("Calibration field missing: ") + key);
    simdjson::dom::array items;
    if (value.get_array().get(items) || items.size() != rows) throw Error(std::string("Calibration field malformed: ") + key);
    std::vector<double> out;
    for (element row : items) {
        const auto v = numbers(row);
        if (v.size() < cols) throw Error(std::string("Calibration field malformed: ") + key);
        out.insert(out.end(), v.begin(), v.end());
    }
    return out;
}

Mat3 mat3(const std::vector<double> &v, std::size_t stride = 3) {
    Mat3 m;
    for (int i = 0; i < 3; ++i)
        for (int j = 0; j < 3; ++j) m[3 * i + j] = v[stride * i + j];
    return m;
}

// Mapas con entrada en double: image_planes promedia los 2×2 en float64, como NumPy, sin pasar por float.
class MapImpl : public SignalMap {
public:
    virtual void mapDouble(const double *rgb, double *ycc, std::size_t count) const = 0;
    void map(const float *rgb, float *ycc, std::size_t count) const override {
        double in[3 * 256], out[3 * 256];
        for (std::size_t done = 0; done < count;) {
            const std::size_t n = std::min<std::size_t>(256, count - done);
            for (std::size_t i = 0; i < 3 * n; ++i) in[i] = rgb[3 * done + i];
            mapDouble(in, out, n);
            for (std::size_t i = 0; i < 3 * n; ++i) ycc[3 * done + i] = float(out[i]);
            done += n;
        }
    }
};

// cdng2braw.VideoAssistMap
class VideoAssist : public MapImpl, public ChannelCodec {
public:
    explicit VideoAssist(const std::filesystem::path &path) {
        simdjson::dom::parser parser; simdjson::padded_string text;
        const element doc = parseJson(parser, text, path);
        k0_ = number(doc, "k0"); s_ = number(doc, "s"); full_ = number(doc, "full");
        element gain;
        if (doc["gain_rgb"].get(gain)) throw Error(damaged(path));
        const auto g = numbers(gain);
        if (g.size() != 3) throw Error(damaged(path));
        for (int k = 0; k < 3; ++k) gain_[k] = g[k];
        // Curva de descodificación medida del SDK: parábola con vértice y
        // desplazamiento propios por encima de la rodilla y recta tangente por debajo, que admite señal bajo el
        // negro. El codificador aplica su inversa. CDNG_SDK_TOE=0 vuelve a la raíz pura con recorte en 0.
        element toe;
        const char *env = std::getenv("CDNG_SDK_TOE");
        if (!doc["sdk_decode"].get(toe) && !(env && std::string(env) == "0")) {
            vertex_ = number(toe, "vertex"); offset_ = number(toe, "offset");
            knee_ = number(toe, "knee_code"); slope_ = number(toe, "linear_slope");
            if (!(slope_ > 0) || knee_ <= vertex_) throw Error(damaged(path));
            kneeX_ = (knee_ - vertex_) * (knee_ - vertex_) / (s_ * s_) + offset_;
            toe_ = true;
        } else {
            vertex_ = k0_;
        }
    }
    // x = rgb · full · gain_ch en códigos de sensor (negro del DNG restado) → código del plano.
    double encode(double x) const {
        if (!toe_) return k0_ + s_ * std::sqrt(std::max(x, 0.0));
        if (x >= kneeX_) return vertex_ + s_ * std::sqrt(x - offset_);
        return std::max(knee_ + (x - kneeX_) / slope_, 0.0);
    }
    // Inversa: código → x (negativo bajo el negro con la recta, como el SDK).
    double decode(double code) const {
        if (!toe_) { const double l = std::max(code - k0_, 0.0) / s_; return l * l; }
        if (code >= knee_) { const double l = (code - vertex_) / s_; return l * l + offset_; }
        return kneeX_ + (code - knee_) * slope_;
    }
    // Señal en el dominio de la raíz: distancia del código al vértice de la parábola (antes, a k0).
    double signal(double code) const override { return code - vertex_; }
    // Fotosito del canal channel (muestra × ganancia) → código, con el mismo orden de operaciones que mapDouble.
    double encode(double sample, int channel) const override { return encode(sample * full_ * gain_[channel]); }
    const ChannelCodec *channelCodec() const override { return this; }
    void mapDouble(const double *rgb, double *ycc, std::size_t count) const override {
        for (std::size_t i = 0; i < count; ++i, rgb += 3, ycc += 3) {
            double code[3];
            for (int k = 0; k < 3; ++k) code[k] = encode(rgb[k] * full_ * gain_[k]);
            ycc[0] = (code[0] + 2 * code[1] + code[2]) / 4;
            ycc[1] = (code[2] - code[1]) / 2;
            ycc[2] = (code[0] - code[1]) / 2;
        }
    }
    double k0_ = 0, s_ = 0, full_ = 0, gain_[3]{};
    double vertex_ = 0, offset_ = 0, knee_ = 0, slope_ = 0, kneeX_ = 0;
    bool toe_ = false;
};

// Ruta og: perfil Camera Support propio
class NativeSensor : public MapImpl {
public:
    explicit NativeSensor(std::shared_ptr<const OgModel::Impl> m) : m_(std::move(m)) {}
    void mapDouble(const double *rgb, double *ycc, std::size_t count) const override {
        const auto &m = *m_;
        for (std::size_t i = 0; i < count; ++i, rgb += 3, ycc += 3) {
            double v[3], c[3];
            if (m.hasTone) m.correct(rgb, c); else std::copy(rgb, rgb + 3, c);
            for (int k = 0; k < 3; ++k) {
                v[k] = c[k] * m.scale[k];
                if (m.hasCurve) v[k] = m.curve.encode(v[k]);
                else if (m.sqrtKind) v[k] = std::sqrt(std::max(v[k] + m.blackOffset, 0.0) / (1 + m.blackOffset)) * m.encodingMax;
                v[k] = v[k] - m.offset[k];
            }
            for (int r = 0; r < 3; ++r) ycc[r] = std::fma(v[2], m.inverse[3 * r + 2], std::fma(v[1], m.inverse[3 * r + 1], v[0] * m.inverse[3 * r]));
        }
    }
    std::shared_ptr<const OgModel::Impl> m_;
};

// cdng2braw.PocketMap(gen=4): Rec.709 → raw Pocket → warp → triangulación del muestreo denso.
class Pocket : public MapImpl {
public:
    explicit Pocket(std::shared_ptr<const Scattered> s) : s_(std::move(s)) {}
    void mapDouble(const double *rgb, double *ycc, std::size_t count) const override {
        int hint = -1;
        for (std::size_t i = 0; i < count; ++i, rgb += 3, ycc += 3) {
            double t[3];
            s_->input(rgb, t);
            s_->eval(t, ycc, hint);
        }
    }
    std::shared_ptr<const Scattered> s_;
};

std::mutex cacheMutex;
std::map<std::filesystem::path, std::shared_ptr<const Scattered>> cache;

std::shared_ptr<const Scattered> scattered(const std::filesystem::path &path) {
    std::lock_guard<std::mutex> lock(cacheMutex);
    auto &slot = cache[path];
    if (!slot) slot = std::make_shared<const Scattered>(path);
    return slot;
}

}  // namespace

double SensorCurve::encode(double raw) const {
    if (raw < kneeRaw) return (raw - linearB) / linearA;
    return (-quadY + std::sqrt(std::max(quadY * quadY + 4 * quadX * (raw - quadZ), 0.0))) / (2 * quadX);
}

double SensorCurve::decode(double code) const {
    return code < knee ? linearA * code + linearB : quadX * (code * code) + quadY * code + quadZ;
}

// ---------------------------------------------------------------------------------------------------------------
// OgModel (load_support_model)

double OgModel::Impl::tone(double x, int channel) const {
    const auto f = [this](double v) { const double l = std::log(v); return std::exp(toneA + toneB * l + toneC * l * l); };
    double y = (x > toneLinearBelow ? f(x) : x * f(toneLinearBelow) / toneLinearBelow) * toneScale[channel];
    if (y > toneShoulder) y = toneShoulder + (1 - toneShoulder) * std::tanh((y - toneShoulder) / (1 - toneShoulder));
    return y;
}

void OgModel::Impl::correct(const double *in, double *out) const {
    for (int k = 0; k < 3; ++k)
        out[k] = tone(toneMatrix[3 * k] * in[0] + toneMatrix[3 * k + 1] * in[1] + toneMatrix[3 * k + 2] * in[2], k);
}

void OgModel::Impl::uncorrect(const double *in, double *out) const {
    double t[3];
    for (int k = 0; k < 3; ++k) t[k] = untone(in[k], k);
    for (int k = 0; k < 3; ++k) out[k] = toneInverse[3 * k] * t[0] + toneInverse[3 * k + 1] * t[1] + toneInverse[3 * k + 2] * t[2];
}

// Inversa de tone para la vista previa (la comparación de ruido mira el original frente al resultado, sin la curva):
// la curva es creciente, así que basta una bisección en escala logarítmica. Por debajo de linearBelow es lineal.
double OgModel::Impl::untone(double y, int channel) const {
    const double edge = tone(toneLinearBelow, channel);
    if (y <= edge) return y * toneLinearBelow / edge;
    if (y >= 1) return 1;
    double lo = std::log(toneLinearBelow), hi = 0;
    while (tone(std::exp(hi), channel) < y) hi += 1;
    for (int i = 0; i < 48; ++i) { const double mid = (lo + hi) / 2; (tone(std::exp(mid), channel) < y ? lo : hi) = mid; }
    return std::exp((lo + hi) / 2);
}

OgModel loadOgModel(const std::filesystem::path &signalJson) {
    simdjson::dom::parser parser; simdjson::padded_string text;
    const element doc = parseJson(parser, text, signalJson);
    auto impl = std::make_shared<OgModel::Impl>();
    OgModel model;
    model.signalPath = signalJson;
    std::string_view cameraType = "SIGMA fp";
    if (has(doc, "camera_type")) (void)doc["camera_type"].get_string().get(cameraType);
    model.cameraType = std::string(cameraType);
    model.gen = int(number(doc, "gen"));
    model.revision = uint32_t(number(doc, "revision"));

    std::string_view profileName;
    if (doc["profile_filename"].get_string().get(profileName)) throw Error(damaged(signalJson));
    const auto profilePath = signalJson.parent_path() / std::string(profileName);
    const auto content = readBytes(profilePath);
    std::string_view sha;
    if (has(doc, "profile_sha256") && !doc["profile_sha256"].get_string().get(sha) && !sha.empty() &&
        sha256Hex(content) != sha)
        throw Error("Camera Support JSON does not match this signal calibration");

    std::string_view kind;
    impl->sqrtKind = has(doc, "signal_map_kind") && !doc["signal_map_kind"].get_string().get(kind) && kind == "sqrt";
    if (impl->sqrtKind) {
        impl->inverse = inverse3(mat3(matrix(doc, "code_from_ycc", 3, 3)));
    } else {
        const auto m = matrix(doc, "camera_from_ycc", 3, 4);
        impl->inverse = inverse3(mat3(m, 4));
        for (int k = 0; k < 3; ++k) impl->offset[k] = m[4 * k + 3];
    }
    if (has(doc, "code_from_ycc")) { impl->hasCodeFromYcc = true; impl->codeFromYcc = mat3(matrix(doc, "code_from_ycc", 3, 3)); }
    element scale;
    if (doc["input_channel_scale"].get(scale)) throw Error(damaged(signalJson));
    const auto s = numbers(scale);
    if (s.size() != 3) throw Error(damaged(signalJson));
    for (int k = 0; k < 3; ++k) impl->scale[k] = s[k];
    if (has(doc, "encoding_max")) impl->encodingMax = number(doc, "encoding_max");
    if (has(doc, "black_offset")) impl->blackOffset = number(doc, "black_offset");
    if (has(doc, "sensor_curve")) {
        element c; (void)doc["sensor_curve"].get(c);
        impl->hasCurve = true;
        impl->curve = {number(c, "linear_a"), number(c, "linear_b"), number(c, "quadratic_x"), number(c, "quadratic_y"),
                       number(c, "quadratic_z"), number(c, "knee"), number(c, "knee_raw")};
    }
    if (has(doc, "rcm_tone")) {
        element c; (void)doc["rcm_tone"].get(c);
        impl->hasTone = true;
        impl->toneA = number(c, "a"); impl->toneB = number(c, "b"); impl->toneC = number(c, "c");
        impl->toneShoulder = number(c, "shoulder"); impl->toneLinearBelow = number(c, "linear_below");
        element s;
        if (c["scale"].get(s)) throw Error(damaged(signalJson));
        const auto values = numbers(s);
        if (values.size() != 3 || !(impl->toneShoulder > 0 && impl->toneShoulder < 1) || !(impl->toneLinearBelow > 0))
            throw Error(damaged(signalJson));
        for (int k = 0; k < 3; ++k) impl->toneScale[k] = values[k];
        if (has(c, "matrix")) { impl->toneMatrix = mat3(matrix(c, "matrix", 3, 3)); impl->toneInverse = inverse3(impl->toneMatrix); }
    }
    element fp;
    if (doc["dng_fingerprint"].get(fp)) throw Error(damaged(signalJson));
    impl->cm1 = mat3(matrix(fp, "CM1", 3, 3)); impl->cm2 = mat3(matrix(fp, "CM2", 3, 3));
    impl->k1 = number(fp, "K1"); impl->k2 = number(fp, "K2");
    impl->baselineGain = number(doc, "baseline_exposure_gain");
    impl->baselineTolerance = number(doc, "baseline_exposure_tolerance");
    const int referenceIso = int(number(doc, "reference_iso"));
    simdjson::dom::array stops;
    if (doc["iso_table"].get_array().get(stops)) throw Error(damaged(signalJson));
    for (element stop : stops) impl->isoTable.push_back({int(number(stop, "iso")), number(stop, "baseline_exposure"), 0});

    simdjson::dom::parser profileParser;
    simdjson::padded_string profileText(reinterpret_cast<const char *>(content.data()), content.size());
    element profile;
    if (profileParser.parse(profileText).get(profile)) throw Error(damaged(profilePath));
    {   // El post_gain de cada ISO debe ser 2^BaselineExposure / baseline_exposure_gain, con pre_gain 1 (los datos se
        // codifican con ganancia unitaria) y la ISO de referencia como base y nativa del perfil.
        const std::string mismatch = "Camera Support ISO table does not match this signal calibration";
        element mode, params;
        simdjson::dom::array gains;
        if (profile["cameras"].at(0)["modes"].at(0).get(mode) || mode["iso_params"].at(0).get(params) ||
            params["iso_gain_table"].get_array().get(gains))
            throw Error(damaged(profilePath));
        if (gains.size() != impl->isoTable.size() || impl->isoTable.empty() || int(number(params, "base_iso")) != referenceIso ||
            int(number(mode, "native_iso")) != referenceIso)
            throw Error(mismatch);
        std::size_t i = 0;
        for (element gain : gains) {
            auto &stop = impl->isoTable[i++];
            stop.postGain = number(gain, "post_gain");
            const double expected = std::pow(2.0, stop.baselineExposure) / impl->baselineGain;
            if (int(number(gain, "iso")) != stop.iso || number(gain, "pre_gain") != 1.0 ||
                !(std::fabs(stop.postGain - expected) <= 1e-6 * expected) || (stop.iso == referenceIso) != (stop.postGain == 1.0))
                throw Error(mismatch);
        }
        // La tolerancia no puede alcanzar a la ISO vecina: un valor entre dos se rechaza, no se redondea.
        for (std::size_t a = 0; a < impl->isoTable.size(); ++a)
            for (std::size_t b = a + 1; b < impl->isoTable.size(); ++b)
                if (!(std::fabs(impl->isoTable[a].baselineExposure - impl->isoTable[b].baselineExposure) > 2 * impl->baselineTolerance))
                    throw Error(mismatch);
        if (!(impl->baselineTolerance > 0)) throw Error(mismatch);
    }
    element table;
    if (profile["cameras"].at(0)["modes"].at(0)["csc_params"]["table"].get(table)) throw Error(damaged(profilePath));
    element axis;
    (void)table["csc_temperature_axis"]["values"].get(axis);
    const auto temps = numbers(axis);
    (void)table["csc_tint_axis"]["values"].get(axis);
    impl->tints = numbers(axis);
    for (auto it = temps.rbegin(); it != temps.rend(); ++it) impl->mired.push_back(1e6 / *it);
    for (const char *key : {"r_gain", "b_gain"}) {
        element rows;
        simdjson::dom::array items;
        if (table[key].get(rows) || rows.get_array().get(items) || items.size() != temps.size()) throw Error(damaged(profilePath));
        std::vector<std::vector<double>> byTemp;
        for (element row : items) {
            element values;
            if (row["values"].get(values)) throw Error(damaged(profilePath));
            byTemp.push_back(numbers(values));
            if (byTemp.back().size() != impl->tints.size()) throw Error(damaged(profilePath));
        }
        auto &out = key[0] == 'r' ? impl->red : impl->blue;
        for (auto it = byTemp.rbegin(); it != byTemp.rend(); ++it) out.insert(out.end(), it->begin(), it->end());
    }
    if (impl->mired.size() < 2 || impl->tints.size() < 2) throw Error(damaged(profilePath));
    model.impl = impl;
    return model;
}

// ---------------------------------------------------------------------------------------------------------------
// Mapas

std::unique_ptr<SignalMap> videoAssistMap(const std::filesystem::path &resources) {
    return std::make_unique<VideoAssist>(resources / "model/video_assist_input_transform.json");
}

std::unique_ptr<SignalMap> ogSignalMap(const OgModel &model) {
    if (!model.impl) throw Error("Missing calibration");
    return std::make_unique<NativeSensor>(model.impl);
}

std::unique_ptr<SignalMap> pocketMap(const std::filesystem::path &resources) {
    return std::make_unique<Pocket>(scattered(resources / "model/pocket_inverse_gen4.bin"));
}

// ---------------------------------------------------------------------------------------------------------------
// image_planes y canvas_planes

namespace {
int mirrorIndex(int x, int size) {
    if (size == 1) return 0;
    const int period = 2 * (size - 1);
    x = (x % period + period) % period;
    return x < size ? x : period - x;
}

std::vector<float> blurPlane(const std::vector<float> &input, int w, int h, const std::vector<double> &kernel) {
    const int radius = int(kernel.size() / 2);
    std::vector<float> temp(input.size()), output(input.size());
    for (int y = 0; y < h; ++y) for (int x = 0; x < w; ++x) {
        double sum = 0;
        for (int k = -radius; k <= radius; ++k)
            sum += kernel[k + radius] * input[std::size_t(y) * w + mirrorIndex(x + k, w)];
        temp[std::size_t(y) * w + x] = float(sum);
    }
    for (int y = 0; y < h; ++y) for (int x = 0; x < w; ++x) {
        double sum = 0;
        for (int k = -radius; k <= radius; ++k)
            sum += kernel[k + radius] * temp[std::size_t(mirrorIndex(y + k, h)) * w + x];
        output[std::size_t(y) * w + x] = float(sum);
    }
    return output;
}

// blurPlane leído solo en los sitios (2i + site, 2j + site): mismas sumas, la mitad de columnas y un cuarto de salidas.
std::vector<float> blurAtSites(const std::vector<float> &input, int w, int h, const std::vector<double> &kernel, int site) {
    const int radius = int(kernel.size() / 2), hw = w / 2, hh = h / 2;
    if (kernel.empty()) {   // sin filtro (prueba): la suma del cuarteto, donde solo el fotosito de ese color es distinto de cero
        std::vector<float> output(std::size_t(hw) * hh);
        for (int j = 0; j < hh; ++j) for (int i = 0; i < hw; ++i) {
            const std::size_t a = std::size_t(2 * j) * w + 2 * i;
            output[std::size_t(j) * hw + i] = input[a] + input[a + 1] + input[a + w] + input[a + w + 1];
        }
        return output;
    }
    std::vector<float> temp(std::size_t(hw) * h), output(std::size_t(hw) * hh);
    for (int y = 0; y < h; ++y) for (int i = 0; i < hw; ++i) {
        double sum = 0;
        for (int k = -radius; k <= radius; ++k)
            sum += kernel[k + radius] * input[std::size_t(y) * w + mirrorIndex(2 * i + site + k, w)];
        temp[std::size_t(y) * hw + i] = float(sum);
    }
    for (int j = 0; j < hh; ++j) for (int i = 0; i < hw; ++i) {
        double sum = 0;
        for (int k = -radius; k <= radius; ++k)
            sum += kernel[k + radius] * temp[std::size_t(mirrorIndex(2 * j + site + k, h)) * hw + i];
        output[std::size_t(j) * hw + i] = float(sum);
    }
    return output;
}

std::vector<double> gaussian(double sigma) {
    const int radius = int(4 * sigma + .5);
    std::vector<double> kernel(2 * radius + 1);
    double sum = 0;
    for (int k = -radius; k <= radius; ++k)
        sum += kernel[k + radius] = std::exp(-.5 * k * k / (sigma * sigma));
    for (auto &value : kernel) value /= sum;
    return kernel;
}

}  // namespace

BayerFields bayerFields(const BayerImage &bayer, const SignalMap &map, double gain) {
    const ChannelCodec *codec = map.channelCodec();
    if (!codec) throw Error("This camera has no per-photosite codec for the Bayer path");
    const auto &va = *codec;
    const int w = bayer.width, h = bayer.height;
    BayerFields out;
    out.width = w; out.height = h;
    const auto encode = [&](double value, int channel) { return va.encode(value * gain, channel); };
    std::vector<float> code(bayer.samples.size()), green(code.size());
    // Verde en los fotositos R/B: cúbico direccional (cuatro verdes por dirección, −1 9 9 −1 / 16, sin rebasar a los
    // vecinos) con los pesos 1/(ε + |izq − dcha|) de cada par. CDNG_GREEN=dir vuelve a la media direccional de dos.
    static const bool cubic = [] { const char *e = std::getenv("CDNG_GREEN"); return !(e && std::string(e) == "dir"); }();
    for (int y = 0; y < h; ++y) for (int x = 0; x < w; ++x) {
        const auto i = std::size_t(y) * w + x;
        code[i] = float(encode(bayer.samples[i], bayer.colors[i]));
        if (bayer.colors[i] == 1) continue;
        const auto &samples = bayer.samples;
        const double left = samples[std::size_t(y) * w + mirrorIndex(x - 1, w)];
        const double right = samples[std::size_t(y) * w + mirrorIndex(x + 1, w)];
        const double up = samples[std::size_t(mirrorIndex(y - 1, h)) * w + x];
        const double down = samples[std::size_t(mirrorIndex(y + 1, h)) * w + x];
        const double wh = 1 / (.005 + std::abs(left - right));
        const double wv = 1 / (.005 + std::abs(up - down));
        double avg;
        if (cubic) {
            const auto at = [&](int yy, int xx) { return double(samples[std::size_t(mirrorIndex(yy, h)) * w + mirrorIndex(xx, w)]); };
            const double lo = std::min(std::min(left, right), std::min(up, down)), hi = std::max(std::max(left, right), std::max(up, down));
            const double eh = std::clamp((-at(y, x - 3) + 9 * left + 9 * right - at(y, x + 3)) / 16, lo, hi);
            const double ev = std::clamp((-at(y - 3, x) + 9 * up + 9 * down - at(y + 3, x)) / 16, lo, hi);
            avg = (wh * eh + wv * ev) / (wh + wv);
        } else {
            avg = (wh * (left + right) + wv * (up + down)) / (2 * (wh + wv));
        }
        green[i] = float(va.signal(encode(avg, 1)));
    }
    out.y = blurPlane(code, w, h, {.25, .5, .25});
    // Filtro de la croma: binomial 7 (CDNG_CHROMA_FILTER=3 o 5 para probar los más cortos).
    static const std::vector<double> binomial = [] {
        const char *e = std::getenv("CDNG_CHROMA_FILTER");
        const int taps = e ? std::atoi(e) : 7;
        if (taps == 1) return std::vector<double>{};   // sin filtro: el fotosito del propio cuarteto
        if (taps == 3) return std::vector<double>{.25, .5, .25};
        if (taps == 5) return std::vector<double>{1. / 16, 4. / 16, 6. / 16, 4. / 16, 1. / 16};
        return std::vector<double>{1. / 64, 6. / 64, 15. / 64, 20. / 64, 15. / 64, 6. / 64, 1. / 64};
    }();
    for (int channel : {2, 0}) {
        std::vector<float> numerator(code.size()), denominator(code.size());
        for (std::size_t i = 0; i < code.size(); ++i) if (bayer.colors[i] == channel) {
            numerator[i] = float(va.signal(code[i]) - green[i]);
            denominator[i] = green[i];
        }
        // El SDK sitúa Cb abajo-derecha y Cr arriba-izquierda del cuarteto.
        const int site = channel == 2 ? 1 : 0;
        (channel == 2 ? out.numB : out.numR) = blurAtSites(numerator, w, h, binomial, site);
        (channel == 2 ? out.denB : out.denR) = blurAtSites(denominator, w, h, binomial, site);
    }
    return out;
}

Planes sitePlanes(const BayerFields &f) {
    Planes out(f.width, f.height);
    out.y = f.y;
    for (int channel : {2, 0}) {
        const auto &numerator = channel == 2 ? f.numB : f.numR, &denominator = channel == 2 ? f.denB : f.denR;
        auto &ratio = channel == 2 ? out.cb : out.cr;
        // Sin señal verde, la proporción no es estimable. Limita la amplificación del ruido cerca del negro.
        for (std::size_t i = 0; i < ratio.size(); ++i) ratio[i] = float(numerator[i] / std::max(double(denominator[i]), 1.0));
    }
    // La escala se aplica tras el reescalado y el procesamiento final de luma.
    return out;
}

Planes bayerPlanes(const BayerImage &bayer, const SignalMap &map, double gain) {
    return sitePlanes(bayerFields(bayer, map, gain));
}

Planes bayerPlanesSized(const BayerImage &bayer, const SignalMap &map, double gain, int width, int height) {
    static const bool sites = [] { const char *e = std::getenv("CDNG_RESIZE_CHROMA"); return e && std::string(e) == "sites"; }();
    if (width == bayer.width && height == bayer.height) return bayerPlanes(bayer, map, gain);
    if (sites) return resizePlanes(bayerPlanes(bayer, map, gain), width, height);
    return resizeBayerFields(bayerFields(bayer, map, gain), width, height);
}

void finishPlanes(Planes &planes, const SignalMap &map, Route route, double luma, double chroma) {
    (void)route;
    const ChannelCodec *codec = map.channelCodec();
    if (!codec) {
        if (luma > 0 || chroma > 0) denoisePlanes(planes, luma, chroma);
        return;
    }
    if (luma > 0) denoisePlanes(planes, luma, 0);
    const int w = planes.width, h = planes.height;
    const auto smooth = blurPlane(planes.y, w, h, gaussian(1));
    std::vector<float> deviations;
    deviations.reserve(planes.y.size() / 17 + 1);
    for (std::size_t i = 0; i < planes.y.size(); i += 17)
        deviations.push_back(std::abs(planes.y[i] - smooth[i]));
    const auto middle = deviations.begin() + deviations.size() / 2;
    std::nth_element(deviations.begin(), middle, deviations.end());
    const double threshold = 1.4826 * *middle;
    // Enfoque de la luma: 1,2 a resolución nativa (el que más se acerca al Video Assist; compensa el suavizado con el
    // que sale la luma del mosaico). Al reducir, ese suavizado se queda en menos de un píxel de salida y el Lanczos ya
    // realza los bordes: baja con el área, 1,2·(1/f² − 0,1)/0,9 (0,41 de 6K a 4K, lo más cercano al 6K revelado y
    // reducido; 0 desde f ≈ 3,2). Al ampliar se queda en 1,2. CDNG_SHARPEN fija el valor nativo.
    static const double native = [] { const char *e = std::getenv("CDNG_SHARPEN"); return e ? std::atof(e) : 1.2; }();
    const double f = std::max(planes.reduction, 1.0);
    const double amount = native * std::max(0.0, (1 / (f * f) - 0.1) / 0.9);
    for (std::size_t i = 0; i < planes.y.size(); ++i) {
        const double detail = planes.y[i] - smooth[i];
        planes.y[i] += float(amount * std::copysign(std::max(std::abs(detail) - threshold, 0.0), detail));
    }
    const auto &va = *codec;
    for (int y = 0; y < h / 2; ++y) for (int x = 0; x < w / 2; ++x) {
        const auto i = std::size_t(y) * (w / 2) + x;
        const auto yi = std::size_t(2 * y) * w + 2 * x;
        const double signalB = std::max(va.signal(planes.y[yi + w + 1]), 0.0);
        const double signalR = std::max(va.signal(planes.y[yi]), 0.0);
        // Las proporciones físicas son >= -1. Lanczos puede sobrepasar ese límite al reescalar.
        const double cb = std::max(double(planes.cb[i]), -1.0);
        const double cr = std::max(double(planes.cr[i]), -1.0);
        const double denominator = 2 * (1 + (cb + cr) / 4);
        planes.cb[i] = float(cb * signalB / denominator);
        planes.cr[i] = float(cr * signalR / denominator);
    }
    if (chroma > 0) denoisePlanes(planes, 0, chroma);
}

Planes imagePlanes(const RgbImage &rgb, const SignalMap &map) {
    const int w = rgb.width, h = rgb.height;
    if (w % 2 || h % 2 || w <= 0 || h <= 0) throw Error("image_planes needs an even image size");
    Planes planes(w, h);
    const auto *impl = dynamic_cast<const MapImpl *>(&map);
    std::vector<float> yccRow(std::size_t(w) * 3);
    const bool dumpFull = stageDumpThisFrame();
    std::vector<float> cbFull, crFull, rgbCode;
    if (dumpFull) {
        cbFull.resize(std::size_t(w) * h);
        crFull.resize(std::size_t(w) * h);
        rgbCode.resize(std::size_t(w) * h * 3);
    }
    for (int y = 0; y < h; ++y) {
        map.map(rgb.px(0, y), yccRow.data(), std::size_t(w));
        for (int x = 0; x < w; ++x) {
            const float Yv = yccRow[3 * std::size_t(x)];
            const float Cb = yccRow[3 * std::size_t(x) + 1];
            const float Cr = yccRow[3 * std::size_t(x) + 2];
            planes.y[std::size_t(y) * w + x] = Yv;
            if (dumpFull) {
                const std::size_t i = std::size_t(y) * w + x;
                cbFull[i] = Cb;
                crFull[i] = Cr;
                // code_from_ycc (model/pocket_og_cinemadng_signal.json): copia, no entra al encode.
                rgbCode[3 * i + 0] = Yv - 0.5f * Cb + 1.5f * Cr;
                rgbCode[3 * i + 1] = Yv - 0.5f * Cb - 0.5f * Cr;
                rgbCode[3 * i + 2] = Yv + 1.5f * Cb - 0.5f * Cr;
            }
        }
    }
    const int hw = w / 2, hh = h / 2;
    std::vector<double> mean(std::size_t(hw) * 3), ycc(std::size_t(hw) * 3);
    std::vector<float> meanF(mean.size()), yccF(ycc.size());
    for (int y = 0; y < hh; ++y) {
        const float *r0 = rgb.px(0, 2 * y), *r1 = rgb.px(0, 2 * y + 1);
        for (int x = 0; x < hw; ++x)
            for (int c = 0; c < 3; ++c) {
                // NumPy: mean(axis=(1, 3)) suma cada fila por parejas y luego las filas: (a + b) + (c + d)
                const double a = r0[6 * x + c], b = r0[6 * x + 3 + c], cc = r1[6 * x + c], d = r1[6 * x + 3 + c];
                mean[3 * std::size_t(x) + c] = ((a + b) + (cc + d)) / 4;
            }
        if (impl) {
            impl->mapDouble(mean.data(), ycc.data(), std::size_t(hw));
            for (int x = 0; x < hw; ++x) {
                planes.cb[std::size_t(y) * hw + x] = float(ycc[3 * std::size_t(x) + 1]);
                planes.cr[std::size_t(y) * hw + x] = float(ycc[3 * std::size_t(x) + 2]);
            }
        } else {
            for (std::size_t i = 0; i < mean.size(); ++i) meanF[i] = float(mean[i]);
            map.map(meanF.data(), yccF.data(), std::size_t(hw));
            for (int x = 0; x < hw; ++x) {
                planes.cb[std::size_t(y) * hw + x] = yccF[3 * std::size_t(x) + 1];
                planes.cr[std::size_t(y) * hw + x] = yccF[3 * std::size_t(x) + 2];
            }
        }
    }
    if (dumpFull) {
        RgbImage codes(w, h);
        codes.data = std::move(rgbCode);
        stageDumpRgb("S3", codes, "nonlinear_camera_codes",
                     "derived via code_from_ycc of per-pixel map(); not a second encode kernel");
        stageDumpYccFull("S4", w, h, planes.y.data(), cbFull.data(), crFull.data(),
                         "per-pixel YCbCr before chroma box 2x2; Cb/Cr full-res discarded in production Planes");
        stageDumpPlanes("S5", planes, "Y full; Cb/Cr mean 2x2 of linear RGB then map (centered 4:2:0)");
    }
    return planes;
}

namespace {

// precompensate_deblock con DEBLOCK_TAPS = (3, 10, 3): filas (fronteras de columnas) y luego columnas.
// No modificar: la interpolación proporcional y la variante restringida se aplican alrededor.
void precompensate(std::vector<double> &p, int width, int height, int bw, int bh) {
    const double T = 16.0, t0 = 3, t1 = 10, det = t1 * t1 - t0 * t0;
    // NumPy indexa i + 1 en cada frontera i = bw, 2·bw…: falla si la última cae en el último píxel
    if ((width > bw && (width - 1) % bw == 0) || (height > bh && (height - 1) % bh == 0))
        throw Error("Canvas size incompatible with deblocking");
    for (int y = 0; y < height; ++y) {
        double *row = p.data() + std::size_t(y) * width;
        for (int i = bw; i < width; i += bw) {
            const double ain = row[i - 2], a = row[i - 1], b = row[i], bin = row[i + 1];
            const double ra = T * a - t0 * ain, rb = T * b - t0 * bin;
            row[i - 1] = (t1 * ra - t0 * rb) / det;
            row[i] = (t1 * rb - t0 * ra) / det;
        }
    }
    for (int i = bh; i < height; i += bh) {
        double *rin = p.data() + std::size_t(i - 2) * width, *ra_ = rin + width, *rb_ = ra_ + width, *rbin = rb_ + width;
        for (int x = 0; x < width; ++x) {
            const double ain = rin[x], a = ra_[x], b = rb_[x], bin = rbin[x];
            const double ra = T * a - t0 * ain, rb = T * b - t0 * bin;
            ra_[x] = (t1 * ra - t0 * rb) / det;
            rb_[x] = (t1 * rb - t0 * ra) / det;
        }
    }
}

// Inversa exacta de un par (sin alterar el algoritmo global): a',b' tal que el filtro (3,10,3)/16 recupera a,b.
void unconstrainedPair(double ain, double a, double b, double bin, double &ao, double &bo) {
    const double T = 16.0, t0 = 3, t1 = 10, det = t1 * t1 - t0 * t0;
    const double ra = T * a - t0 * ain, rb = T * b - t0 * bin;
    ao = (t1 * ra - t0 * rb) / det;
    bo = (t1 * rb - t0 * ra) / det;
}

// Error de reconstrucción ||A[x]−y||² para un par con exteriores fijos.
double pairReconError(double ain, double a, double b, double bin, double xt, double yt) {
    const double aOut = (3 * ain + 10 * a + 3 * b) / 16.0;
    const double bOut = (3 * a + 10 * b + 3 * bin) / 16.0;
    const double da = aOut - xt, db = bOut - yt;
    return da * da + db * db;
}

// Mínimos cuadrados acotados 0…4095 para un par (estrategia B). Conserva la inversa si es representable.
void boundedPair(double ain, double aTgt, double bTgt, double bin, double lo, double hi, double &ao, double &bo) {
    unconstrainedPair(ain, aTgt, bTgt, bin, ao, bo);
    if (ao >= lo && ao <= hi && bo >= lo && bo <= hi) return;
    // Candidatos: proyección por aristas (una variable libre) y las cuatro esquinas.
    double bestA = std::clamp(ao, lo, hi), bestB = std::clamp(bo, lo, hi);
    double best = pairReconError(ain, bestA, bestB, bin, aTgt, bTgt);
    auto consider = [&](double a, double b) {
        a = std::clamp(a, lo, hi);
        b = std::clamp(b, lo, hi);
        const double e = pairReconError(ain, a, b, bin, aTgt, bTgt);
        if (e < best) { best = e; bestA = a; bestB = b; }
    };
    // Aristas: fijar a∈{lo,hi} y optimizar b (y viceversa). 10 a + 3 b ≈ … → b = (16·tgt − 3·ain − 10·a)/3
    for (double aFix : {lo, hi}) {
        // Minimizar error en b dado a fijo: solución LS 1D del sistema acoplado.
        // ∂/∂b ( (3ain+10a+3b)/16 − aT )² + ( (3a+10b+3bin)/16 − bT )² = 0
        const double c0 = (3 * ain + 10 * aFix) / 16.0;
        const double d0 = (3 * aFix + 3 * bin) / 16.0;
        // (c0 + 3/16 b − aT) * (3/16) + (d0 + 10/16 b − bT) * (10/16) = 0
        const double k3 = 3.0 / 16.0, k10 = 10.0 / 16.0;
        const double num = -(k3 * (c0 - aTgt) + k10 * (d0 - bTgt));
        const double den = k3 * k3 + k10 * k10;
        consider(aFix, num / den);
    }
    for (double bFix : {lo, hi}) {
        const double c0 = (3 * ain + 3 * bFix) / 16.0;
        const double d0 = (10 * bFix + 3 * bin) / 16.0;
        const double k10 = 10.0 / 16.0, k3 = 3.0 / 16.0;
        const double num = -(k10 * (c0 - aTgt) + k3 * (d0 - bTgt));
        const double den = k10 * k10 + k3 * k3;
        consider(num / den, bFix);
    }
    consider(lo, lo); consider(lo, hi); consider(hi, lo); consider(hi, hi);
    ao = bestA;
    bo = bestB;
}

void precompensateBounded(std::vector<double> &p, int width, int height, int bw, int bh, double lo, double hi) {
    if ((width > bw && (width - 1) % bw == 0) || (height > bh && (height - 1) % bh == 0))
        throw Error("Canvas size incompatible with deblocking");
    // Mismo orden que precompensate (H luego V): las intersecciones quedan acopladas secuencialmente.
    for (int y = 0; y < height; ++y) {
        double *row = p.data() + std::size_t(y) * width;
        for (int i = bw; i < width; i += bw) {
            const double ain = row[i - 2], a = row[i - 1], b = row[i], bin = row[i + 1];
            double ao, bo;
            boundedPair(ain, a, b, bin, lo, hi, ao, bo);
            row[i - 1] = ao;
            row[i] = bo;
        }
    }
    for (int i = bh; i < height; i += bh) {
        double *rin = p.data() + std::size_t(i - 2) * width, *ra_ = rin + width, *rb_ = ra_ + width, *rbin = rb_ + width;
        for (int x = 0; x < width; ++x) {
            const double ain = rin[x], a = ra_[x], b = rb_[x], bin = rbin[x];
            double ao, bo;
            boundedPair(ain, a, b, bin, lo, hi, ao, bo);
            ra_[x] = ao;
            rb_[x] = bo;
        }
    }
}

void applyDeblockPrecomp(std::vector<double> &y, int width, int height, const DeblockPrecomp &cfg) {
    if (!cfg.enabled) return;
    if (cfg.bounded) {
        precompensateBounded(y, width, height, 8, 8, 0.0, 4095.0);
        return;
    }
    if (cfg.alpha == 0.0) return;
    if (cfg.alpha == 1.0) {
        precompensate(y, width, height, 8, 8);
        return;
    }
    // Salpha = S + alpha·(Scomp − S); no altera precompensate().
    const std::vector<double> S = y;
    precompensate(y, width, height, 8, 8);
    for (std::size_t i = 0; i < y.size(); ++i) y[i] = S[i] + cfg.alpha * (y[i] - S[i]);
}
// Coloca un plano w × h en un lienzo ws × hs en (x0, y0) replicando los bordes, como canvas_planes.
std::vector<double> place(const float *src, int w, int h, int ws, int hs, int x0, int y0) {
    if (x0 < 0 || y0 < 0 || x0 + w > ws || y0 + h > hs || w <= 0 || h <= 0) throw Error("Image does not fit in the canvas");
    std::vector<double> p(std::size_t(ws) * hs);
    for (int y = 0; y < hs; ++y) {
        const int sy = std::clamp(y - y0, 0, h - 1);
        double *row = p.data() + std::size_t(y) * ws;
        const float *s = src + std::size_t(sy) * w;
        for (int x = 0; x < w; ++x) row[x0 + x] = s[x];
        for (int x = 0; x < x0; ++x) row[x] = s[0];
        for (int x = x0 + w; x < ws; ++x) row[x] = s[w - 1];
    }
    return p;
}

std::vector<int16_t> rounded(const std::vector<double> &p, double lo, double hi) {
    std::vector<int16_t> out(p.size());
    for (std::size_t i = 0; i < p.size(); ++i) out[i] = int16_t(std::clamp(std::nearbyint(p[i]), lo, hi));   // np.round: mitad a par
    return out;
}

}  // namespace

namespace {

std::string envLower(const char *value) {
    std::string s;
    if (!value) return s;
    for (const char *p = value; *p; ++p) s.push_back(char(std::tolower(static_cast<unsigned char>(*p))));
    return s;
}

bool parseOnOff(const char *value, const char *name, bool defaultOn) {
    if (!value || !*value) return defaultOn;
    const std::string s = envLower(value);
    if (s == "1" || s == "on" || s == "true" || s == "yes") return true;
    if (s == "0" || s == "off" || s == "false" || s == "no") return false;
    throw Error(std::string(name) + ": unsupported value '" + value + "' (use 0|1|off|on|false|true)");
}

double parseAlpha(const char *value) {
    if (!value || !*value) return 1.0;
    // Solo los valores del diseño EXP-001b.
    static const double allowed[] = {0.0, 0.25, 0.5, 0.75, 1.0};
    char *end = nullptr;
    const double v = std::strtod(value, &end);
    if (end == value || (end && *end)) {
        throw Error("CDNG_OG_DEBLOCK_ALPHA: unsupported value '" + std::string(value) +
                    "' (use 0|0.25|0.5|0.75|1)");
    }
    for (double a : allowed) {
        if (std::fabs(v - a) < 1e-12) return a;
    }
    throw Error("CDNG_OG_DEBLOCK_ALPHA: unsupported value '" + std::string(value) +
                "' (use 0|0.25|0.5|0.75|1)");
}

}  // namespace

bool ogDeblockPrecompensationEnabled() {
    return parseOnOff(std::getenv("CDNG_OG_DEBLOCK_PRECOMP"), "CDNG_OG_DEBLOCK_PRECOMP", true);
}

DeblockPrecomp ogDeblockPrecompensation() {
    const char *pre = std::getenv("CDNG_OG_DEBLOCK_PRECOMP");
    const char *alphaEnv = std::getenv("CDNG_OG_DEBLOCK_ALPHA");
    const char *boundedEnv = std::getenv("CDNG_OG_DEBLOCK_BOUNDED");
    const bool enabled = parseOnOff(pre, "CDNG_OG_DEBLOCK_PRECOMP", true);
    const bool bounded = parseOnOff(boundedEnv, "CDNG_OG_DEBLOCK_BOUNDED", false);
    const bool alphaSet = alphaEnv && *alphaEnv;
    if (!enabled && alphaSet) {
        throw Error("CDNG_OG_DEBLOCK_ALPHA no admite valor con CDNG_OG_DEBLOCK_PRECOMP=0 (ablación)");
    }
    if (!enabled && bounded) {
        throw Error("CDNG_OG_DEBLOCK_BOUNDED=1 contradice CDNG_OG_DEBLOCK_PRECOMP=0");
    }
    if (bounded && alphaSet) {
        throw Error("CDNG_OG_DEBLOCK_BOUNDED y CDNG_OG_DEBLOCK_ALPHA son mutuamente excluyentes");
    }
    DeblockPrecomp cfg;
    cfg.enabled = enabled;
    cfg.bounded = bounded;
    cfg.alpha = (!enabled || bounded) ? 0.0 : parseAlpha(alphaEnv);
    return cfg;
}

RangeStats measureCodeRange(const std::vector<double> &samples, double lo, double hi) {
    RangeStats s;
    s.samples = samples.size();
    for (double v : samples) {
        if (v < lo) {
            ++s.math_below;
            const double ex = lo - v;
            s.excess_sum += ex;
            if (ex > s.excess_max) s.excess_max = ex;
        } else if (v > hi) {
            ++s.math_above;
            const double ex = v - hi;
            s.excess_sum += ex;
            if (ex > s.excess_max) s.excess_max = ex;
        }
        const double r = std::nearbyint(v);
        if (r < lo) ++s.round_below;
        else if (r > hi) ++s.round_above;
        const double c = std::clamp(r, lo, hi);
        if (c != r) {
            if (r < lo) ++s.clamped_low;
            else ++s.clamped_high;
        }
        if (c == lo) {
            ++s.at_lo;
            if (c == r) ++s.at_lo_natural;
        }
        if (c == hi) {
            ++s.at_hi;
            if (c == r) ++s.at_hi_natural;
        }
    }
    return s;
}

void floatCanvasPlanes(const Planes &planes, const Geometry &g, const DeblockPrecomp &precomp,
                       std::vector<double> &y, std::vector<double> &cb, std::vector<double> &cr) {
    if (planes.width != g.w || planes.height != g.h) throw Error("Planes do not match the frame geometry");
    y = place(planes.y.data(), g.w, g.h, g.ws, g.hs, g.x0, g.y0);
    applyDeblockPrecomp(y, g.ws, g.hs, precomp);
    const int hx = g.x0 / 2, hy = g.y0 / 2, hw = g.w / 2, hh = g.h / 2;
    cb = place(planes.cb.data(), hw, hh, g.ws / 2, g.hs / 2, hx, hy);
    cr = place(planes.cr.data(), hw, hh, g.ws / 2, g.hs / 2, hx, hy);
}

void floatCanvasPlanes(const Planes &planes, const Geometry &g, bool deblock,
                       std::vector<double> &y, std::vector<double> &cb, std::vector<double> &cr) {
    DeblockPrecomp cfg;
    cfg.enabled = deblock;
    cfg.alpha = 1.0;
    cfg.bounded = false;
    floatCanvasPlanes(planes, g, cfg, y, cb, cr);
}

CanvasPlanes canvasPlanes(const Planes &planes, const Geometry &g, const DeblockPrecomp &precomp) {
    if (planes.width != g.w || planes.height != g.h) throw Error("Planes do not match the frame geometry");
    CanvasPlanes out;
    out.ws = g.ws; out.hs = g.hs;
    {
        auto y = place(planes.y.data(), g.w, g.h, g.ws, g.hs, g.x0, g.y0);
        applyDeblockPrecomp(y, g.ws, g.hs, precomp);
        out.y = rounded(y, 0, 4095);
    }
    const int hx = g.x0 / 2, hy = g.y0 / 2, hw = g.w / 2, hh = g.h / 2;
    out.cb = rounded(place(planes.cb.data(), hw, hh, g.ws / 2, g.hs / 2, hx, hy), -4096, 4095);
    out.cr = rounded(place(planes.cr.data(), hw, hh, g.ws / 2, g.hs / 2, hx, hy), -4096, 4095);
    return out;
}

CanvasPlanes canvasPlanes(const Planes &planes, const Geometry &g, bool deblock) {
    DeblockPrecomp cfg;
    cfg.enabled = deblock;
    cfg.alpha = 1.0;
    cfg.bounded = false;
    return canvasPlanes(planes, g, cfg);
}
// ---------------------------------------------------------------------------------------------------------------
// Vista previa

Planes decodedPlanes(const CanvasPlanes &canvas, const Geometry &g) {
    std::vector<double> y(canvas.y.begin(), canvas.y.end());
    const int width = canvas.ws, height = canvas.hs;
    // Orden inverso al de precompensate: primero las fronteras entre filas de bloques, luego entre columnas.
    for (int i = 8; i < height; i += 8) {
        double *rin = y.data() + std::size_t(i - 2) * width, *ra = rin + width, *rb = ra + width, *rbin = rb + width;
        for (int x = 0; x < width; ++x) {
            const double a = (3 * rin[x] + 10 * ra[x] + 3 * rb[x]) / 16, b = (3 * ra[x] + 10 * rb[x] + 3 * rbin[x]) / 16;
            ra[x] = a; rb[x] = b;
        }
    }
    for (int r = 0; r < height; ++r) {
        double *row = y.data() + std::size_t(r) * width;
        for (int i = 8; i < width; i += 8) {
            const double a = (3 * row[i - 2] + 10 * row[i - 1] + 3 * row[i]) / 16, b = (3 * row[i - 1] + 10 * row[i] + 3 * row[i + 1]) / 16;
            row[i - 1] = a; row[i] = b;
        }
    }
    Planes out(g.w, g.h);
    for (int r = 0; r < g.h; ++r)
        for (int x = 0; x < g.w; ++x) out.y[std::size_t(r) * g.w + x] = float(y[std::size_t(g.y0 + r) * width + g.x0 + x]);
    const int hw = g.w / 2, hh = g.h / 2, wc = canvas.ws / 2;
    for (int r = 0; r < hh; ++r)
        for (int x = 0; x < hw; ++x) {
            out.cb[std::size_t(r) * hw + x] = canvas.cb[std::size_t(g.y0 / 2 + r) * wc + g.x0 / 2 + x];
            out.cr[std::size_t(r) * hw + x] = canvas.cr[std::size_t(g.y0 / 2 + r) * wc + g.x0 / 2 + x];
        }
    return out;
}

RgbImage planesCropToCamera(const Planes &planes, int x, int y, int width, int height, Route route,
                            const std::filesystem::path &resources, const OgModel *og) {
    if (x < 0 || y < 0 || width <= 0 || height <= 0 || x + width > planes.width || y + height > planes.height ||
        (y + height + 1) / 2 > planes.height / 2 || (x + width + 1) / 2 > planes.width / 2)
        throw Error("Preview crop outside the image");
    RgbImage out(width, height);
    const int hw = planes.width / 2;
    std::shared_ptr<const Scattered> forward;
    std::unique_ptr<const VideoAssist> va;
    if (route == Route::Pocket) {
        forward = scattered(resources / "model/pocket_forward_gen4.bin");
    } else if (route == Route::Va) {
        va = std::make_unique<VideoAssist>(resources / "model/video_assist_input_transform.json");
    } else if (!og || !og->impl || !og->impl->hasCodeFromYcc || !og->impl->hasCurve) {
        throw Error("Missing calibration");
    }
    static const double vaMatrix[9] = {1., -.5, 1.5, 1., -.5, -.5, 1., 1.5, -.5};
    int hint = -1;
    for (int j = 0; j < height; ++j)
        for (int i = 0; i < width; ++i) {
            const double ycc[3] = {planes.y[std::size_t(y + j) * planes.width + x + i],
                                   planes.cb[std::size_t((y + j) / 2) * hw + (x + i) / 2],
                                   planes.cr[std::size_t((y + j) / 2) * hw + (x + i) / 2]};
            float *px = out.px(i, j);
            if (route == Route::Pocket) {
                double rgb[3];
                forward->eval(ycc, rgb, hint);
                for (int k = 0; k < 3; ++k) px[k] = float(std::max(rgb[k], 0.0));
            } else {
                const double *m = route == Route::Va ? vaMatrix : og->impl->codeFromYcc.data();
                double camera[3];
                for (int k = 0; k < 3; ++k) {
                    const double code = std::fma(ycc[2], m[3 * k + 2], std::fma(ycc[1], m[3 * k + 1], ycc[0] * m[3 * k]));   // matmul de Accelerate
                    if (route == Route::Va) {
                        px[k] = float(std::max(va->decode(code), 0.0) / va->full_ / va->gain_[k]);
                    } else {
                        camera[k] = og->impl->curve.decode(code) / og->impl->scale[k];
                        px[k] = float(camera[k]);
                    }
                }
                if (route == Route::Og && og->impl->hasTone) {
                    double plain[3];
                    og->impl->uncorrect(camera, plain);
                    for (int k = 0; k < 3; ++k) px[k] = float(plain[k]);
                }
            }
        }
    return out;
}

}  // namespace cdng
