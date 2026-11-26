#!/usr/bin/env python3
"""Agrega EXP-003b cierre: igual tasa, Pareto, Serie A, max-fid, clasificación A–E."""
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
SERIES_A = ('A01_f052', 'A02_f029', 'A03_f046', 'A04_f044', 'A04_f049', 'A05_f053')
TOL_BYTES = 0.02


def classify_alignment(result):
    al = ((result.get('reference_fidelity') or {}).get('alignment') or {})
    if al.get('gate_failed') and al.get('quality_rejected'):
        return 'aligned_detail_rejected'
    if al.get('gate_failed'):
        return 'gate_failed'
    if al.get('geometric_aligned') and al.get('detail_gate_pass'):
        return 'valid'
    return 'unknown'


def load_prefix(runs_root, prefix='exp003b-'):
    rows = []
    for name in sorted(os.listdir(runs_root)):
        if not name.startswith(prefix):
            continue
        path = os.path.join(runs_root, name)
        if not os.path.isfile(os.path.join(path, 'metrics.json')):
            continue
        run = RD.load_run(path)
        for key, result in sorted(run['metrics'].get('results', {}).items()):
            try:
                row = RD.frame_row(run, key, result, require_bitstream=True, verify_file_sha=True)
            except RD.RdError as e:
                rows.append({'run': run['id'], 'result': key, 'error': str(e),
                             'candidate_class': 'identity_or_conversion_error'})
                continue
            al = ((result.get('reference_fidelity') or {}).get('alignment') or {})
            row['detail_corr'] = al.get('detail_correlation')
            row['gate_failed'] = al.get('gate_failed')
            row['candidate_class'] = classify_alignment(result)
            env = ((result.get('setup') or {}).get('encode') or {}).get('experimental_env') or {}
            row['rdoq'] = env.get('CDNG_RDOQ') in ('1', 'on', 'true', 'yes')
            row['lambda'] = float(env['CDNG_RDOQ_LAMBDA']) if env.get('CDNG_RDOQ_LAMBDA') else None
            row['deadzone'] = env.get('CDNG_DEADZONE')
            row['alpha'] = env.get('CDNG_OG_DEBLOCK_ALPHA')
            # ROI / microtextura si existen
            fid = result.get('reference_fidelity') or {}
            row['duv_global'] = (fid.get('chroma') or {}).get('mean_duv')
            rois = fid.get('rois') or {}
            row['roi_keys'] = sorted(rois.keys()) if isinstance(rois, dict) else []
            rows.append(row)
    return rows


def mean(vals):
    vals = [v for v in vals if isinstance(v, (int, float))]
    return sum(vals) / len(vals) if vals else None


def aggregate(rows, expected=None):
    by = {}
    for r in rows:
        by.setdefault(r['run'], []).append(r)
    out = []
    for run, rs in sorted(by.items()):
        exp = list(expected) if expected else sorted({r.get('result') for r in rs if r.get('result')})
        got = {r.get('result') for r in rs}
        measurable = [r for r in rs if r.get('candidate_class') != 'identity_or_conversion_error'
                      and not r.get('error')]
        valid = [r for r in rs if r.get('candidate_class') == 'valid']
        rejected = [r for r in rs if r.get('candidate_class') == 'aligned_detail_rejected']
        missing = [k for k in exp if k not in got]
        series_approved = (
            expected is not None
            and len(missing) == 0
            and len(valid) == len(exp)
            and not any(r.get('error') for r in rs)
        )
        out.append({
            'run': run,
            'n': len(rs),
            'n_ok': len(measurable),
            'n_valid': len(valid),
            'n_detail_rejected': len(rejected),
            'missing': missing,
            'coverage_complete': len(missing) == 0,
            'series_approved': series_approved,
            'experimental_retained': len(measurable) > 0,
            'lambda': next((r.get('lambda') for r in measurable if r.get('lambda') is not None), None),
            'rdoq': any(r.get('rdoq') for r in measurable),
            'sample_bytes_mean': mean([r.get('sample_bytes') for r in measurable]),
            'payload_bytes_mean': mean([r.get('payload_bytes') for r in measurable]),
            'ratio_sample_mean': mean([r.get('ratio_sample_12bit') for r in measurable]),
            'psnr_mean': mean([r.get('psnr') for r in measurable]),
            'duv_edges_mean': mean([r.get('duv_edges') for r in measurable]),
            'duv_global_mean': mean([r.get('duv_global') for r in measurable]),
            'grad_mean': mean([r.get('grad') for r in measurable]),
            'lap_mean': mean([r.get('lap') for r in measurable]),
            'detail_corr_mean': mean([r.get('detail_corr') for r in measurable]),
            'q_eff_mean': mean([r.get('q_eff') for r in measurable]),
            'gate_failures': sum(1 for r in measurable if r.get('gate_failed')),
            'worst_psnr': min((r.get('psnr') for r in measurable if r.get('psnr') is not None), default=None),
            'worst_detail': min((r.get('detail_corr') for r in measurable if r.get('detail_corr') is not None),
                                default=None),
        })
    return out


def equal_rate_pair(a, b, tol=TOL_BYTES):
    """True si sample_bytes medios están dentro de ±tol."""
    if not a or not b:
        return False
    sa, sb = a.get('sample_bytes_mean'), b.get('sample_bytes_mean')
    if sa is None or sb is None:
        return False
    return abs(sa - sb) <= tol * max(sa, sb)


def compare_pair(cand, ref, label):
    """Comparación multidimensional; no usa métricas ausentes como victoria."""
    out = {
        'label': label,
        'cand': cand['run'],
        'ref': ref['run'],
        'equal_rate': equal_rate_pair(cand, ref),
        'cand_sample': cand.get('sample_bytes_mean'),
        'ref_sample': ref.get('sample_bytes_mean'),
        'metrics': {},
        'verdict': 'inconclusive',
    }
    if not out['equal_rate']:
        out['verdict'] = 'rate_mismatch'
        return out
    keys = [
        ('psnr_mean', 'higher_better'),
        ('detail_corr_mean', 'higher_better'),
        ('grad_mean', 'proximity_to_ref'),  # proximidad: |cand-ref| (reportamos delta)
        ('lap_mean', 'proximity_to_ref'),
        ('duv_edges_mean', 'lower_better'),
    ]
    # Umbrales prácticos (evita declarar victoria por ruido numérico ~1e-3 dB).
    thr = {'psnr_mean': 0.05, 'detail_corr_mean': 0.005, 'duv_edges_mean': 0.0005}
    better = worse = missing = 0
    for k, mode in keys:
        cv, rv = cand.get(k), ref.get(k)
        if cv is None or rv is None:
            missing += 1
            out['metrics'][k] = {'cand': cv, 'ref': rv, 'status': 'missing'}
            continue
        t = thr.get(k, 0.0)
        if mode == 'higher_better':
            status = 'better' if cv > rv + t else ('worse' if cv < rv - t else 'tie')
        elif mode == 'lower_better':
            status = 'better' if cv < rv - t else ('worse' if cv > rv + t else 'tie')
        else:
            status = 'delta'
        out['metrics'][k] = {'cand': cv, 'ref': rv, 'status': status, 'delta': cv - rv}
        if status == 'better':
            better += 1
        elif status == 'worse':
            worse += 1
    # No contar missing como favorable.
    if better > 0 and worse == 0:
        out['verdict'] = 'cand_not_worse_and_improves'
    elif better == 0 and worse == 0 and missing == 0:
        out['verdict'] = 'equivalent'
    elif worse > better:
        out['verdict'] = 'cand_worse'
    else:
        out['verdict'] = 'mixed_or_incomplete'
    out['missing_metrics'] = missing
    return out


def classify(equal_rate_results, series_aggs, reached):
    """A–E según evidencia práctica (3:1 / 5:1 / almacenamiento), no por un empate local."""
    primary = [c for c in equal_rate_results
               if c.get('label', '').startswith(('3to1_', '5to1_'))
               and c.get('verdict') == 'cand_not_worse_and_improves']
    local = [c for c in equal_rate_results
             if 'maxfid' in c.get('label', '')
             and c.get('verdict') == 'cand_not_worse_and_improves']
    g1 = next((a for a in series_aggs if a['run'].endswith('k-g1')), None)
    wins_storage = []
    for a in series_aggs:
        if not a.get('rdoq') or not g1:
            continue
        # G1 puede no estar series_approved (A01); comparar solo si ambos tienen cobertura completa.
        if not (a.get('coverage_complete') and g1.get('coverage_complete')):
            continue
        if (a.get('sample_bytes_mean') and g1.get('sample_bytes_mean')
                and a['sample_bytes_mean'] < g1['sample_bytes_mean'] * (1 - TOL_BYTES)
                and a.get('detail_corr_mean') is not None and g1.get('detail_corr_mean') is not None
                and a['detail_corr_mean'] >= g1['detail_corr_mean'] - 0.01
                and a.get('psnr_mean') is not None and g1.get('psnr_mean') is not None
                and a['psnr_mean'] >= g1['psnr_mean'] - 0.05):
            wins_storage.append(a['run'])
    if primary:
        return 'A'
    if wins_storage:
        return 'B'
    # Serie A: RDOQ ~5:1 series_approved mientras G1 falla A01 → mejora localizada de gate.
    g1_fail = g1 and not g1.get('series_approved') and g1.get('n_detail_rejected', 0) > 0
    rdoq5 = next((a for a in series_aggs if a['run'].endswith('k-rdoq-5to1')), None)
    if local or (g1_fail and rdoq5 and rdoq5.get('series_approved')):
        return 'C'
    if not series_aggs and not equal_rate_results:
        return 'E'
    return 'D'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--runs', default=os.path.join(ROOT, 'out', 'benchmark', 'runs'))
    ap.add_argument('--out', default=os.path.join(ROOT, 'benchmark', 'experiments',
                                                   'exp003b-results-summary.json'))
    args = ap.parse_args()

    rows = load_prefix(args.runs)
    by_run = {}
    for r in rows:
        by_run.setdefault(r['run'], []).append(r)

    # Controles / sweep / perf / I / J / K / L
    controls = aggregate([r for r in rows if r['run'].startswith('exp003b-g')])
    sweep = aggregate([r for r in rows if '-h-q' in r['run'] or '-j-q24-l' in r['run']
                       or '-i-rdoq-q24-l' in r['run']])
    perf_path = os.path.join(ROOT, 'benchmark', 'experiments', 'exp003b', 'h2-perf.json')
    perf = json.load(open(perf_path)) if os.path.isfile(perf_path) else None

    series_rows = [r for r in rows if r['run'].startswith('exp003b-k-')]
    series_aggs = aggregate(series_rows, expected=SERIES_A)

    # Igual tasa: pares documentados
    idx = {a['run']: a for a in aggregate(rows)}
    equal = []
    pairs = [
        ('exp003b-h-q18-l70', 'exp003b-i-ctrl-q18-dz50', '3to1_rdoq_vs_matched_ctrl'),
        ('exp003b-h-q24-l0', 'exp003b-g0-legacy', '3to1_rdoq_vs_G0_near'),
        ('exp003b-h-q24-l100', 'exp003b-g1-a75-dz50', '5to1_rdoq_l100_vs_G1'),
        ('exp003b-g2-plane-y40-c50', 'exp003b-g1-a75-dz50', 'G2_vs_G1'),
    ]
    i_meta = os.path.join(ROOT, 'benchmark', 'experiments', 'exp003b', 'phase-i-equal-rate.json')
    if os.path.isfile(i_meta):
        meta = json.load(open(i_meta, encoding='utf-8'))
        if meta.get('rdoq_3to1') and meta.get('ctrl_3to1'):
            pairs[0] = (meta['rdoq_3to1'], meta['ctrl_3to1'], '3to1_rdoq_vs_matched_ctrl')
        bm = meta.get('best_match_5to1')
        if bm:
            pairs.append((bm, 'exp003b-g1-a75-dz50', '5to1_matched_rdoq_vs_G1'))
    # Max-fid
    for name in sorted(idx):
        if name.startswith('exp003b-l-q') and idx[name].get('rdoq'):
            pairs.append((name, 'exp003b-g3-maxfid-q2', 'maxfid_rdoq_vs_G3'))
            if 'exp003b-l-ctrl-q0' in idx and 'q0' in name:
                pairs.append((name, 'exp003b-l-ctrl-q0', 'maxfid_rdoq_vs_q0_ctrl'))
    for cand, ref, label in pairs:
        if cand in idx and ref in idx:
            equal.append(compare_pair(idx[cand], idx[ref], label))

    # Rate-search resumen
    rate_search = []
    for a in aggregate([r for r in rows if '-j-r' in r['run']]):
        rate_search.append({
            'run': a['run'], 'ratio_sample': a['ratio_sample_mean'],
            'sample_bytes': a['sample_bytes_mean'], 'q_eff': a['q_eff_mean'],
            'rdoq': a['rdoq'], 'psnr': a['psnr_mean'], 'detail': a['detail_corr_mean'],
        })

    series_hi = [a for a in series_aggs if (a.get('ratio_sample_mean') or 0) >= 6.0]
    reached = {
        # Gate-approved útil sobre Serie A completa
        '6_to_1': any((a.get('ratio_sample_mean') or 0) >= 6.0 and a.get('series_approved')
                      for a in series_aggs),
        '8_to_1': any((a.get('ratio_sample_mean') or 0) >= 8.0 and a.get('series_approved')
                      for a in series_aggs),
        '5_to_1_series_approved': any((a.get('ratio_sample_mean') or 0) >= 5.0 and a.get('series_approved')
                                      for a in series_aggs),
        # Físico en Serie A (puede estar rechazado por fidelidad)
        '6_to_1_physical_series_a': bool(series_hi),
        '6_to_1_physical_max_ratio': max((a.get('ratio_sample_mean') or 0) for a in series_hi) if series_hi else None,
        '6_to_1_physical_series_approved': any(a.get('series_approved') for a in series_hi),
        'max_gate_approved_ratio_series_a': max(
            (a.get('ratio_sample_mean') or 0) for a in series_aggs if a.get('series_approved')),
    }
    # Rate-search A04 (QMax): no confundir con fixed-q físico
    reached['6_to_1_rate_search'] = any((a.get('ratio_sample') or 0) >= 6.0 for a in rate_search)
    reached['8_to_1_rate_search'] = any((a.get('ratio_sample') or 0) >= 8.0 for a in rate_search)
    reached['6_to_1_any_a04'] = reached['6_to_1_rate_search']  # alias legacy
    reached['8_to_1_any_a04'] = reached['8_to_1_rate_search']

    strategy = classify(equal, series_aggs, reached)

    rd_curve = sorted(
        [{'run': a['run'], 'sample_bytes': a['sample_bytes_mean'], 'psnr': a['psnr_mean'],
          'ratio': a['ratio_sample_mean'], 'lambda': a.get('lambda'),
          'detail': a.get('detail_corr_mean'), 'rdoq': a.get('rdoq')}
         for a in list(idx.values())
         if a.get('sample_bytes_mean') and a.get('psnr_mean')
         and (a['run'].startswith('exp003b-h-') or a['run'].startswith('exp003b-g')
              or a['run'].startswith('exp003b-i-') or a['run'].startswith('exp003b-j-q'))],
        key=lambda x: x['sample_bytes'])

    near_3 = sorted([a for a in idx.values() if a.get('ratio_sample_mean')],
                    key=lambda a: abs(a['ratio_sample_mean'] - 3.0))[:5]
    near_5 = sorted([a for a in idx.values() if a.get('ratio_sample_mean')],
                    key=lambda a: abs(a['ratio_sample_mean'] - 5.0))[:5]

    base = open(os.path.join(ROOT, 'benchmark', 'experiments', 'exp003b-base-sha.txt')).read().strip()
    doc = {
        'schema': 2,
        'experiment': 'EXP-003b',
        'base_commit': base,
        'branch': 'experiment/pocket-og-rdoq',
        'measurement': {
            'primary': 'sample_bytes via braw_inspect',
            'raw_bytes_12bit_1080p': RAW,
            'equal_rate_tol_pct': TOL_BYTES * 100,
            'note': 'Métricas ausentes no cuentan como evidencia favorable',
        },
        'strategy_class': strategy,
        'strategy_legend': {
            'A': 'Mejora de fidelidad a igual tasa',
            'B': 'Reducción de almacenamiento a fidelidad comparable',
            'C': 'Mejora localizada',
            'D': 'Sin ventaja práctica demostrada',
            'E': 'Evidencia insuficiente',
        },
        'answers': {
            'justifies_complexity': strategy in ('A', 'B'),
            'improves_3_to_1': any(c['label'].startswith('3to1') and c['verdict'] == 'cand_not_worse_and_improves'
                                  for c in equal),
            'improves_5_to_1': any('5to1' in c['label'] and c['verdict'] == 'cand_not_worse_and_improves'
                                  for c in equal),
            # Útil = gate Serie A o rate search (no cuenta el 6.13 físico rechazado).
            'reached_6_to_1': bool(reached.get('6_to_1') or reached.get('6_to_1_rate_search')),
            'reached_8_to_1': bool(reached.get('8_to_1') or reached.get('8_to_1_rate_search')),
            'reached_6_to_1_physical_series_a': bool(reached.get('6_to_1_physical_series_a')),
            'max_gate_approved_ratio_series_a': reached.get('max_gate_approved_ratio_series_a'),
        },
        'compression_ceiling': {
            'physical_series_a_max_ratio': reached.get('6_to_1_physical_max_ratio'),
            'physical_note': '≈6.13:1 @ λ=250 fixed-q; 6/6 detail rejected',
            'gate_approved_series_a_max_ratio': reached.get('max_gate_approved_ratio_series_a'),
            'gate_approved_note': '≈5.16:1 @ λ≈130 series_approved',
            'rate_search_saturates_near': 5.06,
            'rate_search_note': 'DZ ~5.06:1 / RDOQ λ100 ~4.84:1 at QMax; no useful 6:1 or 8:1',
        },
        'n_runs': len(idx),
        'controls': controls,
        'equal_rate_comparisons': equal,
        'lambda_sweep': sweep,
        'rd_curve_pareto_preliminary': rd_curve,
        'series_a': series_aggs,
        'rate_search': rate_search,
        'performance': perf,
        'reached': reached,
        'candidates_near_3_to_1': [{'run': a['run'], 'ratio': a['ratio_sample_mean']} for a in near_3],
        'candidates_near_5_to_1': [{'run': a['run'], 'ratio': a['ratio_sample_mean']} for a in near_5],
        'exp004_recommendations': [
            'Priorizar BUG-002 (halos cromáticos) como bloqueante P0 de producción',
            'No depender de RDOQ para perfiles Maximum Fidelity / Production 3:1 sin nueva evidencia',
            'Mantener DZ uniforme α=0.75 z=0.5 como referencia de tasa ~5:1',
            'Validación perceptual + grading + estabilidad temporal en EXP-004',
        ],
        'aggregates': list(idx.values()),
    }
    os.makedirs(os.path.dirname(args.out) or '.', exist_ok=True)
    with open(args.out, 'w', encoding='utf-8') as f:
        json.dump(doc, f, indent=2)
        f.write('\n')
    print('wrote', args.out)
    print('strategy', strategy)
    print('equal_rate', [(c['label'], c['verdict'], c.get('equal_rate')) for c in equal])
    print('series_approved', [(a['run'], a['series_approved'], a.get('ratio_sample_mean'))
                              for a in series_aggs])
    print('answers', doc['answers'])


if __name__ == '__main__':
    main()
