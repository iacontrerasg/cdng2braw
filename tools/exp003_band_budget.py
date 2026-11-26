#!/usr/bin/env python3
"""Fase E: agrega energía/nz por banda desde coeff-stats.jsonl (sin atribuir VLC compartido)."""
from __future__ import annotations

import argparse
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_lines(path):
    if not os.path.isfile(path):
        return []
    out = []
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def summarize(stats_dir):
    rows = [r for r in load_lines(os.path.join(stats_dir, 'coeff-stats.jsonl'))
            if r.get('stage') == 'sample_written' or r.get('selected')]
    bud = [r for r in load_lines(os.path.join(stats_dir, 'bit-budget.jsonl'))
           if r.get('stage') == 'sample_written' or r.get('selected')]
    bands = []
    for r in rows:
        entry = {
            'source_name': r.get('source_name'),
            'payload_sha256': r.get('payload_sha256'),
            'q': r.get('q_effective'),
            'bands_Y': r.get('bands_Y'),
            'bands_Cb': r.get('bands_Cb'),  # proxy Cb+Cr mezclado
            'bands_note': r.get('bands_note'),
            'ac_zero': r.get('ac_zero'),
            'ac_nonzero': r.get('ac_nonzero'),
        }
        bands.append(entry)
    reconcile = []
    for b in bud:
        budget = b.get('budget') or {}
        attributed = budget.get('attributed_bits')
        payload_b = budget.get('payload_bytes') or b.get('slices_payload_bytes')
        ok = attributed is not None and payload_b is not None and attributed == payload_b * 8
        reconcile.append({
            'source_name': b.get('source_name'),
            'payload_bytes': payload_b,
            'attributed_bits': attributed,
            'reconcile_ok': ok,
            'planes_bits': {
                'Y': sum(budget.get(k, [0, 0, 0])[0] for k in
                         ('dc_bits', 'ac_prefix_bits', 'ac_final_bits', 'ac_magnitude_bits', 'eob_bits')),
                'Cb': sum(budget.get(k, [0, 0, 0])[1] for k in
                          ('dc_bits', 'ac_prefix_bits', 'ac_final_bits', 'ac_magnitude_bits', 'eob_bits')),
                'Cr': sum(budget.get(k, [0, 0, 0])[2] for k in
                          ('dc_bits', 'ac_prefix_bits', 'ac_final_bits', 'ac_magnitude_bits', 'eob_bits')),
            },
            'padding_bits': budget.get('padding_bits'),
            'marker_bits': budget.get('marker_bits'),
            'note': 'VLC por banda no atribuido; bits por plano sí. Runs/EOB son costes compartidos.',
        })
    return {'coeff_frames': bands, 'budget_frames': reconcile}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--stats-root', default=os.path.join(ROOT, 'out', 'benchmark', 'experiments', 'exp003', 'stats'))
    ap.add_argument('--runs', nargs='+', default=[
        'exp003-a0-orig', 'exp003-a1-dz50', 'exp003-b-y40-c50',
        'exp003-d1-protect-low', 'exp003-d5-compress-chroma',
    ])
    ap.add_argument('--out', default=os.path.join(ROOT, 'benchmark', 'experiments', 'exp003-band-budget.json'))
    args = ap.parse_args()
    doc = {'schema': 1, 'runs': {}}
    for run in args.runs:
        path = os.path.join(args.stats_root, run)
        doc['runs'][run] = summarize(path) if os.path.isdir(path) else {'error': 'missing'}
    os.makedirs(os.path.dirname(args.out) or '.', exist_ok=True)
    with open(args.out, 'w', encoding='utf-8') as f:
        json.dump(doc, f, indent=2)
        f.write('\n')
    print('wrote', args.out)


if __name__ == '__main__':
    main()
