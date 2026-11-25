#pragma once
// Bloque D — contenedor .braw. Átomos, moov, audio, metadatos por frame e identidad
// del clip. Port de tools/braw_container.py y del prototipo en Python, que no se publica;
// el resultado era idéntico byte a byte al suyo con los mismos chunks.
#include "common.hpp"
#include "dng.hpp"
#include "geometry.hpp"
#include "signal.hpp"

#include <array>
#include <memory>
#include <string>
#include <vector>

namespace cdng {

struct VideoSample { uint64_t offset; uint32_t size; };
std::vector<VideoSample> videoSamples(const std::filesystem::path &path);

// Cabecera y hueco del primer frame de la plantilla model/cam/orig.braw (lo que lee _init en Python).
struct TemplateFrame { std::vector<uint8_t> header, gap, bmdf; };
TemplateFrame readTemplate(const std::filesystem::path &templateBraw);

// Metadatos de un frame (bmdf de 1024 bytes): ISO, temperatura, tinte, cadencia y, en la ruta Va, la ganancia
// analógica del Video Assist 2^(1/3). En la ruta og, la ISO inferida de BaselineExposure, ganancia analógica 1 y
// Exposure 0.
struct FrameMeta { int iso = 800; int kelvin = 5600; int tint = 10; Rate rate; Route route = Route::Va; std::string camera = "SIGMA fp"; };

// Movimiento como lo graba una cámara Blackmagic: una lectura por muestra, a ritmo fijo desde el principio del clip,
// en las pistas de metadatos con tiempo «mogy» (giroscopio, rad/s) y «moac» (acelerómetro, m/s², con el signo que
// escribe la cámara).
struct MotionSamples {
    uint32_t rate = 0;
    std::vector<std::array<float, 3>> gyro, accel;
};

// stream_mux: escribe frame a frame con mdat extendido (clips > 4 GiB). Nunca sobrescribe: falla si existe.
class StreamMux {
public:
    StreamMux(const std::filesystem::path &templateBraw, const std::filesystem::path &destination);
    StreamMux(const StreamMux &) = delete;
    ~StreamMux();
    void addFrame(const std::vector<uint8_t> &chunk, const FrameMeta &meta);
    // Antes de finish: el movimiento va al final del mdat, tras el audio, y sus pistas se conservan de la plantilla.
    void setMotion(MotionSamples motion);
    // Timecode en frames, audio PCM opcional (24 bits, 48 kHz), cadencia y drop frame del clip.
    void finish(long long timecode, const Pcm24 *audio, Rate rate, bool dropFrame);
    std::size_t frames() const;

private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

// Identidades que se pueden elegir para la ruta va: cámaras que graban CinemaDNG y que el SDK de Blackmagic conoce
// La primera es la de por defecto.
const std::vector<std::string> &vaCameraTypes();
// Ganancia analógica de la ruta va a ISO 800 para esa identidad: 800 / ISO de 0 dB de la cámara en el SDK (fp 800,
// fp L 640; el Video Assist escribe 2^(1/3) con la fp L). Con otra, Resolve anuncia una ISO distinta de 800.
float vaGainFor(const std::string &camera);
// Ganancia analógica de la ruta og (analog_gain del clip y agpf de cada frame): el pre_gain de todas las ISO del perfil.
// Resolve la usa como clave de su tabla ISO por cámara; con 0 no habría ninguna ISO válida.
inline constexpr float ogAnalogGain = 1.0f;

// Datos del clip que no dependen de la ruta: `lineTimeUs` (sensor_line_time, el tiempo de lectura entre las filas de la
// imagen; 0 = el de la plantilla), `lens` (lens_type; vacío = sin objetivo) y la identidad de la toma: `cameraId`
// (camera_id, un UUID), `clipNumber` (clip_number) y `date` (date_recorded, AAAA:MM:DD). Vacíos = los neutros de la
// plantilla. La ruta og no los lleva: su metadata se reduce a lo que pide su perfil.
struct ClipExtras { float lineTimeUs = 0; std::string lens, cameraId, clipNumber, date; };

// finalize_clip: identidad de cámara del moov. Va: `camera` (una de vaCameraTypes), Gen 4, ganancia analógica;
// Pocket: BMPCC 4K Gen 4; Og: camera_type, gen y revisión del perfil propio, ganancia analógica constante 1 y
// visualización P3-D65 lineal (sin los extras, que su metadata no lleva).
void finalizeClip(const std::filesystem::path &path, const Geometry &geometry, Route route, const OgModel *og,
                  const std::string &camera = vaCameraTypes().front(), const ClipExtras &extras = {});

}  // namespace cdng
