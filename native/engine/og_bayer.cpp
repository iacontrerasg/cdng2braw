#include "og_bayer.hpp"
#include "og_debayer.hpp"

#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <cstring>
#include <string>

namespace cdng {
namespace {

std::string lower(const char *v) {
    std::string s;
    if (!v) return s;
    for (; *v; ++v) s.push_back(char(std::tolower(static_cast<unsigned char>(*v))));
    return s;
}

int mirrorIndex(int x, int size) {
    if (size <= 1) return 0;
    const int period = 2 * (size - 1);
    x = (x % period + period) % period;
    return x < size ? x : period - x;
}

}  // namespace

OgBayerConfig ogBayerFromEnv() {
    OgBayerConfig c;
    const char *raw = std::getenv("CDNG_OG_BAYER");
    if (!raw || !*raw) return c;
    c.fromEnv = true;
    const std::string m = lower(raw);
    if (m == "1" || m == "on" || m == "true" || m == "yes") {
        c.enabled = true;
        return c;
    }
    if (m == "0" || m == "off" || m == "false" || m == "no") {
        c.enabled = false;
        return c;
    }
    throw Error("CDNG_OG_BAYER: unsupported value '" + std::string(raw) + "' (use 1|on or 0|off)");
}

void ogBayerRejectIfNotOg(Route route) {
    const OgBayerConfig c = ogBayerFromEnv();
    if (c.fromEnv && route != Route::Og)
        throw Error("CDNG_OG_BAYER is only supported on Pocket OG");
    if (c.enabled) {
        const OgDebayerConfig d = ogDebayerFromEnv();
        if (d.fromEnv)
            throw Error("CDNG_OG_BAYER and CDNG_OG_DEBAYER are mutually exclusive");
    }
}

RgbImage bayerReconstructLinear(const BayerImage &bayer) {
    const int w = bayer.width, h = bayer.height;
    if (w < 4 || h < 4 || w % 2 || h % 2)
        throw Error("bayerReconstructLinear needs an even mosaic at least 4×4");
    if (int(bayer.samples.size()) != w * h || bayer.colors.size() != bayer.samples.size())
        throw Error("bayerReconstructLinear: mosaic size mismatch");
    const auto at = [&](int y, int x) {
        return double(bayer.samples[std::size_t(mirrorIndex(y, h)) * w + mirrorIndex(x, w)]);
    };
    const auto col = [&](int y, int x) {
        return int(bayer.colors[std::size_t(mirrorIndex(y, h)) * w + mirrorIndex(x, w)]);
    };
    auto sampleChannel = [&](int y, int x, int ch) {
        // bilinear: vecinos del mismo canal a ±1 o ±2 según paridad
        double sum = 0;
        int n = 0;
        for (int dy = -2; dy <= 2; ++dy) for (int dx = -2; dx <= 2; ++dx) {
            if (std::abs(dx) + std::abs(dy) == 0) continue;
            if (col(y + dy, x + dx) != ch) continue;
            if (std::abs(dx) > 2 || std::abs(dy) > 2) continue;
            // solo primeros vecinos del canal (distancia 1 en G cruz, 2 en mismo color)
            const int man = std::abs(dx) + std::abs(dy);
            if (ch == 1 && man != 1) continue;
            if (ch != 1 && man != 2 && man != 1) continue;
            sum += at(y + dy, x + dx);
            ++n;
        }
        return n ? sum / n : 0.0;
    };
    auto greenCubic = [&](int y, int x) {
        const double left = at(y, x - 1), right = at(y, x + 1);
        const double up = at(y - 1, x), down = at(y + 1, x);
        const double wh = 1 / (.005 + std::abs(left - right));
        const double wv = 1 / (.005 + std::abs(up - down));
        const double lo = std::min(std::min(left, right), std::min(up, down));
        const double hi = std::max(std::max(left, right), std::max(up, down));
        const double eh = std::clamp((-at(y, x - 3) + 9 * left + 9 * right - at(y, x + 3)) / 16, lo, hi);
        const double ev = std::clamp((-at(y - 3, x) + 9 * up + 9 * down - at(y + 3, x)) / 16, lo, hi);
        return (wh * eh + wv * ev) / (wh + wv);
    };
    RgbImage out(w, h);
    for (int y = 0; y < h; ++y) for (int x = 0; x < w; ++x) {
        const int c = col(y, x);
        const double s = at(y, x);
        double r = 0, g = 0, b = 0;
        if (c == 0) {
            r = s;
            g = greenCubic(y, x);
            b = sampleChannel(y, x, 2);
        } else if (c == 2) {
            b = s;
            g = greenCubic(y, x);
            r = sampleChannel(y, x, 0);
        } else {
            g = s;
            r = sampleChannel(y, x, 0);
            b = sampleChannel(y, x, 2);
        }
        float *p = out.px(x, y);
        p[0] = float(r);
        p[1] = float(g);
        p[2] = float(b);
    }
    return out;
}

}  // namespace cdng
