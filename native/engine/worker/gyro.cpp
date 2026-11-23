// Giroscopio Gyro2 (FSG2) junto al BRAW. Ver gyro.hpp.
#include "gyro.hpp"
#include "json.hpp"
#include "worker.hpp"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <map>

namespace cdng::worker {

namespace {

// Etiquetas que el plugin lee de cada DNG (cdng.rs, read_dng_info): marca, modelo, firmware, número de serie,
// DefaultCropSize, FrameRate y OpcodeList3 (WarpRectilinear: la distorsión del objetivo para el enfoque del frame);
// del Exif, objetivo, exposición, ISO, diafragma, focal y distancia de enfoque.
constexpr uint16_t RootTags[] = {0x010F, 0x0110, 0x0131, 0xC62F, 0xC620, 0xC764, 0xC74E};
constexpr uint16_t ExifTags[] = {0xA434, 0x829A, 0x8827, 0x829D, 0x920A, 0x9206};

constexpr uint16_t FlagDataLost = 1;
constexpr uint8_t EventLevel = 1, EventVd = 3;
constexpr double VdToReadoutUs = 900.0;   // fsg2.rs VD_TO_READOUT_US: de Vd a la primera fila leída
constexpr double SensorClockHz = 72.0e6;  // IMX410: hmax cuenta este reloj
constexpr double SensorColumns = 6000, SensorWidthMm = 35.9;   // zona activa del IMX410
constexpr uint16_t FlagLensTable = 1 << 6;
constexpr double StandardGravity = 9.80665;

uint16_t le16(const uint8_t *p) { return uint16_t(p[0] | p[1] << 8); }
int16_t les16(const uint8_t *p) { return int16_t(le16(p)); }
uint32_t le32(const uint8_t *p) { return uint32_t(p[0]) | uint32_t(p[1]) << 8 | uint32_t(p[2]) << 16 | uint32_t(p[3]) << 24; }
float lef32(const uint8_t *p) { const uint32_t v = le32(p); float f; std::memcpy(&f, &v, 4); return f; }

struct Event { uint16_t pos; uint8_t kind; std::vector<uint8_t> value; };
struct Record {
    int frame = 0;   // posición en la toma convertida
    uint16_t flags = 0;
    uint32_t clipId = 0, frameSeq = 0, periodPs = 0, readoutNs = 0, baseIdx = 0;
    uint16_t columns = 0;            // columnas del sensor que cubre el modo (0 = no se sabe: todas)
    uint32_t calibFocalTenths = 0;   // focal a la que se midió la corrección del objetivo, décimas de mm (0 = no hay)
    float gscale = 0, ascale = 0;
    uint16_t frameMark = 0;
    std::vector<std::array<int16_t, 3>> samples;
    std::vector<Event> events;
};

// Un bloque FSG2 de tipo 2 (fsg2.rs, parse). Sin las comprobaciones de longitud que ya hace el plugin, salvo las
// necesarias para no leer fuera.
std::optional<Record> parse(const std::vector<uint8_t> &block, int frame) {
    if (block.size() < 12 + 48 || std::memcmp(block.data(), "FSG2", 4) != 0 || block[5] != 2) return std::nullopt;
    const std::size_t n = le32(&block[8]);
    if (12 + n > block.size()) return std::nullopt;
    const uint8_t *p = &block[12];
    const uint8_t version = p[0];
    if ((version != 1 && version != 2) || p[1] != 0) return std::nullopt;   // orientación 0 = «xyz»
    Record r;
    r.frame = frame;
    r.flags = le16(p + 2); r.clipId = le32(p + 4); r.frameSeq = le32(p + 8); r.periodPs = le32(p + 12);
    r.gscale = lef32(p + 16); r.ascale = lef32(p + 20);
    // Versión 1: tiempo de lectura y ventana del sensor (x, y, ancho, alto). Versión 2: el modo del sensor (hmax,
    // columnas leídas, líneas, agrupamientos) y la focal de la calibración del objetivo.
    // llround y long long, no lround y long: en MSVC `long` son 32 bits y estos productos (datos del fichero, sin
    // validar) se salen del rango.
    if (version == 1) { r.readoutNs = le32(p + 24); r.columns = le16(p + 34); }
    else {
        if (le16(p + 24)) {
            r.readoutNs = uint32_t(std::llround(le16(p + 26) * double(le16(p + 30)) * 1e9 / SensorClockHz));
            r.columns = uint16_t(std::min<long long>(static_cast<long long>(SensorColumns),
                                                     static_cast<long long>(le16(p + 28)) * std::max<long long>(1, le16(p + 32))));
        }
        r.calibFocalTenths = le16(p + 36);
    }
    r.frameMark = le16(p + 38); r.baseIdx = le32(p + 40);
    const std::size_t samples = le16(p + 44), events = le16(p + 46);
    std::size_t at = 48;
    if (at + 6 * samples > n || r.periodPs == 0 || !(r.gscale > 0) || !(r.ascale > 0)) return std::nullopt;
    for (std::size_t k = 0; k < samples; ++k, at += 6) r.samples.push_back({les16(p + at), les16(p + at + 2), les16(p + at + 4)});
    for (std::size_t k = 0; k < events; ++k) {
        if (at + 4 > n) return std::nullopt;
        const std::size_t len = p[at + 3];
        if (at + 4 + len > n) return std::nullopt;
        r.events.push_back({le16(p + at), p[at + 2], {p + at + 4, p + at + 4 + len}});
        at += 4 + len;
    }
    // Tabla del objetivo (solo versión 1): empieza por la focal de la calibración.
    if (version == 1 && (r.flags & FlagLensTable) && at + 4 <= n) r.calibFocalTenths = le32(p + at);
    return r;
}

double median(std::vector<double> v) { std::sort(v.begin(), v.end()); return v[v.size() / 2]; }

// Posición (en muestras) del Vd del frame 0 de la toma convertida: el ajuste de vd_frame_positions (fsg2.rs). Los
// Vd de toda la toma caen en una recta; cada frame es su frame_seq más un desfase, la mediana de lo que sugieren los
// ganchos de nueve frames. Sin Vd (Gyro2 antiguo), el gancho del primer frame. `perFrame`: muestras por frame, la
// pendiente de esa recta (el reloj de Gyro2 frente al de los frames) o la nominal.
double frameZero(const std::vector<Record> &records, double fps, std::string &note, double &perFrame) {
    const double period = records.front().periodPs * 1e-12, nominal = 1.0 / (fps * period);
    perFrame = nominal;
    std::vector<double> vds;
    for (const auto &r : records)
        for (const auto &e : r.events) if (e.kind == EventVd) vds.push_back(double(r.baseIdx) + e.pos);
    std::sort(vds.begin(), vds.end());
    vds.erase(std::unique(vds.begin(), vds.end()), vds.end());
    const auto &first = records.front();
    const auto hookAt = [](const Record &r) { return double(r.baseIdx) + r.frameMark; };
    if (vds.size() < 2) {
        note = "sin sincronía Vd (Gyro2 sin el gancho Vd): el frame 0 se toma del gancho del primer frame; ajusta el desfase en Gyroflow";
        return hookAt(first) - first.frame * nominal;
    }
    std::vector<double> index;
    for (double v : vds) index.push_back(std::nearbyint((v - vds[0]) / nominal));
    double mi = 0, mv = 0;
    for (std::size_t k = 0; k < vds.size(); ++k) { mi += index[k]; mv += vds[k]; }
    mi /= double(vds.size()); mv /= double(vds.size());
    double sii = 0, siv = 0;
    for (std::size_t k = 0; k < vds.size(); ++k) { sii += (index[k] - mi) * (index[k] - mi); siv += (index[k] - mi) * (vds[k] - mv); }
    const double slope = sii > 0 ? siv / sii : 0;
    if (!(sii > 0) || std::abs(slope / nominal - 1) > 0.01) {
        note = "la sincronía Vd no encaja con la cadencia: el frame 0 se toma del gancho del primer frame";
        return hookAt(first) - first.frame * nominal;
    }
    perFrame = slope;
    const auto line = [&](double i) { return mv + slope * (i - mi); };
    std::vector<double> lags;
    for (const auto &r : records) {
        const double i = std::floor((hookAt(r) - line(0)) / slope);
        if (i >= 0) lags.push_back(hookAt(r) - line(i));
    }
    if (lags.empty()) { note = "ningún gancho tras un Vd"; return hookAt(first) - first.frame * nominal; }
    const double lag = median(lags);
    // Desfase entre el número de Vd y frame_seq, en los primeros nueve frames con datos.
    std::vector<double> offsets;
    for (std::size_t k = 0; k < records.size() && k < 9; ++k)
        offsets.push_back(std::nearbyint((hookAt(records[k]) - lag - line(0)) / slope) - double(records[k].frameSeq));
    const double seqZero = double(first.frameSeq) - first.frame;   // frame_seq del frame 0 de la toma
    return line(seqZero + median(offsets));
}

// Registros de la toma y su reloj.
struct Take {
    std::vector<Record> records;
    double periodUs = 0;
    double startUs = 0;   // lectura de la primera fila del frame 0, en el reloj de Gyro2 (µs)
    double frameUs = 0;   // un frame en ese reloj
    std::string note;
};

Take readTake(const std::vector<GyroFrame> &frames, Rate rate) {
    Take take;
    for (std::size_t i = 0; i < frames.size(); ++i)
        if (auto r = parse(frames[i].block, int(i))) take.records.push_back(std::move(*r));
    if (take.records.empty()) throw Error(t("The gyro blocks of this take cannot be read."));
    // Toda la toma con el mismo registro (fsg2.rs comprueba lo mismo); un frame de otra toma no se mezcla.
    const uint32_t clip = take.records.front().clipId, period = take.records.front().periodPs;
    const float gscale = take.records.front().gscale, ascale = take.records.front().ascale;
    take.records.erase(std::remove_if(take.records.begin(), take.records.end(), [&](const Record &r) {
        return r.clipId != clip || r.periodPs != period || r.gscale != gscale || r.ascale != ascale;
    }), take.records.end());
    const double fps = double(rate.num) / rate.den;
    take.periodUs = take.records.front().periodPs / 1.0e6;
    double perFrame = 0;
    take.startUs = frameZero(take.records, fps, take.note, perFrame) * take.periodUs + VdToReadoutUs;
    take.frameUs = perFrame * take.periodUs;
    return take;
}

// Ejes del acelerómetro, un cuarto de vuelta respecto al giroscopio (fsg2.rs, accel_in_gyro_axes).
std::array<int, 3> accelAxes(const Event &e) {
    return {les16(&e.value[2]), -int(les16(&e.value[0])), les16(&e.value[4])};
}

// Cada muestra de la toma una vez (los bloques vecinos comparten algunas) y en orden: f(índice global, muestra en
// cuentas, acelerómetro en cuentas y en los ejes del giroscopio, que se mantiene entre lecturas).
template <class F> void eachSample(const std::vector<Record> &records, F &&f) {
    std::array<int, 3> held{0, 0, 0};   // la primera lectura de la toma, hasta que llega la siguiente
    for (const auto &r : records) {
        const auto e = std::find_if(r.events.begin(), r.events.end(), [](const Event &e) { return e.kind == EventLevel && e.value.size() == 6; });
        if (e != r.events.end()) { held = accelAxes(*e); break; }
    }
    unsigned long long next = 0;
    for (const auto &r : records) {
        std::vector<const Event *> levels;
        for (const auto &e : r.events) if (e.kind == EventLevel && e.value.size() == 6) levels.push_back(&e);
        std::stable_sort(levels.begin(), levels.end(), [](const Event *a, const Event *b) { return a->pos < b->pos; });
        std::size_t level = 0;
        if (!(r.flags & FlagDataLost)) {
            for (std::size_t k = 0; k < r.samples.size(); ++k) {
                while (level < levels.size() && levels[level]->pos <= k) held = accelAxes(*levels[level++]);
                const unsigned long long global = r.baseIdx + k;
                if (global < next) continue;
                next = global + 1;
                f(global, r.samples[k], held);
            }
        }
        while (level < levels.size()) held = accelAxes(*levels[level++]);
    }
}

// Rolling shutter de la imagen convertida: solo se ven las filas del recorte.
double visibleReadoutMs(const Record &record, const GyroGeometry &g) {
    return record.readoutNs / 1.0e6 * (g.sourceHeight > 0 ? double(g.cropHeight) / g.sourceHeight : 1.0);
}

const RawTag *findTag(const std::vector<RawTag> &tags, uint16_t tag) {
    for (const auto &t : tags) if (t.tag == tag) return &t;
    return nullptr;
}

// Primer valor de una etiqueta RATIONAL o SRATIONAL, en el orden de bytes del DNG.
std::optional<double> rational(const RawTag *tag, bool littleEndian) {
    if (!tag || (tag->type != 5 && tag->type != 10) || tag->bytes.size() < 8) return std::nullopt;
    const auto word = [&](std::size_t at) {
        const uint8_t *b = &tag->bytes[at];
        return littleEndian ? le32(b) : uint32_t(b[0]) << 24 | uint32_t(b[1]) << 16 | uint32_t(b[2]) << 8 | b[3];
    };
    const double num = tag->type == 10 ? double(int32_t(word(0))) : double(word(0));
    const double den = tag->type == 10 ? double(int32_t(word(4))) : double(word(4));
    if (den == 0) return std::nullopt;
    return num / den;
}

std::string ascii(const RawTag *tag) {
    if (!tag) return {};
    std::string s(tag->bytes.begin(), std::find(tag->bytes.begin(), tag->bytes.end(), uint8_t(0)));
    while (!s.empty() && s.back() == ' ') s.pop_back();
    return s;
}

// Primer WarpRectilinear de una lista de opcodes del DNG (especificación DNG 1.3, opcode 1; big-endian): los cuatro
// coeficientes radiales kr0…kr3 del plano verde (o del único plano). Llevan un radio corregido r (fracción de la
// distancia del centro a la esquina más lejana) al radio de origen r·(kr0 + kr1 r² + kr2 r⁴ + kr3 r⁶).
std::optional<std::array<double, 4>> warpRectilinear(const std::vector<uint8_t> &list) {
    const auto be32at = [&](std::size_t at) -> std::optional<uint32_t> {
        if (at + 4 > list.size()) return std::nullopt;
        return uint32_t(list[at]) << 24 | uint32_t(list[at + 1]) << 16 | uint32_t(list[at + 2]) << 8 | list[at + 3];
    };
    const auto f64at = [&](std::size_t at) -> std::optional<double> {
        if (at + 8 > list.size()) return std::nullopt;
        uint64_t v = 0;
        for (int i = 0; i < 8; ++i) v = v << 8 | list[at + i];
        double d; std::memcpy(&d, &v, 8); return d;
    };
    const auto count = be32at(0);
    if (!count) return std::nullopt;
    std::size_t at = 4;
    for (uint32_t i = 0; i < std::min<uint32_t>(*count, 64); ++i) {
        const auto id = be32at(at), size = be32at(at + 12);
        if (!id || !size) return std::nullopt;
        const std::size_t body = at + 16;
        if (*id == 1) {
            const auto planes = be32at(body);
            if (!planes || *planes == 0 || *planes > 4) return std::nullopt;
            const std::size_t base = body + 4 + (*planes >= 3 ? 1 : 0) * 6 * 8;   // seis coeficientes por plano
            std::array<double, 4> kr{};
            for (int k = 0; k < 4; ++k) {
                const auto v = f64at(base + 8 * k);
                if (!v || !std::isfinite(*v)) return std::nullopt;
                kr[k] = *v;
            }
            return kr;
        }
        at = body + *size;
    }
    return std::nullopt;
}

std::string fmt(const char *format, double value) { char b[64]; std::snprintf(b, sizeof b, format, value); return b; }

void put16(std::ofstream &out, uint16_t v) { const uint8_t b[2] = {uint8_t(v), uint8_t(v >> 8)}; out.write(reinterpret_cast<const char *>(b), 2); }
void put32(std::ofstream &out, uint32_t v) { const uint8_t b[4] = {uint8_t(v), uint8_t(v >> 8), uint8_t(v >> 16), uint8_t(v >> 24)}; out.write(reinterpret_cast<const char *>(b), 4); }

}  // namespace

GyroFrame gyroFrame(const Dng &dng) {
    GyroFrame frame;
    frame.block = dng.gyroBlock();
    if (frame.block.empty()) return frame;
    for (uint16_t tag : RootTags) if (auto raw = dng.rawTag(tag)) frame.root.push_back(std::move(*raw));
    for (uint16_t tag : ExifTags) if (auto raw = dng.rawTag(tag, true)) frame.exif.push_back(std::move(*raw));
    return frame;
}

bool hasGyro(const std::vector<GyroFrame> &frames) {
    return std::any_of(frames.begin(), frames.end(), [](const GyroFrame &f) { return !f.block.empty(); });
}

void writeFsg2(const std::filesystem::path &path, const std::vector<GyroFrame> &frames, const GyroGeometry &g,
               const std::string &brawName) {
    const auto header = json::Object()
        .str("format", "cdng2braw-fsg2").integer("version", 1).str("braw", brawName)
        .integer("frames", (long long)frames.size())
        .raw("fps", "[" + std::to_string(g.rate.num) + ", " + std::to_string(g.rate.den) + "]")
        .str("byte_order", g.littleEndian ? "II" : "MM")
        .object("source", json::Object().integer("width", g.sourceWidth).integer("height", g.sourceHeight))
        .object("crop", json::Object().integer("x", g.cropX).integer("y", g.cropY).integer("width", g.cropWidth).integer("height", g.cropHeight))
        .object("output", json::Object().integer("width", g.width).integer("height", g.height))
        .text();
    std::ofstream out(path, std::ios::binary);
    out.write("CDNGFSG2", 8);
    put32(out, 1); put32(out, uint32_t(header.size()));
    out.write(header.data(), std::streamsize(header.size()));
    // Una etiqueta igual que en el frame anterior se escribe con longitud SameAsPrevious y sin datos: la lista de
    // correcciones del objetivo (OpcodeList3) ocupa unos 12 KB y casi nunca cambia.
    constexpr uint32_t SameAsPrevious = 0xFFFFFFFFu;
    std::map<std::pair<int, uint16_t>, const RawTag *> previous;
    const auto tags = [&](int ifd, const std::vector<RawTag> &list) {
        for (const auto &t : list) {
            const RawTag *&last = previous[{ifd, t.tag}];
            const bool same = last && last->type == t.type && last->count == t.count && last->bytes == t.bytes;
            put16(out, t.tag); put16(out, t.type); put32(out, t.count); put32(out, same ? SameAsPrevious : uint32_t(t.bytes.size()));
            if (!same) out.write(reinterpret_cast<const char *>(t.bytes.data()), std::streamsize(t.bytes.size()));
            last = &t;
        }
    };
    for (const auto &f : frames) {
        put32(out, uint32_t(f.block.size()));
        out.write(reinterpret_cast<const char *>(f.block.data()), std::streamsize(f.block.size()));
        put16(out, uint16_t(f.root.size())); put16(out, uint16_t(f.exif.size()));
        tags(0, f.root); tags(1, f.exif);
    }
    out.close();
    if (!out) throw Error(t("Could not write the gyro file."));
}

std::string writeGcsv(const std::filesystem::path &path, const std::vector<GyroFrame> &frames, const GyroGeometry &g,
                      const std::string &brawName) {
    const Take take = readTake(frames, g.rate);
    const auto &first = take.records.front();
    const double readoutMs = visibleReadoutMs(first, g);
    std::string software;
    for (const auto &f : frames) {
        if (f.block.empty()) continue;
        software = ascii(findTag(f.root, 0x0131));
        break;
    }

    // Binario a propósito: en modo texto Windows convertiría cada \n en CRLF y el .gcsv no saldría igual que en macOS.
    std::ofstream out(path, std::ios::binary);
    out << "GYROFLOW IMU LOG\nversion,1.3\nid,sigma_fp_gyro2\norientation,xyz\n"
        << "note,cdng2braw: SIGMA fp Gyro2 (fpSup) " << g.sourceWidth << "x" << g.sourceHeight << " -> " << g.width << "x" << g.height
        << " (crop " << g.cropX << "," << g.cropY << " " << g.cropWidth << "x" << g.cropHeight << ")" << (take.note.empty() ? "" : "; " + take.note) << "\n";
    if (!software.empty()) out << "fwversion," << software << "\n";
    out << "vendor,SIGMA\nvideofilename," << brawName << "\n"
        << "frame_readout_time," << fmt("%.4f", readoutMs) << "\nframe_readout_direction,0\n"
        << "tscale,0.000001\ngscale," << fmt("%.9g", first.gscale) << "\nascale," << fmt("%.9g", first.ascale) << "\n"
        << "t,gx,gy,gz,ax,ay,az\n";
    unsigned long long rows = 0;
    eachSample(take.records, [&](unsigned long long global, const std::array<int16_t, 3> &s, const std::array<int, 3> &a) {
        out << std::llround(double(global) * take.periodUs - take.startUs) << ',' << s[0] << ',' << s[1] << ',' << s[2] << ','
            << a[0] << ',' << a[1] << ',' << a[2] << '\n';
        ++rows;
    });
    out.close();
    if (!out) throw Error(t("Could not write the gyro file."));
    if (!rows) throw Error(t("The gyro blocks of this take cannot be read."));
    return take.note;
}

BrawMotion brawMotion(const std::vector<GyroFrame> &frames, const GyroGeometry &g, std::size_t frameCount) {
    const Take take = readTake(frames, g.rate);
    const auto &first = take.records.front();
    std::vector<double> times;
    std::vector<std::array<int16_t, 3>> gyro;
    std::vector<std::array<int, 3>> accel;
    eachSample(take.records, [&](unsigned long long global, const std::array<int16_t, 3> &s, const std::array<int, 3> &a) {
        times.push_back(double(global) * take.periodUs); gyro.push_back(s); accel.push_back(a);
    });
    if (times.size() < 2) throw Error(t("The gyro blocks of this take cannot be read."));
    std::optional<double> exposure;
    for (const auto &f : frames)
        if (!f.block.empty()) { exposure = rational(findTag(f.exif, 0x829A), g.littleEndian); break; }

    // Una cámara Blackmagic escribe la lectura i en i / ritmo del reloj de los frames, con el frame k en k / fps, y
    // ese instante es el centro de la exposición de la primera fila del frame (telemetry-parser, blackmagic). Aquí el
    // frame 0 empieza a leerse en startUs, así que el origen es media exposición antes; el reloj de Gyro2 avanza
    // frameUs por frame. Las lecturas se interpolan (giroscopio) o se mantienen (acelerómetro) en esos instantes.
    BrawMotion motion;
    motion.note = take.note;
    const double fps = double(g.rate.num) / g.rate.den;
    const uint32_t rate = uint32_t(std::lround(1.0e6 / take.periodUs));
    const double origin = take.startUs - exposure.value_or(0) * 1.0e6 / 2;
    if (!exposure) motion.note += std::string(motion.note.empty() ? "" : "; ") + "sin tiempo de exposición en el DNG: el origen es el inicio de la lectura";
    const std::size_t count = std::size_t(std::floor(double(frameCount) / fps * rate));
    motion.samples.rate = rate;
    motion.samples.gyro.reserve(count); motion.samples.accel.reserve(count);
    const double gscale = first.gscale, ascale = first.ascale * StandardGravity;
    std::size_t i = 0;
    for (std::size_t j = 0; j < count; ++j) {
        const double at = origin + double(j) / rate * fps * take.frameUs;
        while (i + 2 < times.size() && times[i + 1] <= at) ++i;
        const double u = std::clamp((at - times[i]) / (times[i + 1] - times[i]), 0.0, 1.0);
        std::array<double, 3> w{}, a{};
        for (int c = 0; c < 3; ++c) {
            w[c] = gscale * (gyro[i][c] + u * (gyro[i + 1][c] - gyro[i][c]));
            a[c] = ascale * (u < 1 ? accel[i][c] : accel[i + 1][c]);
        }
        // Ejes: Gyroflow lee los de Blackmagic como «yxz» (intercambia x e y) y el acelerómetro con el signo cambiado;
        // así quedan como los del .gcsv («xyz»).
        motion.samples.gyro.push_back({float(w[1]), float(w[0]), float(w[2])});
        motion.samples.accel.push_back({float(-a[1]), float(-a[0]), float(-a[2])});
    }
    // telemetry-parser calcula el tiempo de lectura como filas de la imagen (crop_size, múltiplo de 8 hacia abajo) por
    // sensor_line_time: el equivalente que da el de la imagen convertida.
    motion.readoutMs = visibleReadoutMs(first, g);
    const int rows = g.height / 8 * 8;
    if (rows > 0 && motion.readoutMs > 0) motion.lineTimeUs = float(motion.readoutMs * 1000 / rows);
    return motion;
}

std::string lensModel(const std::vector<GyroFrame> &frames) {
    for (const auto &f : frames)
        if (auto name = ascii(findTag(f.exif, 0xA434)); !name.empty()) return name;
    return {};
}

std::optional<LensFit> lensFit(const std::vector<GyroFrame> &frames, const GyroGeometry &g, std::string &why) {
    const GyroFrame *frame = nullptr;
    std::optional<Record> record;
    for (std::size_t i = 0; i < frames.size() && !record; ++i)
        if ((record = parse(frames[i].block, int(i)))) frame = &frames[i];
    if (!record) { why = "sin bloques de giroscopio legibles"; return std::nullopt; }
    const auto mount = rational(findTag(frame->exif, 0x920A), g.littleEndian);
    if (!mount || !(*mount > 0)) { why = "objetivo sin contactos: los DNG no dicen la focal"; return std::nullopt; }
    const RawTag *opcodes = findTag(frame->root, 0xC74E);
    const auto kr = opcodes ? warpRectilinear(opcodes->bytes) : std::nullopt;
    // Una tabla vacía (objetivo sin datos) da kr0 lejos de 1.
    if (!kr || !((*kr)[0] > 0.5 && (*kr)[0] < 1.5)) { why = "los DNG no llevan la corrección de distorsión del objetivo"; return std::nullopt; }
    // La focal a la que se midió la corrección, si es creíble frente a la de la montura (difieren poco: un 40 mm puede
    // estar medido a 39,4); si no, la de la montura.
    double focal = *mount;
    if (record->calibFocalTenths) {
        const double calib = record->calibFocalTenths / 10.0;
        if (calib >= 1 && calib <= 2000 && std::abs(calib - *mount) <= *mount / 4) focal = calib;
    }
    // Focal en píxeles de la imagen del DNG: su ancho cubre las columnas del sensor que lee el modo.
    const double columns = record->columns ? std::min<double>(record->columns, SensorColumns) : SensorColumns;
    const double focalPx = g.sourceWidth * focal / (SensorWidthMm * columns / SensorColumns);
    // Ojo de pez de OpenCV: un rayo a θ del eje cae a f'·(θ + k1 θ³ + k2 θ⁵ + k3 θ⁷) del centro. La imagen corregida es
    // rectilínea con la focal del objetivo: el radio corregido ρ (fracción de la esquina) es tan θ = ρ·s, con s la
    // esquina en focales. El de origen es ρ·esquina·P(ρ²); kr0 no es distorsión sino el cambio de escala al enfocar,
    // así que va en la focal, f' = focal·kr0, y en unidades de f' el radio de origen es ρ·s·P(ρ²)/kr0. Mínimos
    // cuadrados en todo el radio del DNG (en B001_144 con el 24 mm, 0,6 px de error máximo en su esquina y 0,27 px en
    // la del BRAW de 3840 × 2160; ajustar solo hasta la del BRAW da más error máximo dentro de ella).
    const double s = std::hypot(double(g.sourceWidth), double(g.sourceHeight)) / 2 / focalPx;
    double m[3][3] = {}, v[3] = {};
    constexpr int Steps = 200;
    for (int step = 1; step <= Steps; ++step) {
        const double rho = double(step) / Steps, r2 = rho * rho, u = rho * s;
        const double p = ((*kr)[0] + (*kr)[1] * r2 + (*kr)[2] * r2 * r2 + (*kr)[3] * r2 * r2 * r2) / (*kr)[0];
        const double theta = std::atan(u), t2 = theta * theta;
        const double basis[3] = {theta * t2, theta * t2 * t2, theta * t2 * t2 * t2};
        const double y = p * u - theta;
        for (int a = 0; a < 3; ++a) {
            v[a] += basis[a] * y;
            for (int b = 0; b < 3; ++b) m[a][b] += basis[a] * basis[b];
        }
    }
    // Eliminación de Gauss con pivote parcial.
    for (int c = 0; c < 3; ++c) {
        int pivot = c;
        for (int r = c + 1; r < 3; ++r) if (std::abs(m[r][c]) > std::abs(m[pivot][c])) pivot = r;
        if (!(std::abs(m[pivot][c]) > 0)) { why = "el ajuste del objetivo no tiene solución"; return std::nullopt; }
        std::swap(m[c], m[pivot]); std::swap(v[c], v[pivot]);
        for (int r = c + 1; r < 3; ++r) {
            const double f = m[r][c] / m[c][c];
            for (int k = c; k < 3; ++k) m[r][k] -= f * m[c][k];
            v[r] -= f * v[c];
        }
    }
    std::array<double, 3> k{};
    for (int r = 2; r >= 0; --r) {
        double sum = v[r];
        for (int c = r + 1; c < 3; ++c) sum -= m[r][c] * k[c];
        k[r] = sum / m[r][r];
    }
    if (!std::all_of(k.begin(), k.end(), [](double x) { return std::isfinite(x); })) { why = "el ajuste del objetivo no tiene solución"; return std::nullopt; }
    // A la imagen del BRAW: el recorte es centrado y la escala la misma en los dos ejes.
    LensFit fit;
    fit.focalMm = focal;
    fit.fx = focalPx * (*kr)[0] * g.width / g.cropWidth;
    fit.cx = g.width / 2.0; fit.cy = g.height / 2.0;
    fit.k = {k[0], k[1], k[2], 0};
    fit.lens = lensModel(frames);
    return fit;
}

void writeGyroflowProject(const std::filesystem::path &path, const std::filesystem::path &braw,
                          const std::filesystem::path &gyroSource, std::size_t frameCount, const GyroGeometry &g,
                          const LensFit &lens, double readoutMs) {
    const double fps = double(g.rate.num) / g.rate.den;
    const auto num = [](double v) { return json::number(v); };
    const auto dims = json::Object().integer("w", g.width).integer("h", g.height);
    std::string coeffs = "[";
    for (std::size_t i = 0; i < lens.k.size(); ++i) coeffs += (i ? ", " : "") + num(lens.k[i]);
    coeffs += "]";
    const std::string matrix = "[[" + num(lens.fx) + ", 0.0, " + num(lens.cx) + "], [0.0, " + num(lens.fx) + ", " +
                               num(lens.cy) + "], [0.0, 0.0, 1.0]]";
    // Perfil de objetivo de Gyroflow (lens_profile.rs, LensProfile): calibrado para el tamaño del BRAW.
    const auto calibration = json::Object()
        .str("name", "SIGMA fp " + lens.lens + " " + std::to_string(g.width) + "x" + std::to_string(g.height))
        .str("note", "cdng2braw: de la corrección de distorsión que la SIGMA fp escribe en cada DNG (WarpRectilinear), "
                     "focal " + fmt("%.1f", lens.focalMm) + " mm")
        .str("calibrated_by", "cdng2braw").str("camera_brand", "SIGMA").str("camera_model", "fp").str("lens_model", lens.lens)
        .object("calib_dimension", dims).object("orig_dimension", dims)
        .num("input_horizontal_stretch", 1).num("input_vertical_stretch", 1)
        .num("fps", fps).boolean("official", false).boolean("asymmetrical", false)
        .object("fisheye_params", json::Object().num("RMS_error", 0).raw("camera_matrix", matrix).raw("distortion_coeffs", coeffs))
        .str("distortion_model", "opencv_fisheye");
    const auto text = json::Object()
        .str("title", "Gyroflow data file").integer("version", 4)
        .str("videofile", braw.u8string())
        .object("calibration_data", calibration)
        .object("video_info", json::Object().integer("width", g.width).integer("height", g.height).num("rotation", 0)
                                  .integer("num_frames", (long long)frameCount).num("fps", fps)
                                  .num("duration_ms", frameCount * 1000.0 / fps))
        .object("gyro_source", json::Object().str("filepath", gyroSource.u8string()))
        .object("stabilization", json::Object().num("frame_readout_time", readoutMs))
        .text();
    std::ofstream out(path, std::ios::binary);
    out << text << '\n';
    out.close();
    if (!out) throw Error(t("Could not write the gyro file."));
}

}  // namespace cdng::worker
