"""Visualizaciones PNG. La transformación de visualización (ganancia, recorte, gamma 2.2, 8 bits) vive solo aquí:
las métricas nunca ven estos valores. Las escalas salen del manifiesto, así que son las mismas en todas las ejecuciones."""
import numpy as np

from . import metrics as M
from .images import write_png

# Mapas de color por tramos lineales (posición, RGB 0–1).
_SEQUENTIAL = [(0.0, (0, 0, 0)), (0.25, (0.35, 0.05, 0.45)), (0.5, (0.8, 0.2, 0.25)), (0.75, (0.98, 0.6, 0.1)), (1.0, (1, 1, 0.85))]
_DIVERGING = [(0.0, (0.1, 0.25, 0.85)), (0.5, (1, 1, 1)), (1.0, (0.85, 0.1, 0.1))]


def _ramp(x, stops):
    x = np.clip(np.nan_to_num(x, nan=0.0), 0, 1)
    pos = np.array([s[0] for s in stops])
    cols = np.array([s[1] for s in stops])
    return np.stack([np.interp(x, pos, cols[:, k]) for k in range(3)], -1)


def to8(rgb01):
    return (np.clip(rgb01, 0, 1) * 255 + 0.5).astype(np.uint8)


def display(img, exposure):
    """Lineal normalizado → PNG: ganancia 2^exposure, recorte 0–1 y gamma 1/2.2. Solo para mirar."""
    return to8(np.clip(img * 2.0 ** exposure, 0, 1) ** (1 / 2.2))


def abs_error_map(ref, cand, vmax):
    return to8(_ramp(np.abs(cand - ref).max(-1) / vmax, _SEQUENTIAL))


def signed_error_map(ref, cand, vmax):
    """Error de luminancia con signo: azul = candidata más oscura, rojo = más clara."""
    d = M.luminance(cand) - M.luminance(ref)
    return to8(_ramp(0.5 + 0.5 * d / vmax, _DIVERGING))


def chroma_error_map(ref, cand, vmax, min_y):
    """Δu'v' por píxel; gris donde la referencia es demasiado oscura para tener cromaticidad estable."""
    ur, vr = M.uv_prime(ref); uc, vc = M.uv_prime(cand)
    duv = np.hypot(uc - ur, vc - vr)
    out = _ramp(duv / vmax, _SEQUENTIAL)
    out[(M.luminance(ref) <= min_y) | ~np.isfinite(duv)] = 0.25
    return to8(out)


def colorbar(stops, width=256, height=12):
    return to8(np.repeat(_ramp(np.linspace(0, 1, width), stops)[None], height, 0))


def mask_png(mask):
    return np.repeat((mask.astype(np.uint8) * 255)[..., None], 3, -1)


def _rect(canvas, x, y, w, h, color):
    canvas[y:y + h, x:x + 1] = color; canvas[y:y + h, x + w - 1:x + w] = color
    canvas[y:y + 1, x:x + w] = color; canvas[y + h - 1:y + h, x:x + w] = color


def _line(canvas, x0, y0, x1, y1, color, thickness=1):
    n = int(max(abs(x1 - x0), abs(y1 - y0))) * 2 + 1
    xs = np.rint(np.linspace(x0, x1, n)).astype(int); ys = np.rint(np.linspace(y0, y1, n)).astype(int)
    h, w = canvas.shape[:2]
    r = thickness // 2
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            ok = (xs + dx >= 0) & (xs + dx < w) & (ys + dy >= 0) & (ys + dy < h)
            canvas[ys[ok] + dy, xs[ok] + dx] = color


CATEGORY_COLORS = {'colorchecker': (0, 255, 0), 'uniform': (0, 200, 255), 'text': (255, 255, 0), 'specular': (255, 0, 255),
                   'black_texture': (255, 128, 0), 'deep_shadow': (128, 128, 255), 'edge': (255, 0, 0)}


def roi_overlay(base8, doc):
    canvas = base8.copy()
    for roi in doc.get('rois', []):
        color = CATEGORY_COLORS.get(roi['category'], (255, 255, 255))
        if roi['type'] == 'edge':
            _line(canvas, *roi['line'], color=color, thickness=3)
        else:
            x, y, w, h = roi['rect']
            _rect(canvas, x, y, w, h, color)
    return canvas


def zoom_box(roi, size, pad=8):
    if roi['type'] == 'edge':
        x0, y0, x1, y1 = roi['line']
        half = max(int(roi.get('width', 1)), 16)
        box = [min(x0, x1) - half, min(y0, y1) - half, abs(x1 - x0) + 2 * half, abs(y1 - y0) + 2 * half]
    else:
        x, y, w, h = roi['rect']
        box = [x - pad, y - pad, w + 2 * pad, h + 2 * pad]
    W, H = size
    x, y = max(0, box[0]), max(0, box[1])
    return [x, y, min(W, box[0] + box[2]) - x, min(H, box[1] + box[3]) - y]


def zoom_strip(panels, box, factor):
    """Recortes ampliados por vecino más próximo, uno al lado de otro con separación blanca."""
    x, y, w, h = box
    parts = []
    for p in panels:
        crop = p[y:y + h, x:x + w]
        parts.append(np.repeat(np.repeat(crop, factor, 0), factor, 1))
        parts.append(np.full((h * factor, 4, 3), 255, np.uint8))
    return np.concatenate(parts[:-1], 1)


def histogram(ref, cand, vmax, bins, width=600, height=240):
    """Histograma del error con signo por canal (eje y logarítmico), en raster."""
    d = cand - ref
    edges = np.linspace(-vmax, vmax, bins + 1)
    canvas = np.full((height, width, 3), 255, np.uint8)
    canvas[:, width // 2] = (180, 180, 180)
    colors = ((220, 30, 30), (30, 160, 30), (30, 60, 220))
    counts = [np.histogram(np.clip(d[..., k], -vmax, vmax), edges)[0] for k in range(3)]
    top = max(np.log10(c.max() + 1) for c in counts) or 1
    xs = (np.arange(bins) + 0.5) * width / bins
    for c, color in zip(counts, colors):
        ys = height - 1 - np.log10(c + 1) / top * (height - 4)
        for i in range(bins - 1):
            _line(canvas, xs[i], ys[i], xs[i + 1], ys[i + 1], color)
    return canvas


def profile_plot(edge, width=320, height=160):
    """Perfil de un borde: referencia (línea gruesa) y candidata (fina), por canal."""
    pr = np.array(edge['profile']['reference'], float); pc = np.array(edge['profile']['candidate'], float)
    lo, hi = min(pr.min(), pc.min()), max(pr.max(), pc.max())
    span = (hi - lo) or 1.0
    canvas = np.full((height, width, 3), 255, np.uint8)
    n = len(pr)
    xs = np.linspace(4, width - 5, n)
    colors = ((220, 30, 30), (30, 160, 30), (30, 60, 220))

    def y(v):
        return height - 5 - (v - lo) / span * (height - 10)
    for k, color in enumerate(colors):
        light = tuple(int(c + (255 - c) * 0.55) for c in color)
        for i in range(n - 1):
            _line(canvas, xs[i], y(pr[i, k]), xs[i + 1], y(pr[i + 1, k]), light, thickness=3)
        for i in range(n - 1):
            _line(canvas, xs[i], y(pc[i, k]), xs[i + 1], y(pc[i + 1, k]), color)
    return canvas


def save(path, rgb8):
    write_png(path, rgb8)
    return path
