#pragma once
// Giroscopio del módulo Gyro2 de fpSup (Sigma fp): cada DNG lleva un bloque FSG2 con las muestras de su frame
// (fpSup/projects/gyro2-sup; lectura de referencia en el plugin gyroflow-plugins, common/src/fsg2.rs y cdng.rs,
// rama feature/cdng-makernote). La conversión lo lleva:
//   dentro del BRAW   pistas de movimiento como las de una cámara Blackmagic (mogy/moac) y sensor_line_time: Gyroflow
//                     y su plugin las leen al abrir el BRAW.
//   <braw>.gyroflow   proyecto de Gyroflow con el perfil del objetivo calculado de los DNG: el plugin lo carga solo
//                     (por el nombre) y toma el giroscopio del BRAW.
//   <braw>.gcsv       registro estándar de Gyroflow: muestras, orientación, escalas y tiempo de lectura del rolling
//                     shutter de la imagen convertida.
//   <braw>.fsg2       los bloques FSG2 tal cual y las etiquetas del DNG que usa el plugin, frame a frame, con la
//                     geometría de la conversión.
#include "../container.hpp"
#include "../dng.hpp"

namespace cdng::worker {

struct GyroFrame {
    std::vector<uint8_t> block;   // bloque FSG2 completo; vacío en un frame sustituido por negro
    std::vector<RawTag> root, exif;   // etiquetas de IFD0 y del IFD Exif que lee el plugin
};
GyroFrame gyroFrame(const Dng &dng);

// Geometría de la conversión: la imagen revelada del DNG (DefaultCropSize), la zona que se conserva (recorte
// centrado si cambia la proporción) y el tamaño del BRAW.
struct GyroGeometry {
    int sourceWidth = 0, sourceHeight = 0;
    int cropX = 0, cropY = 0, cropWidth = 0, cropHeight = 0;
    int width = 0, height = 0;
    Rate rate;
    bool littleEndian = true;
};

bool hasGyro(const std::vector<GyroFrame> &frames);
void writeFsg2(const std::filesystem::path &path, const std::vector<GyroFrame> &frames, const GyroGeometry &geometry,
               const std::string &brawName);
// Devuelve una nota para el registro (p. ej. sin sincronía Vd); lanza si no hay muestras utilizables.
std::string writeGcsv(const std::filesystem::path &path, const std::vector<GyroFrame> &frames, const GyroGeometry &geometry,
                      const std::string &brawName);

// Movimiento para las pistas del BRAW, remuestreado al reloj de sus `frameCount` frames, y el sensor_line_time que da
// el tiempo de lectura de la imagen convertida. Lanza si no hay muestras utilizables.
struct BrawMotion { MotionSamples samples; float lineTimeUs = 0; double readoutMs = 0; std::string note; };
BrawMotion brawMotion(const std::vector<GyroFrame> &frames, const GyroGeometry &geometry, std::size_t frameCount);

// Perfil del objetivo en la imagen del BRAW, en el modelo de ojo de pez de OpenCV que usa Gyroflow, a partir de la
// corrección de distorsión que la fp escribe en el DNG (WarpRectilinear) y su focal. Del primer frame con giroscopio:
// un perfil fijo para la toma.
struct LensFit {
    double focalMm = 0, fx = 0, cx = 0, cy = 0;
    std::array<double, 4> k{};
    std::string lens;
};
// nullopt con el motivo en `why` (objetivo sin contactos, sin corrección en el DNG…).
std::optional<LensFit> lensFit(const std::vector<GyroFrame> &frames, const GyroGeometry &geometry, std::string &why);
// Nombre del objetivo (LensModel del Exif) del primer frame que lo lleve.
std::string lensModel(const std::vector<GyroFrame> &frames);

// Proyecto de Gyroflow para el BRAW: el vídeo, el perfil del objetivo y el giroscopio (`gyroSource`: el propio BRAW
// o el .gcsv).
void writeGyroflowProject(const std::filesystem::path &path, const std::filesystem::path &braw,
                          const std::filesystem::path &gyroSource, std::size_t frameCount, const GyroGeometry &geometry,
                          const LensFit &lens, double readoutMs);

}  // namespace cdng::worker
