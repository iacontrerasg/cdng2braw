#!/usr/bin/env python3
"""Agrega EXP-003 con identidad RD (braw_inspect) — no SDK frame_bytes como fuente primaria."""
from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.fidelity import rd as RD

RAW = RD.RAW_BYTES_12BIT

# Tolerancias explícitas (no multiplicar PSNR por 0.998).
TOL = {
    'bytes_pct': 0.02,          # ±2 % tasa
    'psnr_db': 0.05,            # dB absoluto
    'grad': 0.02,               # |grad − ref| (proximidad a 1 / a ref)
    'lap': 0.03,
    'detail_corr': 0.01,
    'duv': 0.0005,
}

# Cobertura esperada por familia de run_id (fotogramas del banco).
EXPECTED_FRAMES = {
    'default_a04': ('A04_f044', 'A04_f049'),
    'series_a': ('A01_f052', 'A02_f029', 'A03_f046', 'A04_f044', 'A04_f049', 'A05_f053'),
}


def expected_keys_for(run_id):
    if run_id.startswith('exp003-k-'):
        return EXPECTED_FRAMES['series_a']
    return EXPECTED_FRAMES['default_a04']


def classify_alignment(result):
    al = ((result.get('reference_fidelity') or {}).get('alignment') or {})
    if al.get('gate_failed') and al.get('quality_rejected'):
        return 'aligned_detail_rejected'
    if al.get('gate_failed') and al.get('gate_reason') == 'shift':
        return 'geometric_misaligned'
    if al.get('gate_failed'):
        return 'gate_failed'
    if al.get('geometric_aligned') and al.get('detail_gate_pass'):
        return 'valid'
    return 'unknown'


def load_exp003_runs(runs_root, prefix='exp003-'):
    runs = []
    for name in sorted(os.listdir(runs_root)):
        if not name.startswith(prefix):
            continue
        path = os.path.join(runs_root, name)
        if not os.path.isfile(os.path.join(path, 'metrics.json')):
            continue
        runs.append(RD.load_run(path))
    return runs


def enrich_row(row, result):
    al = ((result.get('reference_fidelity') or {}).get('alignment') or {})
    row['detail_corr'] = al.get('detail_correlation')
    row['geometric_aligned'] = al.get('geometric_aligned')
    row['detail_gate_pass'] = al.get('detail_gate_pass')
    row['quality_rejected'] = al.get('quality_rejected')
    row['gate_failed'] = al.get('gate_failed')
    row['gate_reason'] = al.get('gate_reason')
    row['candidate_class'] = classify_alignment(result)
    env = ((result.get('setup') or {}).get('encode') or {}).get('experimental_env') or {}
    row['deadzone'] = env.get('CDNG_DEADZONE')
    row['policy'] = env.get('CDNG_DEADZONE_POLICY')
    row['chunk_bytes'] = None
    if row.get('sample_bytes') is not None:
        from tools.fidelity import braw_inspect
        row['chunk_bytes'] = int(row['sample_bytes'] - braw_inspect.BMDF_SIZE)
    return row


def collect(runs_root):
    runs = load_exp003_runs(runs_root)
    rows = []
    for run in runs:
        for key, result in sorted(run['metrics'].get('results', {}).items()):
            try:
                row = RD.frame_row(run, key, result, require_bitstream=True, verify_file_sha=True)
            except RD.RdError as e:
                rows.append({
                    'run': run['id'], 'result': key, 'error': str(e),
                    'candidate_class': 'identity_or_conversion_error',
                    'test': key.split('_f')[0] if '_f' in key else None,
                })
                continue
            enrich_row(row, result)
            rows.append(row)
    return rows, runs


def mean(vals):
    vals = [v for v in vals if isinstance(v, (int, float))]
    return sum(vals) / len(vals) if vals else None


def metric_bundle(frames):
    """Medias sobre el conjunto dado (puede ser vacío → None)."""
    return {
        'n': len(frames),
        'psnr_mean': mean([r.get('psnr') for r in frames]),
        'duv_edges_mean': mean([r.get('duv_edges') for r in frames]),
        'grad_mean': mean([r.get('grad') for r in frames]),
        'lap_mean': mean([r.get('lap') for r in frames]),
        'detail_corr_mean': mean([r.get('detail_corr') for r in frames]),
        'sample_bytes_mean': mean([r.get('sample_bytes') for r in frames]),
        'payload_bytes_mean': mean([r.get('payload_bytes') for r in frames]),
        'sdk_frame_bytes_mean': mean([r.get('sdk_frame_bytes') for r in frames]),
        'ratio_sample_mean': mean([r.get('ratio_sample_12bit') for r in frames]),
        'ratio_payload_mean': mean([r.get('ratio_payload_12bit') for r in frames]),
        'ratio_sdk_mean': mean([r.get('ratio_sdk_12bit') for r in frames]),
        'q_eff_mean': mean([r.get('q_eff') for r in frames]),
    }


def worst_case(frames):
    """Peor caso entre fotogramas medibles (sin errores de identidad)."""
    ok = [r for r in frames if r.get('candidate_class') != 'identity_or_conversion_error'
          and r.get('psnr') is not None]
    if not ok:
        return None
    by_psnr = min(ok, key=lambda r: r.get('psnr') if r.get('psnr') is not None else 1e9)
    by_detail = min(ok, key=lambda r: r.get('detail_corr') if r.get('detail_corr') is not None else 1e9)
    by_bytes = max(ok, key=lambda r: r.get('sample_bytes') or 0)
    return {
        'lowest_psnr': {'result': by_psnr.get('result'), 'psnr': by_psnr.get('psnr'),
                        'class': by_psnr.get('candidate_class')},
        'lowest_detail_corr': {'result': by_detail.get('result'),
                               'detail_corr': by_detail.get('detail_corr'),
                               'class': by_detail.get('candidate_class')},
        'largest_sample_bytes': {'result': by_bytes.get('result'),
                                 'sample_bytes': by_bytes.get('sample_bytes')},
    }


def aggregate(rows):
    by = {}
    for r in rows:
        by.setdefault(r['run'], []).append(r)
    out = []
    for run, rs in sorted(by.items()):
        expected = list(expected_keys_for(run))
        got_keys = {r.get('result') for r in rs}
        measurable = [r for r in rs if r.get('candidate_class') != 'identity_or_conversion_error'
                      and r.get('error') is None]
        valid = [r for r in rs if r.get('candidate_class') == 'valid']
        rejected = [r for r in rs if r.get('candidate_class') == 'aligned_detail_rejected']
        errors = [r for r in rs if r.get('candidate_class') == 'identity_or_conversion_error']
        missing = [k for k in expected if k not in got_keys]
        # Elegibilidad estricta: todos los esperados presentes, válidos, sin errores.
        series_approved = (
            len(missing) == 0
            and len(errors) == 0
            and len(valid) == len(expected)
            and all(r.get('result') in expected for r in valid)
        )
        all_m = metric_bundle(measurable)
        out.append({
            'run': run,
            'expected_frames': expected,
            'expected_count': len(expected),
            'effective_count': len(got_keys),
            'coverage_complete': len(missing) == 0 and len(got_keys) >= len(expected),
            'missing_frames': missing,
            'n_frames': len(rs),
            'n_measurable': len(measurable),
            'n_valid': len(valid),
            'n_detail_rejected': len(rejected),
            'n_errors': len(errors),
            # Aprobado para toda la serie / conjunto esperado.
            'series_approved': series_approved,
            # Conservado como experimental aunque falle algún frame.
            'experimental_retained': len(measurable) > 0,
            'all_frames': all_m,
            'valid_frames_only': metric_bundle(valid),
            'worst_case': worst_case(measurable),
            # Compat: medias principales = todos los medibles (sin sesgo de selección).
            'psnr_mean': all_m['psnr_mean'],
            'duv_edges_mean': all_m['duv_edges_mean'],
            'grad_mean': all_m['grad_mean'],
            'lap_mean': all_m['lap_mean'],
            'detail_corr_mean': all_m['detail_corr_mean'],
            'sample_bytes_mean': all_m['sample_bytes_mean'],
            'payload_bytes_mean': all_m['payload_bytes_mean'],
            'sdk_frame_bytes_mean': all_m['sdk_frame_bytes_mean'],
            'ratio_sample_mean': all_m['ratio_sample_mean'],
            'ratio_payload_mean': all_m['ratio_payload_mean'],
            'ratio_sdk_mean': all_m['ratio_sdk_mean'],
            'q_eff': all_m['q_eff_mean'],
            # Resultados individuales (campos esenciales; sin volcar todo RD).
            'frames': [_slim_frame(r) for r in rs],
        })
    return out


_FRAME_KEYS = (
    'run', 'result', 'test', 'candidate_class', 'error',
    'psnr', 'sample_bytes', 'payload_bytes', 'sdk_frame_bytes', 'chunk_bytes',
    'detail_corr', 'grad', 'lap', 'duv_edges',
    'ratio_sample_12bit', 'ratio_payload_12bit', 'ratio_sdk_12bit', 'q_eff',
    'deadzone', 'policy', 'candidate_sha256',
    'geometric_aligned', 'detail_gate_pass', 'quality_rejected',
    'gate_failed', 'gate_reason',
)


def _slim_frame(r):
    return {k: r.get(k) for k in _FRAME_KEYS}


def _near(a, b, tol):
    if a is None or b is None:
        return False
    return abs(a - b) <= tol


def _not_worse_psnr(a, b, tol):
    return a is not None and b is not None and a >= b - tol


def _not_worse_bytes(a, b, tol_pct):
    return a is not None and b is not None and a <= b * (1 + tol_pct)


def _sharpness_ok(cand, ref, tol):
    """Proximidad al valor de referencia (no «más alto = mejor»)."""
    if cand is None or ref is None:
        return False
    return abs(cand - ref) <= tol


def dominates(a, b, tol=TOL):
    """a domina b: no empeora métricas clave (con tolerancias) y mejora bytes o PSNR."""
    if not a.get('series_approved') or not b.get('series_approved'):
        return False
    if a['sample_bytes_mean'] is None or b['sample_bytes_mean'] is None:
        return False
    bytes_ok = _not_worse_bytes(a['sample_bytes_mean'], b['sample_bytes_mean'], tol['bytes_pct'])
    psnr_ok = _not_worse_psnr(a['psnr_mean'], b['psnr_mean'], tol['psnr_db'])
    detail_ok = _not_worse_psnr(a['detail_corr_mean'], b['detail_corr_mean'], tol['detail_corr'])
    duv_ok = (a['duv_edges_mean'] is None or b['duv_edges_mean'] is None
              or a['duv_edges_mean'] <= b['duv_edges_mean'] + tol['duv'])
    grad_ok = _sharpness_ok(a['grad_mean'], b['grad_mean'], tol['grad'])
    lap_ok = _sharpness_ok(a['lap_mean'], b['lap_mean'], tol['lap'])
    better_bytes = a['sample_bytes_mean'] < b['sample_bytes_mean'] * (1 - tol['bytes_pct'] / 2)
    better_psnr = (a['psnr_mean'] or 0) > (b['psnr_mean'] or 0) + tol['psnr_db']
    return (bytes_ok and psnr_ok and detail_ok and duv_ok and grad_ok and lap_ok
            and (better_bytes or better_psnr))


def paired_frame_keys(a, b):
    """Intersección de result keys medibles."""
    ka = {f.get('result') for f in a.get('frames') or []
          if f.get('candidate_class') != 'identity_or_conversion_error'}
    kb = {f.get('result') for f in b.get('frames') or []
          if f.get('candidate_class') != 'identity_or_conversion_error'}
    return sorted(ka & kb)


def paired_means(agg_entry, keys):
    frames = [f for f in (agg_entry.get('frames') or []) if f.get('result') in keys]
    return metric_bundle(frames)


def compare_to_ref(cands, ref, tol=TOL):
    """Comparación multidimensional sobre fotogramas pareados; solo series_approved."""
    wins = []
    if not ref or not ref.get('series_approved'):
        return wins
    for c in cands:
        if c['run'] == ref['run']:
            continue
        if not c.get('series_approved'):
            # Conservada experimentalmente; no cuenta como victoria de frontera.
            continue
        keys = paired_frame_keys(c, ref)
        if not keys:
            continue
        cm, rm = paired_means(c, keys), paired_means(ref, keys)
        same_rate = _near(cm['sample_bytes_mean'], rm['sample_bytes_mean'],
                          (rm['sample_bytes_mean'] or 0) * tol['bytes_pct'])
        same_psnr = _near(cm['psnr_mean'], rm['psnr_mean'], tol['psnr_db'])
        better_psnr = (cm['psnr_mean'] or 0) > (rm['psnr_mean'] or 0) + tol['psnr_db']
        better_bytes = (cm['sample_bytes_mean'] or 1e18) < (rm['sample_bytes_mean'] or 0) * (
            1 - tol['bytes_pct'] / 2)
        detail_ok = _not_worse_psnr(cm['detail_corr_mean'], rm['detail_corr_mean'], tol['detail_corr'])
        duv_ok = (cm['duv_edges_mean'] is None or rm['duv_edges_mean'] is None
                  or cm['duv_edges_mean'] <= rm['duv_edges_mean'] + tol['duv'])
        grad_ok = _sharpness_ok(cm['grad_mean'], rm['grad_mean'], tol['grad'])
        lap_ok = _sharpness_ok(cm['lap_mean'], rm['lap_mean'], tol['lap'])
        fidelity_ok = detail_ok and duv_ok and grad_ok and lap_ok
        if same_rate and better_psnr and fidelity_ok:
            wins.append({'kind': 'quality_at_rate', 'run': c['run'],
                         'paired_frames': keys, 'cand': cm, 'ref': rm})
        if same_psnr and better_bytes and fidelity_ok:
            wins.append({'kind': 'bytes_at_quality', 'run': c['run'],
                         'paired_frames': keys, 'cand': cm, 'ref': rm})
    return wins


def resolve_discrepancy(agg, hist):
    """SDK vs sample; equivalencia histórica solo si hashes/tamaños lo demuestran."""
    a1 = next((a for a in agg if a['run'] == 'exp003-a1-dz50'), None)
    out = {
        'a1_present': a1 is not None,
        'raw_reference_bytes': RAW,
    }
    if a1:
        out.update({
            'a1_sample_bytes': a1.get('sample_bytes_mean'),
            'a1_sdk_frame_bytes': a1.get('sdk_frame_bytes_mean'),
            'a1_ratio_sample': a1.get('ratio_sample_mean'),
            'a1_ratio_sdk': a1.get('ratio_sdk_mean'),
            'sdk_vs_sample_note': (
                'sdk_frame_bytes suele ser mayor y alineado a 4096; '
                'ratio oficial = RAW/sample_bytes'
            ),
        })
    if hist is None:
        out['historical_equivalence'] = 'not_verifiable'
        out['historical_reason'] = 'artefacto EXP-002a ausente o no comparable en esta ejecución'
        return out
    if hist.get('error'):
        out['historical_equivalence'] = 'not_verifiable'
        out['historical_reason'] = hist['error']
        return out
    checks = (
        hist.get('sample_bytes_equal'),
        hist.get('payload_bytes_equal'),
        hist.get('candidate_sha_equal'),
        hist.get('q_eff_equal'),
        hist.get('psnr_equal'),
    )
    if all(checks):
        out['historical_equivalence'] = 'verified_equivalent'
        out['historical_reason'] = (
            'sample_bytes, payload_bytes, candidate_sha256, q_eff y PSNR coinciden con %s'
            % hist.get('run')
        )
    else:
        out['historical_equivalence'] = 'not_equivalent_or_partial'
        out['historical_reason'] = 'alguna identidad no coincide: %s' % {
            'sample_bytes_equal': hist.get('sample_bytes_equal'),
            'payload_bytes_equal': hist.get('payload_bytes_equal'),
            'candidate_sha_equal': hist.get('candidate_sha_equal'),
            'q_eff_equal': hist.get('q_eff_equal'),
            'psnr_equal': hist.get('psnr_equal'),
        }
    return out


def classify_strategy(plane_wins, band_wins, a1, plane_aggs, band_aggs):
    """Clasificación por evidencia de frontera, no por conteo arbitrario ni umbral 5:1."""
    if a1 is None:
        return 'E'
    # Sin series aprobadas adaptativas → no hay mejora demostrada de frontera.
    approved_adapt = [a for a in (plane_aggs + band_aggs) if a.get('series_approved')]
    if not approved_adapt:
        # Uniforme aprobado y adaptativas no: C o D según si alguna retuvo experimentalmente
        retained = [a for a in (plane_aggs + band_aggs) if a.get('experimental_retained')]
        if a1.get('series_approved') and retained:
            return 'C'
        if a1.get('series_approved'):
            return 'D'
        return 'E'
    if plane_wins or band_wins:
        # Victoria multidimensional verificada → A; si solo local/parcial sin wins fuertes → B
        return 'A' if len(plane_wins) + len(band_wins) >= 1 else 'B'
    # Adaptativas aprobadas pero sin ganar a uniforme en Pareto → equivalencia / dominadas
    if a1.get('series_approved'):
        # Si alguna adaptativa tiene peor tasa y no mejor fidelidad pareada → D; si similares → C
        return 'C'
    return 'E'


def verify_historical(runs_root, compare_id, a1_agg):
    hist_path = os.path.join(runs_root, compare_id)
    if not os.path.isdir(hist_path) or not a1_agg:
        return None
    try:
        hrun = RD.load_run(hist_path)
        hrows = []
        for k, res in hrun['metrics']['results'].items():
            if 'f044' not in k:
                continue
            hrows.append(RD.frame_row(hrun, k, res))
        cur = next((f for f in a1_agg.get('frames') or [] if 'f044' in (f.get('result') or '')), None)
        if not hrows or not cur:
            return {'error': 'no overlapping A04_f044 frames'}
        h = hrows[0]
        return {
            'run': compare_id,
            'sample_bytes_equal': h['sample_bytes'] == cur.get('sample_bytes'),
            'payload_bytes_equal': h['payload_bytes'] == cur.get('payload_bytes'),
            'psnr_equal': abs((h['psnr'] or 0) - (cur.get('psnr') or 0)) < 1e-9,
            'candidate_sha_equal': h.get('candidate_sha256') == cur.get('candidate_sha256'),
            'q_eff_equal': h['q_eff'] == cur.get('q_eff'),
            'hist_sample': h['sample_bytes'],
            'cur_sample': cur.get('sample_bytes'),
            'hist_payload': h['payload_bytes'],
            'cur_payload': cur.get('payload_bytes'),
            'hist_candidate': h.get('candidate_sha256'),
            'cur_candidate': cur.get('candidate_sha256'),
            'hist_q': h['q_eff'],
            'cur_q': cur.get('q_eff'),
            'hist_psnr': h['psnr'],
            'cur_psnr': cur.get('psnr'),
        }
    except Exception as e:
        return {'error': str(e)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--runs', default=os.path.join(ROOT, 'out', 'benchmark', 'runs'))
    ap.add_argument('--out', default=os.path.join(ROOT, 'benchmark', 'experiments',
                                                   'exp003-results-summary.json'))
    ap.add_argument('--compare-002a', default='exp002a-f-a04-a75-dz50-q24')
    args = ap.parse_args()

    rows, _runs = collect(args.runs)
    agg = aggregate(rows)
    a1 = next((a for a in agg if a['run'] == 'exp003-a1-dz50'), None)
    plane = [a for a in agg if a['run'].startswith('exp003-b-')]
    band = [a for a in agg if a['run'].startswith('exp003-d1-') or a['run'].startswith('exp003-d2-')
            or a['run'].startswith('exp003-d3-') or a['run'].startswith('exp003-d4-')
            or a['run'].startswith('exp003-d5-')]
    plane_wins = compare_to_ref(plane, a1)
    band_wins = compare_to_ref(band, a1)
    hist = verify_historical(args.runs, args.compare_002a, a1)
    disc = resolve_discrepancy(agg, hist)
    strategy = classify_strategy(plane_wins, band_wins, a1, plane, band)

    approved_ratios = [a['ratio_sample_mean'] for a in agg
                       if a.get('series_approved') and a.get('ratio_sample_mean')]
    best_ratio_approved = max(approved_ratios) if approved_ratios else None
    # Ratio experimental (cualquier medible) — no usa para clasificar sola.
    all_ratios = [a['ratio_sample_mean'] for a in agg if a.get('ratio_sample_mean')]
    best_ratio_any = max(all_ratios) if all_ratios else None

    doc = {
        'schema': 3,
        'experiment': 'EXP-003',
        'base_commit': '4fb9331bd9210712b77862879d869e1d68ce610c',
        'branch': 'experiment/pocket-og-adaptive-ac-sparsity',
        'measurement': {
            'primary': 'sample_bytes via braw_inspect',
            'raw_bytes_12bit_1080p': RAW,
            'sdk_frame_bytes': 'diagnostic only (often 4096-aligned; not storage)',
            'tolerances': TOL,
            'aggregation': (
                'all_frames = todos los medibles; valid_frames_only etiquetado aparte; '
                'series_approved exige cobertura completa + todos los gates'
            ),
            'sharpness': 'grad/lap evaluados por proximidad a la referencia, no maximización',
        },
        'chroma_geometry': {
            'plane_resolution': 'Cb/Cr a width/2 × height/2 respecto a Y (4:2:0 espacial)',
            'dct_blocks': 'Y 8×8; Cb/Cr 4×8 (un bloque croma ≈ 2 bloques Y horizontales)',
            'normalized_freq': 'relativa al bloque del plano; no frecuencia absoluta de imagen',
            'note': 'No es 4:2:2; la doc anterior que decía 4:2:2 era incorrecta',
        },
        'project_priorities_next': {
            'P0_maximum_fidelity': 'Perfil máxima fidelidad, sin ratio obligatorio',
            'P0_production_3to1': 'Perfil producción profesional ~3:1',
            'P0_BUG002_chroma_halos': (
                'Halos cromáticos artificiales: bloqueante para aptitud de producción'
            ),
            'P1_compression_5_6': 'Compresión 5:1–6:1',
            'P2_compression_8_12': 'Compresión 8:1–12:1 para footage no crítico',
            'acceptance': (
                'Validación matemática, perceptual, robustez en grading y estabilidad temporal'
            ),
            'status': 'documentado; no implementado en EXP-003',
        },
        'ratio_discrepancy': disc,
        'identity_vs_exp002a': hist,
        'strategy_class': strategy,
        'strategy_legend': {
            'A': 'Mejora demostrada RD', 'B': 'Mejora localizada',
            'C': 'Equivalencia práctica con DZ uniforme', 'D': 'Dominada por DZ uniforme',
            'E': 'Evidencia insuficiente',
        },
        'n_runs': len(agg),
        'n_series_approved': sum(1 for a in agg if a.get('series_approved')),
        'plane_wins': [{'kind': w['kind'], 'run': w['run'],
                        'paired_frames': w.get('paired_frames')} for w in plane_wins],
        'band_wins': [{'kind': w['kind'], 'run': w['run'],
                       'paired_frames': w.get('paired_frames')} for w in band_wins],
        'best_ratio_sample_series_approved': best_ratio_approved,
        'best_ratio_sample_any_measurable': best_ratio_any,
        'reached_6_to_1': bool(best_ratio_approved and best_ratio_approved >= 6.0),
        'reached_8_to_1': bool(best_ratio_approved and best_ratio_approved >= 8.0),
        'aggregates': agg,
    }
    os.makedirs(os.path.dirname(args.out) or '.', exist_ok=True)
    with open(args.out, 'w', encoding='utf-8') as f:
        json.dump(doc, f, indent=2)
        f.write('\n')
    print('wrote', args.out)
    print('strategy', strategy,
          'approved', doc['n_series_approved'],
          'best_ratio_approved', best_ratio_approved)
    print('historical', disc.get('historical_equivalence'), disc.get('historical_reason', '')[:80])
    if a1:
        print('A1 series_approved', a1.get('series_approved'),
              'n_valid', a1.get('n_valid'), 'expected', a1.get('expected_count'))


if __name__ == '__main__':
    main()
