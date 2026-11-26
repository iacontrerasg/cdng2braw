#!/usr/bin/env python3
"""Orquesta barridos EXP-002 (A04 q×alpha, ratios, serie A representativa)."""
import argparse
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYTHON = os.environ.get('PYTHON') or sys.executable


def write_manifest(path, *, q=None, ratio=None, alpha=None, bounded=False, ablation=False, notes=''):
    env = {'CDNG_OG_DEBLOCK_PRECOMP': '0' if ablation else '1'}
    if bounded:
        env['CDNG_OG_DEBLOCK_BOUNDED'] = '1'
    elif alpha is not None and not ablation:
        if abs(alpha - 1.0) > 1e-12:
            env['CDNG_OG_DEBLOCK_ALPHA'] = ('%g' % alpha)
    enc = {
        'compression': 'quality' if q is not None else 'ratio',
        'q': q, 'ratio': ratio,
        'rate_search': bool(ratio is not None),
        'noise_synthesis': False, 'nr_luma': 0, 'nr_chroma': 0, 'workers': 2,
        'experimental_env': env,
        'notes': [notes or 'EXP-002'],
    }
    if q is not None:
        enc['ratio'] = None
        enc['rate_search'] = False
    else:
        enc['q'] = None
    # Manifiestos viven en benchmark/experiments/exp002/ → base = ../../manifest.json
    doc = {'base': '../../manifest.json', 'encode': enc}
    os.makedirs(os.path.dirname(path), exist_ok=True)
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
        print('FALLÓ %s (exit %d)' % (run_id, r.returncode), file=sys.stderr)
        raise SystemExit(r.returncode)
    return os.path.join(args.out, 'runs', run_id)


def alpha_tag(a):
    if a is None:
        return 'bd'
    if abs(a - 1.0) < 1e-12:
        return 'a1'
    if abs(a - 0.5) < 1e-12:
        return 'a050'
    if abs(a - 0.75) < 1e-12:
        return 'a075'
    if abs(a - 0.0) < 1e-12:
        return 'a0'
    return 'a' + str(a).replace('.', '')


def phase_a04_grid(args):
    """25 q × {1, 0.5, 0.75} sobre A04."""
    qs = list(range(0, 25)) if args.q_max is None else list(range(0, args.q_max + 1))
    alphas = [1.0, 0.5, 0.75]
    if args.alphas:
        alphas = [float(x) for x in args.alphas.split(',')]
    runs = []
    for a in alphas:
        for q in qs:
            tag = alpha_tag(a)
            run_id = 'exp002-a04-%s-q%02d' % (tag, q)
            man = os.path.join(ROOT, 'benchmark', 'experiments', 'exp002', '%s-q%02d.json' % (tag, q))
            write_manifest(man, q=q, alpha=a, notes='EXP-002 A04 q=%d alpha=%g' % (q, a))
            if args.dry_run:
                print('dry', run_id, man)
                continue
            if args.skip_existing and os.path.isfile(os.path.join(args.out, 'runs', run_id, 'metrics.json')):
                print('skip', run_id)
                runs.append(os.path.join(args.out, 'runs', run_id))
                continue
            runs.append(run_bench(args, run_id, man))
    return runs


def phase_a04_special(args):
    """Ablación y bounded en q representativos + anclas de ratio."""
    qs = [0, 4, 9, 18, 20, 24]
    # Mapear ratios nominales ≈ q del barrido α=1 se hará post-hoc; aquí niveles fijos.
    runs = []
    for kind, kw in [('a0', {'ablation': True, 'alpha': 0.0}),
                     ('bd', {'bounded': True})]:
        for q in qs:
            run_id = 'exp002-a04-%s-q%02d' % (kind, q)
            man = os.path.join(ROOT, 'benchmark', 'experiments', 'exp002', '%s-q%02d.json' % (kind, q))
            write_manifest(man, q=q, notes='EXP-002 A04 special %s q=%d' % (kind, q), **kw)
            if args.dry_run:
                print('dry', run_id)
                continue
            if args.skip_existing and os.path.isfile(os.path.join(args.out, 'runs', run_id, 'metrics.json')):
                runs.append(os.path.join(args.out, 'runs', run_id))
                continue
            runs.append(run_bench(args, run_id, man))
    return runs


def phase_ratios(args):
    alphas = [1.0, 0.5, 0.75]
    ratios = [3, 5, 8, 12]
    runs = []
    for a in alphas:
        for ratio in ratios:
            tag = alpha_tag(a)
            run_id = 'exp002-a04-%s-r%02d' % (tag, ratio)
            man = os.path.join(ROOT, 'benchmark', 'experiments', 'exp002', '%s-r%02d.json' % (tag, ratio))
            write_manifest(man, ratio=ratio, alpha=a, notes='EXP-002 A04 ratio %d:1 alpha=%g' % (ratio, a))
            if args.dry_run:
                print('dry', run_id)
                continue
            if args.skip_existing and os.path.isfile(os.path.join(args.out, 'runs', run_id, 'metrics.json')):
                runs.append(os.path.join(args.out, 'runs', run_id))
                continue
            runs.append(run_bench(args, run_id, man))
    return runs


def phase_series_a(args):
    """Configuraciones representativas en serie A."""
    configs = []
    for a in (1.0, 0.5, 0.75):
        for q in (0, 4, 18, 24):
            configs.append(('q', q, a))
        for ratio in (3, 5, 8, 12):
            configs.append(('r', ratio, a))
    runs = []
    for mode, val, a in configs:
        tag = alpha_tag(a)
        if mode == 'q':
            run_id = 'exp002-sa-%s-q%02d' % (tag, val)
            man = os.path.join(ROOT, 'benchmark', 'experiments', 'exp002', 'sa-%s-q%02d.json' % (tag, val))
            write_manifest(man, q=val, alpha=a, notes='EXP-002 serie A q=%d alpha=%g' % (val, a))
        else:
            run_id = 'exp002-sa-%s-r%02d' % (tag, val)
            man = os.path.join(ROOT, 'benchmark', 'experiments', 'exp002', 'sa-%s-r%02d.json' % (tag, val))
            write_manifest(man, ratio=val, alpha=a, notes='EXP-002 serie A %d:1 alpha=%g' % (val, a))
        if args.dry_run:
            print('dry', run_id)
            continue
        if args.skip_existing and os.path.isfile(os.path.join(args.out, 'runs', run_id, 'metrics.json')):
            runs.append(os.path.join(args.out, 'runs', run_id))
            continue
        runs.append(run_bench(args, run_id, man, test=None, series='A'))
    return runs


def main():
    ap = argparse.ArgumentParser(description='EXP-002 sweep')
    ap.add_argument('--phase', required=True,
                    choices=['a04-grid', 'a04-special', 'ratios', 'series-a', 'all-a04'])
    ap.add_argument('--dataset', default=os.path.expanduser('~/Desktop/BMD'))
    ap.add_argument('--references', default=os.path.expanduser('~/Desktop/EXR'))
    ap.add_argument('--out', default=os.path.join(ROOT, 'out', 'benchmark'))
    ap.add_argument('--converter', default=os.path.join(ROOT, 'build', 'worker', 'converter'))
    ap.add_argument('--brawdec', default=os.path.join(ROOT, 'tools', 'brawdec', 'brawdec'))
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--skip-existing', action='store_true')
    ap.add_argument('--no-cache', action='store_true')
    ap.add_argument('--q-max', type=int, default=None, help='limitar barrido (pruebas)')
    ap.add_argument('--alphas', help='lista p.ej. 1,0.5,0.75')
    args = ap.parse_args()
    if args.phase == 'a04-grid':
        phase_a04_grid(args)
    elif args.phase == 'a04-special':
        phase_a04_special(args)
    elif args.phase == 'ratios':
        phase_ratios(args)
    elif args.phase == 'series-a':
        phase_series_a(args)
    elif args.phase == 'all-a04':
        phase_a04_grid(args)
        phase_a04_special(args)
        phase_ratios(args)


if __name__ == '__main__':
    main()
