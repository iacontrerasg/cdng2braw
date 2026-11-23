// Bloque A — reescalado: recorte centrado a la proporción de salida y, por canal,
// Image.resize(LANCZOS) de Pillow en modo "F". Port de libImaging/Resample.c (ImagingResample → ImagingResampleInner
// con precompute_coeffs y las pasadas ResampleHorizontal/ResampleVertical de 32 bits): coeficientes en double
// normalizados por ventana, acumulación en double y resultado guardado en float tras cada pasada; primero la pasada
// horizontal (solo sobre las filas que usa la vertical) y luego la vertical.
#include "dng.hpp"
#include "signal.hpp"

#include <algorithm>
#include <cmath>

namespace cdng {

namespace {

constexpr double Pi = 3.14159265358979323846;   // M_PI

double sincFilter(double x) {
    if (x == 0.0) return 1.0;
    x = x * Pi;
    return std::sin(x) / x;
}

double lanczosFilter(double x) {   // sinc truncado, soporte 3
    if (-3.0 <= x && x < 3.0) return sincFilter(x) * sincFilter(x / 3);
    return 0.0;
}

constexpr double LanczosSupport = 3.0;

struct Coeffs {
    int ksize = 0;
    std::vector<int> bounds;   // (xmin, xmax) por píxel de salida; xmax es el número de coeficientes
    std::vector<double> kk;    // outSize × ksize
};

// precompute_coeffs(inSize, in0, in1, outSize, &LANCZOS, ...). in0/in1 son float en Pillow (la caja).
Coeffs precomputeCoeffs(int inSize, float in0, float in1, int outSize) {
    double filterscale, scale;
    filterscale = scale = double(in1 - in0) / outSize;
    if (filterscale < 1.0) filterscale = 1.0;
    const double support = LanczosSupport * filterscale;
    Coeffs c;
    c.ksize = int(std::ceil(support)) * 2 + 1;
    c.kk.assign(std::size_t(outSize) * c.ksize, 0.0);
    c.bounds.resize(std::size_t(outSize) * 2);
    for (int xx = 0; xx < outSize; ++xx) {
        const double center = in0 + (xx + 0.5) * scale;
        double ww = 0.0;
        const double ss = 1.0 / filterscale;
        int xmin = int(center - support + 0.5);   // (int) de C: trunca
        if (xmin < 0) xmin = 0;
        int xmax = int(center + support + 0.5);
        if (xmax > inSize) xmax = inSize;
        xmax -= xmin;
        double *k = &c.kk[std::size_t(xx) * c.ksize];
        int x = 0;
        for (; x < xmax; ++x) {
            const double w = lanczosFilter((x + xmin - center + 0.5) * ss);
            k[x] = w;
            ww += w;
        }
        for (x = 0; x < xmax; ++x)
            if (ww != 0.0) k[x] /= ww;
        for (; x < c.ksize; ++x) k[x] = 0;
        c.bounds[std::size_t(xx) * 2] = xmin;
        c.bounds[std::size_t(xx) * 2 + 1] = xmax;
    }
    return c;
}

// Un plano float (modo "F") de inW × inH → outW × outH, como ImagingResampleInner con la caja (x0, y0, x1, y1);
// por defecto (0, 0, inW, inH). Una caja desplazada lleva cada muestra de salida a otro punto de la entrada.
std::vector<float> resamplePlaneBox(const std::vector<float> &in, int inW, int inH, int outW, int outH,
                                    float x0, float y0, float x1, float y1) {
    const bool needH = outW != inW || x0 != 0 || x1 != float(inW), needV = outH != inH || y0 != 0 || y1 != float(inH);
    const Coeffs horiz = precomputeCoeffs(inW, x0, x1, outW);
    Coeffs vert = precomputeCoeffs(inH, y0, y1, outH);
    const int yFirst = vert.bounds[0];
    const int yLast = vert.bounds[std::size_t(outH) * 2 - 2] + vert.bounds[std::size_t(outH) * 2 - 1];

    std::vector<float> tmp;
    const float *cur = in.data();
    int curW = inW;
    if (needH) {
        for (int i = 0; i < outH; ++i) vert.bounds[std::size_t(i) * 2] -= yFirst;
        const int tmpH = yLast - yFirst;
        tmp.resize(std::size_t(outW) * tmpH);
        for (int yy = 0; yy < tmpH; ++yy) {
            const float *row = in.data() + std::size_t(yy + yFirst) * inW;
            float *dst = tmp.data() + std::size_t(yy) * outW;
            for (int xx = 0; xx < outW; ++xx) {
                const int xmin = horiz.bounds[std::size_t(xx) * 2], xmax = horiz.bounds[std::size_t(xx) * 2 + 1];
                const double *k = &horiz.kk[std::size_t(xx) * horiz.ksize];
                double ss = 0.0;
                for (int x = 0; x < xmax; ++x) ss += row[x + xmin] * k[x];
                dst[xx] = float(ss);
            }
        }
        if (!needV) return tmp;
        cur = tmp.data(); curW = outW;
    }
    if (!needV) return in;
    std::vector<float> out(std::size_t(curW) * outH);
    for (int yy = 0; yy < outH; ++yy) {
        const int ymin = vert.bounds[std::size_t(yy) * 2], ymax = vert.bounds[std::size_t(yy) * 2 + 1];
        const double *k = &vert.kk[std::size_t(yy) * vert.ksize];
        float *dst = out.data() + std::size_t(yy) * curW;
        for (int xx = 0; xx < curW; ++xx) {
            double ss = 0.0;
            for (int y = 0; y < ymax; ++y) ss += cur[std::size_t(y + ymin) * curW + xx] * k[y];
            dst[xx] = float(ss);
        }
    }
    return out;
}

std::vector<float> resamplePlane(const std::vector<float> &in, int inW, int inH, int outW, int outH) {
    return resamplePlaneBox(in, inW, inH, outW, outH, 0.0f, 0.0f, float(inW), float(inH));
}

}  // namespace

RgbImage resizeLinear(const RgbImage &image, int width, int height) {
    if (width <= 0 || height <= 0) throw Error("invalid output size");
    int x0 = 0, y0 = 0, w = image.width, h = image.height;
    const long long W = image.width, H = image.height;
    if (W * height != H * width) {   // recorte centrado; round() de Python redondea a par
        if (W * height > H * width) {
            w = int(std::nearbyint(double(H * width) / double(height)));
            x0 = (image.width - w) / 2;
        } else {
            h = int(std::nearbyint(double(W * height) / double(width)));
            y0 = (image.height - h) / 2;
        }
    }
    RgbImage out(width, height);
    if (w == width && h == height) {
        for (int y = 0; y < h; ++y) std::copy_n(image.px(x0, y0 + y), std::size_t(w) * 3, out.px(0, y));
        return out;
    }
    std::vector<float> plane(std::size_t(w) * h);
    for (int c = 0; c < 3; ++c) {
        for (int y = 0; y < h; ++y) {
            const float *src = image.px(x0, y0 + y) + c;
            float *dst = plane.data() + std::size_t(y) * w;
            for (int x = 0; x < w; ++x) dst[x] = src[std::size_t(x) * 3];
        }
        const std::vector<float> res = resamplePlane(plane, w, h, width, height);
        for (std::size_t i = 0; i < res.size(); ++i) out.data[i * 3 + c] = res[i];
    }
    return out;
}

Planes resizeBayerFields(const BayerFields &f, int width, int height) {
    const int w = f.width, h = f.height;
    // El mismo recorte centrado que resizePlanes (par, así la luma queda igual en las dos rutas).
    int cropW = w, cropH = h;
    if (int64_t(w) * height > int64_t(h) * width)
        cropW = std::max(2, int(std::nearbyint(double(h) * width / height)) & ~1);
    else if (int64_t(w) * height < int64_t(h) * width)
        cropH = std::max(2, int(std::nearbyint(double(w) * height / width)) & ~1);
    const int x = ((w - cropW) / 2) & ~1, y = ((h - cropH) / 2) & ~1;
    auto crop = [&](const std::vector<float> &in, int step) {
        const int iw = w / step, cw = cropW / step, ch = cropH / step, cx = x / step, cy = y / step;
        if (cw == iw && ch == h / step) return in;
        std::vector<float> out(std::size_t(cw) * ch);
        for (int row = 0; row < ch; ++row)
            std::copy_n(in.data() + std::size_t(row + cy) * iw + cx, cw, out.data() + std::size_t(row) * cw);
        return out;
    };
    Planes out(width, height);
    out.reduction = double(cropW) / width;
    out.y = resamplePlane(crop(f.y, 1), cropW, cropH, width, height);
    // Croma: numerador y denominador de los sitios de entrada a los de salida (width/2 × height/2), por separado y
    // divididos después. Cada muestra está en el centro del píxel donde se pinta, no en el del cuarteto: Cb ¼ de
    // cuarteto abajo-derecha y Cr ¼ arriba-izquierda, en la entrada y en la salida. El rectángulo del Lanczos se
    // desplaza d · (escala − 1), en muestras de entrada, para que la salida caiga en su sitio; sin escalar no se mueve.
    const int cw = cropW / 2, ch = cropH / 2, hw = width / 2, hh = height / 2;
    const double sx = double(cw) / hw, sy = double(ch) / hh;
    for (int channel : {2, 0}) {
        const double d = channel == 2 ? .25 : -.25;
        const float bx0 = float(d * (sx - 1)), by0 = float(d * (sy - 1)), bx1 = float(cw + d * (sx - 1)), by1 = float(ch + d * (sy - 1));
        const auto num = resamplePlaneBox(crop(channel == 2 ? f.numB : f.numR, 2), cw, ch, hw, hh, bx0, by0, bx1, by1);
        const auto den = resamplePlaneBox(crop(channel == 2 ? f.denB : f.denR, 2), cw, ch, hw, hh, bx0, by0, bx1, by1);
        auto &ratio = channel == 2 ? out.cb : out.cr;
        // Sin señal verde, la proporción no es estimable. Limita la amplificación del ruido cerca del negro.
        for (std::size_t i = 0; i < ratio.size(); ++i) ratio[i] = float(num[i] / std::max(double(den[i]), 1.0));
    }
    return out;
}

Planes resizePlanes(Planes planes, int width, int height) {
    const int w = planes.width, h = planes.height;
    if (w == width && h == height) return planes;
    int cropW = w, cropH = h;
    if (int64_t(w) * height > int64_t(h) * width)
        cropW = std::max(2, int(std::nearbyint(double(h) * width / height)) & ~1);
    else if (int64_t(w) * height < int64_t(h) * width)
        cropH = std::max(2, int(std::nearbyint(double(w) * height / width)) & ~1);
    const int x = ((w - cropW) / 2) & ~1, y = ((h - cropH) / 2) & ~1;
    auto resize = [&](const std::vector<float> &in, int step) {
        const int cw = cropW / step, ch = cropH / step, stride = w / step;
        if (cropW == w && cropH == h) return resamplePlane(in, cw, ch, width / step, height / step);
        std::vector<float> crop(std::size_t(cw) * ch);
        for (int row = 0; row < ch; ++row)
            std::copy_n(in.data() + std::size_t(row + y / step) * stride + x / step,
                        cw, crop.data() + std::size_t(row) * cw);
        return resamplePlane(crop, cw, ch, width / step, height / step);
    };
    Planes result;
    result.width = width; result.height = height; result.reduction = planes.reduction * cropW / width;
    result.y = resize(planes.y, 1);
    result.cb = resize(planes.cb, 2); result.cr = resize(planes.cr, 2);
    return result;
}

}  // namespace cdng
