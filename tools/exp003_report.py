#!/usr/bin/env python3
"""Agrega métricas EXP-003 desde out/benchmark/runs/exp003-*."""
from __future__ import annotations

import argparse
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def mean(vals):
    vals = [v for v in vals if isinstance(v, (int, float))]
    return sum(vals) / len(vals) if vals else None


def frame_row(run_id, key, res):
    rf = res.get('reference_fidelity') or {}
    al = rf.get('alignment') or {}
    n = rf.get('normalized') or {}
    lc = ((rf.get('luma_chroma') or {}).get('all') or {})
    sh = rf.get('sharpness') or {}
    comp = res.get('compression') or {}
    return {
        'run': run_id,
        'result_key': key,
        'psnr': n.get('psnr_db'),
        'duv': lc.get('duv_mean'),
        'gradient': sh.get('gradient_energy_ratio_edges'),
        'laplacian': sh.get('laplacian_energy_ratio_edges'),
        'detail_corr': al.get('detail_correlation'),
        'geometric_aligned': al.get('geometric_aligned'),
        'detail_gate_pass': al.get('detail_gate_pass'),
        'quality_rejected': al.get('quality_rejected'),
        'gate_failed': al.get('gate_failed'),
        'gate_reason': al.get('gate_reason'),
        'frame_bytes': comp.get('frame_bytes'),
        'frame_ratio': comp.get('frame_ratio'),
        'braw_ratio': comp.get('braw_ratio_achieved'),
        'q_min': comp.get('q_min'),
        'q_max': comp.get('q_max'),
    }


def summarize(runs_root):
    rows = []
    for name in sorted(os.listdir(runs_root)):
        if not name.startswith('exp003-'):
            continue
        path = os.path.join(runs_root, name, 'metrics.json')
        if not os.path.isfile(path):
            continue
        with open(path, encoding='utf-8') as f:
            metrics = json.load(f)
        for key, res in sorted((metrics.get('results') or {}).items()):
            rows.append(frame_row(name, key, res))
    return rows


def aggregate(rows):
    by = {}
    for r in rows:
        by.setdefault(r['run'], []).append(r)
    out = []
    for run, rs in sorted(by.items()):
        out.append({
            'run': run,
            'n_frames': len(rs),
            'psnr_mean': mean([r['psnr'] for r in rs]),
            'duv_mean': mean([r['duv'] for r in rs]),
            'gradient_mean': mean([r['gradient'] for r in rs]),
            'laplacian_mean': mean([r['laplacian'] for r in rs]),
            'detail_corr_mean': mean([r['detail_corr'] for r in rs]),
            'frame_bytes_mean': mean([r['frame_bytes'] for r in rs]),
            'frame_ratio_mean': mean([r['frame_ratio'] for r in rs]),
            'quality_rejected_any': any(r.get('quality_rejected') for r in rs),
            'gate_failed_any': any(r.get('gate_failed') for r in rs),
            'frames': rs,
        })
    return out


def answers(agg):
    def get(run):
        for a in agg:
            if a['run'] == run:
                return a
        return None

    a1 = get('exp003-a1-dz50')
    # Mejor per-plane vs a1 a bytes similares o mejor PSNR a bytes menores
    plane = [a for a in agg if a['run'].startswith('exp003-b-')]
    band = [a for a in agg if a['run'].startswith('exp003-d')]

    def better_than_uniform(cands, ref):
        if not ref or not ref.get('frame_bytes_mean'):
            return []
        wins = []
        for c in cands:
            if not c.get('psnr_mean') or not c.get('frame_bytes_mean'):
                continue
            # mejora a igual tasa (~2%): más PSNR con bytes <= ref*1.02
            # o menos bytes con PSNR >= ref*0.998
            same_rate = c['frame_bytes_mean'] <= ref['frame_bytes_mean'] * 1.02
            same_q = c['psnr_mean'] >= ref['psnr_mean'] * 0.998
            if same_rate and c['psnr_mean'] > ref['psnr_mean']:
                wins.append(('quality_at_rate', c))
            if same_q and c['frame_bytes_mean'] < ref['frame_bytes_mean']:
                wins.append(('bytes_at_quality', c))
        return wins

    plane_wins = better_than_uniform(plane, a1)
    band_wins = better_than_uniform(band, a1)
    best_ratio = max((a.get('frame_ratio_mean') or 0) for a in agg) if agg else 0
    return {
        'plane_improves_vs_uniform': bool(plane_wins),
        'plane_wins': [{'kind': k, 'run': c['run'], 'psnr': c['psnr_mean'],
                        'bytes': c['frame_bytes_mean'], 'ratio': c['frame_ratio_mean']}
                       for k, c in plane_wins[:10]],
        'band_improves_vs_uniform': bool(band_wins),
        'band_wins': [{'kind': k, 'run': c['run'], 'psnr': c['psnr_mean'],
                       'bytes': c['frame_bytes_mean'], 'ratio': c['frame_ratio_mean']}
                      for k, c in band_wins[:10]],
        'best_frame_ratio_mean': best_ratio,
        'reached_6_to_1': best_ratio >= 6.0,
        'reached_8_to_1': best_ratio >= 8.0,
        'a1_uniform': a1,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--runs', default=os.path.join(ROOT, 'out', 'benchmark', 'runs'))
    ap.add_argument('--out', default=os.path.join(ROOT, 'benchmark', 'experiments',
                                                   'exp003-results-summary.json'))
    args = ap.parse_args()
    rows = summarize(args.runs)
    agg = aggregate(rows)
    ans = answers(agg)
    doc = {
        'schema': 1,
        'experiment': 'EXP-003',
        'base_commit': '4fb9331bd9210712b77862879d869e1d68ce610c',
        'branch': 'experiment/pocket-og-adaptive-ac-sparsity',
        'n_runs': len(agg),
        'phases_completed': ['infra', 'policy', 'equiv_tests', 'A', 'B', 'C_maps', 'D'],
        'phases_pending': ['E_attribution', 'F_search', 'G_equal_rate', 'H_alpha_q',
                           'I_NR', 'J_rate_search', 'K_series_A', 'temporal'],
        'answers_preliminary': ans,
        'aggregates': agg,
    }
    os.makedirs(os.path.dirname(args.out) or '.', exist_ok=True)
    with open(args.out, 'w', encoding='utf-8') as f:
        json.dump(doc, f, indent=2)
        f.write('\n')
    print('wrote', args.out, 'runs', len(agg))
    print('plane improves:', ans['plane_improves_vs_uniform'], 'wins', len(ans['plane_wins']))
    print('band improves:', ans['band_improves_vs_uniform'], 'wins', len(ans['band_wins']))
    print('best ratio:', ans['best_frame_ratio_mean'])
    if ans['a1_uniform']:
        a = ans['a1_uniform']
        print('A1: psnr=%.3f bytes=%.0f ratio=%.3f detail=%.3f' % (
            a['psnr_mean'], a['frame_bytes_mean'], a['frame_ratio_mean'], a['detail_corr_mean']))
    ranked = sorted([a for a in agg if a['psnr_mean']], key=lambda a: (-(a['psnr_mean'] or 0)))[:12]
    for a in ranked:
        print('%s  psnr=%.3f  ratio=%.3f  bytes=%.0f  detail=%.3f  grad=%.3f' % (
            a['run'], a['psnr_mean'] or 0, a['frame_ratio_mean'] or 0,
            a['frame_bytes_mean'] or 0, a['detail_corr_mean'] or 0, a['gradient_mean'] or 0))


if __name__ == '__main__':
    main()
