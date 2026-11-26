"""Métricas EXP-005 pre-encode / post-SDK. No eligen ganador por PSNR.

Unidades del halo (error RGB lineal P3, no ΔE*):
  peak       — max hypot(Δ(B−G), Δ(R−G))  [adimensional, canales lineales]
  p95/p99/p99.9 — percentiles del mismo campo, máscara de luma común
  integrated — suma de esa magnitud sobre píxeles válidos  [unidades·píxel]
  extent_px  — píxeles consecutivos sobre 20 % del pico en un perfil de borde  [px]
  peak_duv   — max Δu′v′ en la misma máscara  [CIE 1976, invariante de escala]
"""
from __future__ import annotations

import numpy as np

from tools.exp004_halo import CHROMA_MIN_Y, chroma_esf_mtf, extract_profile, halo_extent_px
from tools.fidelity import metrics as M

UV_YMIN = CHROMA_MIN_Y  # 0.005 — misma convención del banco
PERCENTILES = (95.0, 99.0, 99.9)

HALO_UNITS = {
    'peak': 'hypot(Δ(B−G), Δ(R−G)) in linear P3 channel units (not ΔE*)',
    'percentiles': 'same field, common luma mask',
    'integrated': 'sum of magnitude over valid pixels (channel-units · pixel)',
    'extent_px': 'consecutive pixels on an edge profile above 20% of that profile peak',
    'peak_duv': "CIE 1976 Δu′v′ on the same mask (scale-invariant)",
}


def _f(x):
    return M._f(x)


def common_uv_mask(ref, cand, y_min=UV_YMIN):
    """Máscara común: Y_ref y Y_cand > y_min y u′v′ finitos en ambos."""
    ref = np.asarray(ref, dtype=np.float64)
    cand = np.asarray(cand, dtype=np.float64)
    yr, yc = M.luminance(ref), M.luminance(cand)
    ur, vr = M.uv_prime(ref)
    uc, vc = M.uv_prime(cand)
    ok = ((yr > y_min) & (yc > y_min)
          & np.isfinite(ur) & np.isfinite(vr) & np.isfinite(uc) & np.isfinite(vc))
    return ok, yr, yc, ur, vr, uc, vc


def uv_masked(cand, ref, y_min=UV_YMIN):
    ok, yr, yc, ur, vr, uc, vc = common_uv_mask(ref, cand, y_min)
    out = {
        'y_min': y_min,
        'uv_samples': int(ok.sum()),
        'uv_skipped': int((~ok).sum()),
        'mask': 'Y_ref>ymin AND Y_cand>ymin AND finite u′v′ both',
    }
    if not ok.any():
        out.update({'duv_mean': 'nan', 'duv_max': 'nan', 'duv_p95': 'nan', 'duv_p99': 'nan',
                    'duv_p99_9': 'nan', 'duv_valid_fraction': 0.0})
        return out
    duv = np.hypot(uc[ok] - ur[ok], vc[ok] - vr[ok])
    out['duv_mean'] = _f(duv.mean())
    out['duv_max'] = _f(duv.max())
    out['duv_p95'] = _f(np.percentile(duv, 95))
    out['duv_p99'] = _f(np.percentile(duv, 99))
    out['duv_p99_9'] = _f(np.percentile(duv, 99.9))
    out['duv_valid_fraction'] = _f(ok.mean())
    return out


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
    """Fracción de neutros de la referencia con Δu′v′ alta, máscara común."""
    ok, yr, yc, ur, vr, uc, vc = common_uv_mask(rgb_ref, rgb, y_min)
    du = ur - M.D65_UV[0]
    dv = vr - M.D65_UV[1]
    neut = ok & (np.hypot(du, dv) < 0.015)
    if not neut.any():
        return {'neutral_pixels': 0, 'false_color_fraction': 'nan', 'mask': 'common_uv'}
    mag = np.hypot(uc - ur, vc - vr)[neut]
    return {'neutral_pixels': int(neut.sum()),
            'false_color_fraction': _f(np.mean(mag > duv_thr)),
            'duv_mean_on_neutral': _f(mag.mean()),
            'mask': 'common_uv AND |u′v′_ref−D65|<0.015'}


def aliasing_score(rgb):
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


def rgb_chroma_mag(cand, ref):
    cb = cand[..., 2] - cand[..., 1]
    cr = cand[..., 0] - cand[..., 1]
    cb_r = ref[..., 2] - ref[..., 1]
    cr_r = ref[..., 0] - ref[..., 1]
    return np.hypot(cb - cb_r, cr - cr_r)


def halo_stats(cand, ref, edges=None, y_min=UV_YMIN):
    mag = rgb_chroma_mag(cand, ref)
    ok, yr, yc, ur, vr, uc, vc = common_uv_mask(ref, cand, y_min)
    valid = mag[ok] if ok.any() else mag.ravel()
    duv = np.hypot(uc - ur, vc - vr)
    duv_v = duv[ok] if ok.any() else duv[np.isfinite(duv)]
    yrel = mag / np.maximum(yr, 1e-6)
    yrel_v = yrel[ok] if ok.any() else yrel.ravel()
    peak_idx = np.unravel_index(int(np.argmax(mag)), mag.shape)
    py, px = int(peak_idx[0]), int(peak_idx[1])
    out = {
        'units': HALO_UNITS,
        'peak': _f(float(mag.max())),
        'p95': _f(np.percentile(valid, 95)) if valid.size else 'nan',
        'p99': _f(np.percentile(valid, 99)) if valid.size else 'nan',
        'p99_9': _f(np.percentile(valid, 99.9)) if valid.size else 'nan',
        'integrated': _f(float(valid.sum())) if valid.size else 'nan',
        'mean': _f(float(valid.mean())) if valid.size else 'nan',
        'n_valid': int(valid.size),
        'peak_duv': _f(float(duv_v.max())) if getattr(duv_v, 'size', 0) else 'nan',
        'peak_rel_y': _f(float(yrel_v.max())) if getattr(yrel_v, 'size', 0) else 'nan',
        'peak_at': {'y': py, 'x': px},
        'peak_y_ref': _f(float(yr[py, px])),
        'peak_y_cand': _f(float(yc[py, px])),
        'peak_in_highlight': bool(yr[py, px] > 0.64 or yc[py, px] > 0.64),
        'peak_clipped': bool(yr[py, px] > 1.0 or yc[py, px] > 1.0
                             or np.any(ref[py, px] > 1) or np.any(cand[py, px] > 1)),
        'peak_excl_highlight': _f(float(mag[yr <= 0.64].max())) if (yr <= 0.64).any() else 'nan',
    }
    # Correspondencia espacial: p99 de mag ∩ máscara de bordes de la referencia
    cfg = {'percentile': 90, 'dilate': 2, 'min_luminance': 0.002}
    em = M.edge_mask(ref, cfg)
    p99 = np.percentile(valid, 99) if valid.size else mag.max()
    hot = mag >= p99
    out['spatial'] = {
        'edge_mask_fraction': _f(float(em.mean())),
        'hot_p99_fraction': _f(float(hot.mean())),
        'hot_on_edges_fraction': _f(float((hot & em).sum() / max(hot.sum(), 1))),
        'note': 'fracción de píxeles p99-halo que caen en bordes (gradiente log Y p90, dilatados)',
    }
    if edges:
        erois = []
        for name, line, half in edges:
            prof = extract_profile(mag, line, half)
            pk = float(np.max(prof)) if len(prof) else 0.0
            ext = halo_extent_px(np.asarray(prof), 0.2 * pk) if pk > 0 else 0
            erois.append({'name': name, 'profile_peak': _f(pk), 'extent_px': _f(ext)})
        out['extent_by_edge'] = erois
        out['extent_px'] = _f(max((e['extent_px'] if isinstance(e['extent_px'], (int, float)) else 0)
                                  for e in erois) if erois else 0)
    else:
        out['extent_px'] = 'nan'
    return out


def pair_metrics(cand, ref, edges=None):
    cand = np.asarray(cand, dtype=np.float64)
    ref = np.asarray(ref, dtype=np.float64)
    out = {
        'basic': M.basic(ref, cand, peak=1.0),
        'uv': uv_masked(cand, ref),
        'zippering': zippering(cand),
        'false_color': false_color(cand, ref),
        'aliasing': aliasing_score(cand),
        'microtexture': microtexture(cand, ref),
        'clip_cand': clipping(cand),
        'clip_ref': clipping(ref),
        'halo': halo_stats(cand, ref, edges),
    }
    y, yref = M.luminance(cand), M.luminance(ref)
    if y.size > 1 and yref.std() > 0 and y.std() > 0:
        out['detail_correlation'] = _f(np.corrcoef(y.ravel(), yref.ravel())[0, 1])
    else:
        out['detail_correlation'] = 'nan'
    if edges:
        erois = []
        for name, line, half in edges:
            mtf = chroma_esf_mtf(cand, line, half)
            mtf_r = chroma_esf_mtf(ref, line, half)
            slim = {}
            for src, tag in ((mtf, 'cand'), (mtf_r, 'ref')):
                slim[tag] = {k: src.get(k) for k in ('mtf50', 'overshoot', 'undershoot', 'absent', 'chroma_step_norm')
                             if k in src or src.get('absent')}
                if 'mtf_at' in src:
                    slim[tag]['mtf_at'] = src['mtf_at']
            erois.append({'name': name, **slim})
        out['chroma_mtf'] = erois
    return out
