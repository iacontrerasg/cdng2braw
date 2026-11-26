#include "og_chroma.hpp"
#include "common.hpp"

#include <cmath>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

namespace cdng {
namespace {

std::string lower(const char *v) {
    std::string s;
    if (!v) return s;
    for (; *v; ++v) s.push_back(char(std::tolower(static_cast<unsigned char>(*v))));
    return s;
}

void kernelTaps(OgChromaKernel k, std::vector<double> &taps) {
    switch (k) {
    case OgChromaKernel::B3:
        taps = {1, 2, 1};
        break;
    case OgChromaKernel::B5:
        taps = {1, 4, 6, 4, 1};
        break;
    case OgChromaKernel::B7:
        taps = {1, 6, 15, 20, 15, 6, 1};
        break;
    default:
        taps.clear();
        break;
    }
    double s = 0;
    for (double t : taps) s += t;
    if (s <= 0) throw Error("CDNG_OG_CHROMA_KERNEL: DC gain would be zero");
    for (double &t : taps) t /= s;
}

int clampi(int v, int n) { return v < 0 ? 0 : (v >= n ? n - 1 : v); }

void convSep(const float *in, float *out, int w, int h, const std::vector<double> &k) {
    const int r = int(k.size()) / 2;
    std::vector<float> tmp(std::size_t(w) * h);
    for (int y = 0; y < h; ++y) {
        for (int x = 0; x < w; ++x) {
            double acc = 0;
            for (int i = 0; i < int(k.size()); ++i)
                acc += k[std::size_t(i)] * in[std::size_t(y) * w + clampi(x + i - r, w)];
            tmp[std::size_t(y) * w + x] = float(acc);
        }
    }
    for (int y = 0; y < h; ++y) {
        for (int x = 0; x < w; ++x) {
            double acc = 0;
            for (int i = 0; i < int(k.size()); ++i)
                acc += k[std::size_t(i)] * tmp[std::size_t(clampi(y + i - r, h)) * w + x];
            out[std::size_t(y) * w + x] = float(acc);
        }
    }
}

void sample2x2(const float *full, int w, int h, std::vector<float> &half, OgChromaPhase phase) {
    const int hw = w / 2, hh = h / 2;
    half.resize(std::size_t(hw) * hh);
    for (int y = 0; y < hh; ++y) {
        for (int x = 0; x < hw; ++x) {
            const int x0 = 2 * x, y0 = 2 * y;
            float v;
            if (phase == OgChromaPhase::Even) {
                v = full[std::size_t(y0) * w + x0];
            } else if (phase == OgChromaPhase::Odd) {
                v = full[std::size_t(y0 + 1) * w + x0 + 1];
            } else {
                const double a = full[std::size_t(y0) * w + x0];
                const double b = full[std::size_t(y0) * w + x0 + 1];
                const double c = full[std::size_t(y0 + 1) * w + x0];
                const double d = full[std::size_t(y0 + 1) * w + x0 + 1];
                v = float(((a + b) + (c + d)) / 4);
            }
            half[std::size_t(y) * hw + x] = v;
        }
    }
}

}  // namespace

OgChromaConfig ogChromaConfigFromEnv() {
    OgChromaConfig c;
    const char *mode = std::getenv("CDNG_OG_CHROMA_MODE");
    const char *kern = std::getenv("CDNG_OG_CHROMA_KERNEL");
    const char *phase = std::getenv("CDNG_OG_CHROMA_PHASE");
    if (mode && *mode) {
        const std::string m = lower(mode);
        if (m == "c0" || m == "0" || m == "baseline") c.mode = OgChromaMode::C0;
        else if (m == "c1") c.mode = OgChromaMode::C1;
        else if (m == "c2") c.mode = OgChromaMode::C2;
        else if (m == "c3") c.mode = OgChromaMode::C3;
        else if (m == "c4") c.mode = OgChromaMode::C4;
        else throw Error("CDNG_OG_CHROMA_MODE: unsupported value '" + std::string(mode) +
                         "' (use c0|c1|c2|c3|c4)");
    }
    if (kern && *kern) {
        const std::string k = lower(kern);
        if (k == "b3") c.kernel = OgChromaKernel::B3;
        else if (k == "b5") c.kernel = OgChromaKernel::B5;
        else if (k == "b7") c.kernel = OgChromaKernel::B7;
        else throw Error("CDNG_OG_CHROMA_KERNEL: unsupported value '" + std::string(kern) +
                         "' (use b3|b5|b7)");
    }
    if (phase && *phase) {
        const std::string p = lower(phase);
        if (p == "center") c.phase = OgChromaPhase::Center;
        else if (p == "even") c.phase = OgChromaPhase::Even;
        else if (p == "odd") c.phase = OgChromaPhase::Odd;
        else throw Error("CDNG_OG_CHROMA_PHASE: unsupported value '" + std::string(phase) +
                         "' (use center|even|odd)");
    }
    const bool needK = c.mode == OgChromaMode::C2 || c.mode == OgChromaMode::C4;
    if (needK && c.kernel == OgChromaKernel::None)
        throw Error("CDNG_OG_CHROMA_KERNEL required for MODE=c2|c4 (b3|b5|b7)");
    if (!needK && c.kernel != OgChromaKernel::None)
        throw Error("CDNG_OG_CHROMA_KERNEL only valid with MODE=c2|c4");
    if (c.mode == OgChromaMode::C0 && phase && *phase)
        throw Error("CDNG_OG_CHROMA_PHASE requires MODE=c1|c2|c3|c4");
    if (c.mode == OgChromaMode::C3 && c.phase == OgChromaPhase::Center && !(phase && *phase))
        throw Error("CDNG_OG_CHROMA_PHASE required for MODE=c3 (even|odd|center)");
    return c;
}

const char *ogChromaModeName(OgChromaMode m) {
    switch (m) {
    case OgChromaMode::C1: return "c1";
    case OgChromaMode::C2: return "c2";
    case OgChromaMode::C3: return "c3";
    case OgChromaMode::C4: return "c4";
    default: return "c0";
    }
}
const char *ogChromaKernelName(OgChromaKernel k) {
    switch (k) {
    case OgChromaKernel::B3: return "b3";
    case OgChromaKernel::B5: return "b5";
    case OgChromaKernel::B7: return "b7";
    default: return "";
    }
}
const char *ogChromaPhaseName(OgChromaPhase p) {
    switch (p) {
    case OgChromaPhase::Even: return "even";
    case OgChromaPhase::Odd: return "odd";
    default: return "center";
    }
}

void ogChromaDownsample(const float *cb_full, const float *cr_full, int w, int h,
                        Planes &planes, const OgChromaConfig &cfg) {
    if (w % 2 || h % 2) throw Error("og chroma downsample needs even size");
    const bool filt = cfg.mode == OgChromaMode::C2 || cfg.mode == OgChromaMode::C4;
    std::vector<float> cbF, crF;
    const float *cb = cb_full, *cr = cr_full;
    if (filt) {
        std::vector<double> taps;
        kernelTaps(cfg.kernel, taps);
        cbF.resize(std::size_t(w) * h);
        crF.resize(std::size_t(w) * h);
        convSep(cb_full, cbF.data(), w, h, taps);
        convSep(cr_full, crF.data(), w, h, taps);
        cb = cbF.data();
        cr = crF.data();
    }
    OgChromaPhase ph = cfg.phase;
    if (cfg.mode == OgChromaMode::C1) ph = (cfg.phase == OgChromaPhase::Center || !std::getenv("CDNG_OG_CHROMA_PHASE"))
                                               ? OgChromaPhase::Center : cfg.phase;
    std::vector<float> cbH, crH;
    sample2x2(cb, w, h, cbH, ph);
    sample2x2(cr, w, h, crH, ph);
    planes.cb = std::move(cbH);
    planes.cr = std::move(crH);
}

}  // namespace cdng
