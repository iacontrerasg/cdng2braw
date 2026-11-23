#pragma once
// Bloque A — lectura del CinemaDNG. Tags, color, As Shot, código de tiempo, recorte,
// reescalado y WAV; port del prototipo en Python, que no se publica.
#include "common.hpp"

#include <array>
#include <map>
#include <optional>

namespace cdng {

using Mat3 = std::array<double, 9>;   // fila a fila
using Vec3 = std::array<double, 3>;

struct DngColor {
    Mat3 cm1{}, cm2{};       // ColorMatrix1/2 (XYZ → cámara); si solo hay una, cm2 = cm1
    double k1 = 2856, k2 = 6504;
    Vec3 asShotNeutral{};
};

// Valor de una etiqueta tal como está en el fichero (orden de bytes del DNG): para copiarla a otro sitio.
struct RawTag {
    uint16_t tag = 0, type = 0;
    uint32_t count = 0;
    std::vector<uint8_t> bytes;
};

struct BayerImage {
    int width, height;
    std::vector<float> samples;
    std::vector<uint8_t> colors;
};

class Dng {
public:
    // Lee las IFD de la cadena principal (como el Python). headerOnly: solo el primer megabyte si todas las etiquetas
    // caen dentro (exploración rápida de lotes); el resultado de las consultas de etiquetas es el mismo.
    explicit Dng(std::filesystem::path path, bool headerOnly = false);
    const std::filesystem::path &path() const { return path_; }

    std::string cameraName() const;                                        // nombre de la cámara
    std::optional<std::vector<double>> numeric(uint16_t tag) const;        // un tag numérico
    std::optional<double> scalar(uint16_t tag) const;                      // cdng2braw.dng_tag_value
    DngColor color() const;                                                // dng_color_full
    std::optional<double> blackLevel() const;                              // dng_black_level
    std::optional<Rate> frameRate() const;                                 // dng_frame_rate
    std::optional<long long> timecodeFrames(int nominalFps) const;         // dng_timecode_frames
    bool timecodeDropFrame() const;                                        // dng_timecode_drop_frame
    std::pair<double, double> asShot() const;                              // dng_as_shot: (kelvin, tint)
    std::pair<int, int> rawSize() const;                                   // rawpy sizes.width/height (dng_size)
    // Bloque de giroscopio FSG2 del módulo Gyro2 de fpSup (Sigma fp), tal cual: 'FSG2', versión, tipo, build o
    // reservado, longitud y datos (y su CRC-32 en la versión 1). Vacío si el DNG no lo lleva. Mismo recorrido que
    // fpSup/projects/gyro2-sup/fsg2.py: versión 2 en la etiqueta 0x4653 de la MakerNote, versión 1 en el relleno de
    // la etiqueta 0x0200 (un único bloque con CRC válido).
    std::vector<uint8_t> gyroBlock() const;
    // Una etiqueta de IFD0 (exif = false) o del IFD Exif, en crudo; vacío si no está.
    std::optional<RawTag> rawTag(uint16_t tag, bool exif = false) const;
    bool littleEndian() const { return little_; }

    // dng_camera_rgb: LibRaw con exactamente los parámetros de rawpy del Python (AHD, salida raw, 16 bits, sin WB,
    // negro de LibRaw desactivado y negro racional exacto restado después, user_sat = white_level, sin recortar).
    RgbImage cameraRgb() const;
    BayerImage cameraBayer() const;

private:
    std::filesystem::path path_;
    std::vector<uint8_t> bytes_;
    bool little_ = true;
    // Entradas de la cadena principal de IFD en el orden del fichero (cada función del Python elige la primera o la
    // última aparición de un tag; se conserva el orden para reproducirlo). valueOffset: posición del campo de valor
    // de 4 bytes dentro de la entrada.
    struct Entry { uint16_t tag, type; uint32_t count, value; std::size_t valueOffset; };
    std::vector<Entry> chain_;
    std::string chainError_;   // si la cadena está truncada, el error se lanza al pedir un dato que la necesite
    const std::vector<Entry> &chain() const;
    uint16_t u16(std::size_t at) const;
    uint32_t u32(std::size_t at) const;
};

int nominalFps(Rate rate);
std::pair<double, double> xyToTempTint(double x, double y);   // Robertson, como el DNG SDK
// Sigma fp grabada en un modo de color normal: sus matrices pasadas a las del modo OFF del mismo cuerpo, que son las
// que casan con el perfil de Blackmagic del BRAW (Dng::color() ya lo aplica). Otras matrices, sin cambios.
DngColor sigmaOffColor(DngColor color);
Mat3 bradford(const Vec3 &srcWhite, const Vec3 &dstWhite);
// dng_matrix_for_neutral: (matriz XYZ → cámara interpolada en 1/T, temperatura)
std::pair<Mat3, double> matrixForNeutral(const DngColor &color, const Vec3 &neutral);
// Matriz cámara (sin balance) → Rec.709 lineal D65 de dng_to_709 (cm = "interp").
Mat3 cameraTo709(const DngColor &color, const Vec3 &neutral);

// crop_sensor: DefaultCropOrigin (50719) y DefaultCropSize (50720); sin esos tags devuelve la imagen tal cual.
RgbImage cropSensor(const RgbImage &image, const Dng &dng);
// resize_linear: recorte centrado si cambia la proporción y Lanczos de Pillow (modo F) por canal, en luz lineal.
RgbImage resizeLinear(const RgbImage &image, int width, int height);

struct Pcm24 {
    std::vector<uint8_t> data;   // muestras de 24 bits entrelazadas, como las devuelve read_wav_pcm24
    long long frames = 0;
    int rate = 0;
};
Pcm24 readWavPcm24(const std::filesystem::path &path);

// Llamar en el hilo principal antes de usar LibRaw desde otros hilos. LibRaw protege su gestor de memoria con una
// sección crítica de OpenMP; con libomp estático, si el primer hilo que la usa es uno de la exploración, al terminar
// ese hilo libomp se apaga y libera el cerrojo, y la siguiente apertura en el hilo principal falla (SIGSEGV).
void prepareLibRaw();

}  // namespace cdng
