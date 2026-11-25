// Bloque B (señal): pruebas autónomas y de paridad con el prototipo.
#include "../signal_internal.hpp"
#include "check.hpp"

#include <simdjson.h>

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <fstream>
#include <set>

// ogSourceInfo necesita el lector DNG del bloque A: poner a 1 cuando dng.cpp esté implementado.
#ifndef CDNG_SIGNAL_TEST_DNG
#define CDNG_SIGNAL_TEST_DNG 0
#endif

using namespace cdng;

namespace {

const std::filesystem::path Resources = resourceRoot();

constexpr double Pi = 3.14159265358979323846;   // M_PI, que MSVC no define (como en engine/resize.cpp)

struct Diff { double max = 0; std::size_t over = 0, n = 0; };

template <class A, class B> Diff diff(const A &a, const B &b, double tol) {
    Diff d;
    d.n = std::min(a.size(), b.size());
    CHECK(a.size() == b.size());
    for (std::size_t i = 0; i < d.n; ++i) {
        const double e = std::fabs(double(a[i]) - double(b[i]));
        if (!(e <= d.max)) d.max = std::isnan(e) ? INFINITY : std::max(d.max, e);
        if (!(e <= tol)) ++d.over;
    }
    return d;
}

// diferencia relativa (|a − b| / max(|b|, piso)) de cada valor
template <class A, class B> std::vector<double> relative(const A &a, const B &b, double floor) {
    std::vector<double> out(std::min(a.size(), b.size()));
    for (std::size_t i = 0; i < out.size(); ++i) out[i] = std::fabs(double(a[i]) - double(b[i])) / std::max(std::fabs(double(b[i])), floor);
    return out;
}

constexpr uint32_t IsoRevision = 1128878927u, LegacyRevision = 1128549959u;
constexpr const char *LegacySha = "63a0358740bfc98a459769a68d5490345785d1cbba42aa7086c6fe192d62fb5c";

std::string text(const std::filesystem::path &path) {
    const auto bytes = readBytes(path);
    return std::string(bytes.begin(), bytes.end());
}

// Copia el JSON de señal y su perfil a una carpeta temporal, con sustituciones de texto en el JSON de señal (y un
// byte cambiado en el perfil si alterProfile), y los carga.
OgModel loadVariant(const std::vector<std::pair<std::string, std::string>> &replace, bool alterProfile = false) {
    const auto dir = std::filesystem::temp_directory_path() / "cdng_signal_test";
    std::filesystem::remove_all(dir);
    std::filesystem::create_directories(dir);
    auto signal = text(Resources / "model/pocket_og_cinemadng_signal.json");
    for (const auto &[from, to] : replace) {
        const auto at = signal.find(from);
        CHECK(at != std::string::npos);
        if (at != std::string::npos) signal.replace(at, from.size(), to);
    }
    std::ofstream(dir / "pocket_og_cinemadng_signal.json", std::ios::binary) << signal;
    auto profile = text(Resources / "model/pocket_og_cinemadng_iso_camera_support.json");
    if (alterProfile) profile.back() = profile.back() == ' ' ? '\n' : ' ';
    std::ofstream(dir / "pocket_og_cinemadng_iso_camera_support.json", std::ios::binary) << profile;
    struct Cleanup { std::filesystem::path dir; ~Cleanup() { std::filesystem::remove_all(dir); } } cleanup{dir};
    return loadOgModel(dir / "pocket_og_cinemadng_signal.json");
}

// Tabla ISO de la Pocket original: BaselineExposure → ISO con tolerancia, sin redondear a la más cercana.
void isoInference(const OgModel &og) {
    const struct { double baseline; int iso; double gain; } stops[] = {{0.1, 200, 0.5}, {1.1, 400, 1}, {2.1, 800, 2}, {3.1, 1600, 4}};
    for (const auto &s : stops) {
        for (const double delta : {0.0, 1e-9, 0.009, -0.009}) {
            const auto e = ogExposure(og, s.baseline + delta, std::nullopt);
            CHECK(e.iso == s.iso && std::fabs(e.postGain - s.gain) < 1e-9);
        }
        CHECK(ogExposure(og, s.baseline, s.iso).iso == s.iso);                // ISO EXIF coherente
        CHECK(ogExposure(og, double(float(s.baseline)), std::nullopt).iso == s.iso);   // valor guardado en float
        CHECK(ogExposure(og, double(int(std::lround(s.baseline * 10))) / 10, std::nullopt).iso == s.iso);   // SRATIONAL n/10
    }
    auto error = [&](double baseline, std::optional<int> exif) {
        try { ogExposure(og, baseline, exif); } catch (const Error &e) { return std::string(e.what()); }
        return std::string();
    };
    const std::string outside = "BaselineExposure is not represented by the Pocket Cinema Camera ISO table (ISO 200, 400, 800, 1600)";
    // Entre dos ISO, fuera de la tolerancia o fuera de la tabla: error, nunca la ISO más cercana.
    for (const double baseline : {0.0, 0.089, 0.111, 0.6, 1.0, 1.6, 2.6, 3.089, 3.111, 3.6, 4.1, -0.9, 5.0, 1.2})
        CHECK(error(baseline, std::nullopt) == outside);
    CHECK(error(std::nan(""), std::nullopt) == "Invalid source BaselineExposure");
    CHECK(error(INFINITY, std::nullopt) == "Invalid source BaselineExposure");
    for (const auto &[baseline, exif] : std::vector<std::pair<double, int>>{{3.1, 800}, {3.1, 1250}, {1.1, 1600}, {0.1, 400}})
        CHECK(error(baseline, exif) == "source ISO does not match its BaselineExposure");
    // La compensación de cada ISO es BaselineExposure − log2(baseline_exposure_gain), sin tocar los datos.
    for (const auto &s : stops)
        CHECK_NEAR(std::log2(ogExposure(og, s.baseline, std::nullopt).postGain), s.baseline - std::log2(og.impl->baselineGain), 1e-9);
}

// La tabla del JSON de señal y la del Camera Support se cotejan al cargar; cualquier discrepancia es un error.
void isoTableConsistency() {
    const std::string mismatch = "Camera Support ISO table does not match this signal calibration";
    auto message = [](const std::vector<std::pair<std::string, std::string>> &replace) {
        try { loadVariant(replace); } catch (const Error &e) { return std::string(e.what()); }
        return std::string();
    };
    CHECK(message({}).empty());
    CHECK(message({{"\"baseline_exposure\": 3.1", "\"baseline_exposure\": 3.2"}}) == mismatch);   // post_gain ya no es 2^BE/g
    CHECK(message({{"\"iso\": 1600", "\"iso\": 3200"}}) == mismatch);
    CHECK(message({{"\"reference_iso\": 400", "\"reference_iso\": 800"}}) == mismatch);
    CHECK(message({{"\"baseline_exposure_tolerance\": 0.01", "\"baseline_exposure_tolerance\": 0.5"}}) == mismatch);   // alcanza a la vecina
    CHECK(message({{"\"baseline_exposure_tolerance\": 0.01", "\"baseline_exposure_tolerance\": 0"}}) == mismatch);
    CHECK(!message({{"\"baseline_exposure_tolerance\": 0.01", "\"baseline_exposure_tolerance_x\": 0.01"}}).empty());   // obligatorio
}

// Compatibilidad: la revisión calibrada se conserva byte a byte y la nueva solo cambia los campos ISO.
void profileRevisions(const OgModel &og) {
    const auto legacyPath = Resources / "model/pocket_og_cinemadng_camera_support.json";
    const auto isoPath = Resources / "model/pocket_og_cinemadng_iso_camera_support.json";
    CHECK(sha256Hex(readBytes(legacyPath)) == LegacySha);
    simdjson::dom::parser pa, pb;
    const auto legacyText = text(legacyPath), isoText = text(isoPath), signalText = text(Resources / "model/pocket_og_cinemadng_signal.json");
    simdjson::dom::element legacy, iso, signal;
    CHECK(!pa.parse(legacyText).get(legacy) && !pb.parse(isoText).get(iso));
    simdjson::dom::parser ps;
    CHECK(!ps.parse(signalText).get(signal));
    simdjson::dom::element a = legacy["cameras"].at(0), b = iso["cameras"].at(0);
    CHECK(simdjson::minify(a["manufacturer"]) == simdjson::minify(b["manufacturer"]) &&
          simdjson::minify(a["camera_model"]) == simdjson::minify(b["camera_model"]));
    CHECK(uint64_t(a["revision"]) == LegacyRevision && uint64_t(b["revision"]) == og.revision && og.revision != LegacyRevision);
    simdjson::dom::object ma, mb;
    CHECK(!a["modes"].at(0).get_object().get(ma) && !b["modes"].at(0).get_object().get(mb));
    const std::set<std::string> isoFields{"iso_params", "native_iso", "iso_modification_supported"};
    std::size_t compared = 0;
    const std::size_t keysA = ma.size(), keysB = mb.size();
    for (auto field : mb) {
        const std::string key(field.key);
        if (isoFields.count(key)) continue;
        simdjson::dom::element other;
        CHECK(!ma[key].get(other) && simdjson::minify(other) == simdjson::minify(field.value));   // matrices, inv_sqrt, niveles…
        ++compared;
    }
    CHECK(keysA == keysB && compared + isoFields.size() == keysB && mb["csc_params"].error() == simdjson::SUCCESS);
    CHECK(bool(mb["iso_modification_supported"]) && !bool(ma["iso_modification_supported"]));
    CHECK(uint64_t(mb["native_iso"]) == 400 && uint64_t(mb["iso_params"].at(0)["base_iso"]) == 400);
    CHECK(uint64_t(ma["native_iso"]) == 1250);
    simdjson::dom::array legacyRevisions;
    CHECK(!signal["legacy_revisions"].get_array().get(legacyRevisions) && legacyRevisions.size() == 1 &&
          uint64_t(legacyRevisions.at(0)) == LegacyRevision);
    // El JSON de señal no cambia la curva ni las matrices: solo la revisión, el perfil que nombra y la tabla ISO.
    const auto &m = *og.impl;
    CHECK(m.hasCurve && m.encodingMax == 4095 && std::fabs(m.baselineGain - std::pow(2.0, 1.1)) < 1e-9);
}

void report(const std::string &what, const Diff &d) {
    std::printf("  %-48s máx %.6g  (%zu de %zu por encima)\n", what.c_str(), d.max, d.over, d.n);
}

RgbImage image(const check::Case &c, const char *key) {
    const auto &shape = c.shape.at(key);
    RgbImage img{int(shape[1]), int(shape[0])};
    img.data = c.array<float>(key);
    return img;
}

Planes planesOf(const check::Case &c, const char *y = "y", const char *cb = "cb", const char *cr = "cr") {
    const auto &shape = c.shape.at(y);
    Planes p{int(shape[1]), int(shape[0])};
    p.y = c.array<float>(y); p.cb = c.array<float>(cb); p.cr = c.array<float>(cr);
    return p;
}

Route routeOf(const std::string &name) { return name == "va" ? Route::Va : name == "og" ? Route::Og : Route::Pocket; }

std::vector<std::string> cases(const char *prefix) {
    std::vector<std::string> out;
    for (const auto &entry : std::filesystem::directory_iterator(std::string(check::goldenRoot()) + "/signal")) {
        const auto name = entry.path().filename().string();
        if (name.rfind(prefix, 0) == 0 && entry.path().extension() == ".txt") out.push_back(entry.path().stem().string());
    }
    std::sort(out.begin(), out.end());
    return out;
}


// Reescalado de la ruta Sigma: un filo vertical entre dos colores con el mismo verde, de 256 a 128 de ancho. La
// proporción de cada canal debe cruzar la mitad del salto donde está el filo en la imagen de salida, medido en el
// píxel donde el descodificador pinta cada muestra (Cb abajo-derecha, Cr arriba-izquierda del cuarteto).
double chromaEdgeError(const Planes &p, bool blue, double edgeOut, bool vertical = false) {
    // Cruce del filo a lo largo de una fila (o, en vertical, de una columna) del plano de croma.
    const auto &r = blue ? p.cb : p.cr;
    const int hw = p.width / 2, hh = p.height / 2, n = vertical ? hh : hw;
    const auto at = [&](int j) -> double {
        return vertical ? r[std::size_t(j) * hw + hw / 2] : r[std::size_t(hh / 2) * hw + j];
    };
    const double a = at(2), b = at(n - 3), mid = (a + b) / 2;
    for (int j = 2; j < n - 3; ++j) {
        const double v0 = at(j), v1 = at(j + 1);
        if ((v0 - mid) * (v1 - mid) <= 0 && v0 != v1) {
            const double q = j + (mid - v0) / (v1 - v0);              // en muestras de cuarteto
            const double px = 2 * q + (blue ? 1.5 : 0.5);             // centro del píxel donde se pinta, en la salida
            return px - edgeOut;
        }
    }
    return INFINITY;
}

void resizeChromaPhase() {
    // Filo suave (rampa de 6 píxeles) en ocho posiciones seguidas: la media quita el sesgo de dónde caen los fotositos
    // azules y rojos respecto a un filo concreto y deja el desfase sistemático del reescalado. En las dos direcciones.
    const auto map = videoAssistMap(Resources);
    const float left[] = {.6f, .4f, .15f}, right[] = {.2f, .4f, .55f};
    const int cfa[] = {1, 2, 0, 1};                                   // G B / R G
    for (const bool vertical : {false, true}) {
        const int W = vertical ? 32 : 256, H = vertical ? 256 : 32;
        double sum[4] = {0, 0, 0, 0}, base[2] = {0, 0};
        for (int shift = 0; shift < 8; ++shift) {
            const double E = 128 + shift + .5;                        // centro de la rampa, en coordenadas de borde
            BayerImage mosaic{W, H, std::vector<float>(std::size_t(W) * H), std::vector<uint8_t>(std::size_t(W) * H)};
            for (int y = 0; y < H; ++y) for (int x = 0; x < W; ++x) {
                const auto i = std::size_t(y) * W + x;
                const double t = std::clamp(((vertical ? y : x) + .5 - E) / 6 + .5, 0.0, 1.0);
                mosaic.colors[i] = uint8_t(cfa[2 * (y % 2) + x % 2]);
                mosaic.samples[i] = float((1 - t) * left[mosaic.colors[i]] + t * right[mosaic.colors[i]]);
            }
            const auto fresh = bayerPlanesSized(mosaic, *map, 1, W / 2, H / 2);
            const auto old = resizePlanes(bayerPlanes(mosaic, *map, 1), W / 2, H / 2);
            const auto full = bayerPlanes(mosaic, *map, 1);
            base[0] += chromaEdgeError(full, true, E, vertical) / 2; base[1] += chromaEdgeError(full, false, E, vertical) / 2;
            sum[0] += chromaEdgeError(fresh, true, E / 2, vertical); sum[1] += chromaEdgeError(fresh, false, E / 2, vertical);
            sum[2] += chromaEdgeError(old, true, E / 2, vertical); sum[3] += chromaEdgeError(old, false, E / 2, vertical);
        }
        for (auto &v : sum) v /= 8;
        for (auto &v : base) v /= 8;
        std::printf("  %s, sin reescalar (en píxeles de salida): Cb %+.3f, Cr %+.3f px\n", vertical ? "vertical" : "horizontal", base[0], base[1]);
        std::printf("  filo de croma reescalado ½ (media de 8 posiciones): Cb %+.3f, Cr %+.3f px; reescalando las muestras: Cb %+.3f, Cr %+.3f px\n",
                    sum[0], sum[1], sum[2], sum[3]);
        // La referencia es el mismo filo sin reescalar (en píxeles de salida): la curva de raíz y la rejilla de fotositos
        // ya lo desplazan un poco, igual en las dos rutas. Leyendo en los sitios de salida se queda ahí; reescalando las
        // muestras se va ±¼ de píxel (Cb hacia abajo-derecha, Cr hacia arriba-izquierda).
        CHECK(std::abs(sum[0] - base[0]) < .05);
        CHECK(std::abs(sum[1] - base[1]) < .05);
    }
}

// El mismo Bayer con su patrón en las cuatro fases: la imagen empieza un fotosito más a la derecha, más abajo o las
// dos cosas. La luma tiene que ser la misma desplazada; la croma de cada sitio, la de la fase 0 en el mismo punto de la
// escena (interpolada entre sus sitios), sin sesgo. Escena con textura de luma y color que cambia despacio.
void bayerPhase() {
    const auto map = videoAssistMap(Resources);
    const int W = 96, H = 64, B = 10;                       // B: borde que no se mide (filtros de 7 y espejo)
    const int pattern[] = {0, 1, 1, 2};                      // R G / G B en coordenadas de la escena
    const auto scene = [](int c, double y, double x) {
        const double tone[3] = {.5 + .15 * std::sin(x / 23), .4 + .05 * std::cos(y / 31), .25 + .1 * std::cos(y / 19 + x / 41)};
        const double texture = 1 + .3 * std::sin(2 * Pi * x / 14) * std::sin(2 * Pi * y / 18);
        return tone[c] * texture;
    };
    const auto mosaic = [&](int dy, int dx) {
        BayerImage m{W, H, std::vector<float>(std::size_t(W) * H), std::vector<uint8_t>(std::size_t(W) * H)};
        for (int y = 0; y < H; ++y) for (int x = 0; x < W; ++x) {
            const auto i = std::size_t(y) * W + x;
            m.colors[i] = uint8_t(pattern[2 * ((y + dy) % 2) + (x + dx) % 2]);
            m.samples[i] = float(scene(m.colors[i], y + dy, x + dx));
        }
        return m;
    };
    const auto ratio = [](const BayerFields &f, bool blue, int i, int j) {
        const auto q = std::size_t(i) * (f.width / 2) + j;
        return blue ? f.numB[q] / std::max(double(f.denB[q]), 1.0) : f.numR[q] / std::max(double(f.denR[q]), 1.0);
    };
    const auto f0 = bayerFields(mosaic(0, 0), *map, 1);
    for (const auto [dy, dx] : {std::pair{0, 1}, std::pair{1, 0}, std::pair{1, 1}}) {
        const auto f = bayerFields(mosaic(dy, dx), *map, 1);
        double lumaMax = 0;
        for (int y = B; y < H - B; ++y) for (int x = B; x < W - B; ++x)
            lumaMax = std::max(lumaMax, std::abs(double(f.y[std::size_t(y) * W + x]) - f0.y[std::size_t(y + dy) * W + x + dx]));
        double chromaMax = 0, chromaBias = 0; int n = 0;
        for (const bool blue : {true, false})
            for (int i = B / 2; i < H / 2 - B / 2; ++i) for (int j = B / 2; j < W / 2 - B / 2; ++j) {
                // el sitio (i, j) de esta fase está medio cuarteto más allá en la rejilla de sitios de la fase 0
                const double fy = i + dy / 2.0, fx = j + dx / 2.0;
                const int i0 = int(fy), j0 = int(fx); const double ty = fy - i0, tx = fx - j0;
                const double ref = (1 - ty) * ((1 - tx) * ratio(f0, blue, i0, j0) + tx * ratio(f0, blue, i0, j0 + 1)) +
                                   ty * ((1 - tx) * ratio(f0, blue, i0 + 1, j0) + tx * ratio(f0, blue, i0 + 1, j0 + 1));
                const double e = ratio(f, blue, i, j) - ref;
                chromaMax = std::max(chromaMax, std::abs(e)); chromaBias += e; ++n;
            }
        chromaBias /= n;
        std::printf("  fase desplazada (%d, %d): luma máx %.5f códigos · proporción de croma máx %.4f, sesgo %+.5f\n", dx, dy, lumaMax, chromaMax, chromaBias);
        CHECK(lumaMax < 1e-3);
        CHECK(chromaMax < .003);
        CHECK(std::abs(chromaBias) < 5e-4);
    }
}

void bayerSignal() {
    const auto map = videoAssistMap(Resources);
    for (const float red : {.7f, 1.2f}) {
        const float rgb[] = {red, .4f, .2f};
        float expected[3];
        map->map(rgb, expected, 1);
        for (int phase = 0; phase < 4; ++phase) {
            const int cfa[] = {0, 1, 1, 2};
            BayerImage mosaic{16, 12, std::vector<float>(192), std::vector<uint8_t>(192)};
            for (int y = 0; y < mosaic.height; ++y) for (int x = 0; x < mosaic.width; ++x) {
                const auto i = y * mosaic.width + x;
                mosaic.colors[i] = uint8_t(cfa[(2 * (y % 2) + x % 2) ^ phase]);
                mosaic.samples[i] = rgb[mosaic.colors[i]];
            }
            auto planes = resizePlanes(bayerPlanes(mosaic, *map, 1), 8, 8);
            finishPlanes(planes, *map, Route::Va, 0, 0);
            CHECK(planes.y.size() == 64 && planes.cb.size() == 16 && planes.cr.size() == 16);
            for (const auto value : planes.y) CHECK(std::abs(value - expected[0]) < .002f);
            for (const auto value : planes.cb) CHECK(std::abs(value - expected[1]) < .002f);
            for (const auto value : planes.cr) CHECK(std::abs(value - expected[2]) < .002f);
        }
    }
    // Un salto dentro del cuarteto: cada croma sigue la Y de su sitio, no la media.
    for (const bool horizontal : {false, true}) {
        Planes p(16, 12);
        for (int y = 0; y < p.height; ++y) for (int x = 0; x < p.width; ++x)
            p.y[std::size_t(y) * p.width + x] = (horizontal ? x >= 7 : y >= 5) ? 1320 : 521;
        std::fill(p.cb.begin(), p.cb.end(), -.4f);
        std::fill(p.cr.begin(), p.cr.end(), -.1f);
        finishPlanes(p, *map, Route::Va, 0, 0);
        for (int y = 0; y < p.height / 2; ++y) for (int x = 0; x < p.width / 2; ++x) {
            const auto i = std::size_t(y) * (p.width / 2) + x;
            const auto yi = std::size_t(2 * y) * p.width + 2 * x;
            const double scale = 2 * (1 - .5 / 4);
            // La señal es la distancia al vértice de la parábola del SDK (sdk_decode.vertex del modelo), antes k0.
            CHECK(std::abs(p.cb[i] - -.4 * (p.y[yi + p.width + 1] - 64.527006) / scale) < .001);
            CHECK(std::abs(p.cr[i] - -.1 * (p.y[yi] - 64.527006) / scale) < .001);
        }
    }
}

void autonomous(const OgModel &og) {
    CHECK(sha256Hex({}) == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
    const std::string abc = "abc";
    CHECK(sha256Hex(std::vector<uint8_t>(abc.begin(), abc.end())) ==
          "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
    std::vector<uint8_t> million(1000000, 'a');
    CHECK(sha256Hex(million) == "cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0");

    CHECK(og.cameraType == "Blackmagic Pocket Cinema Camera" && og.gen == 5 && og.revision == IsoRevision);
    {   // perfil alterado → mismo error que load_support_model
        std::string message;
        try { loadVariant({}, true); } catch (const Error &e) { message = e.what(); }
        CHECK(message == "Camera Support JSON does not match this signal calibration");
    }
    {   // infer_as_shot en un nodo de la tabla (5600 K, tinte 10) y fuera del dominio
        const auto &m = *og.impl;
        const std::size_t nt = m.tints.size();
        const std::size_t i = std::find_if(m.mired.begin(), m.mired.end(), [](double v) { return std::fabs(v - 1e6 / 5600) < 1e-9; }) - m.mired.begin();
        const std::size_t j = std::find(m.tints.begin(), m.tints.end(), 10.0) - m.tints.begin();
        CHECK(i < m.mired.size() && j < nt);
        const Vec3 neutral{0.5 / m.red[i * nt + j], 0.5, 0.5 / m.blue[i * nt + j]};
        const auto shot = inferAsShot(neutral, m);
        CHECK(shot.first == 5600 && shot.second == 10);
        std::string message;
        try { inferAsShot({1, 1, 10}, m); } catch (const Error &e) { message = e.what(); }
        CHECK(message == "AsShotNeutral is outside the measured profile domain");
    }
    {   // Video Assist: gris → Cb = Cr ≈ 0 salvo la ganancia por canal; negro → el código donde la recta del SDK
        // cruza el cero (sdk_decode: knee_code + (0 − knee_x)/linear_slope = 96,02), no k0 = 65,79 de la raíz pura
        const auto va = videoAssistMap(Resources);
        const float rgb[9] = {0, 0, 0, .18f, .18f, .18f, -.0003f, -.0003f, -.0003f};
        float ycc[9];
        va->map(rgb, ycc, 3);
        CHECK_NEAR(ycc[0], 96.0227, 1e-3);
        CHECK(ycc[6] > 10 && ycc[6] < 96 && std::fabs(ycc[7]) < 3 && std::fabs(ycc[8]) < 3);   // bajo el negro: recta, sin recorte
        CHECK(ycc[3] > 1500 && ycc[3] < 1800 && std::fabs(ycc[4]) < 20 && std::fabs(ycc[5]) < 20);
    }
    {   // lienzo de un plano constante: la precompensación lo deja igual y los bordes se replican
        Planes p(64, 32);
        std::fill(p.y.begin(), p.y.end(), 1000.4f); std::fill(p.cb.begin(), p.cb.end(), -12.5f); std::fill(p.cr.begin(), p.cr.end(), 13.5f);
        const Geometry g(64, 32);
        const auto c = canvasPlanes(p, g, true);
        CHECK(c.ws == g.ws && c.hs == g.hs && c.y.size() == std::size_t(g.ws) * g.hs);
        CHECK(std::all_of(c.y.begin(), c.y.end(), [](int16_t v) { return v == 1000; }));
        CHECK(std::all_of(c.cb.begin(), c.cb.end(), [](int16_t v) { return v == -12; }));   // mitad a par
        CHECK(std::all_of(c.cr.begin(), c.cr.end(), [](int16_t v) { return v == 14; }));
        auto q = p;
        denoisePlanes(q, 0, 0);
        CHECK(q.y == p.y && q.cb == p.cb);
    }
    {   // ruta pocket: gris medio dentro del modelo, vista previa de vuelta al mismo gris
        const auto pocket = pocketMap(Resources);
        const float rgb[3] = {.18f, .18f, .18f};
        float ycc[3];
        pocket->map(rgb, ycc, 1);
        CHECK(ycc[0] > 500 && ycc[0] < 2500);
        Planes p(2, 2);
        std::fill(p.y.begin(), p.y.end(), ycc[0]); p.cb[0] = ycc[1]; p.cr[0] = ycc[2];
        const auto back = planesCropToCamera(p, 0, 0, 2, 2, Route::Pocket, Resources, nullptr);
        for (int k = 0; k < 3; ++k) CHECK_NEAR(back.px(1, 1)[k], .18, .01);
    }
}

void parity(const OgModel &og) {
    std::printf("paridad del bloque B (diferencias absolutas; tolerancias del README)\n");
    {
        const auto c = check::Case::load("signal", "og_model");
        // El prototipo describe la calibración original: la revisión calibrada, que se conserva para los BRAW ya escritos.
        CHECK(og.cameraType == c.str("camera_type") && og.gen == int(c.num("gen")) && LegacyRevision == uint32_t(c.num("revision")));
        CHECK(sha256Hex(readBytes(Resources / "model/pocket_og_cinemadng_camera_support.json")) == c.str("sha"));
    }
    {
        const auto c = check::Case::load("signal", "infer_as_shot");
        const auto neutral = c.array<double>("neutral");
        const auto kelvin = c.array<int32_t>("kelvin"), tint = c.array<int32_t>("tint");
        int same = 0;
        for (std::size_t i = 0; i < kelvin.size(); ++i) {
            int k = -1, t = -1;
            try { std::tie(k, t) = inferAsShot({neutral[3 * i], neutral[3 * i + 1], neutral[3 * i + 2]}, *og.impl); } catch (const Error &) {}
            if (k == kelvin[i] && t == tint[i]) ++same;
            else std::fprintf(stderr, "infer_as_shot %zu: (%d, %d) frente a (%d, %d)\n", i, k, t, kelvin[i], tint[i]);
        }
        CHECK(same == int(kelvin.size()));
        std::printf("  %-48s %d de %zu iguales\n", "infer_as_shot (nube de neutros)", same, kelvin.size());
    }
    for (const auto &name : cases("asshot_")) {
        const auto c = check::Case::load("signal", name);
        const auto n = c.array<double>("neutral");
        const auto shot = inferAsShot({n[0], n[1], n[2]}, *og.impl);
        CHECK(shot.first == int(c.num("kelvin")) && shot.second == int(c.num("tint")));
        std::printf("  %-48s (%d, %d)\n", name.c_str(), shot.first, shot.second);
    }
#if CDNG_SIGNAL_TEST_DNG
    for (const auto &name : cases("source_")) {
        const auto c = check::Case::load("signal", name);
        std::string error;
        OgShot shot;
        try { shot = ogSourceInfo(Dng(c.str("path")), og); } catch (const Error &e) { error = e.what(); }
        CHECK(error == c.str("error"));
        if (error.empty()) CHECK(shot.iso == int(c.num("iso")) && shot.kelvin == int(c.num("kelvin")) && shot.tint == int(c.num("tint")));
    }
#endif
    {   // interpoladores pocket en los puntos del modelo y en una nube aleatoria
        const auto pocket = pocketMap(Resources);
        const auto c = check::Case::load("signal", "pocket_inverse");
        const auto rgb = c.array<float>("rgb");
        const auto expected = c.array<double>("ycc");
        std::vector<float> ycc(rgb.size());
        pocket->map(rgb.data(), ycc.data(), rgb.size() / 3);
        const auto d = diff(ycc, expected, 0.5);
        report("PocketMap (puntos del modelo + aleatorios)", d);
        if (std::getenv("CDNG_SIGNAL_DEBUG"))
            for (std::size_t i = 0; i < ycc.size(); ++i)
                if (std::fabs(ycc[i] - expected[i]) > 0.5) std::fprintf(stderr, "pocket %zu: %.9g frente a %.9g\n", i, ycc[i], expected[i]);
        CHECK(d.max <= 0.5);
    }
    {
        const auto c = check::Case::load("signal", "pocket_forward");
        const auto ycc = c.array<float>("ycc");
        const auto expected = c.array<double>("rgb");
        std::vector<double> got;
        const std::size_t n = ycc.size() / 3;
        Planes p(2, 2);
        for (std::size_t i = 0; i < n; ++i) {   // un píxel cada vez a través de la API de vista previa
            std::fill(p.y.begin(), p.y.end(), ycc[3 * i]); p.cb[0] = ycc[3 * i + 1]; p.cr[0] = ycc[3 * i + 2];
            const auto rgb = planesCropToCamera(p, 0, 0, 1, 1, Route::Pocket, Resources, nullptr);
            for (int k = 0; k < 3; ++k) got.push_back(std::max(expected[3 * i + k], 0.0) - rgb.px(0, 0)[k]);
        }
        const std::vector<double> zero(got.size(), 0.0);
        const auto d = diff(got, zero, 1e-5);
        report("PocketForward (puntos del modelo + aleatorios)", d);
        CHECK(d.max <= 1e-5);
    }
    for (const auto &name : cases("map_")) {
        const auto c = check::Case::load("signal", name);
        const auto route = routeOf(c.str("route"));
        const auto map = route == Route::Va ? videoAssistMap(Resources) : route == Route::Og ? ogSignalMap(og) : pocketMap(Resources);
        const auto rgb = c.array<float>("rgb");
        std::vector<float> ycc(rgb.size());
        map->map(rgb.data(), ycc.data(), rgb.size() / 3);
        const auto d = diff(ycc, c.array<double>("ycc"), 0.05);
        report(name, d);
        CHECK(d.over == 0);
    }
    for (const auto &name : cases("planes_")) {
        const auto c = check::Case::load("signal", name);
        const auto route = routeOf(c.str("route"));
        const auto map = route == Route::Va ? videoAssistMap(Resources) : route == Route::Og ? ogSignalMap(og) : pocketMap(Resources);
        const auto planes = imagePlanes(image(c, "rgb"), *map);
        const Diff d[3] = {diff(planes.y, c.array<float>("y"), 0.05), diff(planes.cb, c.array<float>("cb"), 0.05),
                           diff(planes.cr, c.array<float>("cr"), 0.05)};
        const char *ch[3] = {"Y", "Cb", "Cr"};
        for (int k = 0; k < 3; ++k) { report(name + " " + ch[k], d[k]); CHECK(d[k].over == 0); }
    }
    for (const auto &name : cases("canvas_")) {
        const auto c = check::Case::load("signal", name);
        const auto label = name.substr(7, name.rfind('_') - 7);
        const auto planes = planesOf(check::Case::load("signal", "planes_" + label));
        Geometry g(int(c.num("w")), int(c.num("h")));
        g.ws = int(c.num("ws")); g.hs = int(c.num("hs")); g.x0 = int(c.num("x0")); g.y0 = int(c.num("y0"));
        const auto canvas = canvasPlanes(planes, g, c.num("deblock") != 0);
        const Diff d[3] = {diff(canvas.y, c.array<int16_t>("y"), 0), diff(canvas.cb, c.array<int16_t>("cb"), 0),
                           diff(canvas.cr, c.array<int16_t>("cr"), 0)};
        const char *ch[3] = {"Y", "Cb", "Cr"};
        for (int k = 0; k < 3; ++k) {
            report(name + " " + ch[k], d[k]);
            CHECK(d[k].max <= 1 && double(d[k].over) <= 1e-4 * double(d[k].n));
        }
    }
    for (const auto &name : cases("noise_")) {
        const auto c = check::Case::load("signal", name);
        const auto planes = planesOf(check::Case::load("signal", "planes_" + name.substr(6)));
        const auto luma = noiseModel(planes.y.data(), planes.width, planes.height, nullptr);
        const auto yh = c.array<float>("yh");
        const auto chroma = noiseModel(planes.cb.data(), planes.width / 2, planes.height / 2, yh.data());
        const std::vector<double> zl(luma.variances.size(), 0.0), zc(chroma.variances.size(), 0.0);
        const auto dl = diff(relative(luma.variances, c.array<float>("variances"), 1e-12), zl, 1e-6);
        const auto dc = diff(relative(chroma.variances, c.array<float>("cvariances"), 1e-12), zc, 1e-6);
        CHECK(diff(luma.centers, c.array<float>("centers"), 0).over == 0 && diff(chroma.centers, c.array<float>("ccenters"), 0).over == 0);
        report(name + " varianzas Y (relativa)", dl);
        report(name + " varianzas Cb (relativa)", dc);
        CHECK(dl.over == 0 && dc.over == 0);
    }
    for (const auto &name : cases("nr_")) {
        const auto c = check::Case::load("signal", name);
        auto rest = name.substr(3);
        rest = rest.substr(0, rest.rfind('_')); rest = rest.substr(0, rest.rfind('_'));
        auto planes = planesOf(check::Case::load("signal", "planes_" + rest));
        denoisePlanes(planes, c.num("luma") / 100, c.num("chroma") / 100);
        const Diff d[3] = {diff(planes.y, c.array<float>("y"), 0.05), diff(planes.cb, c.array<float>("cb"), 0.05),
                           diff(planes.cr, c.array<float>("cr"), 0.05)};
        const char *ch[3] = {"Y", "Cb", "Cr"};
        for (int k = 0; k < 3; ++k) { report(name + " " + ch[k], d[k]); CHECK(d[k].over == 0); }
    }
    for (const auto &name : cases("crop_")) {
        const auto c = check::Case::load("signal", name);
        auto label = name.substr(5, name.rfind('_') - 5);
        const auto planes = planesOf(check::Case::load("signal", "planes_" + label));
        const auto route = routeOf(c.str("route"));
        const auto rgb = planesCropToCamera(planes, int(c.num("x")), int(c.num("y")), int(c.num("width")), int(c.num("height")),
                                            route, Resources, &og);
        const auto expected = c.array<double>("rgb");
        std::vector<double> rel(expected.size());
        for (std::size_t i = 0; i < rel.size(); ++i)
            rel[i] = std::fabs(rgb.data[i] - expected[i]) / std::max(std::fabs(expected[i]), 1e-3);
        const auto d = diff(rel, std::vector<double>(rel.size(), 0.0), 1e-5);
        report(name + " (relativa)", d);
        if (std::getenv("CDNG_SIGNAL_DEBUG"))
            for (std::size_t i = 0; i < rel.size(); i += 3)
                if (rel[i] > 1e-5 || rel[i + 1] > 1e-5 || rel[i + 2] > 1e-5) {
                    const int px = int(i / 3) % int(c.num("width")) + int(c.num("x")), py = int(i / 3) / int(c.num("width")) + int(c.num("y"));
                    std::fprintf(stderr, "crop %d %d: %.9g %.9g %.9g | %.9g %.9g %.9g frente a %.9g %.9g %.9g\n", px, py,
                                 planes.y[std::size_t(py) * planes.width + px], planes.cb[std::size_t(py / 2) * (planes.width / 2) + px / 2],
                                 planes.cr[std::size_t(py / 2) * (planes.width / 2) + px / 2], rgb.data[i], rgb.data[i + 1], rgb.data[i + 2],
                                 expected[i], expected[i + 1], expected[i + 2]);
                }
        if (route != Route::Pocket) {
            CHECK(d.over == 0);
        } else {
            // PocketForward: los píxeles con Y exactamente en un plano de la rejilla del muestreo caen en una zona
            // donde la triangulación de Qhull ("Qt", con 16899 símplices degenerados) no es conforme y el valor
            // depende del símplice que encuentra el recorrido de SciPy desde el píxel anterior. Solo vista previa.
            CHECK(d.max <= 1e-2 && double(d.over) <= 1e-3 * double(d.n));
        }
    }
}

}  // namespace

int main() {
    const OgModel og = loadOgModel(Resources / "model/pocket_og_cinemadng_signal.json");
    autonomous(og);
    isoInference(og);
    isoTableConsistency();
    profileRevisions(og);
    bayerSignal();
    bayerPhase();
    resizeChromaPhase();
    if (!check::skipWithoutGolden("signal_test")) parity(og);
    return check::finish("signal_test");
}
