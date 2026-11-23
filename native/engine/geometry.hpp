#pragma once
// Geometría de un frame BRAW y forma de su chunk. Port de tools/braw_container.py (Geometry, split_file_chunk,
// build_file_chunk). Reglas medidas con el SDK el 31 oct 2026: anchura almacenada múltiplo de 32 ≥ w + 32, altura
// múltiplo de 8 ≥ h + 16, franjas de columnas múltiplo de 16 y una tabla 0x0021 de 96 entradas en el hueco.
#include "common.hpp"

namespace cdng {

constexpr int FirstSlice = 0x1200;   // offset del primer slice dentro del chunk
constexpr int BmdfSize = 1024;       // metadatos por frame que preceden al chunk en el mdat
constexpr int MaxSlicesPerStrip = 32;   // el SDK admite 33; ver Geometry

struct Geometry {
    int w = 0, h = 0;        // tamaño entregado
    int ws = 0, hs = 0;      // tamaño almacenado (lienzo)
    int rows = 72;           // filas por slice
    int nstrips = 4;         // franjas de columnas
    std::vector<int> strips; // anchura de cada franja
    std::vector<std::pair<int, int>> bounds;   // [x0, x1) de cada franja
    int slices = 0;          // slices por franja
    int n = 0;               // slices totales = nstrips × slices
    int x0 = 0, y0 = 0;      // posición de la imagen dentro del lienzo

    Geometry() = default;
    Geometry(int w, int h, int ws = 0, int hs = 0, int rows = 72, int nstrips = 4);

    // Cabecera de 120 bytes a partir de la de referencia (q, escalera… se conservan).
    std::vector<uint8_t> header(const std::vector<uint8_t> &reference) const;
    // Hueco entre la tabla de offsets y FirstSlice (ver el comentario de Geometry.gap en Python).
    std::vector<uint8_t> gap(const std::vector<uint8_t> &reference) const;
    // Parchea el moov (desde `from`): stsd, tkhd, floats de recorte/sensor y origen de la plantilla 4128×2176.
    void patchMoov(std::vector<uint8_t> &moov, std::size_t from) const;
};

struct FileChunk {
    std::vector<uint8_t> header;              // 120 bytes
    std::vector<uint32_t> table;              // offsets de los slices
    std::vector<uint8_t> gap;
    std::vector<std::vector<uint8_t>> slices;
};

FileChunk splitFileChunk(const uint8_t *chunk, std::size_t size);
// Cada slice debe estar ya relleno a múltiplo de 8 bytes.
std::vector<uint8_t> buildFileChunk(const std::vector<uint8_t> &header, const std::vector<uint8_t> &gap,
                                    const std::vector<std::vector<uint8_t>> &slices);

// Lectura y escritura big-endian, compartidas por el códec y el contenedor.
inline uint16_t be16(const uint8_t *p) { return uint16_t(p[0] << 8 | p[1]); }
inline uint32_t be32(const uint8_t *p) { return uint32_t(p[0]) << 24 | uint32_t(p[1]) << 16 | uint32_t(p[2]) << 8 | p[3]; }
inline uint64_t be64(const uint8_t *p) { return uint64_t(be32(p)) << 32 | be32(p + 4); }
inline void put16(uint8_t *p, uint16_t v) { p[0] = uint8_t(v >> 8); p[1] = uint8_t(v); }
inline void put32(uint8_t *p, uint32_t v) { p[0] = uint8_t(v >> 24); p[1] = uint8_t(v >> 16); p[2] = uint8_t(v >> 8); p[3] = uint8_t(v); }
inline void put64(uint8_t *p, uint64_t v) { put32(p, uint32_t(v >> 32)); put32(p + 4, uint32_t(v)); }

}  // namespace cdng
