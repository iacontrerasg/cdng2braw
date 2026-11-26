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


def mtf50_from(freq, mtf):
    """Primera frecuencia donde MTF cruza 0.5 (ciclos/píxel). Ausente → nan."""
    f = np.asarray(freq, dtype=np.float64)
    m = np.asarray(mtf, dtype=np.float64)
    for i in range(1, len(m)):
        if m[i - 1] >= 0.5 > m[i]:
            t = (0.5 - m[i - 1]) / (m[i] - m[i - 1] + 1e-18)
            return float(f[i - 1] + t * (f[i] - f[i - 1]))
    return float('nan')


def esf_mtf(profile, dx=1.0, n_keep=None):
    """MTF del ESF: |FFT(LSF)| / DC. No se resta la media de la LSF (anularía el DC)."""
    p = np.asarray(profile, dtype=np.float64)
    if p.size < 4 or not np.isfinite(p).all():
        return {'absent': 'esf_too_short_or_nonfinite', 'mtf50': 'nan'}
    lsf = np.diff(p, prepend=p[0])
    spec = np.abs(np.fft.rfft(lsf))
    dc = float(spec[0]) if spec.size else 0.0
    if dc <= 1e-18:
        return {'absent': 'lsf_dc_zero', 'mtf50': 'nan', 'dc': dc}
    mtf = spec / dc
    freq = np.fft.rfftfreq(len(lsf), d=dx)
    n = len(mtf) if n_keep is None else min(n_keep, len(mtf))
    out = {
        'mtf': [_f(v) for v in mtf[:n]],
        'freq_cycles_per_px': [_f(v) for v in freq[:n]],
        'mtf50': _f(mtf50_from(freq, mtf)),
        'mtf_at': {
            '0.125': _f(_interp_mtf(freq, mtf, 0.125)),
            '0.25': _f(_interp_mtf(freq, mtf, 0.25)),
            '0.5': _f(_interp_mtf(freq, mtf, 0.5)),
        },
        'dc': _f(dc),
    }
    return out


def _interp_mtf(freq, mtf, f0):
    if f0 > float(freq[-1]):
        return float('nan')
    return float(np.interp(f0, freq, mtf))


def mtf_1d(profile, dx=1.0):
    """Alias de esf_mtf (G0: sin restar media de LSF)."""
    r = esf_mtf(profile, dx=dx, n_keep=32)
    if r.get('absent'):
        return {'mtf': ['nan'], 'freq_cycles_per_px': [0.0], 'absent': r['absent']}
    return {'mtf': r['mtf'], 'freq_cycles_per_px': r['freq_cycles_per_px'],
            'mtf50': r['mtf50'], 'mtf_at': r['mtf_at']}


def luma_chroma_split(rgb):
    """P3-D65: Y y residuo RGB (crominancia de escena, independiente del gradiente de luma)."""
    y = M.luminance(rgb)
    chroma = rgb - y[..., None]
    return y, chroma


def chroma_signed_profile(rgb, line, width=9):
    """Proyección del residuo RGB sobre el salto de crominancia del perfil (no sobre Y)."""
    _, chroma = luma_chroma_split(rgb)
    prof = extract_profile(chroma, line, width)
    a = prof[: max(2, len(prof) // 5)].mean(0)
    b = prof[-max(2, len(prof) // 5):].mean(0)
    axis = b - a
    n = float(np.linalg.norm(axis))
    if n < 1e-6:
        return None, {'absent': 'no_chroma_step'}
    signed = prof @ (axis / n)
    return signed, {'chroma_step_norm': _f(n)}


def chroma_esf_mtf(rgb, line, width=9, dx=1.0):
    signed, meta = chroma_signed_profile(rgb, line, width)
    if signed is None:
        return meta
    out = esf_mtf(signed, dx=dx)
    out['chroma_step_norm'] = meta['chroma_step_norm']
    out['overshoot'] = _f(float(np.max(signed) - max(signed[0], signed[-1])))
    out['undershoot'] = _f(float(min(signed[0], signed[-1]) - np.min(signed)))
    out['centroid'] = _f(edge_centroid(signed))
    return out


def binomial_taps(name):
    if name == 'b3':
        return np.array([1, 2, 1], dtype=np.float64) / 4
    if name == 'b5':
        return np.array([1, 4, 6, 4, 1], dtype=np.float64) / 16
    if name == 'b7':
        return np.array([1, 6, 15, 20, 15, 6, 1], dtype=np.float64) / 64
    raise ValueError(name)


def freq_response_binomial_box(name, n=256):
    """Respuesta |H| 1D: binomial * caja 2×2 ([1,1]/2). No solo el binomial."""
    taps = binomial_taps(name)
    box = np.array([0.5, 0.5], dtype=np.float64)
    freq = np.linspace(0, 0.5, n, dtype=np.float64)
    w = 2 * math.pi * freq

    def dtft(h, omega):
        c = (len(h) - 1) / 2.0
        acc = np.zeros_like(omega, dtype=np.complex128)
        for k, t in enumerate(h):
            acc += t * np.exp(-1j * omega * (k - c))
        return acc

    hb, hx = dtft(taps, w), dtft(box, w)
    comb = hb * hx
    return {
        'kernel': name,
        'binomial_taps': [float(x) for x in taps],
        'box_taps': [0.5, 0.5],
        'freq_cycles_per_px': [_f(x) for x in freq[::8]],
        'mag_binomial': [_f(x) for x in np.abs(hb)[::8]],
        'mag_box2x2': [_f(x) for x in np.abs(hx)[::8]],
        'mag_combined': [_f(x) for x in np.abs(comb)[::8]],
        'dc_combined': _f(float(np.abs(comb[0]))),
        'note': 'H_eff(ω)=H_binomial(ω)·cos(ω/2) (caja 2 muestras, fase centrada). Subsample 2×2 aplica esto en x e y.',
    }


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
