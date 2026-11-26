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
    'grad': 0.02,
    'lap': 0.03,
    'detail_corr': 0.01,
    'duv': 0.0005,
}


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
    if row.get('sample_bytes') is not None and row.get('payload_bytes') is not None:
        # chunk ≈ sample − BMDF; BMDF_SIZE=1024 en braw_inspect
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
                })
                continue
            enrich_row(row, result)
            rows.append(row)
    return rows, runs


def mean(vals):
    vals = [v for v in vals if isinstance(v, (int, float))]
    return sum(vals) / len(vals) if vals else None


def aggregate(rows):
    by = {}
    for r in rows:
        by.setdefault(r['run'], []).append(r)
    out = []
    for run, rs in sorted(by.items()):
        valid = [r for r in rs if r.get('candidate_class') == 'valid']
        rejected = [r for r in rs if r.get('candidate_class') == 'aligned_detail_rejected']
        errors = [r for r in rs if r.get('candidate_class') == 'identity_or_conversion_error']
        use = valid if valid else rs  # métricas solo de válidos si existen
        out.append({
            'run': run,
            'n_frames': len(rs),
            'n_valid': len(valid),
            'n_detail_rejected': len(rejected),
            'n_errors': len(errors),
            'eligible': len(valid) > 0 and len(errors) == 0,
            'psnr_mean': mean([r.get('psnr') for r in use]),
            'duv_edges_mean': mean([r.get('duv_edges') for r in use]),
            'grad_mean': mean([r.get('grad') for r in use]),
            'lap_mean': mean([r.get('lap') for r in use]),
            'detail_corr_mean': mean([r.get('detail_corr') for r in use]),
            'sample_bytes_mean': mean([r.get('sample_bytes') for r in use]),
            'payload_bytes_mean': mean([r.get('payload_bytes') for r in use]),
            'sdk_frame_bytes_mean': mean([r.get('sdk_frame_bytes') for r in use]),
            'ratio_sample_mean': mean([r.get('ratio_sample_12bit') for r in use]),
            'ratio_payload_mean': mean([r.get('ratio_payload_12bit') for r in use]),
            'ratio_sdk_mean': mean([r.get('ratio_sdk_12bit') for r in use]),
            'q_eff': mean([r.get('q_eff') for r in use]),
            'frames': rs,
        })
    return out


def dominates(a, b, tol=TOL):
    """a domina b si no empeora ninguna métrica clave y mejora al menos bytes o PSNR."""
    if not a.get('eligible') or not b.get('eligible'):
        return False
    if a['sample_bytes_mean'] is None or b['sample_bytes_mean'] is None:
        return False
    bytes_ok = a['sample_bytes_mean'] <= b['sample_bytes_mean'] * (1 + tol['bytes_pct'])
    psnr_ok = (a['psnr_mean'] or 0) >= (b['psnr_mean'] or 0) - tol['psnr_db']
    grad_ok = (a['grad_mean'] or 0) >= (b['grad_mean'] or 0) - tol['grad']
    detail_ok = (a['detail_corr_mean'] or 0) >= (b['detail_corr_mean'] or 0) - tol['detail_corr']
    better_bytes = a['sample_bytes_mean'] < b['sample_bytes_mean'] * (1 - tol['bytes_pct'] / 2)
    better_psnr = (a['psnr_mean'] or 0) > (b['psnr_mean'] or 0) + tol['psnr_db']
    return bytes_ok and psnr_ok and grad_ok and detail_ok and (better_bytes or better_psnr)


def compare_to_ref(cands, ref, tol=TOL):
    wins = []
    if not ref or not ref.get('eligible'):
        return wins
    for c in cands:
        if not c.get('eligible') or c['run'] == ref['run']:
            continue
        same_rate = abs(c['sample_bytes_mean'] - ref['sample_bytes_mean']) <= ref['sample_bytes_mean'] * tol['bytes_pct']
        better_psnr = (c['psnr_mean'] or 0) > (ref['psnr_mean'] or 0) + tol['psnr_db']
        better_bytes = c['sample_bytes_mean'] < ref['sample_bytes_mean'] * (1 - tol['bytes_pct'] / 2)
        same_psnr = abs((c['psnr_mean'] or 0) - (ref['psnr_mean'] or 0)) <= tol['psnr_db']
        if same_rate and better_psnr:
            wins.append({'kind': 'quality_at_rate', 'run': c['run'], 'cand': c})
        if same_psnr and better_bytes:
            wins.append({'kind': 'bytes_at_quality', 'run': c['run'], 'cand': c})
    return wins


def resolve_discrepancy(agg):
    """Documenta SDK vs sample: EXP-002a 5.055:1 = sample; 4.87 = SDK."""
    a1 = next((a for a in agg if a['run'] == 'exp003-a1-dz50'), None)
    if not a1:
        return {'status': 'missing_a1'}
    return {
        'status': 'methodological_not_regression',
        'explanation': (
            'SDK frame_bytes (638976, múltiplo de 4096) ≠ sample_bytes bitstream (615256). '
            'EXP-002a reportó ratio_sample=5.055:1 (RAW 3110400/615256). '
            'EXP-003 reportó erróneamente ratio_sdk≈4.87. candidate_sha256 idéntico a '
            'exp002a-f-a04-a75-dz50-q24 → sin regresión de imagen.'
        ),
        'a1_sample_bytes': a1.get('sample_bytes_mean'),
        'a1_sdk_frame_bytes': a1.get('sdk_frame_bytes_mean'),
        'a1_ratio_sample': a1.get('ratio_sample_mean'),
        'a1_ratio_sdk': a1.get('ratio_sdk_mean'),
        'raw_reference_bytes': RAW,
    }


def classify_strategy(plane_wins, band_wins, best_ratio_sample):
    if plane_wins or band_wins:
        return 'B' if not (len(plane_wins) + len(band_wins) > 3) else 'A'
    if best_ratio_sample and best_ratio_sample >= 5.0:
        return 'C'  # uniforme sigue siendo el techo práctico
    return 'D'


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
    plane = [a for a in agg if a['run'].startswith('exp003-b-') and a['eligible']]
    band = [a for a in agg if a['run'].startswith('exp003-d') and a['eligible']]
    plane_wins = compare_to_ref(plane, a1)
    band_wins = compare_to_ref(band, a1)
    disc = resolve_discrepancy(agg)
    best_ratio = max((a.get('ratio_sample_mean') or 0) for a in agg if a.get('eligible')) if agg else 0
    strategy = classify_strategy(plane_wins, band_wins, best_ratio)

    # Comparación identidad con EXP-002a si existe
    hist = None
    hist_path = os.path.join(args.runs, args.compare_002a)
    if os.path.isdir(hist_path):
        try:
            hrun = RD.load_run(hist_path)
            hrows = []
            for k, res in hrun['metrics']['results'].items():
                if 'f044' not in k:
                    continue
                hrows.append(RD.frame_row(hrun, k, res))
            if hrows and a1 and a1.get('frames'):
                h, cur = hrows[0], next(f for f in a1['frames'] if 'f044' in f.get('result', ''))
                hist = {
                    'run': args.compare_002a,
                    'sample_bytes_equal': h['sample_bytes'] == cur.get('sample_bytes'),
                    'payload_bytes_equal': h['payload_bytes'] == cur.get('payload_bytes'),
                    'psnr_equal': abs((h['psnr'] or 0) - (cur.get('psnr') or 0)) < 1e-9,
                    'candidate_sha_equal': h.get('candidate_sha256') == cur.get('candidate_sha256'),
                    'q_eff_equal': h['q_eff'] == cur.get('q_eff'),
                    'hist_sample': h['sample_bytes'],
                    'cur_sample': cur.get('sample_bytes'),
                    'hist_candidate': h.get('candidate_sha256'),
                    'cur_candidate': cur.get('candidate_sha256'),
                }
        except Exception as e:
            hist = {'error': str(e)}

    doc = {
        'schema': 2,
        'experiment': 'EXP-003',
        'base_commit': '4fb9331bd9210712b77862879d869e1d68ce610c',
        'branch': 'experiment/pocket-og-adaptive-ac-sparsity',
        'measurement': {
            'primary': 'sample_bytes via braw_inspect',
            'raw_bytes_12bit_1080p': RAW,
            'sdk_frame_bytes': 'diagnostic only (often 4096-aligned; not storage)',
            'tolerances': TOL,
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
        'plane_wins': [{'kind': w['kind'], 'run': w['run']} for w in plane_wins],
        'band_wins': [{'kind': w['kind'], 'run': w['run']} for w in band_wins],
        'best_ratio_sample': best_ratio,
        'reached_6_to_1': best_ratio >= 6.0,
        'reached_8_to_1': best_ratio >= 8.0,
        'aggregates': agg,
    }
    os.makedirs(os.path.dirname(args.out) or '.', exist_ok=True)
    with open(args.out, 'w', encoding='utf-8') as f:
        json.dump(doc, f, indent=2)
        f.write('\n')
    print('wrote', args.out)
    print('strategy', strategy, 'best_ratio_sample', best_ratio)
    print('discrepancy', disc.get('status'), 'ratio_sample', disc.get('a1_ratio_sample'),
          'ratio_sdk', disc.get('a1_ratio_sdk'))
    if hist:
        print('vs 002a', hist)


if __name__ == '__main__':
    main()
