"""Comparación de dos ejecuciones (base y candidata) métrica a métrica: mejora, empeora o equivalente.

Cada métrica tiene una dirección (menor es mejor, mayor es mejor, |x| menor es mejor, más cerca de 1 es mejor) o es
informativa. «Equivalente» significa que la diferencia cabe en la tolerancia de benchmark/thresholds.json, cuyo
fundamento está documentado en ese fichero; no hay umbrales de aceptación implícitos.
"""
import fnmatch
import json
import math

from .metrics import flatten

LOWER = ('mae', 'mse', 'rmse', 'profile_rmse', 'fringe_max', 'duv_mean', 'duv_p95', 'duv_p99', 'duv_max', 'mean_duv',
         'luma_rel_error_abs_mean', 'luma_abs_error_mean', 'mae_mean', 'mae_worst', 'rmse_mean', 'rmse_worst',
         'mean_duv_mean', 'mean_duv_worst', 'profile_rmse_mean', 'profile_rmse_worst', 'fringe_max_mean',
         'fringe_max_worst', 'duv_mean_mean', 'duv_mean_worst', 'duv_invalid')
HIGHER = ('psnr_db', 'correlation_rgb', 'correlation', 'psnr_db_mean', 'psnr_db_worst', 'duv_valid_fraction')
ABS = ('signed_mean_error', 'luminance_rel_error_pct', 'luma_rel_error_mean', 'overshoot_delta', 'undershoot_delta',
       'rise_10_90_delta', 'luminance_rel_error_pct_mean', 'luminance_rel_error_pct_worst', 'overshoot_delta_mean',
       'overshoot_delta_worst', 'undershoot_delta_mean', 'undershoot_delta_worst', 'rise_10_90_delta_mean',
       'rise_10_90_delta_worst', 'neutral_duv_to_d65_delta_mean', 'contrast_delta', 'width_50_delta', 'halo_delta',
       'contrast_delta_mean', 'contrast_delta_worst', 'width_50_delta_mean', 'width_50_delta_worst', 'halo_delta_mean',
       'halo_delta_worst')
UNITY = ('gradient_energy_ratio_edges', 'laplacian_energy_ratio_edges')


def direction(path):
    """Dirección de una métrica a partir de su ruta aplanada ('' = informativa)."""
    leaf = path.rsplit('.', 1)[-1]
    if '.profile.' in path or path.endswith('.samples') or '.reference.' in path or '.channels.' in path or 'reference_' in leaf:
        return ''
    if '.abs_error_percentiles.' in path:
        return 'lower'
    if leaf in UNITY:
        return 'unity'
    if leaf in LOWER:
        return 'lower'
    if leaf in HIGHER:
        return 'higher'
    if leaf in ABS:
        return 'abs'
    if leaf == 'candidate_duv_to_d65_mean':
        return 'lower'
    return ''


def load_thresholds(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def tolerance(path, thresholds):
    tol = dict(thresholds['equivalence']['default'])
    for rule in thresholds['equivalence'].get('rules', []):
        if fnmatch.fnmatch(path, rule['metric']):
            tol.update({k: v for k, v in rule.items() if k in ('abs', 'rel')})
    return tol


def badness(value, kind):
    return {'lower': value, 'higher': -value, 'abs': abs(value), 'unity': abs(value - 1)}[kind]


def as_number(value):
    """Número comparable: acepta float/int y las cadenas 'inf'/'-inf'/'nan' que escribe metrics._f."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if value == 'inf':
        return math.inf
    if value == '-inf':
        return -math.inf
    if value == 'nan':
        return math.nan
    return None


def status(path, base, cand, thresholds):
    kind = direction(path)
    if not kind:
        return None
    b, c = as_number(base), as_number(cand)
    if b is None or c is None:
        return None
    # NaN u otras no evaluables: nunca «equivalent» en silencio.
    if math.isnan(b) or math.isnan(c):
        return 'not_evaluable'
    if math.isinf(b) and math.isinf(c):
        return 'equivalent' if (b > 0) == (c > 0) else 'changed'
    if math.isinf(b) or math.isinf(c):
        # Uno infinito y otro finito: aplicar la dirección (p. ej. PSNR ∞ es mejor).
        return 'improved' if badness(c, kind) < badness(b, kind) else 'worse'
    tol = tolerance(path, thresholds)
    if abs(c - b) <= max(tol.get('abs', 0), tol.get('rel', 0) * max(abs(b), abs(c))):
        return 'equivalent'
    return 'improved' if badness(c, kind) < badness(b, kind) else 'worse'


def compare(base_metrics, cand_metrics, thresholds):
    """base_metrics, cand_metrics: metrics.json de dos ejecuciones. Devuelve filas y recuento por estado."""
    rows = []
    for key in sorted(set(base_metrics['results']) & set(cand_metrics['results'])):
        b = dict(flatten('', base_metrics['results'][key], []))
        c = dict(flatten('', cand_metrics['results'][key], []))
        for path in sorted(set(b) & set(c)):
            s = status(path, b[path], c[path], thresholds)
            if s:
                rows.append({'result': key, 'metric': path, 'base': b[path], 'candidate': c[path], 'status': s,
                             'direction': direction(path)})
    counts = {}
    for r in rows:
        counts[r['status']] = counts.get(r['status'], 0) + 1
    only = {'base_only': sorted(set(base_metrics['results']) - set(cand_metrics['results'])),
            'candidate_only': sorted(set(cand_metrics['results']) - set(base_metrics['results']))}
    return {'rows': rows, 'counts': counts, 'unmatched': only, 'acceptance': acceptance(cand_metrics, thresholds)}


def acceptance(metrics, thresholds):
    """Criterios de aceptación explícitos (vacíos por defecto): cada uno con métrica, min/max y fundamento."""
    out = []
    for rule in thresholds.get('acceptance', []):
        for key, result in sorted(metrics['results'].items()):
            for path, value in flatten('', result, []):
                if fnmatch.fnmatch(path, rule['metric']) and isinstance(value, (int, float)):
                    ok = (rule.get('min') is None or value >= rule['min']) and (rule.get('max') is None or value <= rule['max'])
                    out.append({'result': key, 'metric': path, 'value': value, 'pass': ok, 'basis': rule.get('basis', '')})
    return out
