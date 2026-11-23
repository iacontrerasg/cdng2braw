// Bloque A — lectura del WAV: WAV/BWF PCM de 16, 24 o 32 bits y cualquier número de canales →
// PCM de 24 bits little-endian a 2 canales (mono se duplica; de más de dos se toman los dos primeros).
#include "dng.hpp"

#include <algorithm>
#include <cstring>

namespace cdng {

namespace {
uint32_t le32(const uint8_t *p) { return uint32_t(p[0]) | uint32_t(p[1]) << 8 | uint32_t(p[2]) << 16 | uint32_t(p[3]) << 24; }
uint16_t le16(const uint8_t *p) { return uint16_t(p[0] | p[1] << 8); }
}  // namespace

Pcm24 readWavPcm24(const std::filesystem::path &path) {
    const std::vector<uint8_t> b = readFile(path);
    if (b.size() < 12 || std::memcmp(b.data(), "RIFF", 4) || std::memcmp(b.data() + 8, "WAVE", 4))
        throw Error("not a RIFF/WAVE file");
    // Como el Python: se recorre todo el fichero y gana el último chunk "fmt " y "data"; un chunk que se sale del
    // fichero se recorta (slicing de bytes).
    const std::size_t size = b.size();
    std::size_t off = 12, fmtAt = 0, fmtLen = 0, dataAt = 0, dataLen = 0;
    bool fmt = false, data = false;
    while (off + 8 <= size) {
        const uint64_t sz = le32(b.data() + off + 4);
        const std::size_t start = off + 8, len = std::size_t(std::min<uint64_t>(sz, size - start));
        if (!std::memcmp(b.data() + off, "fmt ", 4)) { fmt = true; fmtAt = start; fmtLen = len; }
        else if (!std::memcmp(b.data() + off, "data", 4)) { data = true; dataAt = start; dataLen = len; }
        off = std::size_t(off + 8 + sz + (sz & 1));
    }
    if (!fmt || fmtLen < 16) throw Error("WAV without fmt chunk");
    if (!data) throw Error("WAV without data chunk");
    const uint8_t *f = b.data() + fmtAt;
    const int ch = le16(f + 2);
    const uint32_t rate = le32(f + 4);
    const int bits = le16(f + 14);
    if (bits != 16 && bits != 24 && bits != 32) throw Error("WAV de " + std::to_string(bits) + " bits no soportado");
    if (ch == 0) throw Error("WAV without channels");
    const int bytesPerSample = bits / 8;
    const std::size_t n = dataLen / (std::size_t(ch) * bytesPerSample);
    Pcm24 out;
    out.frames = (long long)n;
    out.rate = int(rate);
    out.data.resize(n * 6);
    const uint8_t *d = b.data() + dataAt;
    for (std::size_t i = 0; i < n; ++i) {
        for (int c = 0; c < 2; ++c) {
            const int src = ch == 1 ? 0 : c;   // mono → los dos canales iguales
            const uint8_t *p = d + (i * ch + src) * bytesPerSample;
            int32_t s;
            if (bits == 16) s = int32_t(int16_t(le16(p))) * 256;
            else if (bits == 24) { s = int32_t(p[0] | p[1] << 8 | p[2] << 16); if (s >= 1 << 23) s -= 1 << 24; }
            else s = int32_t(le32(p)) >> 8;
            const uint32_t u = uint32_t(s) & 0xFFFFFF;
            uint8_t *o = out.data.data() + i * 6 + c * 3;
            o[0] = uint8_t(u); o[1] = uint8_t(u >> 8); o[2] = uint8_t(u >> 16);
        }
    }
    return out;
}

}  // namespace cdng
