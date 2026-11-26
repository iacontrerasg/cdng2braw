// Pruebas del bloque A. Autónomas: un DNG sintético (little y big endian) con los tags que leen las funciones, el
// remuestreo, el recorte y WAV sintéticos. Paridad (CDNG_GOLDEN): los frames y los casos que guardaba
// el prototipo.
#include "../dng.hpp"
#include "../dng_math.hpp"
#include "../og_debayer.hpp"
#include "../og_bayer.hpp"
#include "../signal_internal.hpp"
#include "../stage_dump.hpp"
#include "check.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <optional>
#include <fstream>
#include <set>
#include <sstream>
#include <thread>
#include <tuple>

using namespace cdng;

namespace {

namespace fs = std::filesystem;

fs::path tempPath(const std::string &name) {
    return fs::temp_directory_path() / ("cdng_dng_test_" + std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()) + "_" + name);
}

void writeFile(const fs::path &p, const std::vector<uint8_t> &b) {
    std::ofstream(p, std::ios::binary).write(reinterpret_cast<const char *>(b.data()), std::streamsize(b.size()));
}

// Escritor TIFF mínimo: IFD encadenadas con valores fuera de línea al final.
struct TiffWriter {
    bool little;
    std::vector<uint8_t> b;
    explicit TiffWriter(bool le) : little(le) {
        b = {uint8_t(le ? 'I' : 'M'), uint8_t(le ? 'I' : 'M')};
        put16(42); put32(0);
    }
    void put16(uint32_t v) { if (little) { b.push_back(uint8_t(v)); b.push_back(uint8_t(v >> 8)); } else { b.push_back(uint8_t(v >> 8)); b.push_back(uint8_t(v)); } }
    void put32(uint32_t v) { if (little) for (int i = 0; i < 4; ++i) b.push_back(uint8_t(v >> 8 * i)); else for (int i = 3; i >= 0; --i) b.push_back(uint8_t(v >> 8 * i)); }
    void set32(std::size_t at, uint32_t v) { for (int i = 0; i < 4; ++i) b[at + i] = uint8_t(little ? v >> 8 * i : v >> 8 * (3 - i)); }
    struct Tag { uint16_t tag, type; uint32_t count; std::vector<uint8_t> data; };
    std::vector<uint8_t> enc16(std::vector<uint32_t> v) { TiffWriter t(little); t.b.clear(); for (auto x : v) t.put16(x); return t.b; }
    std::vector<uint8_t> enc32(std::vector<uint32_t> v) { TiffWriter t(little); t.b.clear(); for (auto x : v) t.put32(x); return t.b; }
    std::vector<uint8_t> encRat(std::vector<std::pair<int32_t, int32_t>> v) {
        TiffWriter t(little); t.b.clear();
        for (auto [n, d] : v) { t.put32(uint32_t(n)); t.put32(uint32_t(d)); }
        return t.b;
    }
    // Escribe una IFD en el final y devuelve (offset de la IFD, posición del campo "siguiente").
    std::pair<uint32_t, std::size_t> ifd(std::vector<Tag> tags) {
        std::sort(tags.begin(), tags.end(), [](const Tag &a, const Tag &c) { return a.tag < c.tag; });
        if (b.size() & 1) b.push_back(0);
        const uint32_t at = uint32_t(b.size());
        put16(uint32_t(tags.size()));
        std::vector<std::pair<std::size_t, const Tag *>> late;
        for (const auto &t : tags) {
            put16(t.tag); put16(t.type); put32(t.count);
            if (t.data.size() <= 4) { auto d = t.data; d.resize(4); b.insert(b.end(), d.begin(), d.end()); }
            else { late.push_back({b.size(), &t}); put32(0); }
        }
        const std::size_t next = b.size(); put32(0);
        for (auto [pos, t] : late) { set32(pos, uint32_t(b.size())); b.insert(b.end(), t->data.begin(), t->data.end()); }
        return {at, next};
    }
};

std::vector<uint8_t> ascii(const std::string &s) { std::vector<uint8_t> v(s.begin(), s.end()); v.push_back(0); return v; }

fs::path syntheticDng(bool little) {
    TiffWriter w(little);
    const std::vector<std::pair<int32_t, int32_t>> cm1 = {{10000, 10000}, {-2000, 10000}, {-500, 10000}, {-4000, 10000},
        {12000, 10000}, {1500, 10000}, {-600, 10000}, {1800, 10000}, {6000, 10000}};
    const std::vector<std::pair<int32_t, int32_t>> cm2 = {{8000, 10000}, {-1500, 10000}, {-700, 10000}, {-4500, 10000},
        {13000, 10000}, {1200, 10000}, {-900, 10000}, {2100, 10000}, {7000, 10000}};
    // SubIFD con DefaultCrop (numeric recorre las SubIFD) y FrameRate en la principal.
    auto [first, next1] = w.ifd({
        {271, 2, 6, ascii("SIGMA")}, {272, 2, 3, ascii("fp")},
        {256, 3, 1, w.enc16({64})}, {257, 4, 1, w.enc32({48})},
        {330, 4, 1, w.enc32({0})},
        {50714, 5, 1, w.encRat({{25601, 100}})},
        {50721, 10, 9, w.encRat(cm1)}, {50722, 10, 9, w.encRat(cm2)},
        {50778, 3, 1, w.enc16({17})}, {50779, 3, 1, w.enc16({21})},
        {50728, 5, 3, w.encRat({{5, 10}, {1, 1}, {7, 10}})},
        {51044, 10, 1, w.encRat({{24000, 1001}})},
        {51043, 1, 8, {0x52 | 0x40, 0x34, 0x12, 0x01, 0, 0, 0, 0}},   // 01:12:34:12, drop frame
    });
    w.set32(4, first);
    // Segunda IFD de la cadena: repite AsShotNeutral (dng_color_full usa la última) y BlackLevel (gana la primera).
    auto [second, next2] = w.ifd({{50714, 3, 1, w.enc16({300})}, {50728, 5, 3, w.encRat({{6, 10}, {1, 1}, {6, 10}})}});
    w.set32(next1, second);
    (void)next2;
    auto [sub, nextSub] = w.ifd({{50719, 5, 2, w.encRat({{4, 1}, {2, 1}})}, {50720, 3, 2, w.enc16({56, 40})}});
    (void)nextSub;
    // Parchea el SubIFD (valor en línea de la entrada 330: entrada 5 tras ordenar 256, 257, 271, 272, 330).
    const std::size_t entry330 = first + 2 + 12 * 4;
    w.set32(entry330 + 8, sub);
    const fs::path p = tempPath(little ? "le.dng" : "be.dng");
    writeFile(p, w.b);
    return p;
}

void autonomousDng() {
    for (bool little : {true, false}) {
        const fs::path p = syntheticDng(little);
        Dng d(p);
        CHECK(d.cameraName() == "SIGMA fp");
        CHECK(d.scalar(256) == 64.0 && d.scalar(257) == 48.0);
        CHECK(!d.scalar(9999));
        CHECK(d.blackLevel() && *d.blackLevel() == 256.01);   // primera aparición
        const auto c = d.color();
        CHECK(c.k1 == 2856.0 && c.k2 == 6504.0);
        CHECK(c.cm1[1] == -0.2 && c.cm2[4] == 1.3);
        CHECK(c.asShotNeutral[0] == 0.6 && c.asShotNeutral[2] == 0.6);   // última aparición
        const auto r = d.frameRate();
        CHECK(r && r->num == 24000 && r->den == 1001);
        CHECK(nominalFps(*r) == 24);
        CHECK(d.timecodeFrames(24) == ((1LL * 60 + 12) * 60 + 34) * 24 + 12);
        CHECK(d.timecodeDropFrame());
        const auto origin = d.numeric(50719), size = d.numeric(50720);
        CHECK(origin && (*origin)[0] == 4 && (*origin)[1] == 2);
        CHECK(size && (*size)[0] == 56 && (*size)[1] == 40);
        CHECK(!d.numeric(50999));
        RgbImage img(64, 48);
        for (std::size_t i = 0; i < img.data.size(); ++i) img.data[i] = float(i);
        const RgbImage cropped = cropSensor(img, d);
        CHECK(cropped.width == 56 && cropped.height == 40);
        CHECK(cropped.px(0, 0)[0] == img.px(4, 2)[0] && cropped.px(55, 39)[2] == img.px(59, 41)[2]);
        RgbImage small(10, 10);
        bool threw = false;
        try { cropSensor(small, d); } catch (const Error &e) { threw = std::string(e.what()) == "DefaultCrop outside decoded sensor image"; }
        CHECK(threw);
        const auto [k, tint] = d.asShot();
        CHECK(k > 2000 && k < 12000 && std::isfinite(tint));
        fs::remove(p);
    }
    // Un fichero que no es TIFF.
    const fs::path junk = tempPath("junk.dng");
    writeFile(junk, {'n', 'o', 't', ' ', 'a', ' ', 't', 'i', 'f', 'f'});
    Dng j(junk);
    CHECK(j.cameraName() == "Unknown camera");
    bool threw = false;
    try { j.numeric(256); } catch (const Error &e) { threw = std::string(e.what()) == "expected classic TIFF/DNG"; }
    CHECK(threw);
    fs::remove(junk);
}

void autonomousMath() {
    // D65 por Robertson (valores del Python: 6503,707 K y tinte +9,770, como el DNG SDK).
    const auto [k, tint] = xyToTempTint(0.3127, 0.3290);
    CHECK_NEAR(k, 6503.70718479529, 1e-8);
    CHECK_NEAR(tint, 9.769990121404899, 1e-10);
    const Mat3 id = bradford({0.95047, 1.0, 1.08883}, {0.95047, 1.0, 1.08883});
    for (int i = 0; i < 9; ++i) CHECK_NEAR(id[i], i % 4 == 0 ? 1.0 : 0.0, 1e-12);
    DngColor same;
    same.cm1 = same.cm2 = {1, 0, 0, 0, 1, 0, 0, 0, 1};
    same.k1 = same.k2 = 5000;
    CHECK(matrixForNeutral(same, {1, 1, 1}).second == 5000);
    CHECK(nominalFps({30000, 1001}) == 30 && nominalFps({25, 1}) == 25 && nominalFps({1, 2}) == 1 && nominalFps({5, 2}) == 2);
    // La matriz a 709 lleva el neutro de la cámara al blanco (R = G = B).
    DngColor c;
    c.cm1 = {1.0, -0.2, -0.05, -0.4, 1.2, 0.15, -0.06, 0.18, 0.6};
    c.cm2 = {0.8, -0.15, -0.07, -0.45, 1.3, 0.12, -0.09, 0.21, 0.7};
    const Vec3 n{0.55, 1.0, 0.65};
    const Mat3 m = cameraTo709(c, n);
    double rgb[3];
    for (int i = 0; i < 3; ++i) rgb[i] = m[i * 3] * n[0] + m[i * 3 + 1] * n[1] + m[i * 3 + 2] * n[2];
    CHECK_NEAR(rgb[0] / rgb[1], 1.0, 2e-3);
    CHECK_NEAR(rgb[2] / rgb[1], 1.0, 2e-3);
    const Mat3 p3 = cameraToP3(c, n);
    double p3n[3];
    for (int i = 0; i < 3; ++i) p3n[i] = p3[i * 3] * n[0] + p3[i * 3 + 1] * n[1] + p3[i * 3 + 2] * n[2];
    CHECK_NEAR(p3n[0] / p3n[1], 1.0, 2e-3);
    CHECK_NEAR(p3n[2] / p3n[1], 1.0, 2e-3);
    const Mat3 round = math::mul(math::inv(p3), p3);
    for (int i = 0; i < 9; ++i) CHECK_NEAR(round[i], i % 4 == 0 ? 1.0 : 0.0, 1e-9);
    {
        check::EnvVar env("CDNG_OG_DEBAYER");
        env.unset();
        const auto d = ogDebayerFromEnv();
        CHECK(!d.fromEnv && d.userQual == 3 && d.name == "ahd");
        env.set("ahd");
        const auto ahd = ogDebayerFromEnv();
        CHECK(ahd.fromEnv && ahd.userQual == 3);
        env.set("vng");
        CHECK(ogDebayerFromEnv().userQual == 1);
        env.set("dht");
        CHECK(ogDebayerFromEnv().userQual == 11);
        env.set("aahd");
        CHECK(ogDebayerFromEnv().userQual == 12);
        env.set("ppg");
        CHECK(ogDebayerFromEnv().userQual == 2);
        env.set("linear");
        CHECK(ogDebayerFromEnv().userQual == 0);
        bool threw = false;
        env.set("amaze");
        try { ogDebayerFromEnv(); } catch (const Error &) { threw = true; }
        CHECK(threw);
        threw = false;
        env.set("nope");
        try { ogDebayerFromEnv(); } catch (const Error &) { threw = true; }
        CHECK(threw);
        env.set("vng");
        threw = false;
        try { ogDebayerRejectIfNotOg(Route::Va); } catch (const Error &) { threw = true; }
        CHECK(threw);
        env.unset();
        ogDebayerRejectIfNotOg(Route::Va);
        ogDebayerRejectIfNotOg(Route::Og);
    }
    {
        check::EnvVar bayer("CDNG_OG_BAYER"), deb("CDNG_OG_DEBAYER");
        bayer.set("1");
        deb.set("vng");
        bool threw = false;
        try { ogBayerRejectIfNotOg(Route::Og); } catch (const Error &) { threw = true; }
        CHECK(threw);
        deb.unset();
        ogBayerRejectIfNotOg(Route::Og);
    }
}

void autonomousResize() {
    RgbImage flat(300, 200);
    std::fill(flat.data.begin(), flat.data.end(), 0.25f);
    for (auto [w, h] : {std::pair{150, 100}, {450, 300}, {100, 100}, {320, 90}}) {
        const RgbImage r = resizeLinear(flat, w, h);
        CHECK(r.width == w && r.height == h);
        float worst = 0;
        for (float v : r.data) worst = std::max(worst, std::fabs(v - 0.25f));
        CHECK(worst < 1e-6f);
    }
    // Misma proporción y tamaño: copia. Otra proporción con el mismo tamaño final que el recorte: recorte puro.
    RgbImage ramp(40, 20);
    for (int y = 0; y < 20; ++y) for (int x = 0; x < 40; ++x) for (int c = 0; c < 3; ++c) ramp.px(x, y)[c] = float(x * 100 + y + c);
    CHECK(resizeLinear(ramp, 40, 20).data == ramp.data);
    const RgbImage sq = resizeLinear(ramp, 20, 20);
    CHECK(sq.px(0, 0)[0] == ramp.px(10, 0)[0] && sq.px(19, 19)[2] == ramp.px(29, 19)[2]);
}

std::vector<uint8_t> wavFile(int ch, int bits, const std::vector<int32_t> &samples, bool extraChunk) {
    std::vector<uint8_t> data;
    for (int32_t s : samples) for (int i = 0; i < bits / 8; ++i) data.push_back(uint8_t(uint32_t(s) >> 8 * i));
    auto le = [](std::vector<uint8_t> &v, uint32_t x, int n) { for (int i = 0; i < n; ++i) v.push_back(uint8_t(x >> 8 * i)); };
    std::vector<uint8_t> b = {'R', 'I', 'F', 'F', 0, 0, 0, 0, 'W', 'A', 'V', 'E'};
    if (extraChunk) { b.insert(b.end(), {'b', 'e', 'x', 't'}); le(b, 3, 4); b.insert(b.end(), {1, 2, 3, 0}); }   // tamaño impar
    b.insert(b.end(), {'f', 'm', 't', ' '}); le(b, 16, 4);
    le(b, 1, 2); le(b, uint32_t(ch), 2); le(b, 48000, 4); le(b, uint32_t(48000 * ch * bits / 8), 4); le(b, uint32_t(ch * bits / 8), 2); le(b, uint32_t(bits), 2);
    b.insert(b.end(), {'d', 'a', 't', 'a'}); le(b, uint32_t(data.size()), 4);
    b.insert(b.end(), data.begin(), data.end());
    return b;
}

void autonomousWav() {
    auto check24 = [](const Pcm24 &p, std::size_t i, int c, int32_t expect) {
        const uint8_t *o = p.data.data() + i * 6 + c * 3;
        const uint32_t u = uint32_t(o[0] | o[1] << 8 | o[2] << 16);
        return u == (uint32_t(expect) & 0xFFFFFF);
    };
    const fs::path p = tempPath("a.wav");
    writeFile(p, wavFile(1, 16, {1, -2, 32767, -32768}, true));
    Pcm24 m = readWavPcm24(p);
    CHECK(m.frames == 4 && m.rate == 48000 && m.data.size() == 24);
    CHECK(check24(m, 0, 0, 256) && check24(m, 0, 1, 256) && check24(m, 1, 1, -512) && check24(m, 3, 0, -32768 * 256));
    writeFile(p, wavFile(2, 24, {0x123456, -0x123456, 0x7FFFFF, -0x800000}, false));
    m = readWavPcm24(p);
    CHECK(m.frames == 2 && check24(m, 0, 0, 0x123456) && check24(m, 0, 1, -0x123456) && check24(m, 1, 1, -0x800000));
    writeFile(p, wavFile(3, 32, {0x12345678, -256, 7, 0x100, 0x200, 0x300}, false));
    m = readWavPcm24(p);
    CHECK(m.frames == 2 && check24(m, 0, 0, 0x123456) && check24(m, 0, 1, -1) && check24(m, 1, 0, 1) && check24(m, 1, 1, 2));
    writeFile(p, wavFile(2, 8, {1, 2}, false));
    bool threw = false;
    try { readWavPcm24(p); } catch (const Error &e) { threw = std::string(e.what()) == "WAV de 8 bits no soportado"; }
    CHECK(threw);
    fs::remove(p);
}

// ---------------------------------------------------------------------------------------------------------------
// Paridad

struct Diff { double abs = 0, rel = 0; };

// Diferencia máxima absoluta y relativa (respecto a max(|ref|, floor)).
template <class A, class B>
Diff compare(const A &got, const B &ref, std::size_t n, double floor = 1e-3) {
    Diff d;
    for (std::size_t i = 0; i < n; ++i) {
        const double e = std::fabs(double(got[i]) - double(ref[i]));
        d.abs = std::max(d.abs, e);
        d.rel = std::max(d.rel, e / std::max(std::fabs(double(ref[i])), floor));
    }
    return d;
}

bool isNone(const check::Case &c, const std::string &key) { return c.type.at(key) == "str" && c.str(key) == "none"; }

void checkOptional(const check::Case &c, const std::string &key, const std::optional<double> &got) {
    if (isNone(c, key)) CHECK(!got);
    else { CHECK(got); if (got) CHECK(*got == c.num(key)); }
}

void parityFrame(const std::string &name, const fs::path &path) {
    const auto c = check::Case::load("dng", name);
    Dng d(path);
    CHECK(d.cameraName() == c.str("camera"));

    std::set<int> present;
    { std::istringstream s(c.str("numeric_present")); std::string t; while (std::getline(s, t, ',')) if (t != "-") present.insert(std::stoi(t)); }
    for (int tag : {256, 257, 258, 271, 33434, 34855, 50714, 50717, 50719, 50720, 50721, 50728, 50730, 51044}) {
        const auto got = d.numeric(uint16_t(tag));
        CHECK(bool(got) == bool(present.count(tag)));
        if (got && present.count(tag)) CHECK(*got == c.array<double>("numeric_" + std::to_string(tag)));
    }
    for (int tag : {256, 257, 50714, 50717, 50730, 51044, 34855, 33434, 50778})
        checkOptional(c, "scalar_" + std::to_string(tag), d.scalar(uint16_t(tag)));

    const DngColor col = d.color();
    const auto cm1 = c.array<double>("cm1"), cm2 = c.array<double>("cm2"), asn = c.array<double>("asn");
    CHECK(std::equal(col.cm1.begin(), col.cm1.end(), cm1.begin()) && std::equal(col.cm2.begin(), col.cm2.end(), cm2.begin()));
    CHECK(std::equal(col.asShotNeutral.begin(), col.asShotNeutral.end(), asn.begin()));
    CHECK(col.k1 == c.num("k1") && col.k2 == c.num("k2"));
    checkOptional(c, "black", d.blackLevel());

    const auto rate = d.frameRate();
    CHECK((rate ? std::to_string(rate->num) + "/" + std::to_string(rate->den) : std::string("none")) == c.str("rate"));
    const int fps = rate ? nominalFps(*rate) : 24;
    CHECK(fps == c.num("nominal"));
    const auto tc = d.timecodeFrames(fps);
    checkOptional(c, "timecode", tc ? std::optional<double>(double(*tc)) : std::nullopt);
    CHECK(int(d.timecodeDropFrame()) == c.num("drop"));
    const auto [kelvin, tint] = d.asShot();
    CHECK_NEAR(kelvin, c.num("kelvin"), 1e-9 * c.num("kelvin"));
    CHECK_NEAR(tint, c.num("tint"), 1e-9);
    const auto [rw, rh] = d.rawSize();
    CHECK(rw == c.num("raw_w") && rh == c.num("raw_h"));

    // Matrices.
    Diff mat;
    const Vec3 other = [&] { const auto v = c.array<double>("other_neutral"); return Vec3{v[0], v[1], v[2]}; }();
    for (auto [suffix, neutral] : {std::pair<std::string, Vec3>{"", col.asShotNeutral}, {"_other", other}}) {
        const auto [cm, t] = matrixForNeutral(col, neutral);
        CHECK_NEAR(t, c.num("mfn_t" + suffix), 1e-9 * c.num("mfn_t" + suffix));
        const Diff a = compare(cm, c.array<double>("mfn_cm" + suffix), 9, 1.0);
        const Diff b = compare(cameraTo709(col, neutral), c.array<double>("to709" + suffix), 9, 1.0);
        mat.abs = std::max({mat.abs, a.abs, b.abs});
    }
    CHECK(mat.abs <= 1e-12);

    // Revelado.
    const RgbImage cam = d.cameraRgb();
    CHECK(cam.width == c.num("cam_w") && cam.height == c.num("cam_h"));
    const int cx = int(c.num("cam_center_x")), cy = int(c.num("cam_center_y"));
    auto region = [&](const RgbImage &img, int x0, int y0, int s) {
        std::vector<float> v; v.reserve(std::size_t(s) * s * 3);
        for (int y = 0; y < s; ++y) v.insert(v.end(), img.px(x0, y0 + y), img.px(x0, y0 + y) + std::size_t(s) * 3);
        return v;
    };
    Diff camDiff;
    for (auto [key, x0, y0, s] : {std::tuple<const char *, int, int, int>{"cam_center", cx, cy, 1024}, {"cam_corner", 0, 0, 256},
                                  {"cam_last", cam.width - 256, cam.height - 256, 256}}) {
        const auto ref = c.array<float>(key);
        const auto got = region(cam, x0, y0, s);
        CHECK(ref.size() == got.size());
        const Diff e = compare(got, ref, std::min(ref.size(), got.size()));
        camDiff.abs = std::max(camDiff.abs, e.abs); camDiff.rel = std::max(camDiff.rel, e.rel);
    }
    double sum[3] = {0, 0, 0}, mn[3] = {1e30, 1e30, 1e30}, mx[3] = {-1e30, -1e30, -1e30};
    for (std::size_t i = 0; i < cam.data.size(); ++i) {
        const int ch = int(i % 3); const double v = cam.data[i];
        sum[ch] += v; mn[ch] = std::min(mn[ch], v); mx[ch] = std::max(mx[ch], v);
    }
    const auto rsum = c.array<double>("cam_sum"), rmin = c.array<double>("cam_min"), rmax = c.array<double>("cam_max");
    double sumRel = 0;
    for (int ch = 0; ch < 3; ++ch) {
        sumRel = std::max(sumRel, std::fabs(sum[ch] - rsum[ch]) / std::fabs(rsum[ch]));
        CHECK_NEAR(mn[ch], rmin[ch], 1e-6 * std::max(1.0, std::fabs(rmin[ch])));
        CHECK_NEAR(mx[ch], rmax[ch], 1e-6 * std::max(1.0, std::fabs(rmax[ch])));
    }
    CHECK(camDiff.abs <= 1e-6);   // misma cuenta de LibRaw; solo cabe el redondeo a float32
    CHECK(sumRel <= 1e-6);

    // Recorte y reescalado.
    const RgbImage sensor = cropSensor(cam, d);
    CHECK(sensor.width == c.num("crop_w") && sensor.height == c.num("crop_h"));
    { const auto ref = c.array<float>("crop_corner"); const auto got = region(sensor, 0, 0, 256); CHECK(compare(got, ref, ref.size()).abs <= 1e-6); }
    std::string resizeReport;
    for (auto [w, h] : {std::pair{1920, 1080}, {1280, 720}, {1000, 1000}}) {
        const std::string key = "resize_" + std::to_string(w) + "x" + std::to_string(h);
        const RgbImage r = resizeLinear(sensor, w, h);
        const auto ref = c.array<float>(key);
        CHECK(r.data.size() == ref.size());
        const Diff e = compare(r.data, ref, std::min(ref.size(), r.data.size()));
        CHECK(e.rel <= 1e-5);
        char buf[96]; std::snprintf(buf, sizeof buf, " %s abs %.2e rel %.2e", key.c_str(), e.abs, e.rel);
        resizeReport += buf;
    }
    std::printf("%-16s %dx%d K %.6f tint %.6f | matrices %.2e | cameraRgb abs %.2e rel %.2e suma %.2e |%s\n",
                name.c_str(), cam.width, cam.height, kelvin, tint, mat.abs, camDiff.abs, camDiff.rel, sumRel,
                resizeReport.c_str());
}

void parityLanczos() {
    const auto c = check::Case::load("dng", "lanczos");
    const auto shape = c.shape.at("synth");
    RgbImage in{int(shape[1]), int(shape[0])};
    in.data = c.array<float>("synth");
    for (auto [w, h] : {std::pair{100, 60}, {263, 80}, {400, 157}, {500, 300}, {37, 211}, {131, 79}, {88, 100}}) {
        const std::string key = "out_" + std::to_string(w) + "x" + std::to_string(h);
        const RgbImage r = resizeLinear(in, w, h);
        const auto ref = c.array<float>(key);
        CHECK(r.data.size() == ref.size());
        const Diff e = compare(r.data, ref, std::min(ref.size(), r.data.size()));
        std::size_t same = 0;
        for (std::size_t i = 0; i < ref.size() && i < r.data.size(); ++i) same += r.data[i] == ref[i];
        std::printf("lanczos %-12s abs %.2e rel %.2e idénticos %zu/%zu\n", key.c_str(), e.abs, e.rel, same, ref.size());
        CHECK(e.rel <= 1e-5);
    }
}

void parityPure() {
    const auto c = check::Case::load("dng", "pure");
    const auto xy = c.array<double>("xy"), tt = c.array<double>("temp_tint");
    double worst = 0;
    for (std::size_t i = 0; i < xy.size() / 2; ++i) {
        const auto [k, t] = xyToTempTint(xy[2 * i], xy[2 * i + 1]);
        worst = std::max({worst, std::fabs(k - tt[2 * i]) / tt[2 * i], std::fabs(t - tt[2 * i + 1]) / std::max(1.0, std::fabs(tt[2 * i + 1]))});
    }
    CHECK(worst <= 1e-12);
    const auto whites = c.array<double>("whites"), brad = c.array<double>("bradford");
    double bw = 0;
    for (std::size_t i = 0; i < whites.size() / 3; ++i) {
        const Mat3 m = bradford({whites[3 * i], whites[3 * i + 1], whites[3 * i + 2]}, {0.95047, 1.0, 1.08883});
        bw = std::max(bw, compare(m, brad.data() + 9 * i, 9, 1.0).abs);
    }
    CHECK(bw <= 1e-14);
    const auto rates = c.array<int32_t>("rates"), nominal = c.array<int32_t>("nominal");
    for (std::size_t i = 0; i < nominal.size(); ++i) CHECK(nominalFps({rates[2 * i], rates[2 * i + 1]}) == nominal[i]);
    std::printf("puras: Robertson rel %.2e, Bradford abs %.2e\n", worst, bw);
}

void parityWav() {
    const auto c = check::Case::load("dng", "wav");
    const Pcm24 p = readWavPcm24(c.str("path"));
    CHECK(p.frames == (long long)c.num("frames") && p.rate == int(c.num("rate")));
    CHECK(p.data == c.bytes("pcm"));
    std::printf("wav: %lld frames a %d Hz, %zu bytes idénticos\n", p.frames, p.rate, p.data.size());
}

// Varios hilos a la vez (cada uno con su instancia de LibRaw) dan lo mismo que en serie.
// La lectura parcial (exploración de lotes) da las mismas etiquetas que la completa.
void parityHeaderOnly(const std::vector<fs::path> &paths) {
    for (const auto &p : paths) {
        const Dng full(p), head(p, true);
        CHECK(full.cameraName() == head.cameraName());
        for (uint16_t tag : {50720, 50719, 50730, 34855, 33434, 51044, 50717, 50714, 50721, 50722, 50728})
            CHECK(full.numeric(tag) == head.numeric(tag));
        CHECK(full.blackLevel() == head.blackLevel());
        const auto a = full.frameRate(), b = head.frameRate();
        CHECK(bool(a) == bool(b) && (!a || (a->num == b->num && a->den == b->den)));
        CHECK(full.timecodeFrames(24) == head.timecodeFrames(24));
        CHECK(full.timecodeDropFrame() == head.timecodeDropFrame());
        CHECK(full.asShot() == head.asShot());
        CHECK(full.color().asShotNeutral == head.color().asShotNeutral && full.color().cm1 == head.color().cm1);
    }
}

// Bloque FSG2 (Gyro2 de fpSup): el mismo que extrae fsg2.py. CDNG_TEST_FSG2_DNG: un DNG con bloque;
// CDNG_TEST_FSG2_BLOCK: los bytes que da fsg2.py (buf[at:at + 12 + n]).
void parityGyro() {
    const char *dng = std::getenv("CDNG_TEST_FSG2_DNG"), *expected = std::getenv("CDNG_TEST_FSG2_BLOCK");
    if (!dng || !expected) return;
    std::ifstream file(expected, std::ios::binary);
    const std::vector<uint8_t> block((std::istreambuf_iterator<char>(file)), std::istreambuf_iterator<char>());
    CHECK(!block.empty());
    CHECK(Dng(dng).gyroBlock() == block);
    CHECK(Dng(dng, true).gyroBlock() == block);   // también con la lectura parcial
}

// DNG sintético de la Pocket original (matrices e iluminantes calibrados, neutro en un nodo de la tabla del perfil):
// ogSourceInfo infiere la ISO del BaselineExposure y codifica siempre con ganancia unitaria.
fs::path pocketOgDng(const OgModel::Impl &m, std::pair<int32_t, int32_t> baseline, std::optional<uint32_t> exifIso) {
    TiffWriter w(true);
    auto rationals = [](const auto &values) {
        std::vector<std::pair<int32_t, int32_t>> out;
        for (const double v : values) out.push_back({int32_t(std::lround(v * 1e8)), 100000000});
        return out;
    };
    const std::size_t i = m.mired.size() / 2, j = std::size_t(std::find(m.tints.begin(), m.tints.end(), 10.0) - m.tints.begin());
    const std::size_t node = i * m.tints.size() + (j < m.tints.size() ? j : 0);
    const std::vector<double> neutral{1 / m.red[node], 1, 1 / m.blue[node]};
    std::vector<TiffWriter::Tag> tags{
        {271, 2, 18, ascii("Blackmagic Design")}, {272, 2, 21, ascii("Pocket Cinema Camera")},
        {256, 3, 1, w.enc16({64})}, {257, 4, 1, w.enc32({48})},
        {50721, 10, 9, w.encRat(rationals(m.cm1))}, {50722, 10, 9, w.encRat(rationals(m.cm2))},
        {50778, 3, 1, w.enc16({17})}, {50779, 3, 1, w.enc16({21})},
        {50728, 5, 3, w.encRat(rationals(neutral))},
        {50730, 10, 1, w.encRat({baseline})},
        {50719, 5, 2, w.encRat({{4, 1}, {2, 1}})}, {50720, 3, 2, w.enc16({56, 40})},
    };
    if (exifIso) tags.push_back({34855, 3, 1, w.enc16({*exifIso})});
    auto [first, next] = w.ifd(tags);
    (void)next;
    w.set32(4, first);
    const fs::path p = tempPath("pocket_og.dng");
    writeFile(p, w.b);
    return p;
}

void autonomousPocketOgExposure() {
    const OgModel og = loadOgModel(resourceRoot() / "model/pocket_og_cinemadng_signal.json");
    const auto &m = *og.impl;
    CHECK(m.k1 == 2856.0 && m.k2 == 6504.0);
    auto shotOf = [&](std::pair<int32_t, int32_t> baseline, std::optional<uint32_t> exif, std::string *error) {
        const auto path = pocketOgDng(m, baseline, exif);
        OgShot shot;
        try { shot = ogSourceInfo(Dng(path), og); } catch (const Error &e) { *error = e.what(); }
        fs::remove(path);
        return shot;
    };
    const struct { std::pair<int32_t, int32_t> baseline; int iso; double postGain; } stops[] = {
        {{1, 10}, 200, 0.5}, {{11, 10}, 400, 1}, {{21, 10}, 800, 2}, {{31, 10}, 1600, 4}};
    for (const auto &s : stops)
        for (const std::optional<uint32_t> exif : {std::optional<uint32_t>(), std::optional<uint32_t>(uint32_t(s.iso))}) {
            std::string error;
            const auto shot = shotOf(s.baseline, exif, &error);
            CHECK(error.empty());
            CHECK(shot.gain == 1.0 && shot.iso == s.iso);                       // ganancia unitaria en la codificación
            CHECK(std::fabs(shot.postGain - s.postGain) < 1e-9);
            CHECK(std::fabs(shot.baselineExposure - double(s.baseline.first) / s.baseline.second) < 1e-12);
            CHECK(shot.kelvin >= 3000 && shot.kelvin <= 10000 && shot.tint >= -30 && shot.tint <= 30);
        }
    const std::string outside = "BaselineExposure is not represented by the Pocket Cinema Camera ISO table (ISO 200, 400, 800, 1600)";
    for (const auto baseline : std::vector<std::pair<int32_t, int32_t>>{{16, 10}, {41, 10}, {-9, 10}, {0, 1}, {1, 1}}) {
        std::string error;
        shotOf(baseline, std::nullopt, &error);
        CHECK(error == outside);
    }
    std::string error;
    shotOf({31, 10}, 800, &error);
    CHECK(error == "source ISO does not match its BaselineExposure");
    error.clear();
    shotOf({11, 0}, std::nullopt, &error);   // denominador 0: el lector lo rechaza antes de la tabla
    CHECK(error == "division by zero");
}

void parityThreads(const std::vector<fs::path> &paths) {
    std::vector<RgbImage> serial;
    for (const auto &p : paths) serial.push_back(Dng(p).cameraRgb());
    std::vector<RgbImage> parallel(paths.size() * 2);
    std::vector<std::thread> threads;
    for (std::size_t i = 0; i < parallel.size(); ++i)
        threads.emplace_back([&, i] { parallel[i] = Dng(paths[i % paths.size()]).cameraRgb(); });
    for (auto &t : threads) t.join();
    for (std::size_t i = 0; i < parallel.size(); ++i) CHECK(parallel[i].data == serial[i % paths.size()].data);
}

void autonomousInjectPlanes() {
    const int w = 4, h = 4;
    Planes src(w, h);
    for (int i = 0; i < w * h; ++i) src.y[std::size_t(i)] = float(100 + i);
    src.cb[0] = 12.5f; src.cb[1] = -3.f; src.cb[2] = 8.f; src.cb[3] = 0.5f;
    src.cr[0] = -9.f; src.cr[1] = 4.f; src.cr[2] = 1.f; src.cr[3] = -2.5f;
    const auto path = tempPath("inject.brwp");
    {
        std::ofstream out(path, std::ios::binary);
        out.write("BRWP", 4);
        const uint32_t dim[2] = {uint32_t(w), uint32_t(h)};
        out.write(reinterpret_cast<const char *>(dim), 8);
        out.write(reinterpret_cast<const char *>(src.y.data()), std::streamsize(src.y.size() * 4));
        out.write(reinterpret_cast<const char *>(src.cb.data()), std::streamsize(src.cb.size() * 4));
        out.write(reinterpret_cast<const char *>(src.cr.data()), std::streamsize(src.cr.size() * 4));
    }
    {
        check::EnvVar env("CDNG_INJECT_PLANES");
        env.unset();
        CHECK(!stageInjectPlanes());
        env.set(path.string().c_str());
        const auto got = stageInjectPlanes();
        CHECK(got.has_value());
        CHECK(got->width == w && got->height == h);
        CHECK(got->y == src.y && got->cb == src.cb && got->cr == src.cr);
    }
    fs::remove(path);
}

}  // namespace

int main() {
    autonomousDng();
    autonomousMath();
    autonomousResize();
    autonomousWav();
    autonomousPocketOgExposure();
    autonomousInjectPlanes();
    parityGyro();

    if (!check::skipWithoutGolden("dng_test")) {
        // Las rutas de los frames están en el caso "frames".
        const auto frames = check::Case::load("dng", "frames");
        std::vector<fs::path> paths;
        for (const auto &[name, type] : frames.type) {
            parityFrame(name, frames.str(name));
            paths.push_back(frames.str(name));
        }
        parityLanczos();
        parityPure();
        parityWav();
        parityThreads({paths[0], paths[1], paths[3]});
        parityHeaderOnly(paths);
    }
    return check::finish("dng_test");
}
