"""Métricas EXP-005 pre-encode / post-SDK. No eligen ganador por PSNR."""
from __future__ import annotations

import numpy as np

from tools.exp004_halo import chroma_esf_mtf, chroma_error_vs_ref, halo_extent_px, uv_local
from tools.fidelity import metrics as M


def _f(x):
    return M._f(x)


def zippering(rgb):
    """Oscilación de 1 px en croma (R−G) entre filas/columnas pares e impares."""
    rgb = np.asarray(rgb, dtype=np.float64)
    rg = rgb[..., 0] - rgb[..., 1]
    bg = rgb[..., 2] - rgb[..., 1]
    hr = min(rg[0::2].shape[0], rg[1::2].shape[0])
    wr = min(rg[:, 0::2].shape[1], rg[:, 1::2].shape[1])
    row = np.mean(np.abs(rg[0::2][:hr] - rg[1::2][:hr]))
    col = np.mean(np.abs(rg[:, 0::2][:, :wr] - rg[:, 1::2][:, :wr]))
    hb = min(bg[0::2].shape[0], bg[1::2].shape[0])
    wb = min(bg[:, 0::2].shape[1], bg[:, 1::2].shape[1])
    return {'row_rg': _f(row), 'col_rg': _f(col),
            'row_bg': _f(np.mean(np.abs(bg[0::2][:hb] - bg[1::2][:hb]))),
            'col_bg': _f(np.mean(np.abs(bg[:, 0::2][:, :wb] - bg[:, 1::2][:, :wb])))}


def false_color(rgb, rgb_ref, y_min=0.01, duv_thr=0.02):
    """Fracción de píxeles con Δu′v′ alta donde la referencia es casi neutra."""
    y = M.luminance(rgb_ref)
    u, v = M.uv_prime(rgb_ref)
    du = u - M.D65_UV[0]
    dv = v - M.D65_UV[1]
    neut = (y > y_min) & np.isfinite(du) & (np.hypot(du, dv) < 0.015)
    if not neut.any():
        return {'neutral_pixels': 0, 'false_color_fraction': 'nan'}
    uc, vc = M.uv_prime(rgb)
    mag = np.hypot(uc - u, vc - v)
    hit = mag[neut]
    hit = hit[np.isfinite(hit)]
    return {'neutral_pixels': int(neut.sum()),
            'false_color_fraction': _f(np.mean(hit > duv_thr)) if hit.size else 'nan',
            'duv_mean_on_neutral': _f(hit.mean()) if hit.size else 'nan'}


def aliasing_score(rgb):
    """Energía cerca de Nyquist en croma (DFT de R−G)."""
    rg = np.asarray(rgb[..., 0] - rgb[..., 1], dtype=np.float64)
    spec = np.abs(np.fft.rfft2(rg - rg.mean()))
    h, w = spec.shape
    hi = spec[int(h * 0.4):, int(w * 0.4):]
    lo = spec[: max(2, h // 8), : max(2, w // 8)]
    return {'nyquist_over_dc': _f(hi.mean() / max(lo.mean(), 1e-12))}


def microtexture(rgb, rgb_ref):
    def e(img):
        y = M.luminance(img)
        out = np.zeros_like(y)
        out[1:-1, 1:-1] = (
            y[:-2, 1:-1] + y[2:, 1:-1] + y[1:-1, :-2] + y[1:-1, 2:] - 4 * y[1:-1, 1:-1])
        return float(np.std(out))
    er, ec = e(rgb_ref), e(rgb)
    return {'lap_std_ref': _f(er), 'lap_std_cand': _f(ec),
            'texture_ratio': _f(ec / er) if er > 1e-12 else 'nan'}


def clipping(rgb):
    x = np.asarray(rgb, dtype=np.float64)
    return {'neg_frac': _f(np.mean(x < 0)), 'above_one': _f(np.mean(x > 1)),
            'min': _f(x.min()), 'max': _f(x.max())}


def pair_metrics(cand, ref, edges=None):
    out = {
        'basic': M.basic(ref, cand, peak=1.0),
        'uv': uv_local(cand, ref),
        'zippering': zippering(cand),
        'false_color': false_color(cand, ref),
        'aliasing': aliasing_score(cand),
        'microtexture': microtexture(cand, ref),
        'clip_cand': clipping(cand),
        'clip_ref': clipping(ref),
    }
    y = M.luminance(cand)
    yref = M.luminance(ref)
    if y.size > 1 and yref.std() > 0 and y.std() > 0:
        out['detail_correlation'] = _f(np.corrcoef(y.ravel(), yref.ravel())[0, 1])
    else:
        out['detail_correlation'] = 'nan'
    cb = cand[..., 2] - cand[..., 1]
    cr = cand[..., 0] - cand[..., 1]
    cb_r = ref[..., 2] - ref[..., 1]
    cr_r = ref[..., 0] - ref[..., 1]
    out['chroma_error'] = chroma_error_vs_ref(cb, cr, cb_r, cr_r)
    mag = np.hypot(cb - cb_r, cr - cr_r)
    out['halo'] = {
        'peak': out['chroma_error']['peak_chroma_error'],
        'integrated': out['chroma_error']['integrated_abs_chroma_error'],
        'extent_px': _f(halo_extent_px(mag.mean(0) if mag.ndim == 2 else mag, float(np.max(mag)) * 0.2)),
    }
    if edges:
        erois = []
        for name, line, half in edges:
            mtf = chroma_esf_mtf(cand, line, half)
            mtf_r = chroma_esf_mtf(ref, line, half)
            erois.append({'name': name, 'cand': mtf, 'ref': mtf_r})
        out['chroma_mtf'] = erois
    return out
