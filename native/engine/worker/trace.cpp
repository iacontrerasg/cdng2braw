#include "worker.hpp"
#include "signal_internal.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <ctime>
#include <fstream>
#include <iostream>
#include <limits>
#include <sstream>

#ifdef __APPLE__
#include <mach-o/dyld.h>
#endif

#ifndef CDNG_BUILD_COMMIT
#define CDNG_BUILD_COMMIT "unknown"
#endif
#ifndef CDNG_BUILD_BRANCH
#define CDNG_BUILD_BRANCH "unknown"
#endif

namespace cdng::worker::trace {

namespace {

std::mutex mutex;
std::ofstream file;
std::filesystem::path filePath;
thread_local bool tracing = false;

std::filesystem::path executablePath() {
#ifdef __APPLE__
    uint32_t size = 0;
    _NSGetExecutablePath(nullptr, &size);
    std::string buffer(size, '\0');
    if (_NSGetExecutablePath(buffer.data(), &size) == 0) {
        std::error_code ec;
        const auto path = std::filesystem::canonical(buffer.c_str(), ec);
        return ec ? std::filesystem::path(buffer.c_str()) : path;
    }
#endif
    return {};
}

std::string fileSha(const std::filesystem::path &path) {
    try { return sha256Hex(readBytes(path)); } catch (const std::exception &error) { return std::string("error: ") + error.what(); }
}

std::string modified(const std::filesystem::path &path) {
    std::error_code ec;
    const auto time = std::filesystem::last_write_time(path, ec);
    if (ec) return "?";
    const auto system = std::chrono::time_point_cast<std::chrono::system_clock::duration>(
        time - std::filesystem::file_time_type::clock::now() + std::chrono::system_clock::now());
    const std::time_t t = std::chrono::system_clock::to_time_t(system);
    char text[32];
    std::strftime(text, sizeof text, "%Y-%m-%d %H:%M:%S", std::localtime(&t));
    return text;
}

std::string num(double v) {
    char text[40];
    std::snprintf(text, sizeof text, "%.9g", v);
    return text;
}

std::string percent(std::size_t part, std::size_t total) {
    char text[40];
    std::snprintf(text, sizeof text, "%.4f%%", total ? 100.0 * double(part) / double(total) : 0.0);
    return text;
}

constexpr double Percentiles[] = {0.1, 1, 50, 99, 99.9, 99.99};

std::string summary(std::vector<float> values, const std::vector<double> &thresholds) {
    std::ostringstream out;
    if (values.empty()) return "empty";
    std::size_t nonFinite = 0;
    values.erase(std::remove_if(values.begin(), values.end(), [&](float v) { return !std::isfinite(v) && ++nonFinite; }), values.end());
    std::sort(values.begin(), values.end());
    const std::size_t n = values.size();
    out << "min " << num(values.front()) << "  max " << num(values.back());
    for (double p : Percentiles) {
        const auto i = std::min(n - 1, std::size_t(std::floor(p / 100 * double(n - 1) + 0.5)));
        out << "  p" << num(p) << " " << num(values[i]);
    }
    for (double t : thresholds) {
        const auto at = std::size_t(values.end() - std::lower_bound(values.begin(), values.end(), float(t)));
        out << "  >=" << num(t) << ": " << at << " (" << percent(at, n) << ")";
    }
    if (nonFinite) out << "  non-finite " << nonFinite;
    return out.str();
}

}  // namespace

std::string buildId() {
    return std::string(CDNG_BUILD_COMMIT) + " (" + CDNG_BUILD_BRANCH + ", compiled " + __DATE__ + " " + __TIME__ + ")";
}

void open(const std::filesystem::path &path, const std::filesystem::path &root, const std::filesystem::path &input,
          const std::filesystem::path &output) {
    std::lock_guard<std::mutex> lock(mutex);
    filePath = path;
    file.open(path, std::ios::out | std::ios::trunc);
    std::ostringstream h;
    const auto now = std::chrono::system_clock::to_time_t(std::chrono::system_clock::now());
    char stamp[32];
    std::strftime(stamp, sizeof stamp, "%Y-%m-%d %H:%M:%S", std::localtime(&now));
    const auto exe = executablePath();
    const auto signal = root / "model/pocket_og_cinemadng_signal.json";
    h << "# cdng2braw pipeline trace\n"
      << "time: " << stamp << "\n"
      << "build: " << buildId() << "\n"
      << "executable: " << exe.string() << "\n"
      << "executable_modified: " << modified(exe) << "\n"
      << "executable_sha256: " << fileSha(exe) << "\n"
      << "resources: " << root.string() << "\n"
      << "og_signal_json: " << signal.string() << "\n"
      << "og_signal_json_sha256: " << fileSha(signal) << "\n"
      << "input: " << input.string() << "\n"
      << "output: " << output.string() << "\n"
      << "output_existed_before: " << (std::filesystem::exists(output) ? "yes" : "no") << "\n";
    file << h.str() << std::flush;
    std::cerr << h.str() << std::flush;
}

std::filesystem::path path() { return filePath; }
bool active() { return tracing; }
void setActive(bool on) { tracing = on; }

void line(const std::string &text) {
    if (!tracing) return;
    std::lock_guard<std::mutex> lock(mutex);
    if (file.is_open()) file << text << "\n" << std::flush;
    std::cerr << "[trace] " << text << "\n" << std::flush;
}

void image(const std::string &label, const float *data, std::size_t pixels, int channels, const std::vector<double> &thresholds) {
    if (!tracing) return;
    static const char *names[] = {"R", "G", "B"};
    line(label + ": " + std::to_string(pixels) + " px × " + std::to_string(channels) + " ch");
    for (int c = 0; c < channels; ++c) {
        std::vector<float> values(pixels);
        for (std::size_t i = 0; i < pixels; ++i) values[i] = data[i * channels + c];
        line(std::string("  ") + (channels == 3 ? names[c] : "S") + ": " + summary(std::move(values), thresholds));
    }
}

void planes(const std::string &label, const Planes &p) {
    if (!tracing) return;
    line(label + ": Y " + std::to_string(p.width) + "×" + std::to_string(p.height) + ", Cb/Cr " +
         std::to_string(p.width / 2) + "×" + std::to_string(p.height / 2));
    line("  Y:  " + summary(p.y, {4094.5, 4095, 4095.5}));
    std::vector<float> negative(p.y.size());
    for (std::size_t i = 0; i < p.y.size(); ++i) negative[i] = -p.y[i];
    line("  Y<0 (as -Y): " + summary(std::move(negative), {0.5}));
    line("  Cb: " + summary(p.cb, {4095}));
    line("  Cr: " + summary(p.cr, {4095}));
    // Códigos por canal reconstruidos con code_from_ycc de la ruta og (R = Y − Cb/2 + 3Cr/2, G = Y − Cb/2 − Cr/2,
    // B = Y + 3Cb/2 − Cr/2), con la croma de la media 2×2.
    std::vector<float> rgb[3];
    const int hw = p.width / 2;
    for (auto &v : rgb) v.reserve(p.y.size());
    for (int y = 0; y < p.height; ++y)
        for (int x = 0; x < p.width; ++x) {
            const double Y = p.y[std::size_t(y) * p.width + x];
            const double cb = p.cb[std::size_t(y / 2) * hw + x / 2], cr = p.cr[std::size_t(y / 2) * hw + x / 2];
            rgb[0].push_back(float(Y - cb / 2 + 1.5 * cr));
            rgb[1].push_back(float(Y - cb / 2 - cr / 2));
            rgb[2].push_back(float(Y + 1.5 * cb - cr / 2));
        }
    line("  code R: " + summary(std::move(rgb[0]), {4094.5, 4095.5}));
    line("  code G: " + summary(std::move(rgb[1]), {4094.5, 4095.5}));
    line("  code B: " + summary(std::move(rgb[2]), {4094.5, 4095.5}));
}

void canvas(const std::string &label, const CanvasPlanes &c, const Geometry &g) {
    if (!tracing) return;
    line(label + ": lienzo " + std::to_string(c.ws) + "×" + std::to_string(c.hs) + ", región entregada " +
         std::to_string(g.w) + "×" + std::to_string(g.h) + " en (" + std::to_string(g.x0) + ", " + std::to_string(g.y0) + ")");
    std::vector<float> y, cb, cr, rgb[3];
    std::size_t yMax = 0, yZero = 0, cLimit = 0;
    for (int r = 0; r < g.h; ++r)
        for (int x = 0; x < g.w; ++x) {
            const int16_t Y = c.y[std::size_t(g.y0 + r) * c.ws + g.x0 + x];
            const std::size_t ci = std::size_t((g.y0 + r) / 2) * (c.ws / 2) + (g.x0 + x) / 2;
            const double B = c.cb[ci], R = c.cr[ci];
            y.push_back(Y); yMax += Y == 4095; yZero += Y == 0;
            rgb[0].push_back(float(Y - B / 2 + 1.5 * R));
            rgb[1].push_back(float(Y - B / 2 - R / 2));
            rgb[2].push_back(float(Y + 1.5 * B - R / 2));
        }
    for (int r = 0; r < g.h / 2; ++r)
        for (int x = 0; x < g.w / 2; ++x) {
            const std::size_t ci = std::size_t(g.y0 / 2 + r) * (c.ws / 2) + g.x0 / 2 + x;
            cb.push_back(c.cb[ci]); cr.push_back(c.cr[ci]);
            cLimit += c.cb[ci] == 4095 || c.cb[ci] == -4096 || c.cr[ci] == 4095 || c.cr[ci] == -4096;
        }
    line("  Y == 4095 (recortado por canvasPlanes/rounded): " + std::to_string(yMax) + " (" + percent(yMax, y.size()) + ")");
    line("  Y == 0: " + std::to_string(yZero) + " (" + percent(yZero, y.size()) + ")");
    line("  Cb/Cr en el límite ±4096/4095: " + std::to_string(cLimit));
    line("  Y:  " + summary(std::move(y), {4095}));
    line("  Cb: " + summary(std::move(cb), {}));
    line("  Cr: " + summary(std::move(cr), {}));
    line("  code R: " + summary(std::move(rgb[0]), {4094.5, 4095.5}));
    line("  code G: " + summary(std::move(rgb[1]), {4094.5, 4095.5}));
    line("  code B: " + summary(std::move(rgb[2]), {4094.5, 4095.5}));
}

}  // namespace cdng::worker::trace
