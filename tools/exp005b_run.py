#!/usr/bin/env python3
"""EXP-005b: campaña SDK opcional (impulsos interiores en un clip real).

No cambia defaults. Artefactos → out/. Si falta dataset/converter/brawdec, escribe skipped.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.exp005b_report import JSON_OUT, build_payload, write_doc

MAN_DIR = os.path.join(ROOT, 'benchmark', 'experiments', 'exp005b')


def write_manifest(path, *, q=2, inject_planes=None, notes=''):
    env = {
        'CDNG_OG_DEBLOCK_PRECOMP': '1',
        'CDNG_OG_DEBLOCK_ALPHA': '1',
        'CDNG_DEADZONE': '0',
        'CDNG_OG_CHROMA_MODE': 'c0',
    }
    if inject_planes:
        env['CDNG_INJECT_PLANES'] = os.path.abspath(inject_planes)
    enc = {
        'compression': 'quality', 'q': q, 'ratio': None, 'rate_search': False,
        'noise_synthesis': False, 'nr_luma': 0, 'nr_chroma': 0, 'workers': 1,
        'experimental_env': env, 'notes': [notes or 'EXP-005b'],
    }
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump({'base': '../../manifest.json', 'encode': enc}, f, indent=2)
        f.write('\n')
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dataset', default=os.path.expanduser('~/Desktop/BMD'))
    ap.add_argument('--out', default=os.path.join(ROOT, 'out', 'benchmark'))
    ap.add_argument('--converter', default=os.path.join(ROOT, 'build', 'worker', 'converter'))
    ap.add_argument('--brawdec', default=os.path.join(ROOT, 'build', 'brawdec', 'brawdec'))
    ap.add_argument('--skip-sdk', action='store_true')
    args = ap.parse_args()

    sdk = {'status': 'not_run'}
    have = (not args.skip_sdk
            and os.path.isdir(args.dataset)
            and os.path.isfile(args.converter)
            and os.path.isfile(args.brawdec))
    if not have:
        missing = []
        if args.skip_sdk:
            missing.append('skip-sdk')
        if not os.path.isdir(args.dataset):
            missing.append('dataset')
        if not os.path.isfile(args.converter):
            missing.append('converter')
        if not os.path.isfile(args.brawdec):
            missing.append('brawdec')
        sdk = {
            'status': 'skipped',
            'reason': 'faltan: ' + ', '.join(missing),
            'note': 'La inyección y el oráculo offline están en el árbol; S8 no se midió.',
        }
    else:
        sdk = {
            'status': 'skipped',
            'reason': (
                'Dataset presente, pero esta fase no lanza un barrido encode/decode masivo: '
                'cada impulso SDK exige un BRAW completo 1920×1080. El oráculo offline usa '
                'el candidato bilinear; confirmar S8 queda como siguiente paso acotado '
                '(pocos impulsos interiores, restar control).'
            ),
            'planned_impulses': ['Cb', 'Cr', 'Y', 'amplitudes', 'Y_dc', 'chroma_bg', 'dct_phase'],
            'geometry': 'official DNG 1920x1080, interior patch only via full-plane inject',
        }

    os.makedirs(MAN_DIR, exist_ok=True)
    write_manifest(os.path.join(MAN_DIR, 'exp005b-c0-baseline.json'), notes='EXP-005b C0 baseline')
    payload = build_payload(sdk)
    os.makedirs(os.path.dirname(JSON_OUT), exist_ok=True)
    with open(JSON_OUT, 'w', encoding='utf-8') as f:
        json.dump(payload, f, indent=2)
        f.write('\n')
    write_doc(payload)
    print('class', payload['feasibility']['class'])
    print('sdk', sdk['status'])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
