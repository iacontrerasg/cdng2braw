#pragma once
// Bloque C — compresión de un frame. Pasos, tablas, DCT, cuantización, piezas AC,
// cadena y códigos DC, slices, control de tasa y calidad fija.
// Es un port del prototipo en Python, que no se publica. La entropía ya está en
// C++ (native/src/entropy.cpp, ABI de native/include/cdng_native.h): se reutiliza tal cual.
//
// encode es estricto por defecto: un DC que exceda la clase 12 o un AC por encima de la clase 11 lanzan
// CodecRangeError (encode_strict). encodeFixed y encodeRateControlled suben entonces de q, y solo si ni QMax cabe
// codifican a QMax recortando (encode con strict = false): el DC se recorta en cadena cerrada, siguiendo desde el
// valor recortado (el bloque siguiente recupera lo que falta), y el AC a ±4095. Así ningún frame hace fallar la
// conversión; stats.clamped cuenta lo recortado.
#include "common.hpp"
#include "geometry.hpp"

#include <memory>
#include <string>

namespace cdng {

struct CodecRangeError : Error {
    using Error::Error;
};

struct EncodeStats {
    uint64_t fallback = 0, clamped = 0, dropped = 0;   // mismos contadores que F.stats
    // Veces que se ha codificado el fotograma para dar con su q (1 = a la primera). Solo para medir: el control
    // de tasa prueba siempre un q más fino del que ya cabe, y ese intento se descarta.
    int attempts = 0;
};

// Diagnóstico opcional (CDNG_BIT_BUDGET / CDNG_COEFF_STATS). No forma parte del bitstream.
struct EncodeDiagnostics {
    bool present = false;
    int q_tried = 0;
    int attempt_index = 0;
    bool strict = true;
    double qY = 0, qC = 0;
    std::size_t slices_payload_bytes = 0;
    std::string budget_json;
    std::string coeff_json;       // campos planos sin llaves exteriores: "ac_total":...
    std::string payload_sha256;
};

struct EncodedFrame {
    std::vector<std::vector<uint8_t>> slices;   // en el orden de la tabla, rellenos a 8 bytes
    int q = 0;
    // Síntesis de ruido del decodificador (cabecera del chunk, u16 big-endian en 20–21 para Y y 22–23 para croma):
    // por defecto la tabla del Video Assist por q, atenuada en los q bajos (videoAssistNoise); ver codec.cpp.
    uint16_t noiseY = 0, noiseC = 0;
    // Ruido de entrada estimado en el 30 % de bloques más lisos, en códigos del plano descodificado (σ por píxel).
    double noiseLevelY = 0, noiseLevelC = 0;
    std::size_t size = 0;   // FirstSlice + suma de los slices, como en encode_rate_controlled
    EncodeStats stats;
    EncodeDiagnostics diag;
};

// Campos de síntesis de ruido que escribiría el Video Assist a ese q (ver codec.cpp): los que se escriben por defecto.
std::pair<uint16_t, uint16_t> videoAssistNoise(int q);

// Ruido que pone el descodificador con ese campo de síntesis en un bloque liso (σ en códigos de 10 bits gamma 709,
// medido con el SDK; ver codec.cpp), y su inversa: el campo que más se acerca a sigma.
double decoderNoise(double field, bool chroma);
uint16_t decoderNoiseField(double sigma, bool chroma);

constexpr int QMin = 0;    // cdngseq2braw.Q_MIN: un frame que desborda la tabla VLC a un q fino pasa al siguiente q seguro
constexpr int QMax = 24;   // q máximo

class Codec {
public:
    explicit Codec(const std::filesystem::path &resources);   // model/vlc-table.json, model/quant_steps.json
    ~Codec();
    Codec(Codec &&) noexcept;

    // encode_frame_q en modo estricto (encode_strict) o, con strict = false, recortando lo que no cabe.
    EncodedFrame encode(const CanvasPlanes &planes, int q, const Geometry &geometry, bool strict = true) const;
    // encode_rate_controlled: el q más fino cuyo frame cabe en `target` bytes, empezando en q0; los q que
    // desbordan se tratan como demasiado finos. Si ni QMax es válido, QMax recortando.
    EncodedFrame encodeRateControlled(const CanvasPlanes &planes, const Geometry &geometry, std::size_t target,
                                      int q0, int qMin = QMin) const;
    // Calidad fija: q dado o, si desborda, el siguiente q seguro (frame_chunk en Python); si ninguno, QMax recortando.
    EncodedFrame encodeFixed(const CanvasPlanes &planes, const Geometry &geometry, int q) const;
    // Control de tasa a lo largo del clip: q del siguiente fotograma según el tamaño que haya dado el actual.
    // Con una sola codificación por fotograma (encodeFixed) la relación pedida no se clava en cada uno, sino que
    // se alcanza de media: un fotograma que se pasa lo compensa el siguiente. La banda 0,80–1,05 es histéresis,
    // para que el q no oscile con las variaciones normales de contenido entre fotogramas.
    static int nextQuantiser(int q, std::size_t size, std::size_t target, int qMin = QMin);

    // Lo que reconstruye un decodificador a partir de encode(planes, q): DC y AC cuantizados como los escribe el
    // codificador, descuantizados y con la DCT inversa, en el lienzo (sin el filtro antibloques del decodificador,
    // que aplica decodedPlanes). No modela los coeficientes que la entropía descarta (stats.dropped, raros).
    // Medido frente al SDK (brawdec --decoded-out, Sigma UHD a 5:1, q 12), con decodedPlanes: luma a 1,6 códigos de
    // media (máx. 14 de 4095), croma a ±4.
    CanvasPlanes reconstruct(const CanvasPlanes &planes, int q, const Geometry &geometry) const;

private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

// Cabecera de 120 bytes del frame con su q (hdr[1] = q) y chunk completo listo para el mux.
std::vector<uint8_t> frameChunk(const std::vector<uint8_t> &geometryHeader, const std::vector<uint8_t> &geometryGap,
                                const EncodedFrame &frame);

}  // namespace cdng
