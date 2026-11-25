"""ROI configurables (benchmark/rois/*.json) y perfiles perpendiculares a bordes.

Tipos de ROI: "patch" y "region" (rectángulo [x, y, ancho, alto]) y "edge" (segmento [x0, y0, x1, y1] cruzando el
borde, promediando width píxeles en perpendicular). category es libre (colorchecker, text, specular, black_texture,
deep_shadow, uniform, edge…): el informe agrupa por categoría, así que añadir tipos no requiere tocar el código.
"""
import json
import math

import numpy as np

from . import metrics as M

TYPES = ('patch', 'region', 'edge')


class RoiError(ValueError):
    pass


def load(path, size):
    """Lee y valida un fichero de ROI contra el tamaño de imagen (ancho, alto)."""
    with open(path, encoding='utf-8') as f:
        doc = json.load(f)
    w, h = size
    names = set()
    for roi in doc.get('rois', []):
        name = roi.get('name')
        if not name or name in names:
            raise RoiError('%s: ROI sin nombre o repetida (%r)' % (path, name))
        names.add(name)
        if roi.get('type') not in TYPES:
            raise RoiError('%s: %s tiene tipo %r (válidos: %s)' % (path, name, roi.get('type'), ', '.join(TYPES)))
        if not roi.get('category'):
            raise RoiError('%s: %s sin category' % (path, name))
        if roi['type'] == 'edge':
            x0, y0, x1, y1 = roi['line']
            pts = [(x0, y0), (x1, y1)]
            if (x0, y0) == (x1, y1):
                raise RoiError('%s: %s tiene un segmento vacío' % (path, name))
        else:
            x, y, rw, rh = roi['rect']
            if rw <= 0 or rh <= 0:
                raise RoiError('%s: %s tiene tamaño no positivo' % (path, name))
            pts = [(x, y), (x + rw - 1, y + rh - 1)]
        for px, py in pts:
            if not (0 <= px < w and 0 <= py < h):
                raise RoiError('%s: %s se sale de la imagen %dx%d' % (path, name, w, h))
    return doc


def crop(img, rect):
    x, y, w, h = rect
    return img[y:y + h, x:x + w]


def rect_metrics(ref, cand, roi, cfg):
    """Métricas de un rectángulo sobre imágenes ya normalizadas (float64)."""
    r, c = crop(ref, roi['rect']), crop(cand, roi['rect'])
    out = M.basic(r, c, cfg['psnr_peak'])
    mr, mc = r.reshape(-1, 3).mean(0), c.reshape(-1, 3).mean(0)
    out['reference_mean_rgb'] = [M._f(v) for v in mr]
    out['candidate_mean_rgb'] = [M._f(v) for v in mc]
    out['signed_mean_error_rgb'] = [M._f(v) for v in mc - mr]
    yr, yc = float(mr @ M.P3_LUMA), float(mc @ M.P3_LUMA)
    out['luminance_rel_error_pct'] = M._f(100 * (yc / yr - 1)) if yr > 0 else 'nan'
    ur, vr = M.uv_prime(mr[None]); uc, vc = M.uv_prime(mc[None])
    out['mean_duv'] = M._f(math.hypot(uc[0] - ur[0], vc[0] - vr[0]))
    out['pixel'] = M.luma_chroma(r, c, np.ones(r.shape[:2], bool), cfg['chroma_min_luminance'])
    out['reference_noise_rgb'] = [M._f(v) for v in r.reshape(-1, 3).std(0)]
    out['candidate_noise_rgb'] = [M._f(v) for v in c.reshape(-1, 3).std(0)]
    if roi.get('neutral'):
        out['neutral'] = {'reference_duv_to_d65': M._f(math.hypot(ur[0] - M.D65_UV[0], vr[0] - M.D65_UV[1])),
                          'candidate_duv_to_d65': M._f(math.hypot(uc[0] - M.D65_UV[0], vc[0] - M.D65_UV[1]))}
    return out


def sample_line(img, line, width):
    """Perfil a lo largo de line (vecino más próximo, un píxel por muestra) promediando width píxeles en perpendicular."""
    x0, y0, x1, y1 = line
    n = int(round(max(abs(x1 - x0), abs(y1 - y0)))) + 1
    t = np.linspace(0, 1, n)
    dx, dy = x1 - x0, y1 - y0
    length = math.hypot(dx, dy)
    px, py = -dy / length, dx / length
    offs = np.arange(width) - (width - 1) / 2
    xs = np.rint(x0 + dx * t[:, None] + px * offs[None]).astype(int)
    ys = np.rint(y0 + dy * t[:, None] + py * offs[None]).astype(int)
    xs = np.clip(xs, 0, img.shape[1] - 1); ys = np.clip(ys, 0, img.shape[0] - 1)
    return img[ys, xs].mean(1)


def step_shape(p, plateau=0.2):
    """Forma de un escalón 1D: niveles de las mesetas (primer y último 20 %), overshoot y undershoot relativos al
    salto y anchura 10–90 % en muestras."""
    n = len(p)
    k = max(2, int(round(n * plateau)))
    a, b = float(p[:k].mean()), float(p[-k:].mean())
    lo, hi = min(a, b), max(a, b)
    step = hi - lo
    out = {'level_start': M._f(a), 'level_end': M._f(b), 'step': M._f(b - a)}
    if step <= 0:
        out.update({'overshoot': 'nan', 'undershoot': 'nan', 'rise_10_90': 'nan'})
        return out
    out['overshoot'] = M._f(max(0.0, float(p.max()) - hi) / step)
    out['undershoot'] = M._f(max(0.0, lo - float(p.min())) / step)
    q = (p - a) / (b - a)    # 0 en la meseta inicial, 1 en la final

    def crossing(level):
        for i in range(1, n):
            if q[i - 1] < level <= q[i]:
                return i - 1 + (level - q[i - 1]) / (q[i] - q[i - 1])
        return None
    c10, c90 = crossing(0.1), crossing(0.9)
    out['rise_10_90'] = M._f(c90 - c10) if c10 is not None and c90 is not None and c90 >= c10 else 'nan'
    return out


def line_shape(p, plateau=0.2):
    """Forma de una línea fina sobre un fondo (un cable): contraste respecto al fondo, anchura a media amplitud y
    halo (excursión de signo contrario junto a la línea), relativo a la amplitud."""
    n = len(p)
    k = max(2, int(round(n * plateau)))
    bg = float(np.concatenate([p[:k], p[-k:]]).mean())
    i = int(np.argmax(np.abs(p - bg)))
    amp = float(p[i] - bg)
    out = {'background': M._f(bg), 'amplitude': M._f(amp), 'contrast': M._f(amp / bg) if bg else 'nan'}
    if amp == 0:
        out.update({'width_50': 'nan', 'halo': 'nan'})
        return out
    q = (p - bg) / amp
    lo = i
    while lo > 0 and q[lo - 1] >= 0.5:
        lo -= 1
    hi = i
    while hi < n - 1 and q[hi + 1] >= 0.5:
        hi += 1
    out['width_50'] = M._f(hi - lo + 1)
    out['halo'] = M._f(max(0.0, float(-q.min())))
    return out


def edge_metrics(ref, cand, roi, cfg):
    """Perfil RGB perpendicular a un borde (profile "step", por defecto) o a una línea fina (profile "line") en
    referencia y candidata (normalizadas)."""
    width = int(roi.get('width', 1))
    pr, pc = sample_line(ref, roi['line'], width), sample_line(cand, roi['line'], width)
    yr, yc = pr @ M.P3_LUMA, pc @ M.P3_LUMA
    shape = line_shape if roi.get('profile', 'step') == 'line' else step_shape
    out = {'samples': len(yr), 'profile_kind': roi.get('profile', 'step'), 'profile_rmse': M._f(math.sqrt(float(np.mean((pc - pr) ** 2))))}
    sr, sc = shape(yr), shape(yc)
    out['reference'] = sr
    out['candidate'] = sc
    for key in ('overshoot', 'undershoot', 'rise_10_90', 'contrast', 'width_50', 'halo'):
        if isinstance(sr.get(key), float) and isinstance(sc.get(key), float):
            out[key + '_delta'] = M._f(sc[key] - sr[key])
    out['channels'] = {}
    for k, ch in enumerate(M.CHANNELS):
        out['channels'][ch] = {'reference': shape(pr[:, k]), 'candidate': shape(pc[:, k])}
    step = abs(float(yr[-1] - yr[0])) or float(np.ptp(yr)) or 1.0
    # Franja de color: separación entre canales que la referencia no tiene, relativa al salto de luminancia.
    fr = pr - yr[:, None]; fc = pc - yc[:, None]
    out['fringe_max'] = M._f(float(np.abs(fc - fr).max()) / step)
    ok = yr > cfg['chroma_min_luminance']
    if ok.any():
        ur, vr = M.uv_prime(pr[ok]); uc, vc = M.uv_prime(pc[ok])
        duv = np.hypot(uc - ur, vc - vr)
        out['duv_mean'] = M._f(np.nanmean(duv)); out['duv_max'] = M._f(np.nanmax(duv))
    out['profile'] = {'reference': [[M._f(v) for v in s] for s in pr], 'candidate': [[M._f(v) for v in s] for s in pc]}
    return out


def evaluate(ref, cand, doc, cfg):
    """Métricas de todas las ROI de una escena; resumen por categoría con la media de cada métrica escalar."""
    per_roi = {}
    for roi in doc.get('rois', []):
        if roi['type'] == 'edge':
            per_roi[roi['name']] = dict(edge_metrics(ref, cand, roi, cfg), category=roi['category'], type='edge')
        else:
            per_roi[roi['name']] = dict(rect_metrics(ref, cand, roi, cfg), category=roi['category'], type=roi['type'])
    by_cat = {}
    for name, m in per_roi.items():
        by_cat.setdefault(m['category'], []).append(m)
    summary = {}
    keys = ('mae', 'rmse', 'psnr_db', 'mean_duv', 'luminance_rel_error_pct', 'profile_rmse', 'fringe_max', 'duv_mean',
            'overshoot_delta', 'undershoot_delta', 'rise_10_90_delta', 'contrast_delta', 'width_50_delta', 'halo_delta')
    for cat, items in by_cat.items():
        s = {'count': len(items)}
        for key in keys:
            vals = [m[key] for m in items if isinstance(m.get(key), float) and math.isfinite(m[key])]
            if vals:
                s[key + '_mean'] = M._f(np.mean(vals))
                s[key + '_worst'] = M._f(max(vals, key=abs) if key.endswith('_delta') or key == 'luminance_rel_error_pct'
                                         else (min(vals) if key == 'psnr_db' else max(vals)))
        neutral = [m['neutral']['candidate_duv_to_d65'] - m['neutral']['reference_duv_to_d65'] for m in items if 'neutral' in m]
        if neutral:
            s['neutral_duv_to_d65_delta_mean'] = M._f(np.mean(neutral))
        summary[cat] = s
    return per_roi, summary
