#!/usr/bin/env python3
"""Orquesta fases EXP-003 (adaptive AC sparsity). No altera defaults del encoder."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYTHON = os.environ.get('PYTHON') or sys.executable
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.fidelity import rd as RD

POLICIES = os.path.join(ROOT, 'benchmark', 'experiments', 'exp003', 'policies')
MAN_DIR = os.path.join(ROOT, 'benchmark', 'experiments', 'exp003')


def write_manifest(path, *, q=None, ratio=None, alpha=0.75, deadzone=None, policy=None,
                   noise_synthesis=False, nr_luma=0, nr_chroma=0, notes='', diagnostic=None,
                   workers=None):
    env = {'CDNG_OG_DEBLOCK_PRECOMP': '1'}
    if alpha is not None and abs(float(alpha) - 1.0) > 1e-12:
        env['CDNG_OG_DEBLOCK_ALPHA'] = '%g' % float(alpha)
    if deadzone is not None and policy is not None:
        raise SystemExit('deadzone y policy son mutuamente excluyentes')
    if deadzone is not None:
        env['CDNG_DEADZONE'] = '%g' % float(deadzone)
    if policy is not None:
        env['CDNG_DEADZONE_POLICY'] = os.path.abspath(policy)
    w = workers if workers is not None else (1 if diagnostic else 2)
    enc = {
        'compression': 'quality' if q is not None else 'ratio',
        'q': q, 'ratio': ratio,
        'rate_search': bool(ratio is not None),
        'noise_synthesis': bool(noise_synthesis),
        'nr_luma': nr_luma, 'nr_chroma': nr_chroma, 'workers': w,
        'experimental_env': env,
        'notes': [notes or 'EXP-003'],
    }
    if q is not None:
        enc['ratio'] = None
        enc['rate_search'] = False
    else:
        enc['q'] = None
    doc = {'base': '../../manifest.json', 'encode': enc}
    if diagnostic:
        doc['encode']['diagnostic_env'] = diagnostic
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(doc, f, indent=2)
        f.write('\n')
    return path


def run_bench(args, run_id, manifest, *, test='A04', series=None):
    cmd = [PYTHON, '-m', 'tools.fidelity', 'run',
           '--dataset', args.dataset, '--references', args.references,
           '--out', args.out, '--converter', args.converter, '--brawdec', args.brawdec,
           '--run', run_id, '--manifest', manifest]
    if test:
        cmd += ['--test', test]
    if series:
        cmd += ['--series', series]
    if args.no_cache:
        cmd.append('--no-cache')
    print('+', ' '.join(cmd), flush=True)
    r = subprocess.run(cmd, cwd=ROOT)
    if r.returncode:
        raise SystemExit(r.returncode)
    return os.path.join(args.out, 'runs', run_id)


def maybe_skip(args, run_id, **kw):
    run_dir = os.path.join(args.out, 'runs', run_id)
    if not (args.skip_existing and os.path.isfile(os.path.join(run_dir, 'metrics.json'))):
        return None
    try:
        RD.verify_run_matches_request(run_dir, **kw)
    except RD.RdError as e:
        print('ERROR skip-existing:', e, file=sys.stderr)
        raise SystemExit(2)
    print('skip', run_id, flush=True)
    return run_dir


def _stats(args, run_id):
    stats = os.path.abspath(os.path.join(args.out, 'experiments', 'exp003', 'stats', run_id))
    os.makedirs(stats, exist_ok=True)
    return {'CDNG_BIT_BUDGET': '1', 'CDNG_COEFF_STATS': '1', 'CDNG_STATS_DIR': stats,
            'CDNG_ALIGN_SOFT': '1'}


def phase_a(args):
    """Controles A0–A3 sobre A04."""
    ctrls = [
        ('a0-orig', dict(q=24, alpha=1.0, deadzone=0.0, notes='EXP-003 A0 original')),
        ('a1-dz50', dict(q=24, alpha=0.75, deadzone=0.5, notes='EXP-003 A1 DZ uniforme')),
        ('a2-nr50-dz25', dict(q=24, alpha=0.75, deadzone=0.25, nr_luma=50, nr_chroma=50,
                              notes='EXP-003 A2 NR+DZ')),
        ('a3-q18', dict(q=18, alpha=0.75, deadzone=0.0, notes='EXP-003 A3 q18')),
    ]
    runs = []
    for tag, kw in ctrls:
        run_id = 'exp003-%s' % tag
        man = os.path.join(MAN_DIR, '%s.json' % tag)
        write_manifest(man, diagnostic=_stats(args, run_id), **kw)
        if args.dry_run:
            print('dry', run_id); continue
        skipped = maybe_skip(args, run_id, q=kw['q'], alpha=kw['alpha'],
                             deadzone=kw.get('deadzone'))
        if skipped:
            runs.append(skipped); continue
        runs.append(run_bench(args, run_id, man, test='A04'))
    return runs


def phase_b(args):
    """Matriz zY × zC (uniforme en diagonal + per_plane fuera)."""
    zs = [0.0, 0.1, 0.25, 0.4, 0.5]
    runs = []
    for zy in zs:
        for zc in zs:
            if zy == zc:
                pol = os.path.join(POLICIES, 'uniform-z%02d.json' % int(zy * 100))
                tag = 'b-u%02d' % int(zy * 100)
            else:
                pol = os.path.join(POLICIES, 'plane-y%02d-c%02d.json' % (int(zy * 100), int(zc * 100)))
                tag = 'b-y%02d-c%02d' % (int(zy * 100), int(zc * 100))
            if not os.path.isfile(pol):
                print('missing', pol, file=sys.stderr); continue
            run_id = 'exp003-%s' % tag
            man = os.path.join(MAN_DIR, '%s.json' % tag)
            write_manifest(man, q=24, alpha=0.75, policy=pol,
                           notes='EXP-003 Fase B zy=%g zc=%g' % (zy, zc),
                           diagnostic=_stats(args, run_id))
            if args.dry_run:
                print('dry', run_id); continue
            skipped = maybe_skip(args, run_id, q=24, alpha=0.75)
            if skipped:
                runs.append(skipped); continue
            runs.append(run_bench(args, run_id, man, test='A04'))
    return runs


def phase_d(args):
    """Políticas representativas D1–D5."""
    runs = []
    for name in ('d1-protect-low', 'd2-protect-high', 'd3-protect-mid',
                 'd4-protect-chroma', 'd5-compress-chroma'):
        pol = os.path.join(POLICIES, name + '.json')
        run_id = 'exp003-%s' % name
        man = os.path.join(MAN_DIR, name + '.json')
        write_manifest(man, q=24, alpha=0.75, policy=pol,
                       notes='EXP-003 Fase D %s' % name,
                       diagnostic=_stats(args, run_id))
        if args.dry_run:
            print('dry', run_id); continue
        skipped = maybe_skip(args, run_id, q=24, alpha=0.75)
        if skipped:
            runs.append(skipped); continue
        runs.append(run_bench(args, run_id, man, test='A04'))
    return runs


def phase_j(args):
    """Rate search 3/5/8/12 con política candidata (d1 por defecto)."""
    pol = os.path.join(POLICIES, args.candidate_policy + '.json')
    runs = []
    for ratio in (3, 5, 8, 12):
        run_id = 'exp003-j-r%02d-%s' % (ratio, args.candidate_policy)
        man = os.path.join(MAN_DIR, 'j-r%02d-%s.json' % (ratio, args.candidate_policy))
        write_manifest(man, ratio=float(ratio), alpha=0.75, policy=pol,
                       notes='EXP-003 Fase J ratio=%d' % ratio,
                       diagnostic=_stats(args, run_id))
        if args.dry_run:
            print('dry', run_id); continue
        skipped = maybe_skip(args, run_id, ratio=float(ratio), alpha=0.75)
        if skipped:
            runs.append(skipped); continue
        runs.append(run_bench(args, run_id, man, test='A04'))
    return runs


def main():
    ap = argparse.ArgumentParser(description='EXP-003 adaptive AC sparsity sweep')
    ap.add_argument('--dataset', default=os.path.expanduser('~/Desktop/BMD'))
    ap.add_argument('--references', default='')
    ap.add_argument('--out', default=os.path.join(ROOT, 'out', 'benchmark'))
    ap.add_argument('--converter', default=os.path.join(ROOT, 'build', 'worker', 'converter'))
    ap.add_argument('--brawdec', default=os.path.join(ROOT, 'tools', 'brawdec', 'brawdec'))
    ap.add_argument('--phase', choices=['a', 'b', 'd', 'j', 'all'], default='a')
    ap.add_argument('--candidate-policy', default='d1-protect-low')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--skip-existing', action='store_true')
    ap.add_argument('--no-cache', action='store_true')
    args = ap.parse_args()
    os.environ['CDNG_ALIGN_SOFT'] = '1'
    phases = {
        'a': phase_a,
        'b': phase_b,
        'd': phase_d,
        'j': phase_j,
    }
    if args.phase == 'all':
        for fn in (phase_a, phase_b, phase_d, phase_j):
            fn(args)
    else:
        phases[args.phase](args)


if __name__ == '__main__':
    main()
