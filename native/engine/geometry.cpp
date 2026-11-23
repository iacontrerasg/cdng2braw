#include "geometry.hpp"

#include <algorithm>
#include <cstdio>
#include <cstring>

namespace cdng {

Geometry::Geometry(int w_, int h_, int ws_, int hs_, int rows_, int nstrips_)
    : w(w_), h(h_), rows(rows_), nstrips(nstrips_) {
    ws = ws_ ? ws_ : (w + 32 + 31) / 32 * 32;
    hs = hs_ ? hs_ : (h + 16 + 7) / 8 * 8;
    // El SDK se cuelga con más de 33 slices por franja (medido el 9 nov 2026: 33 descodifica, 34 no; las filas por slice
    // tienen que ser múltiplo de 8). Con las 72 filas de la cámara eso solo pasa por encima de 2304 líneas de lienzo
    // (6K, 4:3…): entonces se usan más filas por slice, hasta que queden 32. La imagen descodificada no cambia.
    if ((hs + rows - 1) / rows > MaxSlicesPerStrip) rows = ((hs + MaxSlicesPerStrip - 1) / MaxSlicesPerStrip + 7) / 8 * 8;
    const int units = ws / 16;
    if (nstrips == 4) {
        const int a = units / 2 / 2 * 16, b = (ws - 2 * a) / 2;
        if (a % 16 || b % 16) throw Error("Unsupported frame geometry.");
        strips = {a, a, b, b};
    } else {
        // Las franjas más anchas al final, como en el Video Assist.
        const int base = units / nstrips, extra = units % nstrips;
        for (int i = 0; i < nstrips; ++i) strips.push_back(16 * (base + (i >= nstrips - extra ? 1 : 0)));
    }
    int x = 0;
    for (int s : strips) { bounds.emplace_back(x, x + s); x += s; }
    slices = (hs + rows - 1) / rows;
    n = nstrips * slices;
    x0 = (ws - w) / 2;
    y0 = (hs - h) / 2;
}

std::vector<uint8_t> Geometry::header(const std::vector<uint8_t> &reference) const {
    std::vector<uint8_t> hdr(reference);
    hdr[2] = uint8_t(nstrips);
    hdr[3] = uint8_t(slices);
    put16(&hdr[4], uint16_t(ws)); put16(&hdr[6], uint16_t(hs)); put16(&hdr[8], uint16_t(rows));
    std::fill(hdr.begin() + 0x38, hdr.begin() + 0x40, 0);
    for (int i = 0; i < nstrips; ++i) put16(&hdr[0x38 + 2 * i], uint16_t(strips[i]));
    return hdr;
}

std::vector<uint8_t> Geometry::gap(const std::vector<uint8_t> &reference) const {
    // Tabla u16 BE 0x0021 × 96 en 0x1080 (64 coeficientes de luma + 32 de croma), siempre 96 entradas: escribirla
    // por slice dejaba a cero las de croma en Full HD y el decodificador descartaba la croma (problema D3).
    const int base = 0x80 + 4 * n;
    if (base > 0x1080) throw Error("Too many slices for the offset table.");
    std::vector<uint8_t> g(FirstSlice - base, 0);
    for (int i = 0; i < 96; ++i) put16(&g[0x1080 - base + 2 * i], 0x21);
    std::copy(reference.end() - 16, reference.end(), g.end() - 16);
    return g;
}

void Geometry::patchMoov(std::vector<uint8_t> &b, std::size_t from) const {
    auto replaceAll = [&](const std::vector<uint8_t> &old, const std::vector<uint8_t> &now) {
        auto i = b.begin() + std::ptrdiff_t(from);
        while ((i = std::search(i, b.end(), old.begin(), old.end())) != b.end()) {
            std::copy(now.begin(), now.end(), i);
            i += std::ptrdiff_t(now.size());
        }
    };
    auto u16 = [](int a, int c) { std::vector<uint8_t> v(4); put16(&v[0], uint16_t(a)); put16(&v[2], uint16_t(c)); return v; };
    auto u32 = [](uint32_t a, uint32_t c) { std::vector<uint8_t> v(8); put32(&v[0], a); put32(&v[4], c); return v; };
    auto f32 = [](float a, float c) {
        uint32_t ua, uc; std::memcpy(&ua, &a, 4); std::memcpy(&uc, &c, 4);
        std::vector<uint8_t> v(8); put32(&v[0], ua); put32(&v[4], uc); return v;
    };
    replaceAll(u16(4128, 2176), u16(ws, hs));
    replaceAll(u32(4128u << 16, 2176u << 16), u32(uint32_t(ws) << 16, uint32_t(hs) << 16));
    replaceAll(f32(4096, 2160), f32(float(w), float(h)));
    replaceAll(f32(16, 8), f32(float(x0), float(y0)));
}

FileChunk splitFileChunk(const uint8_t *chunk, std::size_t size) {
    if (size < FirstSlice || std::memcmp(chunk, "braw", 4) != 0) throw Error("Invalid BRAW frame.");
    FileChunk out;
    const uint32_t total = be32(chunk + 4);
    out.header.assign(chunk + 8, chunk + 8 + 120);
    const int n = out.header[2] * out.header[3];
    for (int i = 0; i < n; ++i) out.table.push_back(be32(chunk + 0x80 + 4 * i));
    out.gap.assign(chunk + 0x80 + 4 * n, chunk + FirstSlice);
    for (int i = 0; i < n; ++i) {
        const uint32_t end = i + 1 < n ? out.table[i + 1] : total;
        out.slices.emplace_back(chunk + out.table[i], chunk + end);
    }
    return out;
}

std::vector<uint8_t> buildFileChunk(const std::vector<uint8_t> &header, const std::vector<uint8_t> &gap,
                                    const std::vector<std::vector<uint8_t>> &slices) {
    std::size_t offset = FirstSlice;
    std::vector<uint32_t> table;
    for (const auto &s : slices) {
        if (s.size() % 8) throw Error("Slice is not padded to 8 bytes.");
        table.push_back(uint32_t(offset)); offset += s.size();
    }
    std::vector<uint8_t> body;
    body.reserve(offset);
    body.insert(body.end(), {'b', 'r', 'a', 'w', 0, 0, 0, 0});
    put32(&body[4], uint32_t(offset));
    body.insert(body.end(), header.begin(), header.end());
    for (uint32_t t : table) { uint8_t v[4]; put32(v, t); body.insert(body.end(), v, v + 4); }
    body.insert(body.end(), gap.begin(), gap.end());
    for (const auto &s : slices) body.insert(body.end(), s.begin(), s.end());
    if (body.size() != offset) throw Error("Inconsistent BRAW frame size.");
    return body;
}

std::vector<uint8_t> readFile(const std::filesystem::path &path) {
    FILE *f = std::fopen(path.string().c_str(), "rb");
    if (!f) throw Error("Could not read " + path.string());
    std::vector<uint8_t> data;
    uint8_t buffer[1 << 16];
    std::size_t n;
    while ((n = std::fread(buffer, 1, sizeof buffer, f)) > 0) data.insert(data.end(), buffer, buffer + n);
    std::fclose(f);
    return data;
}

}  // namespace cdng
