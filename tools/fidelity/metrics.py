"""Métricas de fidelidad sobre RGB float lineal (DCI-P3 D65).

Nada se recorta a 0–1, nada pasa por gamma ni por 8 bits: los valores negativos y los superiores a 1 cuentan tal
cual. La normalización por exposición multiplica las dos imágenes por el mismo factor (ISO de referencia / ISO ·
2^−exposición), así que una amplificación lineal no se confunde con una pérdida de fidelidad.
"""
import math

import numpy as np

# Fila Y y matriz RGB→XYZ de DCI-P3 con blanco D65.
P3_TO_XYZ = np.array([[0.4865709486, 0.2656676932, 0.1982172852],
                      [0.2289745641, 0.6917385219, 0.0792869141],
                      [0.0000000000, 0.0451133819, 1.0439443689]])
P3_LUMA = P3_TO_XYZ[1]
D65_UV = (0.19783000664283, 0.46831999493879)
CHANNELS = 'RGB'


def luminance(img):
    return img @ P3_LUMA


def uv_prime(img):
    """Cromaticidad CIE 1976 u'v' de cada píxel (NaN donde X + 15Y + 3Z ≤ 0)."""
    xyz = img @ P3_TO_XYZ.T
    d = xyz[..., 0] + 15 * xyz[..., 1] + 3 * xyz[..., 2]
    with np.errstate(divide='ignore', invalid='ignore'):
        u = np.where(d > 0, 4 * xyz[..., 0] / d, np.nan)
        v = np.where(d > 0, 9 * xyz[..., 1] / d, np.nan)
    return u, v


def psnr(mse, peak):
    return float('inf') if mse == 0 else 10 * math.log10(peak * peak / mse)


def _f(x):
    """float JSON: inf y NaN se guardan como cadenas para que el JSON siga siendo estándar."""
    x = float(x)
    if math.isnan(x):
        return 'nan'
    if math.isinf(x):
        return 'inf' if x > 0 else '-inf'
    return x


def basic(ref, cand, peak=1.0):
    """MAE, MSE, RMSE, PSNR (pico explícito) y correlación RGB de dos imágenes (o recortes) ya en float64."""
    d = cand - ref
    mse = float(np.mean(d * d)) if d.size else float('nan')
    corr = float(np.corrcoef(ref.ravel(), cand.ravel())[0, 1]) if d.size > 1 and ref.std() > 0 and cand.std() > 0 else float('nan')
    return {'mae': _f(np.mean(np.abs(d))) if d.size else 'nan', 'mse': _f(mse), 'rmse': _f(math.sqrt(mse)) if d.size else 'nan',
            'psnr_db': _f(psnr(mse, peak)) if d.size else 'nan', 'psnr_peak': peak, 'correlation_rgb': _f(corr)}


def channel_stats(img):
    out = {}
    for k, c in enumerate(CHANNELS):
        x = img[..., k]
        out[c] = {'min': _f(x.min()), 'max': _f(x.max()), 'mean': _f(x.mean()),
                  'negative_fraction': _f(np.mean(x < 0)), 'negative_min': _f(min(x.min(), 0.0)),
                  'negative_mass': _f(np.sum(np.minimum(x, 0)) / x.size),
                  'above_one_fraction': _f(np.mean(x > 1.0))}
    return out


def edge_mask(ref_norm, cfg):
    """Bordes de la referencia: gradiente de log2(Y) por encima del percentil configurado, dilatado."""
    y = luminance(ref_norm)
    ly = np.log2(np.maximum(y, cfg['min_luminance']))
    gx = np.zeros_like(ly); gy = np.zeros_like(ly)
    gx[:, 1:-1] = (ly[:, 2:] - ly[:, :-2]) / 2
    gy[1:-1, :] = (ly[2:, :] - ly[:-2, :]) / 2
    g = np.hypot(gx, gy)
    valid = y > cfg['min_luminance']
    if not valid.any():
        return np.zeros_like(valid)
    mask = valid & (g > np.percentile(g[valid], cfg['percentile']))
    for _ in range(int(cfg.get('dilate', 0))):
        m = mask.copy()
        m[1:, :] |= mask[:-1, :]; m[:-1, :] |= mask[1:, :]; m[:, 1:] |= mask[:, :-1]; m[:, :-1] |= mask[:, 1:]
        mask = m
    return mask


def _gradient_energy(y, mask):
    gx = y[:, 2:] - y[:, :-2]
    gy = y[2:, :] - y[:-2, :]
    m = mask[1:-1, 1:-1]
    return float(np.sum(gx[1:-1, :][m] ** 2) + np.sum(gy[:, 1:-1][m] ** 2))


def _laplacian(y):
    return 4 * y[1:-1, 1:-1] - y[:-2, 1:-1] - y[2:, 1:-1] - y[1:-1, :-2] - y[1:-1, 2:]


def chroma_validity(duv):
    """Conteos de muestras con Δu'v' finito; una bajada de Δu'v' con más inválidas debe verse en el informe."""
    total = int(duv.size)
    valid = int(np.isfinite(duv).sum()) if total else 0
    return {'duv_samples': total, 'duv_valid': valid, 'duv_invalid': total - valid,
            'duv_valid_fraction': _f(valid / total) if total else 'nan'}


def luma_chroma(ref, cand, mask, min_y):
    """Error de luminancia (relativo) y de cromaticidad (Δu'v') dentro de una máscara, donde la referencia supera
    min_y (debajo, la cromaticidad es ruido)."""
    yr, yc = luminance(ref), luminance(cand)
    m = mask & (yr > min_y)
    if not m.any():
        return {'pixels': 0, 'duv_samples': 0, 'duv_valid': 0, 'duv_invalid': 0, 'duv_valid_fraction': 'nan'}
    ur, vr = uv_prime(ref[m]); uc, vc = uv_prime(cand[m])
    duv = np.hypot(uc - ur, vc - vr)
    validity = chroma_validity(duv)
    finite = duv[np.isfinite(duv)]
    rel = (yc[m] - yr[m]) / yr[m]
    out = {'pixels': int(m.sum()), **validity,
           'luma_rel_error_mean': _f(rel.mean()), 'luma_rel_error_abs_mean': _f(np.abs(rel).mean()),
           'luma_abs_error_mean': _f(np.abs(yc[m] - yr[m]).mean()),
           'duv_mean': _f(finite.mean()) if finite.size else 'nan',
           'duv_p95': _f(np.percentile(finite, 95)) if finite.size else 'nan',
           'duv_p99': _f(np.percentile(finite, 99)) if finite.size else 'nan'}
    return out


def fidelity(ref, cand, cfg, norm, ceiling):
    """Métricas globales de una pareja referencia/candidata.

    ref, cand: float32/64 HxWx3 tal como salen (sin normalizar). norm: factor de exposición común. ceiling: techo
    de señal de la candidata en sus unidades sin normalizar (None si no aplica)."""
    ref = ref.astype(np.float64); cand = cand.astype(np.float64)
    rn, cn = ref * norm, cand * norm
    d = cn - rn
    a = np.abs(d)
    out = {'normalization': {'factor': norm, 'to_iso': cfg['normalize_to_iso']},
           'raw': basic(ref, cand, cfg['psnr_peak']), 'normalized': basic(rn, cn, cfg['psnr_peak'])}
    yr, yc = luminance(rn), luminance(cn)
    out['normalized']['luminance_rel_error_pct'] = _f(100 * (yc.mean() / yr.mean() - 1))
    out['normalized']['abs_error_percentiles'] = {str(p): _f(np.percentile(a, p)) for p in cfg['error_percentiles']}
    out['normalized']['per_channel'] = {
        c: {'signed_mean_error': _f(d[..., k].mean()), 'mae': _f(a[..., k].mean()),
            'correlation': _f(np.corrcoef(rn[..., k].ravel(), cn[..., k].ravel())[0, 1])} for k, c in enumerate(CHANNELS)}
    out['reference_channels'] = channel_stats(ref)
    out['candidate_channels'] = channel_stats(cand)
    if ceiling:
        lim = ceiling * cfg['ceiling_margin']
        out['ceiling'] = {'value': ceiling, 'reference_fraction': _f(np.mean(np.any(ref >= lim, -1))),
                          'candidate_fraction': _f(np.mean(np.any(cand >= lim, -1)))}
    bins = cfg['luminance_bins']
    out['luminance_bins'] = []
    for lo, hi in zip(bins[:-1], bins[1:]):
        m = (yr >= lo) & (yr < hi)
        n = int(m.sum())
        row = {'low': lo, 'high': hi if hi < 1e8 else 'inf', 'pixels': n}
        if n:
            dm = d[m]
            row.update({'mae': _f(np.abs(dm).mean()), 'rmse': _f(math.sqrt(np.mean(dm * dm))), 'signed_mean_error': _f(dm.mean()),
                        'luminance_rel_error_pct': _f(100 * (yc[m].mean() / yr[m].mean() - 1)) if yr[m].mean() > 0 else 'nan'})
        out['luminance_bins'].append(row)
    everything = np.ones(yr.shape, bool)
    edges = edge_mask(rn, cfg['edge_mask'])
    out['luma_chroma'] = {'all': luma_chroma(rn, cn, everything, cfg['chroma_min_luminance']),
                          'edges': luma_chroma(rn, cn, edges, cfg['chroma_min_luminance']),
                          'flat': luma_chroma(rn, cn, ~edges, cfg['chroma_min_luminance'])}
    out['edges'] = {'mask_fraction': _f(edges.mean()),
                    'inside': basic(rn[edges], cn[edges], cfg['psnr_peak']) if edges.any() else {},
                    'outside': basic(rn[~edges], cn[~edges], cfg['psnr_peak']) if (~edges).any() else {}}
    er, ec = _gradient_energy(yr, edges), _gradient_energy(yc, edges)
    lr, lc = _laplacian(yr)[edges[1:-1, 1:-1]], _laplacian(yc)[edges[1:-1, 1:-1]]
    out['sharpness'] = {'gradient_energy_ratio_edges': _f(ec / er) if er > 0 else 'nan',
                        'laplacian_energy_ratio_edges': _f(np.sum(lc ** 2) / np.sum(lr ** 2)) if lr.size and np.sum(lr ** 2) > 0 else 'nan'}
    out['neutral'] = neutral(rn, cn, cfg['neutral_mask'])
    return out, edges


def neutral(ref, cand, cfg, mask=None):
    """Píxeles casi neutros de la referencia (Δu'v' a D65 < max_duv): desviación de la candidata del neutro."""
    yr = luminance(ref)
    ur, vr = uv_prime(ref)
    dr = np.hypot(ur - D65_UV[0], vr - D65_UV[1])
    m = (yr > cfg['min_luminance']) & np.isfinite(dr) & (dr < cfg['max_duv'])
    if mask is not None:
        m &= mask
    if not m.any():
        return {'pixels': 0, 'duv_samples': 0, 'duv_valid': 0, 'duv_invalid': 0, 'duv_valid_fraction': 'nan'}
    uc, vc = uv_prime(cand[m])
    dc = np.hypot(uc - D65_UV[0], vc - D65_UV[1])
    pair = np.hypot(uc - ur[m], vc - vr[m])
    validity = chroma_validity(pair)
    return {'pixels': int(m.sum()), **validity,
            'reference_duv_to_d65_mean': _f(np.nanmean(dr[m])),
            'candidate_duv_to_d65_mean': _f(np.nanmean(dc)), 'duv_mean': _f(np.nanmean(pair)), 'duv_p95': _f(np.nanpercentile(pair, 95))}


def dark_frame(img):
    """Nivel de negro y ruido de un dark frame: media, desviación, percentiles y ruido de filas y columnas."""
    out = {}
    img = img.astype(np.float64)
    for k, c in enumerate(CHANNELS):
        x = img[..., k]
        out[c] = {'mean': _f(x.mean()), 'std': _f(x.std()), 'p0_1': _f(np.percentile(x, 0.1)), 'p99_9': _f(np.percentile(x, 99.9)),
                  'row_noise_std': _f(x.mean(1).std()), 'column_noise_std': _f(x.mean(0).std())}
    return out


def flatten(prefix, value, out):
    """{'a': {'b': 1}} → [('a.b', 1)] para el CSV y la comparación."""
    if isinstance(value, dict):
        for k, v in value.items():
            flatten(prefix + '.' + str(k) if prefix else str(k), v, out)
    elif isinstance(value, list):
        for i, v in enumerate(value):
            flatten('%s[%d]' % (prefix, i), v, out)
    else:
        out.append((prefix, value))
    return out
