#include "worker.hpp"
#include "gyro.hpp"

#include <simdjson.h>

#include <algorithm>
#include <cctype>
#include <chrono>
#include <cmath>
#include <condition_variable>
#include <cstdio>
#include <cstdlib>
#include <exception>
#include <fstream>
#include <mutex>
#include <random>
#include <thread>

#ifndef _WIN32
#include <cerrno>
#include <fcntl.h>
#include <unistd.h>
#endif

namespace cdng::worker {

namespace {

struct Request {
    std::filesystem::path input, output, cancel, control;
    bool audio = false;
    bool gyroGcsv = false, gyroFsg2 = false;   // giroscopio Gyro2 en ficheros junto al BRAW (si los DNG lo traen)
    bool gyroBraw = true;      // y dentro, como el de una cámara Blackmagic
    bool gyroProject = true;   // proyecto de Gyroflow con el perfil del objetivo
    std::string camera = "auto";   // identidad del BRAW en la ruta va: «auto», la de la cámara que grabó (vaCameraFor)
    std::string work = "auto";   // carpeta de trabajo: auto | output | temp | una ruta
    int width = 0, height = 0;
    Options options;
};

double number(simdjson::ondemand::object &object, const char *key, double fallback) {
    auto value = object.find_field_unordered(key);
    if (value.error()) return fallback;
    double d;
    if (!value.get_double().get(d)) return d;
    int64_t i;
    if (!object.find_field_unordered(key).get_int64().get(i)) return double(i);
    return fallback;
}
std::string text(simdjson::ondemand::object &object, const char *key, const std::string &fallback = {}) {
    std::string_view value;
    if (object.find_field_unordered(key).get_string().get(value)) return fallback;
    return std::string(value);
}

// CDNG_NR_THREADS: hilos dentro de cada fotograma en la reducción de ruido. Al convertir se queda en 1 porque el
// reparto ya es por fotograma; existe para comprobar que el resultado no depende del número de hilos (la salida
// tiene que ser idéntica byte a byte) y para convertir un clip muy corto, donde no hay fotogramas que repartir.
void applyNoiseThreads() {
    if (const char *env = std::getenv("CDNG_NR_THREADS")) {
        const int wanted = std::atoi(env);
        if (wanted > 0) setNoiseThreads(wanted);
    }
}

Request readRequest(const std::filesystem::path &path) {
    simdjson::ondemand::parser parser;
    auto json = simdjson::padded_string::load(path.string());
    if (json.error()) throw Error("Could not read the conversion request.");
    auto document = parser.iterate(json.value_unsafe());
    simdjson::ondemand::object object = document.get_object();
    Request r;
    r.input = text(object, "input"); r.output = text(object, "output"); r.cancel = text(object, "cancel"); r.control = text(object, "control");
    bool audio = false;
    if (!object.find_field_unordered("audio").get_bool().get(audio)) r.audio = audio;
    bool flag = true;
    if (!object.find_field_unordered("gyro_gcsv").get_bool().get(flag)) r.gyroGcsv = flag;
    if (!object.find_field_unordered("gyro_fsg2").get_bool().get(flag)) r.gyroFsg2 = flag;
    if (!object.find_field_unordered("gyro_braw").get_bool().get(flag)) r.gyroBraw = flag;
    if (!object.find_field_unordered("gyro_project").get_bool().get(flag)) r.gyroProject = flag;
    r.camera = text(object, "camera_type", r.camera);
    r.work = text(object, "work", r.work);   // dónde se escribe mientras convierte (ver workParent)
    const auto &cameras = vaCameraTypes();
    if (r.camera != "auto" && std::find(cameras.begin(), cameras.end(), r.camera) == cameras.end()) throw Error(t("Unsupported camera identity."));
    r.width = int(number(object, "width", 0)); r.height = int(number(object, "height", 0));
    // encoding_options: valida antes de arrancar ningún hilo; q en [QMin, QMax] (un frame que desborda a q fino sube de q).
    const double workers = number(object, "workers", 2), q = number(object, "q", 4), ratio = number(object, "ratio", 5);
    const auto mode = text(object, "compression", "quality");
    r.options.nrLuma = number(object, "nr_luma", 0); r.options.nrChroma = number(object, "nr_chroma", 0);
    if (!object.find_field_unordered("noise_synthesis").get_bool().get(flag)) r.options.noiseSynthesis = flag;
    if (!(workers >= 1 && workers <= MaxWorkers) || !(q >= QMin && q <= QMax)) throw Error(t("Unsupported quality or worker count."));
    if ((mode != "quality" && mode != "ratio") || !std::isfinite(ratio) || ratio < 3 || ratio > 12)
        throw Error(t("Unsupported compression: use q 0…24 or ratio 3:1…12:1."));
    for (double v : {r.options.nrLuma, r.options.nrChroma})
        if (!std::isfinite(v) || v < 0 || v > 100) throw Error(t("Noise reduction must be between 0 and 100 % for luma and chroma."));
    r.options.workers = int(workers); r.options.ratio = ratio;
    if (mode == "quality") r.options.q = int(q);
    return r;
}

// unsigned char: con char con signo, un byte >= 0x80 llega negativo a tolower y es comportamiento indefinido.
// Identidad de la ruta va para un nombre de cámara del DNG: «SIGMA fp L» si lo dice, si no «SIGMA fp».
std::string vaCameraFor(std::string name) {
    std::transform(name.begin(), name.end(), name.begin(), [](char c) { return char(std::toupper((unsigned char)c)); });
    return name.find("SIGMA FP L") != std::string::npos ? "SIGMA fp L" : "SIGMA fp";
}

// UUID versión 4 para camera_id.
std::string randomUuid() {
    std::random_device device;
    std::mt19937_64 random((uint64_t(device()) << 32) ^ device());
    uint8_t b[16];
    for (int i = 0; i < 16; i += 8) { const uint64_t v = random(); for (int k = 0; k < 8; ++k) b[i + k] = uint8_t(v >> (8 * k)); }
    b[6] = uint8_t((b[6] & 0x0f) | 0x40); b[8] = uint8_t((b[8] & 0x3f) | 0x80);
    static const char *hex = "0123456789abcdef";
    std::string out;
    for (int i = 0; i < 16; ++i) {
        if (i == 4 || i == 6 || i == 8 || i == 10) out += '-';
        out += hex[b[i] >> 4]; out += hex[b[i] & 15];
    }
    return out;
}

// Fecha de grabación del DNG como la escribe Blackmagic en date_recorded (AAAA:MM:DD): DateTimeOriginal del IFD Exif
// o, si no está, DateTime de IFD0. Vacía si no hay ninguna válida.
std::string recordingDate(const cdng::Dng &dng) {
    for (const auto &[tag, exif] : {std::pair<uint16_t, bool>{0x9003, true}, {0x0132, false}}) {
        const auto raw = dng.rawTag(tag, exif);
        if (!raw || raw->bytes.size() < 10) continue;
        const std::string date(raw->bytes.begin(), raw->bytes.begin() + 10);
        const bool valid = std::all_of(date.begin(), date.end(), [&, i = 0](char c) mutable {
            const int at = i++; return at == 4 || at == 7 ? c == ':' : std::isdigit((unsigned char)c) != 0; });
        if (valid && date != "0000:00:00") return date;
    }
    return {};
}

std::string lower(std::string s) {
    std::transform(s.begin(), s.end(), s.begin(), [](char c) { return char(std::tolower((unsigned char)c)); });
    return s;
}

// Carpeta de trabajo de la toma: en el temporal del sistema (el disco interno, más rápido que un externo o el NAS)
// y, si no cabe allí, junto a la salida.
struct TempDir {
    std::filesystem::path path;
    explicit TempDir(const std::filesystem::path &parent) {
        std::random_device device; std::mt19937_64 random(device());
        for (int attempt = 0; attempt < 100; ++attempt) {
            char name[40]; std::snprintf(name, sizeof name, ".cdng-conversion-%08llx", (unsigned long long)(random() & 0xffffffffull));
            if (std::filesystem::create_directory(parent / name)) { path = parent / name; return; }
        }
        throw Error(t("Could not create the temporary directory."));
    }
    ~TempDir() { std::error_code ignored; std::filesystem::remove_all(path, ignored); }
};

void syncFile(const std::filesystem::path &path) {
#ifndef _WIN32
    const int fd = ::open(path.c_str(), O_RDONLY);
    if (fd >= 0) { ::fsync(fd); ::close(fd); }
#else
    (void)path;
#endif
}

// Publica `stage` como `output` sin sustituir nunca un fichero existente. El enlace duro es atómico, pero los
// recursos SMB (el NAS) no lo admiten: entonces renamex_np con RENAME_EXCL (macOS) y, si tampoco, un renombrado
// tras comprobar que el destino no existe (la app ya lo comprueba al empezar). Devuelve false si no se pudo.
bool publish(const std::filesystem::path &stage, const std::filesystem::path &output, std::string &why) {
    std::error_code ec;
    std::filesystem::create_hard_link(stage, output, ec);
    if (!ec) { std::filesystem::remove(stage, ec); return true; }
    if (ec == std::errc::file_exists) { why = t("The destination already exists. Choose another name."); return false; }
    why = ec.message();
#ifdef __APPLE__
    if (::renamex_np(stage.c_str(), output.c_str(), RENAME_EXCL) == 0) return true;
    if (errno == EEXIST) { why = t("The destination already exists. Choose another name."); return false; }
    why = std::strerror(errno);
#endif
    if (std::filesystem::exists(output, ec)) { why = t("The destination already exists. Choose another name."); return false; }
    std::filesystem::rename(stage, output, ec);
    if (!ec) return true;
    why = ec.message();
    return false;
}

// Lleva `stage` a `output`. Desde el temporal del sistema se copia primero a un nombre oculto junto al destino y
// luego se publica en el mismo volumen: el nombre definitivo nunca muestra un BRAW a medias.
bool deliver(const std::filesystem::path &stage, const std::filesystem::path &output, std::string &why) {
    std::error_code ec;
    if (std::filesystem::equivalent(stage.parent_path().parent_path(), output.parent_path(), ec)) return publish(stage, output, why);
    const auto part = output.parent_path() / ("." + output.filename().string() + ".part");
    std::filesystem::remove(part, ec);
    std::filesystem::copy_file(stage, part, ec);
    if (ec) { why = ec.message(); std::filesystem::remove(part, ec); return false; }
    syncFile(part);
    if (!publish(part, output, why)) { std::filesystem::remove(part, ec); return false; }
    std::filesystem::remove(stage, ec);
    return true;
}

// Dónde trabajar: **siempre en la carpeta de destino**, dentro de un directorio propio que se borra al terminar,
// y la publicación es un renombrado en el mismo volumen. Así el clip se escribe una sola vez: pasar por el
// temporal del sistema obliga a copiarlo entero al final, que con un destino en red es el doble de tráfico, y
// además llena el disco del sistema (medido: 2,5 GB por copiar en una toma de 1483 fotogramas).
//
// `work` en la petición permite otra cosa para pruebas y medidas: "temp" fuerza el temporal del sistema y una
// ruta cualquiera lo lleva ahí. Sin el campo, o con "auto", va al destino.
std::filesystem::path workParent(const std::filesystem::path &output, const std::string &work) {
    std::error_code ec;
    if (work == "temp") {
        const auto system = std::filesystem::temp_directory_path(ec);
        if (ec) throw Error(t("The system temporary folder is not available."));
        return system;
    }
    if (!work.empty() && work != "auto" && work != "output") {
        const std::filesystem::path chosen(work);
        if (!std::filesystem::is_directory(chosen, ec)) throw Error(t("The working folder does not exist."));
        return chosen;
    }
    return output.parent_path();
}

// Instrumentación opcional. CDNG_PROFILE_LOG=<fichero> escribe una línea por fotograma con el reparto del tiempo,
// para ver qué manda: el disco de origen, el cálculo o la escritura en el destino. Sin la variable no hace nada.
//
//   espera   el hilo sin fotograma que coger, porque la ventana de lectura está llena (está atada a lo escrito)
//   lee      abrir y leer el DNG del origen
//   revela   LibRaw y el revelado (CPU)
//   ruido    reducción de ruido y preparación de la señal (CPU, lo más caro)
//   codifica DCT, cuantización y entropía (CPU); `pases` cuenta las veces que recodifica buscando la relación
//   ordena   el escritor esperando a que llegue **este** fotograma, estando los siguientes ya hechos
//   escribe  volcar el chunk al contenedor (disco de destino)
struct Profile {
    bool on = false;
    std::ofstream file;
    std::mutex mutex;
    std::chrono::steady_clock::time_point start;
    void open() {
        const char *path = std::getenv("CDNG_PROFILE_LOG");
        if (!path || !*path) return;
        file.open(path, std::ios::binary | std::ios::app);
        on = file.is_open();
        start = std::chrono::steady_clock::now();
    }
    void header(const std::filesystem::path &input, const std::filesystem::path &work,
                const std::filesystem::path &output, int workers, int slots) {
        if (!on) return;
        file << "# origen:  " << input.string() << "\n# trabajo: " << work.string()
             << "\n# destino: " << output.string() << "\n# procesos: " << workers << " de " << slots << " hilos\n"
             << "# fotograma\tespera\tlee\trevela\truido\tcodifica\tordena\tescribe\tintentos\tq\tsegundos\n" << std::flush;
    }
    void frame(std::size_t index, double wait, double read, double develop, double noise, double encode,
               double order, double write, int attempts, int q) {
        if (!on) return;
        const double now = std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count();
        char line[256];
        std::snprintf(line, sizeof line, "%zu\t%.3f\t%.3f\t%.3f\t%.3f\t%.3f\t%.3f\t%.3f\t%d\t%d\t%.1f\n",
                      index + 1, wait, read, develop, noise, encode, order, write, attempts, q, now);
        std::lock_guard<std::mutex> lock(mutex);
        file << line << std::flush;
    }
};

double since(std::chrono::steady_clock::time_point from) {
    return std::chrono::duration<double>(std::chrono::steady_clock::now() - from).count();
}

// Fichero de control que la app reescribe durante la conversión: {"workers": N}. Se relee solo si cambia su fecha.
class Control {
public:
    explicit Control(std::filesystem::path path) : path_(std::move(path)) {}
    // Procesos pedidos, o 0 si no hay cambio.
    int workers() {
        if (path_.empty()) return 0;
        std::error_code ec;
        const auto stamp = std::filesystem::last_write_time(path_, ec);
        if (ec || stamp == stamp_) return 0;
        stamp_ = stamp;
        simdjson::ondemand::parser parser;
        auto json = simdjson::padded_string::load(path_.string());
        if (json.error()) return 0;
        simdjson::ondemand::document document;
        if (parser.iterate(json.value_unsafe()).get(document)) return 0;
        int64_t n = 0;
        if (document.find_field_unordered("workers").get_int64().get(n)) return 0;
        return int(std::clamp<int64_t>(n, 1, MaxWorkers));
    }
private:
    std::filesystem::path path_;
    std::filesystem::file_time_type stamp_{};
};

struct FrameResult {
    bool replaced = false;   // frame dañado o ausente, sustituido por negro
    std::string reason;      // por qué (solo si replaced)
    std::optional<SourceInfo> info;   // metadatos del frame (ISO, balance…) si se leyó bien
    std::vector<uint8_t> chunk;
    EncodeStats stats;
    int q = 0;
    double noiseLevelY = 0, noiseLevelC = 0;   // ruido de entrada estimado (diagnóstico y calibración)
    uint16_t noiseY = 0, noiseC = 0;           // campos de síntesis escritos
    std::exception_ptr error;
    GyroFrame gyro;   // bloque FSG2 y etiquetas del DNG (si se piden)
    double tWait = 0, tRead = 0, tDevelop = 0, tNoise = 0, tEncode = 0;   // solo con CDNG_PROFILE_LOG
};

// Ruido del descodificador según la resolución (14 nov 2026). La tabla del Video Assist corresponde al grano de la
// toma con un cuarteto Bayer por píxel (en las cámaras con demosaico parcial; en las demás, un píxel de la fuente). Si
// se reduce por debajo, cada píxel de salida promedia (referencia / salida)² de ellos y el ruido baja como el lado:
// la escala es salida / referencia, la reducción teórica del ruido blanco, sin pasar de 1. Con el recorte centrado
// de la salida (resizeLinear). En la fp a 6K, Full HD es 0,64 y 4K y 3K, 1.
// La escala se aplica al ruido que sale del descodificador, no al campo, que no es proporcional: el campo es el que da
// la escala por el ruido del de la tabla (decoderNoise, medido con el SDK).
struct NoiseResolution {
    double scale = 1;
};
NoiseResolution noiseResolution(const Engine &engine, Route route, std::pair<int, int> source, int width, int height) {
    const double crop = std::min(double(source.first), double(source.second) * width / height);   // ancho recortado
    const double reference = engine.map(route).channelCodec() ? crop / 2 : crop;
    return {std::min(1.0, width / reference)};
}

}  // namespace

int convert(const std::filesystem::path &requestPath) {
    const auto root = resourceRoot();
    checkResources(root);
    applyNoiseThreads();
    const Request request = readRequest(requestPath);
    const auto folder = std::filesystem::weakly_canonical(request.input);
    const auto output = std::filesystem::absolute(request.output);
    auto check = [&] { if (!request.cancel.empty() && std::filesystem::exists(request.cancel)) throw Cancelled{}; };
    check();
    if (lower(output.extension().string()) != ".braw") throw Error(t("The output file must have the .braw extension."));
    if (std::filesystem::exists(std::filesystem::symlink_status(output)))
        throw Error(t("The destination exists. Choose a new name; files are never overwritten."));
    if (!std::filesystem::is_directory(output.parent_path())) throw Error(t("The destination folder does not exist."));
    auto gcsvPath = output, fsg2Path = output, projectPath = output;
    gcsvPath.replace_extension(".gcsv"); fsg2Path.replace_extension(".fsg2"); projectPath.replace_extension(".gyroflow");
    for (const auto &[wanted, sidecar] : {std::pair{request.gyroGcsv, gcsvPath}, std::pair{request.gyroFsg2, fsg2Path},
                                          std::pair{request.gyroProject, projectPath}})
        if (wanted && std::filesystem::exists(std::filesystem::symlink_status(sidecar)))
            throw Error(t("The destination exists. Choose a new name; files are never overwritten."));
    const bool wantGyro = request.gyroGcsv || request.gyroFsg2 || request.gyroBraw || request.gyroProject;
    const auto files = sequence(folder);
    emit("status", json::Object().str("message", t("Preparing the conversion…")));
    trace::open(output.parent_path() / (output.stem().string() + ".pipeline-trace.txt"), root, folder, output);
    const Engine engine(root);

    // Sin pasada previa por toda la toma (en el NAS costaba minutos y leía un tercio de cada DNG): el primer frame
    // legible fija la cadencia, la cámara y el tamaño, y cada frame se compara con él al revelarlo. Un frame ausente,
    // ilegible o que no encaja se sustituye por negro; si pasan de MaxReplacedShare de la toma, se para.
    std::optional<SourceInfo> reference;
    std::optional<Rate> rate;
    std::size_t firstGood = files.size();
    std::string firstError;
    for (std::size_t i = 0; i < files.size() && !reference && double(i) <= MaxReplacedShare * double(files.size()); ++i) {
        check();
        if (files[i].empty()) continue;
        try {
            const Dng dng(files[i], true);
            auto info = sourceInfo(dng, engine.og);
            rate = dng.frameRate();
            if (!rate) throw Error(t("The DNG does not declare a FrameRate."));
            reference = std::move(info); firstGood = i;
        } catch (const std::exception &error) {
            if (firstError.empty()) firstError = files[i].filename().string() + ": " + t(error.what());
        }
    }
    if (!reference) throw Error(firstError.empty() ? t("No readable DNG at the start of the take.") : firstError);
    const auto referenceCrop = reference->defaultCrop;
    std::optional<std::pair<int, int>> dimensions = referenceCrop;
    const Route route = reference->route;
    if (!dimensions) {
        const auto [w, h] = Dng(files[firstGood]).rawSize();
        dimensions = std::pair<int, int>{w / 2 * 2, h / 2 * 2};
    }
    const auto outputDimensions = outputSize(request.width, request.height, *dimensions);
    const int width = outputDimensions.first, height = outputDimensions.second;
    const Geometry geometry(width, height);
    const std::optional<int> q = request.options.q;
    const std::size_t target = q ? 0 : std::size_t(double(width) * height * 12 / 8 / request.options.ratio);
    // Con CDNG_NOISE_SYNTH (diagnóstico: valores fijos o adaptativos) no se escala.
    const NoiseResolution noise = request.options.noiseSynthesis && !std::getenv("CDNG_NOISE_SYNTH")
                                      ? noiseResolution(engine, route, *dimensions, width, height) : NoiseResolution{};
    std::optional<Pcm24> pcm;
    if (request.audio) {
        std::vector<std::filesystem::path> wavs;
        for (const auto &entry : std::filesystem::directory_iterator(folder))
            if (entry.is_regular_file() && lower(entry.path().extension().string()) == ".wav") wavs.push_back(entry.path());
        std::sort(wavs.begin(), wavs.end()); wavs.erase(std::unique(wavs.begin(), wavs.end()), wavs.end());   // SMB repite entradas (ver sequence)
        if (wavs.size() > 1) throw Error(t("There are multiple WAV files; keep only the matching audio or turn audio off."));
        if (!wavs.empty()) {
            pcm = readWavPcm24(wavs.front());
            if (pcm->rate != 48000) throw Error(t("This version supports PCM WAV audio at 48 kHz."));
        }
    }
    check();
    // Timecode del primer frame de la toma: el del primer frame bueno menos su posición.
    const Dng firstDng(files[firstGood], true);
    // Identidad del BRAW en la ruta va: la de la cámara que grabó, salvo que se pida otra (el Video Assist escribe
    // «SIGMA fp L» también con una fp, y quien mezcle tomas suyas puede querer la misma).
    const std::string camera = request.camera != "auto" ? request.camera : vaCameraFor(firstDng.cameraName());
    const bool dropFrame = firstDng.timecodeDropFrame();
    const long long timecode = std::max<long long>(0, firstDng.timecodeFrames(nominalFps(*rate)).value_or(0) - (long long)firstGood);
    const int workers = request.options.workers;
    emit("started", json::Object()
        .integer("total", (long long)files.size()).integer("width", width).integer("height", height).integer("workers", workers)
        .num("fps", double(rate->num) / rate->den).str("compression", q ? "quality" : "ratio")
        .raw("q", q ? std::to_string(*q) : "null").num("ratio", request.options.ratio)
        .num("noise_scale", std::round(noise.scale * 100) / 100)
        .raw("nr", "[" + json::number(request.options.nrLuma) + ", " + json::number(request.options.nrChroma) + "]")
        .raw("noise_synthesis", request.options.noiseSynthesis ? "true" : "false")
        .str("build", trace::buildId()).str("trace", trace::path().string()));
    const auto start = std::chrono::steady_clock::now();
    auto seconds = [&] { return std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count(); };

    const auto header = geometry.header(engine.tpl.header), gap = geometry.gap(engine.tpl.gap);
    Profile profile;
    profile.open();
    TempDir temp(workParent(output, request.work));
    const auto stage = temp.path / "clip.braw";
    EncodeStats total;
    std::size_t videoBytes = 0;
    int qMin = QMax, qMax = 0;
    std::vector<std::size_t> replaced;   // posiciones (desde 1) sustituidas por negro
    std::string firstBad;
    std::optional<SourceInfo> lastGood = reference;
    std::vector<GyroFrame> gyro(wantGyro ? files.size() : 0);
    // Geometría para el giroscopio: el recorte centrado de resizeLinear cuando cambia la proporción.
    const auto gyroGeometry = [&] {
        GyroGeometry g;
        g.sourceWidth = dimensions->first; g.sourceHeight = dimensions->second; g.width = width; g.height = height;
        g.rate = *rate; g.littleEndian = Dng(files[firstGood], true).littleEndian();
        g.cropWidth = g.sourceWidth; g.cropHeight = g.sourceHeight;
        if ((long long)g.sourceWidth * height != (long long)g.sourceHeight * width) {
            if ((long long)g.sourceWidth * height > (long long)g.sourceHeight * width) {
                g.cropWidth = int(std::nearbyint(double(g.sourceHeight) * width / height)); g.cropX = (g.sourceWidth - g.cropWidth) / 2;
            } else {
                g.cropHeight = int(std::nearbyint(double(g.sourceWidth) * height / width)); g.cropY = (g.sourceHeight - g.cropHeight) / 2;
            }
        }
        return g;
    };
    std::vector<std::string> gyroProblems;   // lo que no se pudo escribir del giroscopio: la toma sigue adelante
    std::optional<BrawMotion> motion;        // el giroscopio que va dentro del BRAW
    {
        StreamMux mux(root / "model/cam/orig.braw", stage);
        // Hilos con a lo sumo `limit` frames por delante del último escrito: memoria acotada. Se arrancan MaxWorkers
        // hilos y solo trabajan los `limit` primeros: la app puede cambiar `limit` durante la conversión (Control).
        std::vector<FrameResult> results(files.size());
        std::vector<bool> ready(files.size(), false);
        std::mutex mutex; std::condition_variable changed;
        std::size_t next = 0, written = 0; bool stop = false;
        // Un hilo por proceso posible en esta máquina (o los pedidos, si son más): los que sobran esperan sin coste.
        const int slots = std::clamp(std::max(workers, int(std::thread::hardware_concurrency())), 1, MaxWorkers);
        int limit = workers;
        profile.header(folder, temp.path, output, workers, slots);
        Control control(request.control);
        auto poll = [&] {
            if (const int n = std::min(control.workers(), slots); n > 0 && n != limit) {
                { std::lock_guard<std::mutex> lock(mutex); limit = n; }
                changed.notify_all();
                emit("workers", json::Object().integer("workers", n));
            }
        };
        // CDNG_RATE=search recupera la búsqueda exacta del q en cada fotograma (lo de antes), para comparar.
        const char *rateMode = std::getenv("CDNG_RATE");
        const bool exhaustiveRate = rateMode && std::string(rateMode) == "search";
        auto work = [&](int slot) {
            // Punto de partida del control de tasa. Es por hilo a propósito: compartirlo entre hilos no ahorra
            // nada, porque los N primeros fotogramas se reparten antes de que ninguno termine y todos leerían el
            // mismo valor inicial (medido: 92 codificaciones para 12 fotogramas, con y sin compartir).
            int qLast = q.value_or(8);
            bool seeded = false;   // este hilo ya ha buscado su q de partida
            for (;;) {
                std::size_t index;
                const auto waitFrom = std::chrono::steady_clock::now();
                double waited = 0;
                {
                    std::unique_lock<std::mutex> lock(mutex);
                    changed.wait(lock, [&] { return stop || (slot < limit && next < files.size() && next < written + std::size_t(limit)); });
                    if (stop || next >= files.size()) return;
                    index = next++;
                    waited = since(waitFrom);
                }
                FrameResult result;
                result.tWait = waited;
                trace::setActive(index == firstGood);
                if (trace::active()) trace::line("--- frame " + std::to_string(index) + ": " + files[index].string());
                try {
                    Planes planes;
                    if (files[index].empty()) result.reason = t("missing from the numbering");
                    else try {
                        const auto readFrom = std::chrono::steady_clock::now();
                        const Dng dng(files[index]);
                        result.tRead = since(readFrom);
                        auto info = sourceInfo(dng, engine.og);
                        const auto frameRate = dng.frameRate();
                        if (!frameRate) throw Error(t("The DNG does not declare a FrameRate."));
                        if (frameRate->num != rate->num || frameRate->den != rate->den) throw Error(t("Frame rate changes within the sequence."));
                        if (info.route != route) throw Error(t("Camera changes within the sequence."));
                        if (info.defaultCrop != referenceCrop) throw Error(t("Resolution changes within the sequence."));
                        const auto developFrom = std::chrono::steady_clock::now();
                        planes = develop(engine, dng, info, width, height);
                        result.tDevelop = since(developFrom);
                        result.info = std::move(info);
                        if (wantGyro) result.gyro = gyroFrame(dng);
                    } catch (const std::exception &error) {
                        result.reason = t(error.what());
                    }
                    result.replaced = !result.reason.empty();
                    if (result.replaced) {
                        RgbImage black(width, height);   // ceros = negro del sensor, por el mismo mapa de la ruta
                        planes = imagePlanes(black, engine.map(route));
                    } else {
                        const auto noiseFrom = std::chrono::steady_clock::now();
                        finishPlanes(planes, engine.map(route), route, request.options.nrLuma / 100, request.options.nrChroma / 100);
                        result.tNoise = since(noiseFrom);
                        if (trace::active()) {
                            trace::line("finishPlanes(): nr luma " + std::to_string(request.options.nrLuma) + " chroma " + std::to_string(request.options.nrChroma));
                            trace::planes("after finishPlanes (before rounding)", planes);
                        }
                    }
                    if (trace::active() && result.replaced) trace::line("frame replaced with black: " + result.reason);
                    const auto encodeFrom = std::chrono::steady_clock::now();
                    const auto canvas = canvasPlanes(planes, geometry);
                    if (trace::active()) {
                        trace::canvas("canvasPlanes (deblock precompensation + rounded/clamped, input to the codec)", canvas, geometry);
                        trace::canvas("canvasPlanes without deblock precompensation", canvasPlanes(planes, geometry, false), geometry);
                        trace::line("--- end of traced frame");
                        trace::setActive(false);
                    }
                    // Diagnóstico: CDNG_DUMP_CANVAS=fichero vuelca los planos del primer fotograma en el lienzo, sin la
                    // precompensación del filtro antibloques (lo que el decodificador debe devolver), con el formato de
                    // brawdec --decoded-out (Y u16, Cb y Cr i16), para medir la pérdida de la compresión.
                    if (const char *dump = std::getenv("CDNG_DUMP_CANVAS"); dump && index == 0) {
                        const auto plain = canvasPlanes(planes, geometry, false);
                        std::ofstream out(dump, std::ios::binary);
                        out.write(reinterpret_cast<const char *>(plain.y.data()), std::streamsize(plain.y.size() * 2));
                        out.write(reinterpret_cast<const char *>(plain.cb.data()), std::streamsize(plain.cb.size() * 2));
                        out.write(reinterpret_cast<const char *>(plain.cr.data()), std::streamsize(plain.cr.size() * 2));
                    }
                    // En modo relación, el q se busca una sola vez por hilo —en su primer fotograma— y a partir de
                    // ahí va una única codificación por fotograma, ajustando el q del siguiente con el tamaño que
                    // haya dado este. La relación se cumple de media a lo largo del clip y no fotograma a
                    // fotograma. Buscarlo en todos costaba 2,17 codificaciones de media (medido en una toma real)
                    // y la segunda casi nunca servía: era la sonda de «¿cabe un q más fino?». Partir en frío sin
                    // buscar tampoco vale: con N procesos, cada uno tarda varios fotogramas en converger y
                    // mientras tanto entrega menos datos de los que la relación permite, o sea peor calidad.
                    const bool search = exhaustiveRate || !seeded;
                    auto frame = q ? engine.codec.encodeFixed(canvas, geometry, *q)
                                   : search ? engine.codec.encodeRateControlled(canvas, geometry, target, qLast)
                                            : engine.codec.encodeFixed(canvas, geometry, qLast);
                    seeded = true;
                    if (!request.options.noiseSynthesis) frame.noiseY = frame.noiseC = 0;
                    else if (noise.scale < 1) {
                        frame.noiseY = decoderNoiseField(noise.scale * decoderNoise(frame.noiseY, false), false);
                        frame.noiseC = decoderNoiseField(noise.scale * decoderNoise(frame.noiseC, true), true);
                    }
                    qLast = q || search ? frame.q : Codec::nextQuantiser(frame.q, frame.size, target);
                    result.chunk = frameChunk(header, gap, frame); result.stats = frame.stats; result.q = frame.q;
                    result.noiseLevelY = frame.noiseLevelY; result.noiseLevelC = frame.noiseLevelC; result.noiseY = frame.noiseY; result.noiseC = frame.noiseC;
                    result.tEncode = since(encodeFrom);
                } catch (...) {
                    result.error = std::current_exception();
                }
                std::lock_guard<std::mutex> lock(mutex);
                results[index] = std::move(result); ready[index] = true;
                changed.notify_all();
            }
        };
        std::vector<std::thread> threads;
        for (int i = 0; i < slots; ++i) threads.emplace_back(work, i);
        auto halt = [&] {
            { std::lock_guard<std::mutex> lock(mutex); stop = true; }
            changed.notify_all();
            for (auto &thread : threads) if (thread.joinable()) thread.join();
        };
        try {
            for (std::size_t index = 0; index < files.size(); ++index) {
                FrameResult result;
                const auto orderFrom = std::chrono::steady_clock::now();
                double ordered = 0;
                {
                    std::unique_lock<std::mutex> lock(mutex);
                    while (!ready[index]) {
                        changed.wait_for(lock, std::chrono::milliseconds(200));
                        lock.unlock(); check(); poll(); lock.lock();
                    }
                    result = std::move(results[index]);
                    ordered = since(orderFrom);
                }
                if (result.error) std::rethrow_exception(result.error);
                // Metadatos de un frame en negro: los del último frame bueno (o los de la referencia, al principio).
                if (result.replaced) {
                    replaced.push_back(index + 1);
                    if (firstBad.empty()) firstBad = (files[index].empty() ? format(t("frame {n}"), {{"n", std::to_string(index + 1)}}) : files[index].filename().string()) + ": " + result.reason;
                    if (double(replaced.size()) > MaxReplacedShare * double(files.size()))
                        throw Error(format(t("{count} damaged or missing frames (more than 10 % of the take). First: {name}"),
                                           {{"count", std::to_string(replaced.size())}, {"name", firstBad}}));
                } else lastGood = std::move(result.info);
                if (wantGyro) gyro[index] = std::move(result.gyro);
                const auto &info = *lastGood;
                FrameMeta meta;
                meta.iso = info.iso; meta.kelvin = info.kelvin.value_or(5600); meta.tint = info.tint.value_or(10);
                meta.rate = *rate; meta.route = route; meta.camera = camera;
                const auto writeFrom = std::chrono::steady_clock::now();
                mux.addFrame(result.chunk, meta);
                const double wrote = since(writeFrom);
                profile.frame(index, result.tWait, result.tRead, result.tDevelop, result.tNoise, result.tEncode, ordered, wrote,
                              result.stats.attempts, result.q);
                videoBytes += result.chunk.size();
                total.fallback += result.stats.fallback; total.clamped += result.stats.clamped; total.dropped += result.stats.dropped;
                qMin = std::min(qMin, result.q); qMax = std::max(qMax, result.q);
                { std::lock_guard<std::mutex> lock(mutex); ++written; }
                changed.notify_all();
                poll();
                emit("progress", json::Object().integer("done", (long long)index + 1).integer("total", (long long)files.size()).integer("workers", limit)
                                     .integer("q", result.q).num("seconds", seconds())
                                     .raw("noise_level", "[" + json::number(result.noiseLevelY) + ", " + json::number(result.noiseLevelC) + "]")
                                     .raw("noise", "[" + std::to_string(result.noiseY) + ", " + std::to_string(result.noiseC) + "]"));
            }
        } catch (...) {
            halt();
            throw;
        }
        halt();
        check();
        if (request.gyroBraw && hasGyro(gyro)) {
            try {
                motion = brawMotion(gyro, gyroGeometry(), files.size());
                mux.setMotion(motion->samples);
            } catch (const std::exception &error) {
                motion.reset();
                gyroProblems.push_back(output.filename().string() + ": " + error.what());
            }
        }
        mux.finish(timecode, pcm ? &*pcm : nullptr, *rate, dropFrame);
    }
    check();
    emit("status", json::Object().str("message", t("Finalising the BRAW container…")));
    ClipExtras extras;
    if (motion) extras.lineTimeUs = motion->lineTimeUs;
    if (hasGyro(gyro)) extras.lens = lensModel(gyro);
    // Identidad de la toma, en lugar de los valores neutros de la plantilla: un camera_id nuevo (no hay forma de
    // saber de qué cámara sin publicar su número de serie), el nombre de la carpeta y la fecha de grabación del DNG.
    extras.cameraId = randomUuid();
    extras.clipNumber = folder.filename().string();
    extras.date = recordingDate(firstDng);
    finalizeClip(stage, geometry, route, route == Route::Og ? &engine.og : nullptr, camera, extras);
    // Validación estructural antes de publicar, sin necesitar el SDK.
    if (videoSamples(stage).size() != files.size()) throw Error(t("The container does not contain every frame."));
    check();
    syncFile(stage);
    if (std::error_code ignored; !std::filesystem::equivalent(temp.path.parent_path(), output.parent_path(), ignored))
        emit("status", json::Object().str("message", t("Copying the BRAW to the destination folder…")));
    if (std::string why; !deliver(stage, output, why)) {
        // Un BRAW terminado no se tira nunca: sale de la carpeta temporal (que se borra al terminar) con un nombre
        // visible junto al destino o, si no cabe allí, en el temporal del sistema, para no perder horas de proceso.
        auto kept = output; kept.replace_filename(output.stem().string() + ".unpublished" + output.extension().string());
        std::error_code ec;
        if (!std::filesystem::exists(kept, ec)) {
            std::error_code cleanup;
            std::filesystem::rename(stage, kept, ec);
            if (ec) std::filesystem::copy_file(stage, kept, ec);
            if (ec) {
                std::filesystem::remove(kept, cleanup);
                kept = temp.path.parent_path() / kept.filename();
                if (!std::filesystem::exists(kept, cleanup)) std::filesystem::rename(stage, kept, ec);
            }
        }
        if (!ec && std::filesystem::exists(kept))
            throw Error(format(t("Could not publish the BRAW ({reason}). The finished file was kept as {path}"), {{"reason", why}, {"path", kept.string()}}));
        throw Error(format(t("Could not publish the BRAW ({reason})."), {{"reason", why}}));
    }
    // Giroscopio Gyro2: después del BRAW, que es lo valioso; si falla, la toma no se pierde y se avisa.
    json::Object gyroResult;
    if (motion) {
        gyroResult.boolean("braw", true);
        if (!motion->note.empty()) gyroResult.str("note", motion->note);
    }
    if (wantGyro && hasGyro(gyro)) {
        const GyroGeometry g = gyroGeometry();
        const auto brawName = output.filename().string();
        const auto side = [&](bool wanted, const std::filesystem::path &dest, const char *key, auto &&write) {
            if (!wanted) return false;
            const auto staged = temp.path / dest.filename();
            try {
                write(staged);
                if (std::string why; !deliver(staged, dest, why)) throw Error(why);
                gyroResult.str(key, dest.string());
                return true;
            } catch (const std::exception &error) {
                gyroProblems.push_back(dest.filename().string() + ": " + error.what());
                return false;
            }
        };
        const bool gcsv = side(request.gyroGcsv, gcsvPath, "gcsv", [&](const std::filesystem::path &at) {
            // La misma nota que la del giroscopio del BRAW: se da una vez.
            if (auto note = writeGcsv(at, gyro, g, brawName); !note.empty() && !motion) gyroResult.str("note", note);
        });
        side(request.gyroFsg2, fsg2Path, "fsg2", [&](const std::filesystem::path &at) { writeFsg2(at, gyro, g, brawName); });
        // Proyecto de Gyroflow: el perfil del objetivo y el giroscopio del BRAW (o del .gcsv si no va dentro).
        if (request.gyroProject) {
            std::string why;
            const auto lens = lensFit(gyro, g, why);
            const auto source = motion ? output : gcsv ? gcsvPath : std::filesystem::path();
            if (!lens) gyroResult.str("lens", why);
            else if (source.empty()) gyroResult.str("lens", "sin giroscopio para el proyecto");
            else side(true, projectPath, "gyroflow", [&](const std::filesystem::path &at) {
                writeGyroflowProject(at, output, source, files.size(), g, *lens, motion ? motion->readoutMs : 0);
            });
        }
    }
    if (!gyroProblems.empty()) {
        std::string all;
        for (const auto &p : gyroProblems) all += (all.empty() ? "" : "; ") + p;
        gyroResult.str("error", all);
    }
    const double achieved = double(width) * height * 12 / 8 * files.size() / std::max<std::size_t>(videoBytes, 1);
    emit("completed", json::Object()
        .raw("gyro", gyroResult.text() == "{}" ? "null" : gyroResult.text())
        .str("output", output.string()).integer("frames", (long long)files.size())
        .integer("bytes", (long long)std::filesystem::file_size(output)).num("seconds", seconds())
        .object("stats", json::Object().integer("fallback_rk", (long long)total.fallback).integer("clamped", (long long)total.clamped)
                             .integer("dropped", (long long)total.dropped))
        .raw("ratio_requested", q ? "null" : json::number(request.options.ratio))
        .num("noise_scale", std::round(noise.scale * 100) / 100)
        .num("ratio_achieved", std::round(achieved * 100) / 100).integer("q_min", qMin).integer("q_max", qMax)
        .integer("q_ceiling", QMax)
        .raw("replaced_frames", [&] { std::string a = "["; for (auto n : replaced) a += (a.size() > 1 ? ", " : "") + std::to_string(n); return a + "]"; }()));
    return 0;
}

}  // namespace cdng::worker
