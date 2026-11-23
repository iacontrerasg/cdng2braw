// Bloque D — contenedor .braw. Ver container.hpp: cada función cita el Python del que es port; el resultado es
// idéntico byte a byte (tests/container_test.cpp).
#include "container.hpp"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <functional>
#include <map>
#include <set>
#include <string>

namespace cdng {

namespace {

using Bytes = std::vector<uint8_t>;
constexpr std::size_t npos = std::size_t(-1);

// 2^(1/3), la ganancia analógica que escribe el Video Assist a ISO 800.
float vaGain() { return float(std::pow(2.0, 1.0 / 3.0)); }

uint32_t floatBits(float v) { uint32_t u; std::memcpy(&u, &v, 4); return u; }

void append(Bytes &out, const uint8_t *data, std::size_t size) { out.insert(out.end(), data, data + size); }
void append(Bytes &out, const Bytes &data) { out.insert(out.end(), data.begin(), data.end()); }
Bytes be16Bytes(uint16_t v) { Bytes b(2); put16(b.data(), v); return b; }
Bytes be32Bytes(uint32_t v) { Bytes b(4); put32(b.data(), v); return b; }

// struct.pack('>I', …) falla fuera de rango; aquí también.
uint32_t u32(long long v) {
    if (v < 0 || v > 0xFFFFFFFFLL) throw Error("'I' format requires 0 <= number <= 4294967295");
    return uint32_t(v);
}

std::size_t find(const Bytes &b, const char *needle, std::size_t from, std::size_t length = 4) {
    if (from > b.size()) return npos;
    auto i = std::search(b.begin() + std::ptrdiff_t(from), b.end(), needle, needle + length);
    return i == b.end() ? npos : std::size_t(i - b.begin());
}

// Cabecera de 32 bits.
Bytes atom(const char *type, const uint8_t *body, std::size_t size) {
    if (size + 8 > 0xFFFFFFFFull) throw Error("atom too large");
    Bytes out(8);
    put32(out.data(), uint32_t(size + 8));
    std::memcpy(out.data() + 4, type, 4);
    append(out, body, size);
    return out;
}
Bytes atom(const char *type, const Bytes &body) { return atom(type, body.data(), body.size()); }

struct Box {
    std::size_t off = 0, size = 0, header = 8;
    char type[4] = {};
    bool is(const char *t) const { return std::memcmp(type, t, 4) == 0; }
    std::size_t body() const { return off + header; }
    std::size_t end() const { return off + size; }
};

// braw_container.read_atoms: el cuerpo se recorre desde off + 8 (como el Python, que no salta la
// cabecera ampliada dentro del moov).
std::vector<Box> muxAtoms(const uint8_t *b, std::size_t off, std::size_t end) {
    std::vector<Box> out;
    while (off + 8 <= end) {
        Box x; x.off = off;
        uint64_t size = be32(b + off);
        std::memcpy(x.type, b + off + 4, 4);
        if (size == 1) {
            if (off + 16 > end) throw Error("invalid atom size");
            size = be64(b + off + 8);
        }
        if (size == 0) size = end - off;
        if (size < 8 || size > end - off) throw Error("invalid atom size");
        x.size = std::size_t(size);
        out.push_back(x);
        off += x.size;
    }
    return out;
}

// Estricto, cuerpo tras la cabecera de 8 o 16 bytes.
std::vector<Box> profileAtoms(const uint8_t *b, std::size_t off, std::size_t end) {
    std::vector<Box> out;
    while (off < end) {
        if (end - off < 8) throw Error("truncated atom");
        Box x; x.off = off;
        uint64_t size = be32(b + off);
        std::memcpy(x.type, b + off + 4, 4);
        if (size == 1) {
            if (end - off < 16) throw Error("invalid atom size");
            size = be64(b + off + 8); x.header = 16;
        } else if (size == 0) {
            size = end - off;
        }
        if (size < x.header || size > end - off) throw Error("invalid atom size");
        x.size = std::size_t(size);
        out.push_back(x);
        off += x.size;
    }
    return out;
}

// ---------------------------------------------------------------------------------------------------------------
// Fichero: lectura y escritura con desplazamientos de 64 bits.

struct File {
    std::FILE *f = nullptr;
    File() = default;
    File(const std::filesystem::path &path, const char *mode) { open(path, mode); }
    void open(const std::filesystem::path &path, const char *mode) {
#ifdef _WIN32
        std::wstring m(mode, mode + std::strlen(mode));
        f = _wfopen(path.c_str(), m.c_str());
#else
        f = std::fopen(path.c_str(), mode);
#endif
    }
    File(const File &) = delete;
    File &operator=(const File &) = delete;
    ~File() { if (f) std::fclose(f); }
    explicit operator bool() const { return f != nullptr; }
    void seek(uint64_t offset) {
#ifdef _WIN32
        const int r = _fseeki64(f, (long long)offset, SEEK_SET);
#else
        const int r = fseeko(f, off_t(offset), SEEK_SET);
#endif
        if (r != 0) throw Error("Could not seek in the BRAW file.");
    }
    uint64_t length() {
#ifdef _WIN32
        _fseeki64(f, 0, SEEK_END); return uint64_t(_ftelli64(f));
#else
        fseeko(f, 0, SEEK_END); return uint64_t(ftello(f));
#endif
    }
    Bytes read(uint64_t offset, std::size_t size) {
        seek(offset);
        Bytes out(size);
        if (size && std::fread(out.data(), 1, size, f) != size) throw Error("Could not read the BRAW file.");
        return out;
    }
    void write(const uint8_t *data, std::size_t size) {
        if (size && std::fwrite(data, 1, size, f) != size) throw Error("Could not write the BRAW file.");
    }
    void close() {
        if (f) {
            const int r = std::fclose(f); f = nullptr;
            if (r != 0) throw Error("Could not write the BRAW file.");
        }
    }
};

// Átomo de primer nivel `kind` (sin cargar el mdat). Python: el recorrido de video_samples y rewrite_moov_file.
struct TopAtom { uint64_t offset = 0, size = 0; bool last = false; };
TopAtom findTopAtom(File &file, const char *kind) {
    const uint64_t length = file.length();
    uint64_t offset = 0;
    while (offset < length) {
        if (length - offset < 8) throw Error("invalid top-level atom");
        Bytes head = file.read(offset, 8);
        uint64_t size = be32(head.data());
        if (size == 1) {
            if (length - offset < 16) throw Error("invalid top-level atom");
            size = be64(file.read(offset + 8, 8).data());
        } else if (size == 0) {
            size = length - offset;
        }
        if (size < 8 || size > length - offset) throw Error("invalid top-level atom");
        if (std::memcmp(head.data() + 4, kind, 4) == 0) return {offset, size, offset + size == length};
        offset += size;
    }
    throw Error("missing moov");
}

Bytes readMoov(const std::filesystem::path &path) {
    File file(path, "rb");
    if (!file) throw Error("Could not read " + path.string());
    const TopAtom moov = findTopAtom(file, "moov");
    return file.read(moov.offset, std::size_t(moov.size));
}

std::vector<VideoSample> samplesFromMoov(const Bytes &f) {
    const auto top = muxAtoms(f.data(), 8, f.size());
    auto trak = std::find_if(top.begin(), top.end(), [](const Box &b) { return b.is("trak"); });
    if (trak == top.end()) throw Error("missing video track");
    Box box = *trak;
    for (const char *t : {"mdia", "minf", "stbl"}) {
        const auto kids = muxAtoms(f.data(), box.off + 8, box.end());
        auto it = std::find_if(kids.begin(), kids.end(), [t](const Box &b) { return b.is(t); });
        if (it == kids.end()) throw Error("missing video sample table");
        box = *it;
    }
    const auto kids = muxAtoms(f.data(), box.off + 8, box.end());
    auto stsz = std::find_if(kids.begin(), kids.end(), [](const Box &b) { return b.is("stsz"); });
    auto co = std::find_if(kids.begin(), kids.end(), [](const Box &b) { return b.is("co64") || b.is("stco"); });
    if (stsz == kids.end() || co == kids.end()) throw Error("missing video sample table");
    const bool wide = co->is("co64");
    const uint32_t n = be32(&f[stsz->off + 16]);
    const uint32_t m = be32(&f[co->off + 12]);
    if (stsz->off + 20 + 4ull * n > f.size() || co->off + 16 + (wide ? 8ull : 4ull) * m > f.size())
        throw Error("invalid video sample table");
    std::vector<VideoSample> out(std::min(n, m));
    for (std::size_t i = 0; i < out.size(); ++i) {
        out[i].size = be32(&f[stsz->off + 20 + 4 * i]);
        out[i].offset = wide ? be64(&f[co->off + 16 + 8 * i]) : be32(&f[co->off + 16 + 4 * i]);
    }
    return out;
}

// ---------------------------------------------------------------------------------------------------------------
// moov del clip.

std::string trakKind(const Bytes &trak) {
    for (const char *key : {"vide", "tmcd", "soun", "meta"}) {
        for (std::size_t i = find(trak, "hdlr", 0); i != npos; i = find(trak, "hdlr", i + 4))
            if (i + 16 <= trak.size() && std::memcmp(&trak[i + 12], key, 4) == 0) {
                if (std::strcmp(key, "meta") != 0) return key;
                // Pistas de movimiento de la plantilla (una Pocket 4K): la clave de su entrada mebx.
                const char *gyro = "com.blackmagicdesign.motiondata.gyroscope";
                const char *accel = "com.blackmagicdesign.motiondata.accelerometer";
                if (find(trak, gyro, 0, std::strlen(gyro)) != npos) return "mogy";
                if (find(trak, accel, 0, std::strlen(accel)) != npos) return "moac";
                return "other";
            }
    }
    return "other";
}

using Patch = std::map<std::string, Bytes>;

Bytes rebuildStbl(const Bytes &trak, const Patch &patch) {
    std::function<Bytes(std::size_t, std::size_t)> rebuild = [&](std::size_t off, std::size_t end) {
        Bytes out;
        for (const Box &a : muxAtoms(trak.data(), off, end)) {
            if (a.is("trak") || a.is("mdia") || a.is("minf")) {
                append(out, atom(a.type, rebuild(a.off + 8, a.end())));
            } else if (a.is("stbl")) {
                Bytes body;
                for (const Box &c : muxAtoms(trak.data(), a.off + 8, a.end())) {
                    std::string key(c.type, 4);
                    if (key == "stco") key = "co64";
                    auto it = patch.find(key);
                    if (it != patch.end()) append(body, it->second);
                    else append(body, &trak[c.off], c.size);
                }
                append(out, atom("stbl", body));
            } else {
                append(out, &trak[a.off], a.size);
            }
        }
        return out;
    };
    return rebuild(0, trak.size());
}

void putAt(Bytes &b, std::size_t at, uint32_t v) {
    if (at + 4 > b.size()) throw Error("truncated atom");
    put32(&b[at], v);
}

Bytes patchDurations(Bytes b, uint32_t movieDur, uint32_t mediaDur, uint32_t timescale) {
    std::function<void(std::size_t, std::size_t)> walk = [&](std::size_t off, std::size_t end) {
        for (const Box &a : muxAtoms(b.data(), off, end)) {
            if (a.is("trak") || a.is("mdia") || a.is("minf") || a.is("stbl") || a.is("edts")) walk(a.off + 8, a.end());
            else if (a.is("tkhd")) putAt(b, a.off + 8 + 20, movieDur);
            else if (a.is("mdhd")) {
                if (timescale) putAt(b, a.off + 8 + 12, timescale);
                putAt(b, a.off + 8 + 16, mediaDur);
            } else if (a.is("elst")) putAt(b, a.off + 8 + 8, movieDur);
        }
    };
    walk(8, b.size());
    return b;
}

Bytes tmcdStsd(const Bytes &trak, int num, int den, bool dropFrame) {
    const std::size_t i = find(trak, "stsd", 0);
    if (i == npos || i < 4) throw Error("la pista tmcd no tiene entrada tmcd");
    const uint32_t size = be32(&trak[i - 4]);
    if (i - 4 + size > trak.size() || size < 16 + 33) throw Error("la pista tmcd no tiene entrada tmcd");
    Bytes b(trak.begin() + std::ptrdiff_t(i - 4), trak.begin() + std::ptrdiff_t(i - 4 + size));
    const std::size_t e = 16;
    if (std::memcmp(&b[e + 4], "tmcd", 4) != 0) throw Error("la pista tmcd no tiene entrada tmcd");
    uint32_t flags = be32(&b[e + 20]);
    flags = dropFrame ? flags | 1u : flags & ~1u;
    put32(&b[e + 20], flags); put32(&b[e + 24], uint32_t(num)); put32(&b[e + 28], uint32_t(den));
    // round() de Python: empates al par (std::nearbyint con el redondeo por defecto).
    const double frames = std::max(1.0, std::nearbyint(double(num) / double(den)));
    if (frames > 255) throw Error("byte must be in range(0, 256)");
    b[e + 32] = uint8_t(frames);
    return b;
}

// Átomo de 12 bytes `tag` + u32, si aparece exactamente una vez. Con strict, si no
// aparece una vez, lanza; sin strict, lo deja igual.
void patchFlag(Bytes &data, const char *tag, uint32_t value, bool strict) {
    char needle[8];
    put32(reinterpret_cast<uint8_t *>(needle), 12);
    std::memcpy(needle + 4, tag, 4);
    const std::size_t i = find(data, needle, 0, 8);
    if (i == npos || find(data, needle, i + 1, 8) != npos) {
        if (strict) throw Error(std::string("expected exactly one b'") + std::string(tag, 4) + "' atom");
        return;
    }
    put32(&data[i + 8], value);
}

struct Audio { std::vector<uint64_t> offsets; uint32_t perChunk; long long total; };

// Movimiento escrito en el mdat: trozos de MotionChunk lecturas de 20 bytes, como los escribe la cámara.
constexpr uint32_t MotionChunk = 1024, MotionSampleSize = 20;
struct Motion { uint32_t rate = 0; long long total = 0; std::vector<uint64_t> gyroOffsets, accelOffsets; };

Bytes motionTrak(const Bytes &trakIn, const Motion &motion, const std::vector<uint64_t> &offsets, uint32_t movieDur) {
    const long long total = motion.total;
    const std::size_t chunks = offsets.size();
    Bytes trak = patchDurations(trakIn, movieDur, u32(total), motion.rate);
    std::vector<std::array<uint32_t, 3>> entries{{1, MotionChunk, 1}};
    if (total % MotionChunk && chunks > 1) entries.push_back({uint32_t(chunks), u32(total - MotionChunk * ((long long)chunks - 1)), 1});
    else if (total % MotionChunk) entries = {{1, u32(total), 1}};
    Bytes stsc(8); put32(&stsc[4], uint32_t(entries.size()));
    for (const auto &e : entries) for (uint32_t v : e) append(stsc, be32Bytes(v));
    Bytes stts(16); put32(&stts[4], 1); put32(&stts[8], u32(total)); put32(&stts[12], 1);
    Bytes stsz(12); put32(&stsz[4], MotionSampleSize); put32(&stsz[8], u32(total));
    Bytes co64(8 + 8 * chunks); put32(&co64[4], uint32_t(chunks));
    for (std::size_t i = 0; i < chunks; ++i) put64(&co64[8 + 8 * i], offsets[i]);
    return rebuildStbl(trak, {{"stts", atom("stts", stts)}, {"stsc", atom("stsc", stsc)},
                              {"stsz", atom("stsz", stsz)}, {"co64", atom("co64", co64)}});
}

Bytes rebuildMoov(const Bytes &moov, std::size_t n, const std::vector<uint64_t> &videoOffsets,
                  const std::vector<uint32_t> &videoSizes, uint64_t tmcdOffset, const Audio *audio, Rate rate,
                  bool dropFrame, const Motion *motion = nullptr) {
    const int num = rate.num, den = rate.den;
    if (num <= 0 || den <= 0)
        throw Error("cadencia inválida (" + std::to_string(num) + ", " + std::to_string(den) + ")");
    std::vector<std::pair<std::string, Bytes>> traks, others;
    for (const Box &a : muxAtoms(moov.data(), 8, moov.size())) {
        Bytes body(moov.begin() + std::ptrdiff_t(a.off), moov.begin() + std::ptrdiff_t(a.end()));
        if (a.is("trak")) { std::string kind = trakKind(body); traks.emplace_back(kind, std::move(body)); }
        else others.emplace_back(std::string(a.type, 4), std::move(body));
    }
    const long long frames = (long long)n, videoDur = frames * den;
    long long movieDur = videoDur;
    if (audio) movieDur = std::max(videoDur, (audio->total * num + 47999) / 48000);
    Bytes out;
    for (auto &[type, body] : others) {
        if (type == "mvhd") {
            Bytes b = body;
            putAt(b, 8 + 12, uint32_t(num)); putAt(b, 8 + 16, u32(movieDur));
            append(out, b);
        } else {
            append(out, body);
        }
    }
    for (auto &[kind, trakIn] : traks) {
        Bytes trak = trakIn;
        if (kind == "vide") {
            trak = patchDurations(trak, u32(videoDur), u32(videoDur), uint32_t(num));
            Bytes stts(16); put32(&stts[4], 1); put32(&stts[8], u32(frames)); put32(&stts[12], uint32_t(den));
            Bytes stsz(12 + 4 * n); put32(&stsz[8], u32(frames));
            Bytes co64(8 + 8 * n); put32(&co64[4], u32(frames));
            for (std::size_t i = 0; i < n; ++i) { put32(&stsz[12 + 4 * i], videoSizes[i]); put64(&co64[8 + 8 * i], videoOffsets[i]); }
            trak = rebuildStbl(trak, {{"stts", atom("stts", stts)}, {"stsz", atom("stsz", stsz)}, {"co64", atom("co64", co64)}});
        } else if (kind == "tmcd") {
            trak = patchDurations(trak, u32(videoDur), u32(videoDur), uint32_t(num));
            Bytes stts(16); put32(&stts[4], 1); put32(&stts[8], 1); put32(&stts[12], u32(videoDur));
            Bytes co64(16); put32(&co64[4], 1); put64(&co64[8], tmcdOffset);
            trak = rebuildStbl(trak, {{"stsd", tmcdStsd(trak, num, den, dropFrame)}, {"stts", atom("stts", stts)},
                                      {"co64", atom("co64", co64)}});
        } else if (kind == "soun" && audio) {
            const long long total = audio->total, per = audio->perChunk;
            const std::size_t chunks = audio->offsets.size();
            trak = patchDurations(trak, u32(movieDur), u32(total), 0);
            std::vector<std::array<uint32_t, 3>> entries{{1, uint32_t(per), 1}};
            if (total % per && chunks > 1) entries.push_back({uint32_t(chunks), u32(total - per * ((long long)chunks - 1)), 1});
            else if (total % per) entries = {{1, u32(total), 1}};
            Bytes stsc(8); put32(&stsc[4], uint32_t(entries.size()));
            for (const auto &e : entries) for (uint32_t v : e) append(stsc, be32Bytes(v));
            Bytes stts(16); put32(&stts[4], 1); put32(&stts[8], u32(total)); put32(&stts[12], 1);
            Bytes stsz(12); put32(&stsz[4], 1); put32(&stsz[8], u32(total));
            Bytes co64(8 + 8 * chunks); put32(&co64[4], uint32_t(chunks));
            for (std::size_t i = 0; i < chunks; ++i) put64(&co64[8 + 8 * i], audio->offsets[i]);
            trak = rebuildStbl(trak, {{"stts", atom("stts", stts)}, {"stsc", atom("stsc", stsc)},
                                      {"stsz", atom("stsz", stsz)}, {"co64", atom("co64", co64)}});
        } else if ((kind == "mogy" || kind == "moac") && motion && motion->total) {
            trak = motionTrak(trak, *motion, kind == "mogy" ? motion->gyroOffsets : motion->accelOffsets, u32(movieDur));
        } else {
            continue;
        }
        append(out, trak);
    }
    Bytes result = atom("moov", out);
    patchFlag(result, "bfdn", uint32_t(den), false);
    return result;
}

// ---------------------------------------------------------------------------------------------------------------
// Metadatos por frame.

struct Entry { std::string type; std::size_t offset; Bytes value; };

// Como el dict de Python: una clave repetida conserva su posición y toma el último valor.
std::vector<Entry> bmdfEntries(const Bytes &data, std::size_t f0) {
    if (f0 + 4 > data.size()) throw Error("truncated frame metadata");
    const std::size_t hsz = be32(&data[f0]);
    if (f0 + hsz > data.size()) throw Error("truncated frame metadata");
    std::vector<Entry> entries;
    std::size_t off = 8;
    while (off + 8 <= hsz) {
        const std::size_t size = be32(&data[f0 + off]);
        std::string type(reinterpret_cast<const char *>(&data[f0 + off + 4]), 4);
        if (size < 8 || off + size > hsz) break;
        Entry e{type, f0 + off + 8, Bytes(data.begin() + std::ptrdiff_t(f0 + off + 8), data.begin() + std::ptrdiff_t(f0 + off + size))};
        auto it = std::find_if(entries.begin(), entries.end(), [&](const Entry &x) { return x.type == type; });
        if (it != entries.end()) *it = std::move(e); else entries.push_back(std::move(e));
        off += size;
    }
    return entries;
}

const Entry &entry(const std::vector<Entry> &entries, const char *type) {
    for (const Entry &e : entries) if (e.type == type) return e;
    throw Error(std::string("missing frame parameter ") + type);
}

Bytes minimalFrame(const Bytes &data) {
    static const std::set<std::string> keep{"srte", "isoe", "wkel", "wtin", "expo"};
    if (data.size() < 4) throw Error("truncated frame metadata");
    const std::size_t size = be32(data.data());
    const auto entries = bmdfEntries(data, 0);
    std::size_t found = 0;
    for (const Entry &e : entries) found += keep.count(e.type);
    if (found != keep.size()) throw Error("missing frame parameters");
    Bytes body;
    for (const Entry &e : entries) if (keep.count(e.type)) append(body, atom(e.type.c_str(), e.value));
    append(body, atom("agpf", be32Bytes(0)));
    if (size < 8 + body.size() + 8) throw Error("insufficient frame metadata padding");
    const std::size_t padding = size - 8 - body.size();
    append(body, atom("skip", Bytes(padding - 8)));
    return atom("bmdf", body);
}

Bytes frameMetadata(const Bytes &base, const FrameMeta &meta) {
    Bytes body = minimalFrame(base);
    const auto entries = bmdfEntries(body, 0);
    if (meta.tint < -32768 || meta.tint > 32767) throw Error("'h' format requires -32768 <= number <= 32767");
    putAt(body, entry(entries, "isoe").offset, u32(meta.iso));
    putAt(body, entry(entries, "wkel").offset, u32(meta.kelvin));
    const std::size_t tint = entry(entries, "wtin").offset;
    if (tint + 2 > body.size()) throw Error("truncated frame metadata");
    put16(&body[tint], uint16_t(int16_t(meta.tint)));
    if (meta.route == Route::Va) putAt(body, entry(entries, "agpf").offset, floatBits(vaGainFor(meta.camera)));
    // Python solo escribe srte si info['rate'] existe; el proceso de trabajo siempre la da.
    const std::size_t srte = entry(entries, "srte").offset;
    putAt(body, srte, u32(meta.rate.num)); putAt(body, srte + 4, u32(meta.rate.den));
    return body;
}

// ---------------------------------------------------------------------------------------------------------------
// Identidad del clip.

struct Metadata { std::vector<Box> parts; std::vector<std::string> names; };

Metadata metadataEntries(const Bytes &meta) {
    Metadata m;
    m.parts = profileAtoms(meta.data(), 0, meta.size());
    auto keys = std::find_if(m.parts.begin(), m.parts.end(), [](const Box &b) { return b.is("keys"); });
    if (keys == m.parts.end()) throw Error("missing metadata keys");
    const uint8_t *k = &meta[keys->body()];
    const std::size_t length = keys->end() - keys->body();
    if (length < 8) throw Error("invalid metadata key");
    const uint32_t count = be32(k + 4);
    std::size_t offset = 8;
    for (uint32_t i = 0; i < count; ++i) {
        if (offset + 4 > length) throw Error("invalid metadata key");
        const uint32_t size = be32(k + offset);
        if (size < 8 || offset + size > length) throw Error("invalid metadata key");
        m.names.emplace_back(reinterpret_cast<const char *>(k + offset + 8), size - 8);
        offset += size;
    }
    return m;
}

const std::string &keyName(const Metadata &m, const uint8_t *key) {
    const uint32_t index = be32(key);
    if (index < 1 || index > m.names.size()) throw Error("invalid metadata key");
    return m.names[index - 1];
}

// Sustituye el valor (tras los 8 bytes de tipo y locale) de cada hijo 'data' de un elemento del ilst.
Bytes replaceData(const Bytes &item, std::size_t from, std::size_t to, const Bytes &value, bool *replaced) {
    Bytes out;
    for (const Box &c : profileAtoms(item.data(), from, to)) {
        Bytes data(item.begin() + std::ptrdiff_t(c.body()), item.begin() + std::ptrdiff_t(c.end()));
        if (c.is("data")) {
            data.resize(std::min<std::size_t>(data.size(), 8));
            append(data, value);
            if (replaced) *replaced = true;
        }
        append(out, atom(c.type, data));
    }
    return out;
}

using Changes = std::vector<std::pair<std::string, Bytes>>;

Bytes patchMetadata(const Bytes &meta, const Changes &changes) {
    const Metadata m = metadataEntries(meta);
    std::set<std::string> found;
    Bytes result;
    for (const Box &part : m.parts) {
        Bytes body(meta.begin() + std::ptrdiff_t(part.body()), meta.begin() + std::ptrdiff_t(part.end()));
        if (part.is("ilst")) {
            Bytes entries;
            for (const Box &item : profileAtoms(meta.data(), part.body(), part.end())) {
                const std::string &name = keyName(m, reinterpret_cast<const uint8_t *>(item.type));
                Bytes value(meta.begin() + std::ptrdiff_t(item.body()), meta.begin() + std::ptrdiff_t(item.end()));
                auto change = std::find_if(changes.begin(), changes.end(), [&](const auto &c) { return c.first == name; });
                if (change != changes.end()) {
                    bool replaced = false;
                    value = replaceData(value, 0, value.size(), change->second, &replaced);
                    if (replaced) found.insert(name);
                }
                append(entries, atom(item.type, value));
            }
            body = std::move(entries);
        }
        append(result, atom(part.type, body));
    }
    std::string missing;
    for (const auto &c : changes)
        if (!found.count(c.first)) missing += (missing.empty() ? "'" : ", '") + c.first + "'";
    if (!missing.empty()) throw Error("missing metadata: {" + missing + "}");
    return result;
}

// with_profile sobre el átomo moov completo (en Python data[:offset] queda vacío: el moov empieza en 0).
Bytes withProfile(const Bytes &data, const std::string &cameraType, int gen, const Changes &extra) {
    const auto top = profileAtoms(data.data(), 0, data.size());
    if (top.empty() || !top.back().is("moov")) throw Error("profile patch requires moov after media");
    const Box &moov = top.back();
    Changes changes{{"camera_type", Bytes(cameraType.begin(), cameraType.end())},
                    {"viewing_bmdgen", be16Bytes(uint16_t(gen))}};
    for (const auto &e : extra) {
        auto it = std::find_if(changes.begin(), changes.end(), [&](const auto &c) { return c.first == e.first; });
        if (it != changes.end()) it->second = e.second; else changes.push_back(e);
    }
    Bytes parts;
    bool found = false;
    for (const Box &a : profileAtoms(data.data(), moov.body(), moov.end())) {
        Bytes body(data.begin() + std::ptrdiff_t(a.body()), data.begin() + std::ptrdiff_t(a.end()));
        if (a.is("meta")) { body = patchMetadata(body, changes); found = true; }
        append(parts, atom(a.type, body));
    }
    if (!found) throw Error("missing moov metadata");
    Bytes out(data.begin(), data.begin() + std::ptrdiff_t(moov.off));
    append(out, atom("moov", parts));
    return out;
}

Bytes minimalMetadata(const Bytes &meta, const std::string &cameraType) {
    static const std::set<std::string> keep{"camera_type", "manufacturer", "sensor_area_captured", "crop_origin",
                                            "crop_size", "viewing_gamma", "viewing_gamut", "viewing_bmdgen",
                                            "analog_gain"};
    // La identidad de la toma (finalizeClip) se conserva si está, pero no se exige.
    static const std::set<std::string> optional{"sensor_line_time", "lens_type", "camera_id", "clip_number", "date_recorded"};
    const Metadata m = metadataEntries(meta);
    auto ilst = std::find_if(m.parts.begin(), m.parts.end(), [](const Box &b) { return b.is("ilst"); });
    if (ilst == m.parts.end()) throw Error("missing metadata items");
    std::vector<std::string> names;
    Bytes items;
    for (const Box &item : profileAtoms(meta.data(), ilst->body(), ilst->end())) {
        const std::string &name = keyName(m, reinterpret_cast<const uint8_t *>(item.type));
        if (!keep.count(name) && !optional.count(name)) continue;
        Bytes body(meta.begin() + std::ptrdiff_t(item.body()), meta.begin() + std::ptrdiff_t(item.end()));
        if (name == "camera_type") body = replaceData(body, 0, body.size(), Bytes(cameraType.begin(), cameraType.end()), nullptr);
        if (name == "analog_gain") body = replaceData(body, 0, body.size(), be32Bytes(0), nullptr);
        names.push_back(name);
        char index[4];
        put32(reinterpret_cast<uint8_t *>(index), uint32_t(names.size()));
        append(items, atom(index, body));
    }
    std::set<std::string> have;
    for (const auto &n : names) if (keep.count(n)) have.insert(n);
    if (have != keep) {
        std::string missing;
        for (const auto &k : keep) if (!have.count(k)) missing += (missing.empty() ? "'" : ", '") + k + "'";
        throw Error("missing required fields: {" + missing + "}");
    }
    Bytes keys(8); put32(&keys[4], uint32_t(names.size()));
    for (const auto &name : names)
        append(keys, atom("mdta", reinterpret_cast<const uint8_t *>(name.data()), name.size()));
    Bytes out;
    for (const Box &part : m.parts) {
        if (part.is("keys")) append(out, atom("keys", keys));
        else if (part.is("ilst")) append(out, atom("ilst", items));
        else append(out, atom(part.type, &meta[part.body()], part.end() - part.body()));
    }
    return out;
}

// Solo se lee y reescribe el moov final.
void rewriteMoovFile(const std::filesystem::path &path, const std::function<Bytes(const Bytes &)> &modifier) {
    uint64_t offset = 0, newLength = 0;
    {
        File file(path, "r+b");
        if (!file) throw Error("Could not open " + path.string());
        const TopAtom moov = findTopAtom(file, "moov");
        if (!moov.last) throw Error("profile patch requires moov after media");
        offset = moov.offset;
        const Bytes updated = modifier(file.read(moov.offset, std::size_t(moov.size)));
        file.seek(offset);
        file.write(updated.data(), updated.size());
        newLength = offset + updated.size();
        file.close();
    }
    std::filesystem::resize_file(path, newLength);
}

}  // namespace

// ---------------------------------------------------------------------------------------------------------------

std::vector<VideoSample> videoSamples(const std::filesystem::path &path) { return samplesFromMoov(readMoov(path)); }

TemplateFrame readTemplate(const std::filesystem::path &templateBraw) {
    const auto samples = videoSamples(templateBraw);
    if (samples.empty()) throw Error("The BRAW template has no frames.");
    File file(templateBraw, "rb");
    if (!file) throw Error("Could not read " + templateBraw.string());
    const VideoSample s = samples[0];
    if (s.size < BmdfSize) throw Error("Invalid BRAW frame.");
    const Bytes sample = file.read(s.offset, s.size);
    const FileChunk chunk = splitFileChunk(sample.data() + BmdfSize, sample.size() - BmdfSize);
    TemplateFrame out;
    out.header = chunk.header;
    out.gap = chunk.gap;
    out.bmdf.assign(sample.begin(), sample.begin() + BmdfSize);
    return out;
}

struct StreamMux::Impl {
    File file;
    uint64_t position = 0;
    Bytes moov, base;
    std::vector<uint64_t> offsets;
    std::vector<uint32_t> sizes;
    MotionSamples motion;
    bool finished = false;

    void write(const uint8_t *data, std::size_t size) { file.write(data, size); position += size; }
    void write(const Bytes &data) { write(data.data(), data.size()); }
    void zeros(std::size_t size) { static const uint8_t z[8192] = {}; while (size) { const auto k = std::min(size, sizeof z); write(z, k); size -= k; } }
};

StreamMux::StreamMux(const std::filesystem::path &templateBraw, const std::filesystem::path &destination)
    : impl_(std::make_unique<Impl>()) {
    impl_->moov = readMoov(templateBraw);
    const auto samples = samplesFromMoov(impl_->moov);
    if (samples.empty()) throw Error("The BRAW template has no frames.");
    {
        File source(templateBraw, "rb");
        if (!source) throw Error("Could not read " + templateBraw.string());
        impl_->base = source.read(samples[0].offset, BmdfSize);
    }
    // open('xb'): nunca sobrescribe.
    if (std::filesystem::exists(std::filesystem::symlink_status(destination)))
        throw Error("The destination exists. Choose a new name; files are never overwritten.");
    impl_->file.open(destination, "wbx");
    if (!impl_->file) {
        if (std::filesystem::exists(std::filesystem::symlink_status(destination)))
            throw Error("The destination exists. Choose a new name; files are never overwritten.");
        throw Error("Could not create " + destination.string());
    }
    uint8_t head[24] = {};
    put32(head, 8); std::memcpy(head + 4, "wide", 4);       // atom(b'wide', b'')
    put32(head + 8, 1); std::memcpy(head + 12, "mdat", 4);  // mdat de tamaño ampliado; se fija en finish
    impl_->write(head, sizeof head);
    impl_->zeros(8192);
}

StreamMux::~StreamMux() = default;

void StreamMux::addFrame(const std::vector<uint8_t> &chunk, const FrameMeta &meta) {
    Impl &m = *impl_;
    if (m.finished) throw Error("The clip is already finished.");
    if (chunk.size() < 8) throw Error("Invalid BRAW frame.");
    std::size_t size = BmdfSize + chunk.size();
    const std::size_t pad = (8 - size % 8) % 8;
    size += pad;
    const Bytes body = frameMetadata(m.base, meta);
    if (body.size() != BmdfSize) throw Error("Unexpected frame metadata size.");
    m.offsets.push_back(m.position);
    m.sizes.push_back(u32((long long)size));
    m.write(body);
    uint8_t field[4];
    put32(field, u32((long long)size - BmdfSize));
    m.write(chunk.data(), 4);
    m.write(field, 4);
    m.write(chunk.data() + 8, chunk.size() - 8);
    m.zeros(pad);
}

void StreamMux::finish(long long timecode, const Pcm24 *audio, Rate rate, bool dropFrame) {
    Impl &m = *impl_;
    if (m.finished) throw Error("The clip is already finished.");
    m.finished = true;
    const uint64_t tcOffset = m.position;
    uint8_t tc[8] = {};
    put32(tc, u32(timecode));
    m.write(tc, sizeof tc);
    Audio pcm{{}, 48000, 0};   // AUDIO_CHUNK: trozos de 1 s a 48 kHz
    const bool withAudio = audio && !audio->data.empty();
    if (withAudio) {
        pcm.total = (long long)(audio->data.size() / 6);
        for (long long start = 0; start < pcm.total; start += pcm.perChunk) {
            pcm.offsets.push_back(m.position);
            const long long stop = std::min(start + (long long)pcm.perChunk, pcm.total);
            m.write(audio->data.data() + 6 * start, std::size_t(6 * (stop - start)));
        }
        m.zeros((8 - m.position % 8) % 8);
    }
    Motion motion;
    if (m.motion.rate && !m.motion.gyro.empty()) {
        motion.rate = m.motion.rate;
        motion.total = (long long)m.motion.gyro.size();
        // Un trozo de giroscopio y otro de acelerómetro, alternos; cada lectura es una muestra mebx de la clave
        // mogy/moac con los tres valores en float32 little-endian (lo que lee telemetry-parser y el SDK).
        uint8_t sample[MotionSampleSize];
        put32(sample, MotionSampleSize);
        for (std::size_t start = 0; start < m.motion.gyro.size(); start += MotionChunk) {
            const std::size_t stop = std::min<std::size_t>(start + MotionChunk, m.motion.gyro.size());
            for (int track = 0; track < 2; ++track) {
                (track ? motion.accelOffsets : motion.gyroOffsets).push_back(m.position);
                std::memcpy(sample + 4, track ? "moac" : "mogy", 4);
                const auto &values = track ? m.motion.accel : m.motion.gyro;
                for (std::size_t j = start; j < stop; ++j) {
                    for (int c = 0; c < 3; ++c) {
                        const uint32_t v = floatBits(values[j][c]);
                        for (int b = 0; b < 4; ++b) sample[8 + 4 * c + b] = uint8_t(v >> (8 * b));
                    }
                    m.write(sample, sizeof sample);
                }
            }
        }
        m.zeros((8 - m.position % 8) % 8);
    }
    const uint64_t end = m.position;
    uint8_t size[8];
    put64(size, end - 8);
    m.file.seek(16);
    m.file.write(size, 8);
    m.file.seek(end);
    const Bytes moov = rebuildMoov(m.moov, m.sizes.size(), m.offsets, m.sizes, tcOffset, withAudio ? &pcm : nullptr,
                                   rate, dropFrame, motion.total ? &motion : nullptr);
    m.write(moov);
    m.file.close();
}

void StreamMux::setMotion(MotionSamples motion) {
    if (impl_->finished) throw Error("The clip is already finished.");
    if (motion.gyro.size() != motion.accel.size()) throw Error("Invalid motion data.");
    impl_->motion = std::move(motion);
}

std::size_t StreamMux::frames() const { return impl_->sizes.size(); }

const std::vector<std::string> &vaCameraTypes() {
    // La fp graba con «SIGMA fp»; el Video Assist escribe «SIGMA fp L» incluso con una fp. Mismo color en el SDK (Gen 4);
    // cambia la ISO de 0 dB (800 / 640), que solo toca la curva Film.
    static const std::vector<std::string> names{"SIGMA fp", "SIGMA fp L"};
    return names;
}

float vaGainFor(const std::string &camera) { return camera == "SIGMA fp L" ? vaGain() : 1.0f; }

const char *const ogViewingGamut = "DCI-P3 D65";
const char *const ogViewingGamma = "Linear";

void finalizeClip(const std::filesystem::path &path, const Geometry &geometry, Route route, const OgModel *og,
                  const std::string &camera, const ClipExtras &extras) {
    if (route == Route::Og && !og) throw Error("The Pocket Cinema Camera calibration is missing.");
    const auto &allowed = vaCameraTypes();
    if (route == Route::Va && std::find(allowed.begin(), allowed.end(), camera) == allowed.end())
        throw Error("Unsupported camera identity: " + camera);
    Changes clip;
    if (extras.lineTimeUs > 0) clip.push_back({"sensor_line_time", be32Bytes(floatBits(extras.lineTimeUs))});
    if (!extras.lens.empty()) clip.push_back({"lens_type", Bytes(extras.lens.begin(), extras.lens.end())});
    for (const auto &[key, value] : {std::pair<const char *, const std::string &>{"camera_id", extras.cameraId},
                                     {"clip_number", extras.clipNumber}, {"date_recorded", extras.date}})
        if (!value.empty()) clip.push_back({key, Bytes(value.begin(), value.end())});
    rewriteMoovFile(path, [&](const Bytes &input) {
        Bytes moov = input;
        geometry.patchMoov(moov, 4);
        if (route == Route::Va) {
            Changes changes{{"analog_gain", be32Bytes(floatBits(vaGainFor(camera)))},
                            {"analog_gain_is_constant", be16Bytes(1)},
                            {"manufacturer", Bytes{'S', 'I', 'G', 'M', 'A'}}};
            changes.insert(changes.end(), clip.begin(), clip.end());
            return withProfile(moov, camera, 4, changes);
        }
        if (route == Route::Pocket) return withProfile(moov, "Blackmagic Pocket Cinema Camera 4K", 4, clip);
        // Ruta og.
        const std::string ogCamera = og->cameraType.empty() ? "SIGMA fp" : og->cameraType;
        const std::string maker = ogCamera.substr(0, ogCamera.find(' '));
        // Resolve sin gestión de color abre el clip con el espacio y la gamma de visualización: P3-D65 lineal es donde el
        // BRAW coincide con el DNG (con «Blackmagic Design Film» no puede: el DNG usa la Film Gen 1, que el SDK no tiene).
        Changes changes{{"manufacturer", Bytes(maker.begin(), maker.end())},
                        {"viewing_gamut", Bytes(ogViewingGamut, ogViewingGamut + std::strlen(ogViewingGamut))},
                        {"viewing_gamma", Bytes(ogViewingGamma, ogViewingGamma + std::strlen(ogViewingGamma))}};
        changes.insert(changes.end(), clip.begin(), clip.end());
        moov = withProfile(moov, ogCamera, og->gen, changes);
        const auto top = profileAtoms(moov.data(), 0, moov.size());
        auto box = std::find_if(top.begin(), top.end(), [](const Box &b) { return b.is("moov"); });
        if (box == top.end()) throw Error("missing moov");
        Bytes body;
        for (const Box &a : profileAtoms(moov.data(), box->body(), box->end())) {
            Bytes value(moov.begin() + std::ptrdiff_t(a.body()), moov.begin() + std::ptrdiff_t(a.end()));
            append(body, atom(a.type, a.is("meta") ? minimalMetadata(value, ogCamera) : value));
        }
        Bytes out = atom("moov", body);
        patchFlag(out, "ctrn", og->revision, true);
        return out;
    });
}

}  // namespace cdng
