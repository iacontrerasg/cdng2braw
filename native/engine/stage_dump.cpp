#include "stage_dump.hpp"
#include "common.hpp"

#include <cstdlib>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <mutex>
#include <sstream>
#include <vector>

namespace cdng {
namespace {

std::mutex gMutex;
thread_local int gFrame = 0;
thread_local std::vector<std::string> gWritten;

const char *dumpDir() {
    const char *d = std::getenv("CDNG_DUMP_STAGES");
    return (d && *d) ? d : nullptr;
}

int dumpFrameWanted() {
    const char *v = std::getenv("CDNG_DUMP_FRAME");
    if (!v || !*v) return 0;
    return std::atoi(v);
}

std::filesystem::path frameDir() {
    const char *root = dumpDir();
    if (!root) return {};
    std::ostringstream name;
    name << "frame_" << gFrame;
    auto dir = std::filesystem::path(root) / name.str();
    std::filesystem::create_directories(dir);
    return dir;
}

void writeBytes(const std::filesystem::path &path, const void *data, std::size_t nbytes) {
    if (std::filesystem::exists(path))
        throw Error("CDNG_DUMP_STAGES: refusing to overwrite " + path.string() +
                    " (use a unique dump directory per conversion / rate-search attempt)");
    std::ofstream out(path, std::ios::binary);
    if (!out) throw Error("CDNG_DUMP_STAGES: cannot write " + path.string());
    out.write(reinterpret_cast<const char *>(data), std::streamsize(nbytes));
    if (!out) throw Error("CDNG_DUMP_STAGES: write failed " + path.string());
}

void writeSidecar(const std::filesystem::path &jsonPath, const std::string &body) {
    std::ofstream out(jsonPath);
    if (!out) throw Error("CDNG_DUMP_STAGES: cannot write " + jsonPath.string());
    out << body;
}

std::string jsonEscape(const std::string &s) {
    std::string o;
    o.reserve(s.size());
    for (char c : s) {
        if (c == '"' || c == '\\') { o.push_back('\\'); o.push_back(c); }
        else if (c == '\n') o += "\\n";
        else o.push_back(c);
    }
    return o;
}

void dumpBuffer(const char *stage, const char *plane, const void *data, std::size_t nbytes,
                const char *dtype, int w, int h, int channels, const char *layout,
                const char *domain, const char *units, const char *note,
                int originX = 0, int originY = 0) {
    if (!stageDumpThisFrame() || !data) return;
    std::lock_guard<std::mutex> lock(gMutex);
    const auto dir = frameDir();
    std::string stem = std::string(stage) + "_" + plane;
    auto bin = dir / (stem + ".bin");
    writeBytes(bin, data, nbytes);
    std::ostringstream js;
    js << "{\n"
       << "  \"stage\": \"" << jsonEscape(stage) << "\",\n"
       << "  \"plane\": \"" << jsonEscape(plane) << "\",\n"
       << "  \"path\": \"" << jsonEscape(bin.filename().string()) << "\",\n"
       << "  \"dtype\": \"" << dtype << "\",\n"
       << "  \"width\": " << w << ",\n"
       << "  \"height\": " << h << ",\n"
       << "  \"channels\": " << channels << ",\n"
       << "  \"layout\": \"" << layout << "\",\n"
       << "  \"nbytes\": " << nbytes << ",\n"
       << "  \"origin_x\": " << originX << ",\n"
       << "  \"origin_y\": " << originY << ",\n"
       << "  \"domain\": \"" << jsonEscape(domain ? domain : "") << "\",\n"
       << "  \"units\": \"" << jsonEscape(units ? units : "") << "\",\n"
       << "  \"endian\": \"little\",\n"
       << "  \"note\": \"" << jsonEscape(note ? note : "") << "\"\n"
       << "}\n";
    writeSidecar(dir / (stem + ".json"), js.str());
    gWritten.push_back(stem);
}

RgbImage loadBrwd(const std::filesystem::path &path) {
    std::ifstream in(path, std::ios::binary);
    if (!in) throw Error("CDNG_INJECT_RGB: cannot read " + path.string());
    char mag[4];
    in.read(mag, 4);
    if (!in || std::memcmp(mag, "BRWD", 4) != 0)
        throw Error("CDNG_INJECT_RGB: not BRWD " + path.string());
    uint32_t w = 0, h = 0, c = 0, planar = 0;
    in.read(reinterpret_cast<char *>(&w), 4);
    in.read(reinterpret_cast<char *>(&h), 4);
    in.read(reinterpret_cast<char *>(&c), 4);
    in.read(reinterpret_cast<char *>(&planar), 4);
    if (!in || c != 3 || w == 0 || h == 0)
        throw Error("CDNG_INJECT_RGB: bad header " + path.string());
    RgbImage img{int(w), int(h)};
    const std::size_t n = std::size_t(w) * h * 3;
    if (planar) {
        std::vector<float> planarBuf(n);
        in.read(reinterpret_cast<char *>(planarBuf.data()), std::streamsize(n * 4));
        if (!in) throw Error("CDNG_INJECT_RGB: truncated " + path.string());
        for (std::size_t i = 0; i < std::size_t(w) * h; ++i) {
            img.data[3 * i + 0] = planarBuf[i];
            img.data[3 * i + 1] = planarBuf[std::size_t(w) * h + i];
            img.data[3 * i + 2] = planarBuf[2 * std::size_t(w) * h + i];
        }
    } else {
        in.read(reinterpret_cast<char *>(img.data.data()), std::streamsize(n * 4));
        if (!in) throw Error("CDNG_INJECT_RGB: truncated " + path.string());
    }
    return img;
}

}  // namespace

bool stageDumpEnabled() { return dumpDir() != nullptr; }

void stageDumpSetFrame(int index) {
    gFrame = index;
    gWritten.clear();
}

bool stageDumpThisFrame() {
    return dumpDir() && gFrame == dumpFrameWanted();
}

std::optional<RgbImage> stageInjectRgb() {
    const char *p = std::getenv("CDNG_INJECT_RGB");
    if (!p || !*p) return std::nullopt;
    return loadBrwd(p);
}

std::string stageInjectAt() {
    const char *v = std::getenv("CDNG_INJECT_AT");
    if (!v || !*v) return "camera";
    const std::string s(v);
    if (s == "camera" || s == "pre_map") return s;
    throw Error("CDNG_INJECT_AT: unsupported value '" + s + "' (use camera|pre_map)");
}

void stageDumpBayer(const BayerImage &bayer, const char *note) {
    dumpBuffer("S0", "samples", bayer.samples.data(), bayer.samples.size() * sizeof(float),
               "float32", bayer.width, bayer.height, 1, "HxW",
               "linear_sensor_normalized", "(raw-black)/(white-black)", note);
    dumpBuffer("S0", "cfa", bayer.colors.data(), bayer.colors.size(),
               "uint8", bayer.width, bayer.height, 1, "HxW",
               "cfa_index", "0=R,1=G,2=B", note);
}

void stageDumpRgb(const char *stage, const RgbImage &rgb, const char *domain, const char *note) {
    dumpBuffer(stage, "rgb", rgb.data.data(), rgb.data.size() * sizeof(float),
               "float32", rgb.width, rgb.height, 3, "HxWx3_interleaved",
               domain, "camera_rgb", note);
}

void stageDumpYccFull(const char *stage, int w, int h, const float *y, const float *cb, const float *cr,
                      const char *note) {
    const std::size_t n = std::size_t(w) * h;
    dumpBuffer(stage, "Y", y, n * sizeof(float), "float32", w, h, 1, "HxW",
               "braw_ycbcr_full", "decoder_codes", note);
    dumpBuffer(stage, "Cb", cb, n * sizeof(float), "float32", w, h, 1, "HxW",
               "braw_ycbcr_full", "decoder_codes", note);
    dumpBuffer(stage, "Cr", cr, n * sizeof(float), "float32", w, h, 1, "HxW",
               "braw_ycbcr_full", "decoder_codes", note);
}

void stageDumpPlanes(const char *stage, const Planes &planes, const char *note) {
    dumpBuffer(stage, "Y", planes.y.data(), planes.y.size() * sizeof(float),
               "float32", planes.width, planes.height, 1, "HxW",
               "braw_ycbcr_subsampled", "decoder_codes", note, 0, 0);
    dumpBuffer(stage, "Cb", planes.cb.data(), planes.cb.size() * sizeof(float),
               "float32", planes.width / 2, planes.height / 2, 1, "HxW",
               "braw_ycbcr_4_2_0", "decoder_codes", note, 0, 0);
    dumpBuffer(stage, "Cr", planes.cr.data(), planes.cr.size() * sizeof(float),
               "float32", planes.width / 2, planes.height / 2, 1, "HxW",
               "braw_ycbcr_4_2_0", "decoder_codes", note, 0, 0);
}

void stageDumpWriteIndex(int q, int width, int height, const char *chroma_mode,
                         const char *kernel, const char *phase) {
    if (!stageDumpThisFrame()) return;
    std::lock_guard<std::mutex> lock(gMutex);
    const auto dir = frameDir();
    const char *label = std::getenv("CDNG_STATS_LABEL");
    const char *inject = std::getenv("CDNG_INJECT_RGB");
    const char *injectAt = std::getenv("CDNG_INJECT_AT");
    auto has = [](const char *stem) {
        for (const auto &s : gWritten) if (s == stem) return true;
        return false;
    };
    const char *encoder[] = {
        "S0_samples", "S0_cfa", "S1_rgb", "S2_rgb", "S3_rgb",
        "S4_Y", "S4_Cb", "S4_Cr", "S5_Y", "S5_Cb", "S5_Cr",
        "S6_pre_Y", "S6_pre_Cb", "S6_pre_Cr", "S6_post_Y", "S6_post_Cb", "S6_post_Cr",
        "S7_Y", "S7_Cb", "S7_Cr"
    };
    std::ostringstream js;
    js << "{\n"
       << "  \"frame_index\": " << gFrame << ",\n"
       << "  \"q\": " << q << ",\n"
       << "  \"width\": " << width << ",\n"
       << "  \"height\": " << height << ",\n"
       << "  \"og_chroma_mode\": \"" << jsonEscape(chroma_mode ? chroma_mode : "c0") << "\",\n"
       << "  \"og_chroma_kernel\": \"" << jsonEscape(kernel ? kernel : "") << "\",\n"
       << "  \"og_chroma_phase\": \"" << jsonEscape(phase ? phase : "center") << "\",\n"
       << "  \"stats_label\": \"" << jsonEscape(label ? label : "") << "\",\n"
       << "  \"inject_rgb\": \"" << jsonEscape(inject ? inject : "") << "\",\n"
       << "  \"inject_at\": \"" << jsonEscape(injectAt ? injectAt : "") << "\",\n"
       << "  \"stages_written\": [";
    for (std::size_t i = 0; i < gWritten.size(); ++i) {
        if (i) js << ", ";
        js << "\"" << jsonEscape(gWritten[i]) << "\"";
    }
    js << "],\n  \"stages_absent_encoder\": [";
    bool first = true;
    for (const char *s : encoder) {
        if (has(s)) continue;
        if (!first) js << ", ";
        first = false;
        js << "\"" << s << "\"";
    }
    js << "],\n"
       << "  \"stages_absent_external\": [\"S8_sdk_rgb\", \"S9_reference_exr\"],\n"
       << "  \"note\": \"S8/S9 are produced by brawdec/SDK and the EXR bank, not this encoder dump\"\n"
       << "}\n";
    writeSidecar(dir / "stages_index.json", js.str());
}

void stageDumpCanvas(const char *stage, const CanvasPlanes &canvas, const Geometry &g, const char *note) {
    dumpBuffer(stage, "Y", canvas.y.data(), canvas.y.size() * sizeof(int16_t),
               "int16", g.ws, g.hs, 1, "HxW",
               "canvas_int16", "Y_0_4095", note, g.x0, g.y0);
    dumpBuffer(stage, "Cb", canvas.cb.data(), canvas.cb.size() * sizeof(int16_t),
               "int16", g.ws / 2, g.hs / 2, 1, "HxW",
               "canvas_int16", "C_-4096_4095", note, g.x0 / 2, g.y0 / 2);
    dumpBuffer(stage, "Cr", canvas.cr.data(), canvas.cr.size() * sizeof(int16_t),
               "int16", g.ws / 2, g.hs / 2, 1, "HxW",
               "canvas_int16", "C_-4096_4095", note, g.x0 / 2, g.y0 / 2);
}

}  // namespace cdng
