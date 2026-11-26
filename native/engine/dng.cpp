// Bloque A — lectura del CinemaDNG: tags TIFF/DNG, color, As Shot y revelado con LibRaw. Port del
// prototipo en Python (ver dng.hpp). Cada función reproduce la semántica de su original: qué IFD recorre, qué
// aparición de un tag usa (primera o última) y cómo interpreta tipo y offset.
#include "dng.hpp"
#include "dng_math.hpp"
#include "og_debayer.hpp"

#include <libraw.h>

#include <algorithm>
#include <climits>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <memory>
#include <numeric>
#include <set>

namespace cdng {

namespace {

constexpr const char *Truncated = "truncated DNG";

double illuminantK(uint16_t code, double fallback) {   // ILLUMINANT_K del Python
    switch (code) {
    case 17: return 2856.0; case 18: return 3200.0; case 19: return 5500.0; case 20: return 6500.0;
    case 21: return 6504.0; case 23: return 5003.0; case 1: return 5500.0; case 2: return 4874.0; case 3: return 2856.0;
    default: return fallback;
    }
}

// str.strip() de Python para lo que puede salir de un tag ASCII: blancos ASCII en los extremos.
std::string strip(const std::string &s) {
    const char *ws = " \t\n\r\v\f\x1c\x1d\x1e\x1f";
    const auto a = s.find_first_not_of(ws);
    if (a == std::string::npos) return {};
    return s.substr(a, s.find_last_not_of(ws) - a + 1);
}

// bytes.decode('utf-8', errors='replace'): secuencias inválidas → U+FFFD (una por subparte máxima, como CPython).
std::string decodeUtf8(const uint8_t *p, std::size_t n) {
    std::string out;
    std::size_t i = 0;
    while (i < n) {
        const uint8_t c = p[i];
        if (c < 0x80) { out += char(c); ++i; continue; }
        int len = 0; uint8_t lo = 0x80, hi = 0xBF;
        if (c >= 0xC2 && c <= 0xDF) len = 2;
        else if (c >= 0xE0 && c <= 0xEF) { len = 3; if (c == 0xE0) lo = 0xA0; if (c == 0xED) hi = 0x9F; }
        else if (c >= 0xF0 && c <= 0xF4) { len = 4; if (c == 0xF0) lo = 0x90; if (c == 0xF4) hi = 0x8F; }
        if (!len) { out += "\xEF\xBF\xBD"; ++i; continue; }
        std::size_t j = 1;
        for (; j < std::size_t(len) && i + j < n; ++j) {
            const uint8_t b = p[i + j];
            const uint8_t l = j == 1 ? lo : 0x80, h = j == 1 ? hi : 0xBF;
            if (b < l || b > h) break;
        }
        if (j == std::size_t(len)) { out.append(reinterpret_cast<const char *>(p + i), len); i += len; }
        else { out += "\xEF\xBF\xBD"; i += j; }
    }
    return out;
}

std::string lowerAscii(std::string s) {
    for (auto &c : s) if (c >= 'A' && c <= 'Z') c = char(c - 'A' + 'a');
    return s;
}

}  // namespace

// ---------------------------------------------------------------------------------------------------------------
// Lectura de bytes y cadena principal

uint16_t Dng::u16(std::size_t at) const {
    if (at + 2 > bytes_.size() || at + 2 < at) throw Error(Truncated);
    const uint8_t *p = bytes_.data() + at;
    return little_ ? uint16_t(p[0] | p[1] << 8) : uint16_t(p[0] << 8 | p[1]);
}

uint32_t Dng::u32(std::size_t at) const {
    if (at + 4 > bytes_.size() || at + 4 < at) throw Error(Truncated);
    const uint8_t *p = bytes_.data() + at;
    return little_ ? uint32_t(p[0]) | uint32_t(p[1]) << 8 | uint32_t(p[2]) << 16 | uint32_t(p[3]) << 24
                   : uint32_t(p[0]) << 24 | uint32_t(p[1]) << 16 | uint32_t(p[2]) << 8 | uint32_t(p[3]);
}

namespace {
std::vector<uint8_t> readPrefix(const std::filesystem::path &path, std::size_t limit, bool *complete) {
    FILE *f = std::fopen(path.string().c_str(), "rb");
    if (!f) throw Error("Could not read " + path.string());
    std::vector<uint8_t> data(limit);
    const std::size_t n = std::fread(data.data(), 1, limit, f);
    *complete = n < limit || std::fgetc(f) == EOF;
    std::fclose(f);
    data.resize(n);
    return data;
}

// ¿Están dentro de `size` todas las IFD (cadena principal, SubIFD y EXIF) y los valores de sus etiquetas? Los datos
// de imagen no cuentan: las etiquetas solo guardan sus offsets.
bool tagsWithin(const std::vector<uint8_t> &b, bool little) {
    const std::size_t size = b.size();
    auto u16 = [&](std::size_t at) { return little ? uint16_t(b[at] | b[at + 1] << 8) : uint16_t(b[at] << 8 | b[at + 1]); };
    auto u32 = [&](std::size_t at) {
        return little ? uint32_t(b[at]) | uint32_t(b[at + 1]) << 8 | uint32_t(b[at + 2]) << 16 | uint32_t(b[at + 3]) << 24
                      : uint32_t(b[at]) << 24 | uint32_t(b[at + 1]) << 16 | uint32_t(b[at + 2]) << 8 | uint32_t(b[at + 3]);
    };
    if (size < 8) return false;
    std::vector<uint32_t> queue{u32(4)};
    std::set<uint32_t> seen;
    while (!queue.empty()) {
        const uint32_t off = queue.back(); queue.pop_back();
        if (!off || !seen.insert(off).second) continue;
        if (std::size_t(off) + 2 > size) return false;
        const uint16_t n = u16(off);
        if (std::size_t(off) + 2 + 12 * std::size_t(n) + 4 > size) return false;
        for (uint16_t i = 0; i < n; ++i) {
            const std::size_t pos = std::size_t(off) + 2 + 12 * i;
            const uint16_t tag = u16(pos), type = u16(pos + 2);
            const uint32_t count = u32(pos + 4), value = u32(pos + 8);
            static const int width[] = {0, 1, 1, 2, 4, 8, 1, 1, 2, 4, 8, 4, 8};
            const std::size_t bytes = std::size_t(count) * (type < 13 ? width[type] : 1);
            if (bytes > 4 && std::size_t(value) + bytes > size) return false;
            if ((tag == 330 || tag == 34665) && (type == 4 || type == 13)) {
                if (count == 1) queue.push_back(value);
                else if (std::size_t(value) + 4 * std::size_t(count) <= size)
                    for (uint32_t k = 0; k < count; ++k) queue.push_back(u32(std::size_t(value) + 4 * k));
            }
        }
        queue.push_back(u32(std::size_t(off) + 2 + 12 * std::size_t(n)));
    }
    return true;
}
}  // namespace

Dng::Dng(std::filesystem::path path, bool headerOnly) : path_(std::move(path)) {
    // headerOnly (explorar un lote, sobre todo en red): basta el primer megabyte si todas las etiquetas caen dentro;
    // si no, se lee el fichero entero y el resultado es el mismo que sin la opción.
    bool complete = true;
    if (headerOnly) {
        bytes_ = readPrefix(path_, std::size_t(1) << 20, &complete);
        if (!complete && !tagsWithin(bytes_, bytes_.size() >= 2 && bytes_[0] == 'I' && bytes_[1] == 'I')) complete = true, bytes_ = readFile(path_);
    } else bytes_ = readFile(path_);
    // Como el Python: "<" si empieza por II y ">" en cualquier otro caso.
    little_ = bytes_.size() >= 2 && bytes_[0] == 'I' && bytes_[1] == 'I';
    try {
        std::set<uint32_t> seen;   // el Python no se protege de ciclos; aquí se corta la cadena al repetirse
        uint32_t off = u32(4);
        while (off && seen.insert(off).second) {
            const uint16_t n = u16(off);
            for (uint32_t i = 0; i < n; ++i) {
                const std::size_t pos = std::size_t(off) + 2 + 12 * i;
                chain_.push_back({u16(pos), u16(pos + 2), u32(pos + 4), u32(pos + 8), pos + 8});
            }
            off = u32(std::size_t(off) + 2 + 12 * std::size_t(n));
        }
    } catch (const Error &e) {
        chainError_ = e.what();
    }
}

const std::vector<Dng::Entry> &Dng::chain() const {
    if (!chainError_.empty()) throw Error(chainError_);
    return chain_;
}

// ---------------------------------------------------------------------------------------------------------------
// Nombre de la cámara y tags numéricos

std::string Dng::cameraName() const {
    const std::string unknown = "Unknown camera";
    if (bytes_.size() < 8 || !((bytes_[0] == 'I' && bytes_[1] == 'I') || (bytes_[0] == 'M' && bytes_[1] == 'M')))
        return unknown;
    if (u16(2) != 42) return unknown;
    const std::size_t offset = u32(4);
    if (offset + 2 > bytes_.size()) return unknown;
    const std::size_t count = u16(offset);
    const std::size_t avail = std::min(12 * count, bytes_.size() - offset - 2) / 12;   // stream.read corto
    std::map<uint16_t, std::string> values;
    for (std::size_t i = 0; i < avail; ++i) {
        const std::size_t pos = offset + 2 + 12 * i;
        const uint16_t tag = u16(pos), kind = u16(pos + 2);
        const uint32_t length = u32(pos + 4), value = u32(pos + 8);
        if ((tag != 271 && tag != 272 && tag != 50708) || kind != 2 || !(length > 0 && length <= 512)) continue;
        std::size_t start = pos + 8, len = length;
        if (length > 4) {
            start = value;
            len = start >= bytes_.size() ? 0 : std::min<std::size_t>(length, bytes_.size() - start);
        }
        const uint8_t *p = bytes_.data() + start;
        const std::size_t nul = std::find(p, p + len, uint8_t(0)) - p;
        values[tag] = strip(decodeUtf8(p, nul));
    }
    auto get = [&](uint16_t t) { auto it = values.find(t); return it == values.end() ? std::string() : it->second; };
    std::string name = get(50708);
    if (name.empty()) name = get(272);
    const std::string make = get(271);
    if (!make.empty() && lowerAscii(name).rfind(lowerAscii(make), 0) != 0) name = strip(make + " " + name);
    return name.empty() ? unknown : name;
}

std::optional<std::vector<double>> Dng::numeric(uint16_t wanted) const {
    if (bytes_.size() < 2 || !((bytes_[0] == 'I' && bytes_[1] == 'I') || (bytes_[0] == 'M' && bytes_[1] == 'M')))
        throw Error("expected classic TIFF/DNG");
    if (u16(2) != 42) throw Error("BigTIFF is not supported");
    auto kindSize = [](uint16_t k) -> int {
        switch (k) {
        case 1: return 1; case 3: return 2; case 4: return 4; case 5: return 8; case 8: return 2;
        case 9: return 4; case 10: return 8; case 11: return 4; case 12: return 8; default: return 0;
        }
    };
    std::vector<double> queue{double(u32(4))};   // pila, como queue.pop() del Python
    std::set<double> visited;
    std::optional<std::vector<double>> result;
    const std::size_t size = bytes_.size();
    while (!queue.empty()) {
        const double offd = queue.back(); queue.pop_back();
        if (offd == 0 || visited.count(offd)) continue;
        // Los offsets de 330/34665 son enteros en un fichero sano; uno racional o negativo no apunta a nada útil.
        if (offd < 0 || offd != std::floor(offd) || offd + 2 > double(size)) throw Error("IFD outside file");
        const std::size_t offset = std::size_t(offd);
        visited.insert(offd);
        const std::size_t count = u16(offset);
        if (offset + 6 + 12 * count > size) throw Error("truncated IFD");
        for (std::size_t i = 0; i < count; ++i) {
            const std::size_t pos = offset + 2 + 12 * i;
            const uint16_t tag = u16(pos), kind = u16(pos + 2);
            const uint32_t length = u32(pos + 4), value = u32(pos + 8);
            const int sz = kindSize(kind);
            if (!sz || (tag != wanted && tag != 330 && tag != 34665)) continue;
            const uint64_t bytes = uint64_t(sz) * length;
            const uint64_t start = bytes <= 4 ? pos + 8 : value;
            if (start + bytes > size) throw Error("tag outside file");
            std::vector<double> numbers(length);
            for (uint32_t j = 0; j < length; ++j) {
                const std::size_t at = std::size_t(start) + std::size_t(j) * sz;
                double v = 0;
                switch (kind) {
                case 1: v = bytes_[at]; break;
                case 3: v = u16(at); break;
                case 4: v = u32(at); break;
                case 5: v = double(u32(at)) / double(u32(at + 4)); break;
                case 8: v = int16_t(u16(at)); break;
                case 9: v = int32_t(u32(at)); break;
                case 10: v = double(int32_t(u32(at))) / double(int32_t(u32(at + 4))); break;
                case 11: { uint32_t b = u32(at); float f; std::memcpy(&f, &b, 4); v = f; break; }
                case 12: {
                    const uint64_t b = little_ ? uint64_t(u32(at)) | uint64_t(u32(at + 4)) << 32
                                               : uint64_t(u32(at)) << 32 | uint64_t(u32(at + 4));
                    std::memcpy(&v, &b, 8); break;
                }
                }
                numbers[j] = v;
            }
            if (tag == 330 || tag == 34665) queue.insert(queue.end(), numbers.begin(), numbers.end());
            if (tag == wanted) result = numbers;
        }
        queue.push_back(double(u32(offset + 2 + count * 12)));
    }
    return result;
}

// ---------------------------------------------------------------------------------------------------------------
// cdng2braw.py: tags de la cadena principal

std::optional<double> Dng::scalar(uint16_t tag) const {   // primera aparición con cnt == 1 y tipo 3/4/5/10
    for (const auto &e : chain_) {
        if (e.tag != tag || e.count != 1) continue;
        if (e.type == 5 || e.type == 10) {
            const uint32_t a = u32(e.value), c = u32(std::size_t(e.value) + 4);
            const double num = e.type == 10 ? double(int32_t(a)) : double(a);
            const double den = e.type == 10 ? double(int32_t(c)) : double(c);
            if (den == 0) throw Error("division by zero");
            return num / den;
        }
        if (e.type == 3) return double(u16(e.valueOffset));
        if (e.type == 4) return double(u32(e.valueOffset));
    }
    chain();
    return std::nullopt;
}

DngColor Dng::color() const {   // dng_color_full: última aparición de cada tag
    std::map<uint16_t, Entry> tags;
    for (const auto &e : chain()) tags[e.tag] = e;
    auto srat = [&](uint16_t tag) {
        const auto it = tags.find(tag);
        if (it == tags.end()) throw Error("DNG tag " + std::to_string(tag) + " not found");
        std::vector<double> v(it->second.count);
        for (uint32_t i = 0; i < it->second.count; ++i) {
            const std::size_t at = std::size_t(it->second.value) + 8 * std::size_t(i);
            const int32_t a = int32_t(u32(at)), b = int32_t(u32(at + 4));
            if (b == 0) throw Error("division by zero");
            v[i] = double(a) / double(b);
        }
        return v;
    };
    auto mat = [&](uint16_t tag) {
        const auto v = srat(tag);
        if (v.size() != 9) throw Error("cannot reshape DNG color matrix");
        Mat3 m; std::copy(v.begin(), v.end(), m.begin()); return m;
    };
    DngColor c;
    c.cm1 = mat(50721);
    c.k1 = tags.count(50778) ? illuminantK(u16(tags[50778].valueOffset), 2856.0) : 2856.0;
    if (tags.count(50722)) {
        c.cm2 = mat(50722);
        c.k2 = tags.count(50779) ? illuminantK(u16(tags[50779].valueOffset), 6504.0) : 6504.0;
    } else {
        c.cm2 = c.cm1; c.k2 = c.k1;
    }
    const auto asn = srat(50728);
    if (asn.size() != 3) throw Error("AsShotNeutral must have 3 values");
    std::copy(asn.begin(), asn.end(), c.asShotNeutral.begin());
    return sigmaOffColor(c);
}

DngColor sigmaOffColor(DngColor c) {
    // Sigma fp: las ColorMatrix1/2 cambian con el modo de color (OFF frente a Standard, Teal & Orange…) aunque el RAW
    // sea el mismo; el perfil de Blackmagic del BRAW casa con las del modo OFF. La cámara escribe inv(D)·ROM con D
    // diagonal (calibración del cuerpo): de un DNG en modo normal se despeja D por filas y se aplica a la ROM del OFF,
    // para que el As Shot y la vista previa sean los del BRAW. Valores medidos en los
    // DNG de la cámara en los dos modos.
    static const Mat3 normal1{1.5277721, -0.78369206, -0.5323574, -0.33300821, 1.1711164, -0.06048574, -0.07630887, 0.16665812, 0.59807446};
    static const Mat3 normal2{0.86104544, -0.21332054, -0.18195931, -0.49615946, 1.1648603, 0.06317307, -0.1159516, 0.16117894, 0.36183215};
    static const Mat3 off1{1.554819, -0.737553, -0.239139, -1.173321, 2.271041, 0.050301, 0.087264, -0.320104, 1.659033};
    static const Mat3 off2{1.59522, -0.756707, -0.245419, -0.969263, 1.876122, 0.041503, 0.036092, -0.132337, 0.686154};
    for (int i = 3; i < 6; ++i)   // fila central: la calibración del cuerpo no la escala (d ≈ 1)
        if (std::fabs(c.cm1[i] - normal1[i]) > .01) return c;
    auto toOff = [](const Mat3 &stored, const Mat3 &normal, const Mat3 &off) {
        Mat3 out{};
        for (int r = 0; r < 3; ++r) {
            double num = 0, den = 0;
            for (int k = 0; k < 3; ++k) { num += stored[3 * r + k] * normal[3 * r + k]; den += normal[3 * r + k] * normal[3 * r + k]; }
            for (int k = 0; k < 3; ++k) out[3 * r + k] = off[3 * r + k] * num / den;
        }
        return out;
    };
    const bool swapped = c.k1 > c.k2;   // ColorMatrix1 de la Sigma es la de luz A (2856 K)
    c.cm1 = toOff(c.cm1, swapped ? normal2 : normal1, swapped ? off2 : off1);
    c.cm2 = toOff(c.cm2, swapped ? normal1 : normal2, swapped ? off1 : off2);
    return c;
}

std::optional<double> Dng::blackLevel() const {   // dng_black_level: primera aparición de tipo 3/4/5
    for (const auto &e : chain_) {
        if (e.tag != 50714) continue;
        if (e.type == 5) {
            const uint32_t a = u32(e.value), c = u32(std::size_t(e.value) + 4);
            if (c == 0) throw Error("division by zero");
            return double(a) / double(c);
        }
        if (e.type == 3) return double(e.count <= 2 ? u16(e.valueOffset) : u16(e.value));
        if (e.type == 4) return double(e.count == 1 ? e.value : u32(e.value));
    }
    chain();
    return std::nullopt;
}

std::optional<Rate> Dng::frameRate() const {   // dng_frame_rate: primera aparición SRATIONAL/RATIONAL
    for (const auto &e : chain_) {
        if (e.tag != 51044 || (e.type != 5 && e.type != 10)) continue;
        const uint32_t a = u32(e.value), b = u32(std::size_t(e.value) + 4);
        const long long num = e.type == 5 ? (long long)a : (long long)int32_t(a);
        const long long den = e.type == 5 ? (long long)b : (long long)int32_t(b);
        if (num <= 0 || den <= 0) return std::nullopt;
        const long long g = std::gcd(num, den);
        // Las cadencias reales caben de sobra en int; un valor absurdo se rechaza en vez de truncarse.
        if (num / g > INT32_MAX || den / g > INT32_MAX) throw Error("unsupported frame rate");
        return Rate{int(num / g), int(den / g)};
    }
    chain();
    return std::nullopt;
}

std::optional<long long> Dng::timecodeFrames(int fps) const {   // dng_timecode_frames: tag 51043 en BCD
    for (const auto &e : chain_) {
        if (e.tag != 51043) continue;
        if (std::size_t(e.value) + 4 > bytes_.size()) throw Error(Truncated);
        const uint8_t *tc = bytes_.data() + e.value;
        auto bcd = [](uint8_t x, int m) { return (x >> 4 & m) * 10 + (x & 15); };
        const long long ff = bcd(tc[0], 3), ss = bcd(tc[1], 7), mm = bcd(tc[2], 7), hh = bcd(tc[3], 3);
        return ((hh * 60 + mm) * 60 + ss) * fps + ff;
    }
    chain();
    return std::nullopt;
}

bool Dng::timecodeDropFrame() const {
    for (const auto &e : chain_) {
        if (e.tag != 51043) continue;
        if (e.value >= bytes_.size()) throw Error(Truncated);
        return bytes_[e.value] & 0x40;
    }
    chain();
    return false;
}

std::pair<double, double> Dng::asShot() const {   // dng_as_shot
    DngColor c = color();
    Mat3 cm1 = c.cm1, cm2 = c.cm2;
    double t1 = c.k1, t2 = c.k2;
    if (t1 > t2) { std::swap(t1, t2); std::swap(cm1, cm2); }
    auto cmFor = [&](double t) {
        if (t <= t1) return cm1;
        if (t >= t2) return cm2;
        const double g = (1 / t - 1 / t2) / (1 / t1 - 1 / t2);
        Mat3 m; for (int i = 0; i < 9; ++i) m[i] = g * cm1[i] + (1 - g) * cm2[i];
        return m;
    };
    auto xyOf = [&](const Mat3 &cm) {
        const Vec3 xyz = math::solve(cm, c.asShotNeutral);
        const double s = xyz[0] + xyz[1] + xyz[2];
        return std::pair<double, double>(xyz[0] / s, xyz[1] / s);
    };
    std::pair<double, double> xy(0.3457, 0.3585);
    for (int i = 0; i < 30; ++i) {
        const double t = xyToTempTint(xy.first, xy.second).first;
        const auto nxy = xyOf(cmFor(t));
        if (std::fabs(nxy.first - xy.first) < 1e-7 && std::fabs(nxy.second - xy.second) < 1e-7) { xy = nxy; break; }
        xy = nxy;
    }
    return xyToTempTint(xy.first, xy.second);
}

// ---------------------------------------------------------------------------------------------------------------
// LibRaw (rawpy)

namespace {

struct LibRawAllocator : LibRaw {
    void touch() { free(calloc(1, 1)); }   // calloc/free pasan por la sección crítica de libraw_memmgr
};

struct LibRawHandle {
    std::unique_ptr<LibRaw> raw = std::make_unique<LibRaw>();
    // RawPy.handle_error: código > 0 es errno, < 0 un error de LibRaw; después, cualquier error de datos.
    void check(int code) const {
        if (code > 0) throw Error(std::string("cannot read DNG: ") + std::strerror(code));
        if (code < 0) throw Error(std::string("LibRaw: ") + libraw_strerror(code));
        if (raw->error_count() > 0) throw Error("Data error or unsupported file format");
    }
    void open(const std::filesystem::path &path) {
#ifdef _WIN32
        check(raw->open_file(path.wstring().c_str()));
#else
        check(raw->open_file(path.string().c_str()));
#endif
    }
};

// rawpy black_level_per_channel (data_helper.h adjust_bl_), solo el canal 0, que es el que usa dng_camera_rgb.
unsigned blackLevelChannel0(LibRaw &p) {
    std::vector<unsigned> cb(std::begin(p.imgdata.rawdata.color.cblack), std::end(p.imgdata.rawdata.color.cblack));
    unsigned black = p.imgdata.rawdata.color.black;
    const unsigned filters = p.imgdata.idata.filters;
    if (filters > 1000 && (cb[4] + 1) / 2 == 1 && (cb[5] + 1) / 2 == 1) {
        int clrs[4], lastg = -1, gcnt = 0;
        for (int c = 0; c < 4; ++c) {
            clrs[c] = p.FC(c / 2, c % 2);
            if (clrs[c] == 1) { ++gcnt; lastg = c; }
        }
        if (gcnt > 1 && lastg >= 0) clrs[lastg] = 3;
        for (int c = 0; c < 4; ++c) cb[clrs[c]] += cb[6 + c / 2 % cb[4] * cb[5] + c % 2 % cb[5]];
        cb[4] = cb[5] = 0;
    } else if (filters <= 1000 && cb[4] == 1 && cb[5] == 1) {
        for (int c = 0; c < 4; ++c) cb[c] += cb[6];
        cb[4] = cb[5] = 0;
    }
    unsigned i = cb[3];
    for (int c = 0; c < 3; ++c) if (i > cb[c]) i = cb[c];
    for (int c = 0; c < 4; ++c) cb[c] -= i;
    black += i;
    if (cb[4] && cb[5]) {
        i = cb[6];
        for (unsigned c = 1; c < cb[4] * cb[5]; ++c) if (i > cb[6 + c]) i = cb[6 + c];
        int nonz = 0;
        for (unsigned c = 0; c < cb[4] * cb[5]; ++c) { cb[6 + c] -= i; if (cb[6 + c]) ++nonz; }
        black += i;
        if (!nonz) cb[4] = cb[5] = 0;
    }
    return cb[0] + black;
}

}  // namespace

void prepareLibRaw() { LibRawAllocator().touch(); }

std::pair<int, int> Dng::rawSize() const {   // rawpy.imread(path).sizes: basta con abrir (sin desempaquetar)
    LibRawHandle h;
    h.open(path_);
    return {h.raw->imgdata.sizes.width, h.raw->imgdata.sizes.height};
}

BayerImage Dng::cameraBayer() const {
    LibRawHandle handle;
    handle.open(path_);
    auto &raw = *handle.raw;
    handle.check(raw.unpack());
    const auto &data = raw.imgdata;
    if (!data.rawdata.raw_image || data.idata.filters < 1000)
        throw Error("Expected a Bayer DNG");
    const auto &size = data.sizes;
    int x = 0, y = 0, w = size.width, h = size.height;
    const auto origin = numeric(50719), crop = numeric(50720);
    if (origin && crop) {
        if (origin->size() != 2 || crop->size() != 2) throw Error("DefaultCrop must have 2 values");
        x = int((*origin)[0]); y = int((*origin)[1]);
        w = int((*crop)[0]); h = int((*crop)[1]);
    }
    if (x < 0 || y < 0 || w < 2 || h < 2 || x + w > size.width || y + h > size.height)
        throw Error("DefaultCrop outside decoded sensor image");
    w -= w % 2; h -= h % 2;
    const double black = blackLevel().value_or(blackLevelChannel0(raw));
    const double range = data.rawdata.color.maximum - black;
    if (range <= 0) throw Error("Invalid DNG black and white levels");
    const std::size_t count = std::size_t(w) * h;
    BayerImage out{w, h, std::vector<float>(count), std::vector<uint8_t>(count)};
    const auto pitch = size.raw_pitch / sizeof(uint16_t);
    int pattern[4]{}, counts[3]{};
    for (int row = 0; row < 2; ++row) for (int col = 0; col < 2; ++col) {
        const char channel = data.idata.cdesc[raw.COLOR(row + y, col + x)];
        const int c = channel == 'R' ? 0 : channel == 'G' ? 1 : channel == 'B' ? 2 : -1;
        if (c < 0) throw Error("Expected a Bayer DNG");
        pattern[2 * row + col] = c;
        ++counts[c];
    }
    if (counts[0] != 1 || counts[1] != 2 || counts[2] != 1 || (pattern[0] == 1) != (pattern[3] == 1))
        throw Error("Expected a Bayer DNG");
    for (int row = 0; row < h; ++row) {
        const auto *src = data.rawdata.raw_image + std::size_t(row + y + size.top_margin) * pitch + x + size.left_margin;
        for (int col = 0; col < w; ++col) {
            const auto i = std::size_t(row) * w + col;
            out.samples[i] = float((src[col] - black) / range);
            out.colors[i] = uint8_t(pattern[2 * (row % 2) + col % 2]);
        }
    }
    return out;
}

RgbImage Dng::cameraRgb() const {
    LibRawHandle h;
    LibRaw &p = *h.raw;
    h.open(path_);
    h.check(p.unpack());
    const unsigned white = p.imgdata.rawdata.color.maximum;   // r.white_level

    // rawpy.Params(...) + RawPy.apply_params con los argumentos de dng_camera_rgb; el resto, valores por defecto de
    // rawpy (no los de LibRaw).
    libraw_output_params_t &o = p.imgdata.params;
    const OgDebayerConfig debayer = ogDebayerFromEnv();
    o.user_qual = debayer.userQual;   // default 3 = AHD; CDNG_OG_DEBAYER otherwise
    o.half_size = 0;
    o.four_color_rgb = 0;
    o.dcb_iterations = 0;
    o.dcb_enhance_fl = 0;
    o.fbdd_noiserd = 0;
    o.threshold = 0.0f;
    o.med_passes = 0;
    o.use_camera_wb = 0;
    o.use_auto_wb = 0;
    for (int i = 0; i < 4; ++i) o.user_mul[i] = 1.0f;
    o.output_color = 0;   // ColorSpace.raw
    o.output_bps = 16;
    o.user_flip = 0;
    o.user_black = 0;
    o.user_sat = int(white);
    o.no_auto_bright = 1;
    o.no_auto_scale = 0;
    o.auto_bright_thr = LIBRAW_DEFAULT_AUTO_BRIGHTNESS_THRESHOLD;
    o.adjust_maximum_thr = 0.0f;
    o.bright = 1.0f;
    o.highlight = 0;   // HighlightMode.Clip
    o.exp_correc = -1;
    o.exp_shift = 1.0f;
    o.exp_preser = 0.0f;
    o.bad_pixels = nullptr;
    o.gamm[0] = 1.0 / 1.0;
    o.gamm[1] = 1.0;
    o.aber[0] = 1.0;
    o.aber[2] = 1.0;

    h.check(p.dcraw_process());
    if (debayer.userQual != 3 && (p.imgdata.process_warnings & LIBRAW_WARN_FALLBACK_TO_AHD)) {
        throw Error("CDNG_OG_DEBAYER=" + debayer.name +
                    " is not available in this LibRaw build (fell back to AHD; refusing silent substitute)");
    }
    int err = 0;
    libraw_processed_image_t *img = p.dcraw_make_mem_image(&err);
    h.check(err);
    std::unique_ptr<libraw_processed_image_t, void (*)(libraw_processed_image_t *)> guard(img, LibRaw::dcraw_clear_mem);
    if (!img || img->type != LIBRAW_IMAGE_BITMAP || img->colors != 3 || img->bits != 16)
        throw Error("unexpected LibRaw output");

    const std::optional<double> tagBlack = blackLevel();
    const double black = tagBlack ? *tagBlack : double(blackLevelChannel0(p));
    const double scale = double(white) / 65535.0, range = double(white) - black;
    RgbImage out(img->width, img->height);
    const auto *src = reinterpret_cast<const uint16_t *>(img->data);
    for (std::size_t i = 0; i < out.data.size(); ++i)
        out.data[i] = float((double(src[i]) * scale - black) / range);
    return out;
}

// ---------------------------------------------------------------------------------------------------------------
// Funciones libres

int nominalFps(Rate rate) {   // max(1, round(num / den)), redondeo de Python (a par)
    return std::max(1, int(std::nearbyint(double(rate.num) / double(rate.den))));
}

std::pair<double, double> xyToTempTint(double x, double y) {
    static const double R[31][4] = {
        {0, .18006, .26352, -.24341}, {10, .18066, .26589, -.25479}, {20, .18133, .26846, -.26876},
        {30, .18208, .27119, -.28539}, {40, .18293, .27407, -.30470}, {50, .18388, .27709, -.32675},
        {60, .18494, .28021, -.35156}, {70, .18611, .28342, -.37915}, {80, .18740, .28668, -.40955},
        {90, .18880, .28997, -.44278}, {100, .19032, .29326, -.47888}, {125, .19462, .30141, -.58204},
        {150, .19962, .30921, -.70471}, {175, .20525, .31647, -.84901}, {200, .21142, .32312, -1.0182},
        {225, .21807, .32909, -1.2168}, {250, .22511, .33439, -1.4512}, {275, .23247, .33904, -1.7298},
        {300, .24010, .34308, -2.0637}, {325, .24702, .34655, -2.4681}, {350, .25591, .34951, -2.9641},
        {375, .26400, .35200, -3.5814}, {400, .27218, .35407, -4.3633}, {425, .28039, .35577, -5.3762},
        {450, .28863, .35714, -6.7262}, {475, .29685, .35823, -8.5955}, {500, .30505, .35907, -11.324},
        {525, .31320, .35968, -15.628}, {550, .32129, .36011, -23.325}, {575, .32931, .36038, -40.770},
        {600, .33724, .36051, -116.45}};
    const int n = 31;
    const double u = 2 * x / (1.5 - x + 6 * y), v = 3 * y / (1.5 - x + 6 * y);
    double lastDt = 0, lastDu = 0, lastDv = 0;
    for (int i = 1; i < n; ++i) {
        double du = 1.0, dv = R[i][3], len = math::hypot(du, dv);
        du /= len; dv /= len;
        const double uu = u - R[i][1], vv = v - R[i][2];
        double dt = -uu * dv + vv * du;
        if (dt <= 0 || i == n - 1) {
            dt = std::max(-dt, 0.0);
            const double f = i == 1 ? 0.0 : dt / (lastDt + dt);
            const double mired = R[i - 1][0] * f + R[i][0] * (1 - f);
            const double ud = R[i - 1][1] * f + R[i][1] * (1 - f), vd = R[i - 1][2] * f + R[i][2] * (1 - f);
            du = du * (1 - f) + lastDu * f; dv = dv * (1 - f) + lastDv * f;
            len = math::hypot(du, dv); du /= len; dv /= len;
            return {1e6 / mired, ((u - ud) * du + (v - vd) * dv) * -3000.0};
        }
        lastDt = dt; lastDu = du; lastDv = dv;
    }
    return {0, 0};   // inalcanzable: la última isoterma siempre devuelve
}

Mat3 bradford(const Vec3 &srcWhite, const Vec3 &dstWhite) {
    const Vec3 s = math::mul(math::Bradford, srcWhite), d = math::mul(math::Bradford, dstWhite);
    Mat3 diag{};
    for (int i = 0; i < 3; ++i) diag[i * 4] = d[i] / s[i];
    return math::mul(math::mul(math::inv(math::Bradford), diag), math::Bradford);
}

std::pair<Mat3, double> matrixForNeutral(const DngColor &c, const Vec3 &neutral) {
    if (c.k1 == c.k2) return {c.cm2, c.k2};
    const bool order = c.k1 < c.k2;
    const double lo = order ? c.k1 : c.k2, hi = order ? c.k2 : c.k1;
    const Mat3 &cmLo = order ? c.cm1 : c.cm2, &cmHi = order ? c.cm2 : c.cm1;
    double t = hi;
    Mat3 cm = cmHi;
    for (int it = 0; it < 12; ++it) {
        double w = (1 / t - 1 / hi) / (1 / lo - 1 / hi);
        w = std::min(std::max(w, 0.0), 1.0);
        for (int i = 0; i < 9; ++i) cm[i] = w * cmLo[i] + (1 - w) * cmHi[i];
        const Vec3 xyz = math::mul(math::inv(cm), neutral);
        const double s = xyz[0] + xyz[1] + xyz[2], x = xyz[0] / s, y = xyz[1] / s;
        const double nn = (x - 0.3320) / (y - 0.1858);
        const double mccamy = -449 * nn * nn * nn + 3525 * nn * nn - 6823.3 * nn + 5520.33;
        const double tNew = std::min(std::max(mccamy, lo), hi);
        if (std::fabs(tNew - t) < 0.5) { t = tNew; break; }
        t = tNew;
    }
    return {cm, t};
}

Mat3 cameraTo709(const DngColor &color, const Vec3 &neutral) {
    const Mat3 camToXyz = math::inv(matrixForNeutral(color, neutral).first);
    const Vec3 whiteXyz = math::mul(camToXyz, neutral);
    const Mat3 adapt = bradford(whiteXyz, math::D65);
    return math::mul(math::mul(math::XyzTo709, adapt), camToXyz);
}

Mat3 cameraToP3(const DngColor &color, const Vec3 &neutral) {
    const Mat3 camToXyz = math::inv(matrixForNeutral(color, neutral).first);
    const Vec3 whiteXyz = math::mul(camToXyz, neutral);
    const Mat3 adapt = bradford(whiteXyz, math::D65);
    return math::mul(math::mul(math::XyzToP3, adapt), camToXyz);
}

namespace {
uint32_t crc32(const uint8_t *data, std::size_t size) {   // zlib.crc32
    uint32_t crc = 0xffffffffu;
    for (std::size_t i = 0; i < size; ++i) {
        crc ^= data[i];
        for (int k = 0; k < 8; ++k) crc = (crc >> 1) ^ (0xedb88320u & (0u - (crc & 1u)));
    }
    return ~crc;
}
}  // namespace

std::optional<RawTag> Dng::rawTag(uint16_t tag, bool exif) const {
    static const int sizes[13] = {0, 1, 1, 2, 4, 8, 1, 1, 2, 4, 8, 4, 8};
    const auto copy = [&](uint16_t type, uint32_t count, std::size_t valueAt) -> std::optional<RawTag> {
        if (type == 0 || type > 12) return std::nullopt;
        const std::size_t size = std::size_t(sizes[type]) * count;
        const std::size_t at = size <= 4 ? valueAt : u32(valueAt);
        if (at + size > bytes_.size()) return std::nullopt;
        return RawTag{tag, type, count, {bytes_.begin() + std::ptrdiff_t(at), bytes_.begin() + std::ptrdiff_t(at + size)}};
    };
    try {
        std::optional<uint32_t> exifAt;
        for (const auto &entry : chain()) {
            if (!exif && entry.tag == tag) return copy(entry.type, entry.count, entry.valueOffset);
            if (entry.tag == 0x8769 && !exifAt) exifAt = entry.value;
        }
        if (!exif || !exifAt || *exifAt + 2 > bytes_.size()) return std::nullopt;
        const unsigned n = u16(*exifAt);
        for (unsigned k = 0; k < n; ++k) {
            const std::size_t e = *exifAt + 2 + 12 * std::size_t(k);
            if (e + 12 > bytes_.size()) break;
            if (u16(e) == tag) return copy(u16(e + 2), u32(e + 4), e + 8);
        }
    } catch (const std::exception &) {}
    return std::nullopt;
}

std::vector<uint8_t> Dng::gyroBlock() const {
    struct Field { uint16_t type = 0; uint32_t count = 0, value = 0; };
    const auto ifd = [&](std::size_t at) {
        std::map<uint16_t, Field> out;
        if (at + 2 > bytes_.size()) return out;
        const unsigned n = u16(at);
        for (unsigned k = 0; k < n; ++k) {
            const std::size_t e = at + 2 + 12 * std::size_t(k);
            if (e + 12 > bytes_.size()) break;
            out[u16(e)] = {u16(e + 2), u32(e + 4), u32(e + 8)};
        }
        return out;
    };
    const auto le32 = [&](std::size_t at) { return uint32_t(bytes_[at]) | uint32_t(bytes_[at + 1]) << 8 | uint32_t(bytes_[at + 2]) << 16 | uint32_t(bytes_[at + 3]) << 24; };
    std::optional<uint32_t> exifAt;
    try {
        for (const auto &entry : chain()) if (entry.tag == 0x8769) { exifAt = entry.value; break; }
    } catch (const std::exception &) { return {}; }
    if (!exifAt) return {};
    const auto exif = ifd(*exifAt);
    const auto note = exif.find(0x927C);
    if (note == exif.end()) return {};
    const auto tags = ifd(std::size_t(note->second.value) + 10);   // tras «SIGMA\0\0\0» y la versión
    if (const auto g = tags.find(0x4653); g != tags.end()) {
        const auto &f = g->second;
        if (f.type != 7 || f.count <= 4) return {};
        const std::size_t at = (std::size_t(f.value) + 3) & ~std::size_t(3), end = std::size_t(f.value) + f.count;
        if (end > bytes_.size() || at + 12 > end || std::memcmp(&bytes_[at], "FSG2", 4) != 0) return {};
        const uint32_t n = le32(at + 8);
        if (bytes_[at + 4] != 2 || n == 0 || at + 12 + n > end) return {};
        return {bytes_.begin() + std::ptrdiff_t(at), bytes_.begin() + std::ptrdiff_t(at + 12 + n)};
    }
    const auto area = tags.find(0x200);
    if (area == tags.end()) return {};
    const std::size_t start = area->second.value, end = std::min(bytes_.size(), start + area->second.count);
    std::vector<uint8_t> found;
    int valid = 0;
    for (std::size_t i = start; i + 16 <= end; ++i) {
        if (std::memcmp(&bytes_[i], "FSG2", 4) != 0) continue;
        const uint32_t n = le32(i + 8);
        const std::size_t body = i + 12 + std::size_t(n);
        if (bytes_[i + 4] != 1 || bytes_[i + 6] != 0 || bytes_[i + 7] != 0 || body + 4 > end) continue;
        if (crc32(&bytes_[i], body - i) != le32(body)) continue;
        ++valid; found.assign(bytes_.begin() + std::ptrdiff_t(i), bytes_.begin() + std::ptrdiff_t(body + 4));
    }
    return valid == 1 ? found : std::vector<uint8_t>{};
}

RgbImage cropSensor(const RgbImage &image, const Dng &dng) {
    const auto origin = dng.numeric(50719), size = dng.numeric(50720);
    if (!origin || !size) return image;
    if (origin->size() != 2 || size->size() != 2) throw Error("DefaultCrop must have 2 values");
    // int() de Python: trunca hacia cero.
    const long long x = (long long)std::trunc((*origin)[0]), y = (long long)std::trunc((*origin)[1]);
    const long long w = (long long)std::trunc((*size)[0]), h = (long long)std::trunc((*size)[1]);
    if (x < 0 || y < 0 || w < 0 || h < 0 || x + w > image.width || y + h > image.height)
        throw Error("DefaultCrop outside decoded sensor image");
    RgbImage out{int(w), int(h)};
    for (long long r = 0; r < h; ++r)
        std::copy_n(image.px(int(x), int(y + r)), std::size_t(w) * 3, out.px(0, int(r)));
    return out;
}

}  // namespace cdng
