#!/usr/bin/env python3
"""Agrega EXP-003b: controles G, barrido λ, curvas RD preliminares (sample_bytes primario)."""
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


def load_prefix(runs_root, prefix):
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
                rows.append({'run': run['id'], 'result': key, 'error': str(e)})
                continue
            al = ((result.get('reference_fidelity') or {}).get('alignment') or {})
            row['detail_corr'] = al.get('detail_correlation')
            row['gate_failed'] = al.get('gate_failed')
            env = ((result.get('setup') or {}).get('encode') or {}).get('experimental_env') or {}
            row['rdoq'] = env.get('CDNG_RDOQ')
            row['lambda'] = float(env['CDNG_RDOQ_LAMBDA']) if env.get('CDNG_RDOQ_LAMBDA') else None
            row['deadzone'] = env.get('CDNG_DEADZONE')
            row['alpha'] = env.get('CDNG_OG_DEBLOCK_ALPHA')
            rows.append(row)
    return rows


def mean(vals):
    vals = [v for v in vals if isinstance(v, (int, float))]
    return sum(vals) / len(vals) if vals else None


def aggregate(rows):
    by = {}
    for r in rows:
        by.setdefault(r['run'], []).append(r)
    out = []
    for run, rs in sorted(by.items()):
        ok = [r for r in rs if not r.get('error')]
        out.append({
            'run': run,
            'n': len(rs),
            'n_ok': len(ok),
            'lambda': next((r.get('lambda') for r in ok if r.get('lambda') is not None), None),
            'rdoq': any(r.get('rdoq') in ('1', 'on', 'true', 'yes') for r in ok),
            'sample_bytes_mean': mean([r.get('sample_bytes') for r in ok]),
            'payload_bytes_mean': mean([r.get('payload_bytes') for r in ok]),
            'sdk_frame_bytes_mean': mean([r.get('sdk_frame_bytes') for r in ok]),
            'ratio_sample_mean': mean([r.get('ratio_sample_12bit') for r in ok]),
            'psnr_mean': mean([r.get('psnr') for r in ok]),
            'duv_edges_mean': mean([r.get('duv_edges') for r in ok]),
            'grad_mean': mean([r.get('grad') for r in ok]),
            'lap_mean': mean([r.get('lap') for r in ok]),
            'detail_corr_mean': mean([r.get('detail_corr') for r in ok]),
            'q_eff_mean': mean([r.get('q_eff') for r in ok]),
            'gate_failures': sum(1 for r in ok if r.get('gate_failed')),
            'frames': [{
                'result': r.get('result'), 'sample_bytes': r.get('sample_bytes'),
                'psnr': r.get('psnr'), 'detail_corr': r.get('detail_corr'),
                'ratio_sample_12bit': r.get('ratio_sample_12bit'), 'q_eff': r.get('q_eff'),
                'error': r.get('error'),
            } for r in rs],
        })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--runs', default=os.path.join(ROOT, 'out', 'benchmark', 'runs'))
    ap.add_argument('--out', default=os.path.join(ROOT, 'benchmark', 'experiments',
                                                   'exp003b-results-summary.json'))
    args = ap.parse_args()

    rows = load_prefix(args.runs, 'exp003b-')
    agg = aggregate(rows)
    controls = [a for a in agg if a['run'].startswith('exp003b-g')]
    sweep = [a for a in agg if a['run'].startswith('exp003b-h-q')]
    perf = [a for a in agg if a['run'].startswith('exp003b-h2-')]

    # Candidatos cerca de 3:1 y 5:1 (ratio sample).
    near_3 = sorted(
        [a for a in agg if a.get('ratio_sample_mean')],
        key=lambda a: abs(a['ratio_sample_mean'] - 3.0))[:5]
    near_5 = sorted(
        [a for a in agg if a.get('ratio_sample_mean')],
        key=lambda a: abs(a['ratio_sample_mean'] - 5.0))[:5]

    # Curva RD preliminar: rate = sample_bytes, dist = -PSNR (para plot) / PSNR.
    rd_curve = sorted(
        [{'run': a['run'], 'sample_bytes': a['sample_bytes_mean'], 'psnr': a['psnr_mean'],
          'ratio': a['ratio_sample_mean'], 'lambda': a.get('lambda'), 'detail': a.get('detail_corr_mean')}
         for a in sweep if a.get('sample_bytes_mean') and a.get('psnr_mean')],
        key=lambda x: x['sample_bytes'])

    doc = {
        'schema': 1,
        'experiment': 'EXP-003b',
        'base_commit': open(os.path.join(ROOT, 'benchmark', 'experiments',
                                         'exp003b-base-sha.txt')).read().strip(),
        'branch': 'experiment/pocket-og-rdoq',
        'measurement': {
            'primary': 'sample_bytes via braw_inspect',
            'raw_bytes_12bit_1080p': RAW,
            'sdk_frame_bytes': 'diagnostic only',
        },
        'n_runs': len(agg),
        'controls': controls,
        'lambda_sweep': sweep,
        'rd_curve_preliminary': rd_curve,
        'performance': perf,
        'candidates_near_3_to_1': near_3,
        'candidates_near_5_to_1': near_5,
        'aggregates': agg,
    }
    os.makedirs(os.path.dirname(args.out) or '.', exist_ok=True)
    with open(args.out, 'w', encoding='utf-8') as f:
        json.dump(doc, f, indent=2)
        f.write('\n')
    print('wrote', args.out)
    print('controls', len(controls), 'sweep', len(sweep), 'perf', len(perf))
    if rd_curve:
        print('RD points', len(rd_curve),
              'ratio range', rd_curve[0].get('ratio'), '…', rd_curve[-1].get('ratio'))


if __name__ == '__main__':
    main()
