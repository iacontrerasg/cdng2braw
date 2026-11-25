#pragma once
// Bloque B — de la imagen de cámara a los planos del decodificador. Rutas de cada cámara, curva del
// sensor, planos, compensación del desbloqueo, reducción de ruido y vista previa.
// Es un port del prototipo en Python, que no se publica.
#include "common.hpp"
#include "dng.hpp"
#include "geometry.hpp"

#include <memory>

namespace cdng {

enum class Route { Va, Og, Pocket };   // Sigma fp → Va; Pocket Cinema Camera (2013) → Og; otras → Pocket

// Calibración de la Pocket original: model/pocket_og_cinemadng_signal.json + el Camera Support JSON que nombra,
// comprobado por SHA256 (load_support_model).
struct OgModel {
    std::filesystem::path signalPath;
    std::string cameraType;   // "Blackmagic Pocket Cinema Camera"
    int gen = 5;
    uint32_t revision = 0;
    // El resto de campos los decide el bloque B (curva, matrices, tabla de balance del perfil…).
    struct Impl;
    std::shared_ptr<const Impl> impl;
};
OgModel loadOgModel(const std::filesystem::path &signalJson);

// source_info de la ruta og: comprueba matrices, BaselineExposure e ISO frente a la calibración y estima el As
// Shot con la tabla de balance del perfil (infer_as_shot). Lanza Error con los mismos mensajes que el Python.
struct OgShot {
    int iso = 1250; int kelvin = 5600; int tint = 10; double gain = 1.0;
    // Diagnóstico: BaselineExposure del DNG, 2^BaselineExposure y la ganancia calculada antes de forzarla.
    double baselineExposure = 0, exposureGain = 1, computedGain = 1;
};
OgShot ogSourceInfo(const Dng &dng, const OgModel &model);

// Lo que necesita el demosaico parcial de cada cámara: cómo pasa un fotosito a código y la señal de un código. Solo lo
// tienen los mapas que guardan los canales del sensor sin mezclarlos (Y = (R + 2G + B)/4, Cb = (B − G)/2, Cr = (R − G)/2
// sobre los códigos de cada canal); con ellos el Bayer se procesa igual tenga el patrón que tenga.
class ChannelCodec {
public:
    virtual ~ChannelCodec() = default;
    virtual double encode(double sample, int channel) const = 0;   // muestra lineal del sensor (× ganancia) → código del plano
    virtual double signal(double code) const = 0;                  // código → señal desde el cero de la curva (vértice)
};

// Cámara lineal (o Rec.709 lineal en la ruta Pocket) → (Y, Cb, Cr) del decodificador, píxel a píxel.
class SignalMap {
public:
    virtual ~SignalMap() = default;
    virtual void map(const float *rgb, float *ycc, std::size_t count) const = 0;   // count píxeles entrelazados
    // Si el mapa guarda los canales del sensor por separado, su códec por fotosito: entonces la cámara va por el
    // demosaico parcial (bayerPlanes y finishPlanes). Si no (mapas que mezclan colores), nullptr.
    virtual const ChannelCodec *channelCodec() const { return nullptr; }
};
std::unique_ptr<SignalMap> videoAssistMap(const std::filesystem::path &resources);   // model/video_assist_input_transform.json
std::unique_ptr<SignalMap> ogSignalMap(const OgModel &model);                       // NativeSensorMap
// PocketMap gen 4: Rec.709 → raw Pocket → interpolación precalculada
// (model/pocket_inverse_gen4.bin). No es una tabla regular: una tabla N³ no baja de
// decenas de códigos de error; se guarda la triangulación de SciPy y se recorre con un índice en
// rejilla regular, con el vecino más cercano fuera de la envolvente, como el Python.
std::unique_ptr<SignalMap> pocketMap(const std::filesystem::path &resources);

// Ruta Sigma: Y 3×3 y proporciones de croma 2×2 desde el Bayer.
// Los campos cb/cr contienen proporciones hasta que finishPlanes los convierte en códigos.
Planes bayerPlanes(const BayerImage &bayer, const SignalMap &map, double gain);
Planes resizePlanes(Planes planes, int width, int height);
// Campos de la ruta Sigma: Y (3×3 de los códigos) a resolución completa y, para azul y rojo, B − Ĝ y Ĝ filtrados con
// el binomial 7 (numerador y denominador de la proporción) y leídos solo en el sitio de cada cuarteto (Cb abajo-derecha,
// Cr arriba-izquierda), width/2 × height/2. El binomial 7 suma lo mismo en sus tomas pares que en las impares: filtrar
// una rejilla de un fotosito de cada cuatro deja un campo liso que no pasa de un valor por cuarteto, así que leerlo en
// los sitios no pierde nada (tampoco al reescalar).
struct BayerFields {
    int width = 0, height = 0;
    std::vector<float> y, numB, denB, numR, denR;
};
BayerFields bayerFields(const BayerImage &bayer, const SignalMap &map, double gain);
// Proporciones en los sitios (numerador / denominador), como bayerPlanes.
Planes sitePlanes(const BayerFields &fields);
// Planos a width × height desde los campos: Y con Lanczos a resolución completa (recorte centrado de resizePlanes);
// numerador y denominador remuestreados por separado de los sitios de entrada a los de salida, con la fase de cada
// sitio, y divididos después.
Planes resizeBayerFields(const BayerFields &fields, int width, int height);
// bayerPlanes a width × height con resizeBayerFields: la croma queda en el píxel donde la pinta el descodificador.
// CDNG_RESIZE_CHROMA=sites vuelve a reescalar las proporciones ya divididas sin corregir la fase (resizePlanes).
Planes bayerPlanesSized(const BayerImage &bayer, const SignalMap &map, double gain, int width, int height);
void finishPlanes(Planes &planes, const SignalMap &map, Route route, double luma, double chroma);
// image_planes: Y a resolución completa del mapa del píxel; Cb/Cr del mapa de la media 2×2 del RGB.
Planes imagePlanes(const RgbImage &rgb, const SignalMap &map);
// canvas_planes: lienzo con bordes replicados, precompensación del filtro de desbloqueo (3,10,3)/16 en luma y
// redondeo/recorte a 0…4095 (Y) y −4096…4095 (croma).
CanvasPlanes canvasPlanes(const Planes &planes, const Geometry &geometry, bool deblock = true);
// Inversa para la vista previa de la compresión: filtro antibloques del decodificador sobre la luma (DEBLOCK_TAPS
// 3-10-3 en los dos píxeles de cada frontera de bloque; el inverso de la precompensación) y recorte al tamaño entregado.
Planes decodedPlanes(const CanvasPlanes &canvas, const Geometry &geometry);

// Reducción de ruido intra-frame. luma y chroma en 0…1 (porcentaje / 100).
// Lee una vez las muestras normales fijas de resourceRoot()/model/nr_normals_6000x64.f32.
void denoisePlanes(Planes &planes, double luma, double chroma, int stride = 2);

// Hilos que reparten la reducción de ruido **dentro** de un fotograma. 1 por omisión, que es lo que quiere la
// conversión: allí ya hay un fotograma por proceso y repartir también por dentro solo haría competir a los hilos.
// La vista previa trabaja sobre un único fotograma, así que sube esto a los núcleos de la máquina.
// Solo se reparten los bucles que escriben en sitios disjuntos, de modo que el resultado es el mismo bit a bit
// con cualquier número de hilos; las fases cuyo orden de acumulación importa siguen en serie.
void setNoiseThreads(int threads);

// Vista previa: planos del decodificador de un recorte → RGB de cámara lineal (va, og) o Rec.709 lineal (pocket).
RgbImage planesCropToCamera(const Planes &planes, int x, int y, int width, int height, Route route,
                            const std::filesystem::path &resources, const OgModel *og);

}  // namespace cdng
