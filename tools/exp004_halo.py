"""Métricas de halo cromático EXP-004. No sustituyen gates globales del banco."""
from __future__ import annotations

import math

import numpy as np

from tools.fidelity import metrics as M

CHROMA_MIN_Y = 0.005  # misma convención del banco; u'v' inestable bajo este umbral


def _f(x):
    return M._f(x)


def signed_chroma(y, cb, cr):
    return {'cb_mean': _f(np.mean(cb)), 'cr_mean': _f(np.mean(cr)),
            'cb_abs_mean': _f(np.mean(np.abs(cb))), 'cr_abs_mean': _f(np.mean(np.abs(cr))),
            'cb_peak': _f(np.max(np.abs(cb))), 'cr_peak': _f(np.max(np.abs(cr)))}


def chroma_error_vs_ref(cb, cr, cb_ref, cr_ref):
    dcb, dcr = cb - cb_ref, cr - cr_ref
    mag = np.hypot(dcb, dcr)
    return {
        'peak_chroma_error': _f(np.max(mag)),
        'integrated_abs_chroma_error': _f(np.sum(mag)),
        'mean_abs_chroma_error': _f(np.mean(mag)),
        'cb_signed_mean': _f(np.mean(dcb)),
        'cr_signed_mean': _f(np.mean(dcr)),
        'cb_overshoot': _f(np.max(dcb)),
        'cb_undershoot': _f(np.min(dcb)),
        'cr_overshoot': _f(np.max(dcr)),
        'cr_undershoot': _f(np.min(dcr)),
    }


def uv_local(rgb, rgb_ref, y_min=CHROMA_MIN_Y):
    y = M.luminance(rgb)
    ok = y > y_min
    out = {'uv_samples': int(ok.sum()), 'uv_skipped_low_luma': int((~ok).sum()),
           'chroma_min_luminance': y_min}
    if not ok.any():
        out.update({'duv_mean': 'nan', 'duv_max': 'nan', 'duv_valid_fraction': 0.0})
        return out
    ur, vr = M.uv_prime(rgb_ref[ok])
    uc, vc = M.uv_prime(rgb[ok])
    duv = np.hypot(uc - ur, vc - vr)
    finite = duv[np.isfinite(duv)]
    out['duv_mean'] = _f(finite.mean()) if finite.size else 'nan'
    out['duv_max'] = _f(finite.max()) if finite.size else 'nan'
    out['duv_valid_fraction'] = _f(finite.size / max(ok.sum(), 1))
    return out


def edge_centroid(profile):
    """Centroide 10–90 % del salto; NaN si el perfil es plano."""
    p = np.asarray(profile, dtype=np.float64)
    a, b = float(p[: max(2, len(p) // 5)].mean()), float(p[-max(2, len(p) // 5):].mean())
    step = b - a
    if abs(step) < 1e-12:
        return math.nan
    q = (p - a) / step
    w = np.clip(q, 0, 1)
    s = w.sum()
    if s <= 0:
        return math.nan
    return float(np.arange(len(p)) @ w / s)


def edge_spread(profile):
    p = np.asarray(profile, dtype=np.float64)
    a, b = float(p[: max(2, len(p) // 5)].mean()), float(p[-max(2, len(p) // 5):].mean())
    step = b - a
    if abs(step) < 1e-12:
        return {'width_10_90': 'nan', 'centroid': 'nan'}
    q = (p - a) / step

    def cross(level):
        for i in range(1, len(q)):
            if q[i - 1] < level <= q[i] or q[i - 1] > level >= q[i]:
                return i - 1 + (level - q[i - 1]) / (q[i] - q[i - 1] + 1e-18)
        return None
    c10, c90 = cross(0.1), cross(0.9)
    w = (c90 - c10) if c10 is not None and c90 is not None else None
    return {'width_10_90': _f(w) if w is not None else 'nan', 'centroid': _f(edge_centroid(p))}


def halo_extent_px(chroma_mag, thresh):
    """Píxeles consecutivos sobre el umbral alrededor del máximo."""
    m = np.asarray(chroma_mag, dtype=np.float64)
    if m.size == 0 or m.max() < thresh:
        return 0
    i = int(np.argmax(m))
    lo = i
    while lo > 0 and m[lo - 1] >= thresh:
        lo -= 1
    hi = i
    while hi < m.size - 1 and m[hi + 1] >= thresh:
        hi += 1
    return int(hi - lo + 1)


def profile_metrics(y, cb, cr, y_ref=None, cb_ref=None, cr_ref=None, thresh=None):
    mag = np.hypot(cb, cr) if cb_ref is None else np.hypot(cb - cb_ref, cr - cr_ref)
    if thresh is None:
        thresh = 0.05 * (float(np.max(mag)) + 1e-9)
    out = {
        'y': edge_spread(y),
        'chroma': {
            'profile_cb_peak': _f(np.max(np.abs(cb))),
            'profile_cr_peak': _f(np.max(np.abs(cr))),
            'profile_chroma_peak': _f(np.max(mag)),
            'halo_extent_px': halo_extent_px(mag, thresh),
            'centroid_y': _f(edge_centroid(y)),
            'centroid_cb': _f(edge_centroid(cb)),
            'centroid_cr': _f(edge_centroid(cr)),
        },
    }
    out['centroid_shift_cb_vs_y'] = _f(
        (edge_centroid(cb) - edge_centroid(y)) if math.isfinite(edge_centroid(cb)) else float('nan'))
    if y_ref is not None:
        out['error'] = chroma_error_vs_ref(cb, cr, cb_ref, cr_ref)
    return out


def mtf_1d(profile, dx=1.0):
    """MTF grosera: |FFT| del LSF (derivada del ESF), normalizada en DC."""
    p = np.asarray(profile, dtype=np.float64)
    lsf = np.diff(p, prepend=p[0])
    spec = np.abs(np.fft.rfft(lsf))
    if spec[0] == 0:
        return {'mtf': [0.0], 'freq_cycles_per_px': [0.0]}
    mtf = spec / spec[0]
    freq = np.fft.rfftfreq(len(lsf), d=dx)
    return {'mtf': [_f(v) for v in mtf[: min(32, len(mtf))]],
            'freq_cycles_per_px': [_f(v) for v in freq[: min(32, len(freq))]]}


def residual_spectrum(cb, cr):
    mag = np.hypot(cb, cr)
    spec = np.abs(np.fft.rfft(mag - mag.mean()))
    energy = float(np.sum(spec ** 2)) or 1.0
    return {'spectral_energy': _f(energy),
            'high_band_fraction': _f(np.sum(spec[len(spec) // 2:] ** 2) / energy)}


def extract_profile(plane, line, width=1):
    """Muestras a lo largo de line=[x0,y0,x1,y1], media de `width` píxeles perpendiculares."""
    x0, y0, x1, y1 = [float(v) for v in line]
    n = int(max(abs(x1 - x0), abs(y1 - y0))) + 1
    xs = np.linspace(x0, x1, n)
    ys = np.linspace(y0, y1, n)
    dx, dy = x1 - x0, y1 - y0
    L = math.hypot(dx, dy) or 1.0
    px, py = -dy / L, dx / L
    acc = []
    h, w = plane.shape[:2]
    for t in np.linspace(-(width - 1) / 2, (width - 1) / 2, max(1, width)):
        xi = np.clip(np.round(xs + t * px).astype(int), 0, w - 1)
        yi = np.clip(np.round(ys + t * py).astype(int), 0, h - 1)
        acc.append(plane[yi, xi])
    return np.mean(acc, axis=0)
