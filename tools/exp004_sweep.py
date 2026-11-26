#!/usr/bin/env python3
"""EXP-004 fases B–D: dumps, fixtures, ablaciones. NR/synth/RDOQ OFF. No altera defaults."""
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

from tools.exp004_fixtures import build_set, write_fixture
from tools.fidelity import identity
from tools.fidelity import rd as RD

MAN_DIR = os.path.join(ROOT, 'benchmark', 'experiments', 'exp004')
POL = os.path.join(ROOT, 'benchmark', 'experiments', 'exp003', 'policies', 'plane-y40-c50.json')


def write_manifest(path, *, q, alpha=1.0, deadzone=None, policy=None,
                   inject_rgb=None, inject_at=None, diagnostic=None, notes=''):
    env = {'CDNG_OG_DEBLOCK_PRECOMP': '1'}
    if abs(float(alpha) - 1.0) > 1e-12:
        env['CDNG_OG_DEBLOCK_ALPHA'] = '%g' % float(alpha)
    if deadzone is not None:
        env['CDNG_DEADZONE'] = '%g' % float(deadzone)
    if policy:
        env['CDNG_DEADZONE_POLICY'] = os.path.abspath(policy)
    if inject_rgb:
        env['CDNG_INJECT_RGB'] = os.path.abspath(inject_rgb)
        env['CDNG_INJECT_AT'] = inject_at or 'pre_map'
    enc = {
        'compression': 'quality', 'q': q, 'ratio': None, 'rate_search': False,
        'noise_synthesis': False, 'nr_luma': 0, 'nr_chroma': 0, 'workers': 1,
        'experimental_env': env, 'notes': [notes or 'EXP-004'],
    }
    doc = {'base': '../../manifest.json', 'encode': enc}
    if diagnostic:
        doc['encode']['diagnostic_env'] = diagnostic
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(doc, f, indent=2)
        f.write('\n')
    return path


def dump_diag(args, run_id, extra=None):
    dump = os.path.abspath(os.path.join(args.out, 'experiments', 'exp004', 'stages', run_id))
    stats = os.path.abspath(os.path.join(args.out, 'experiments', 'exp004', 'stats', run_id))
    os.makedirs(dump, exist_ok=True)
    os.makedirs(stats, exist_ok=True)
    d = {'CDNG_DUMP_STAGES': dump, 'CDNG_DUMP_FRAME': '0',
         'CDNG_BIT_BUDGET': '1', 'CDNG_COEFF_STATS': '1', 'CDNG_STATS_DIR': stats,
         'CDNG_ALIGN_SOFT': '1'}
    if extra:
        d.update(extra)
    return d


def run_bench(args, run_id, manifest, *, test, frames=None, no_reference=False):
    cmd = [PYTHON, '-m', 'tools.fidelity', 'run',
           '--dataset', args.dataset, '--references', args.references,
           '--out', args.out, '--converter', args.converter, '--brawdec', args.brawdec,
           '--run', run_id, '--manifest', manifest, '--test', test]
    if frames:
        cmd += ['--frames', frames]
    if no_reference:
        cmd.append('--no-reference')
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
    RD.verify_run_matches_request(run_dir, **kw)
    print('skip', run_id, flush=True)
    return run_dir


def _do(args, run_id, man_name, test, frames, man_kw, verify_kw, no_reference=False):
    man = os.path.join(MAN_DIR, man_name)
    write_manifest(man, diagnostic=dump_diag(args, run_id), **man_kw)
    if maybe_skip(args, run_id, **verify_kw):
        return os.path.join(args.out, 'runs', run_id)
    return run_bench(args, run_id, man, test=test, frames=frames, no_reference=no_reference)


def phase_real(args):
    jobs = [
        ('exp004-a04-g0-q24', 'A04', '44',
         dict(q=24, alpha=1.0, deadzone=0.0, notes='G0 legacy'),
         dict(q=24, alpha=1.0, deadzone=0.0)),
        ('exp004-a04-g3-q2', 'A04', '44',
         dict(q=2, alpha=1.0, deadzone=0.0, notes='G3 maxfid'),
         dict(q=2, alpha=1.0, deadzone=0.0)),
        ('exp004-a04-g2-q24', 'A04', '44',
         dict(q=24, alpha=1.0, policy=POL, notes='G2 plane-y40-c50'),
         dict(q=24, alpha=1.0, policy_sha=identity.sha256_file(POL))),
        ('exp004-a04-q18', 'A04', '44',
         dict(q=18, alpha=1.0, deadzone=0.0), dict(q=18, alpha=1.0, deadzone=0.0)),
        ('exp004-a04-a075-q24', 'A04', '44',
         dict(q=24, alpha=0.75, deadzone=0.0), dict(q=24, alpha=0.75, deadzone=0.0)),
        ('exp004-a04-a0-q24', 'A04', '44',
         dict(q=24, alpha=0.0, deadzone=0.0), dict(q=24, alpha=0.0, deadzone=0.0)),
        ('exp004-a04-f049-g0', 'A04', '49',
         dict(q=24, alpha=1.0, deadzone=0.0), dict(q=24, alpha=1.0, deadzone=0.0)),
        ('exp004-a01-g0', 'A01', '52',
         dict(q=24, alpha=1.0, deadzone=0.0), dict(q=24, alpha=1.0, deadzone=0.0)),
    ]
    # bounded: PRECOMP on + BOUNDED — encode_request handles via experimental_env in write_manifest?
    out = []
    for run_id, test, frames, man_kw, vkw in jobs:
        vkw = {k: v for k, v in vkw.items() if v is not None}
        vkw['q'] = man_kw['q']
        out.append(_do(args, run_id, run_id + '.json', test, frames, man_kw, vkw))
    # bounded + precomp off via extra env in a dedicated manifest
    for run_id, extra, notes in (
        ('exp004-a04-bounded-q24', {'CDNG_OG_DEBLOCK_BOUNDED': '1'}, 'bounded precomp'),
        ('exp004-a04-precomp-off-q24', {'CDNG_OG_DEBLOCK_PRECOMP': '0'}, 'precomp off'),
    ):
        man = os.path.join(MAN_DIR, run_id + '.json')
        write_manifest(man, q=24, alpha=1.0, deadzone=0.0, notes=notes, diagnostic=dump_diag(args, run_id))
        # merge extra into experimental_env
        doc = json.load(open(man, encoding='utf-8'))
        doc['encode']['experimental_env'].update(extra)
        if extra.get('CDNG_OG_DEBLOCK_PRECOMP') == '0':
            doc['encode']['experimental_env'].pop('CDNG_OG_DEBLOCK_ALPHA', None)
        with open(man, 'w', encoding='utf-8') as f:
            json.dump(doc, f, indent=2)
            f.write('\n')
        if not maybe_skip(args, run_id, q=24, bounded=extra.get('CDNG_OG_DEBLOCK_BOUNDED') == '1',
                          ablation=extra.get('CDNG_OG_DEBLOCK_PRECOMP') == '0'):
            run_bench(args, run_id, man, test='A04', frames='44')
        out.append(os.path.join(args.out, 'runs', run_id))
    return out


def phase_synth(args):
    fxdir = os.path.join(args.out, 'experiments', 'exp004', 'fixtures')
    fx = build_set(1080, 1920)
    names_q = [('edge_neutral_v', (2, 24)), ('edge_neutral_v_phase1', (2,)),
               ('edge_rg_v', (2,)), ('checker_chroma', (2,)), ('chirp_chroma', (2,))]
    out = []
    for name, qs in names_q:
        rgb_path = write_fixture(fxdir, name, fx[name])
        for q in qs:
            run_id = 'exp004-syn-%s-q%d' % (name.replace('_', '-'), q)
            man_kw = dict(q=q, alpha=1.0, deadzone=0.0, inject_rgb=rgb_path, inject_at='pre_map',
                          notes='synthetic RGB ' + name)
            verify = dict(q=q)
            out.append(_do(args, run_id, run_id + '.json', 'A04', '44', man_kw, verify, no_reference=True))
    return out


def phase_identity(args):
    """Dump ON vs implícito OFF: misma petición de encode; el dump es diagnóstico."""
    run_id = 'exp004-ident-g0'
    man = os.path.join(MAN_DIR, run_id + '.json')
    write_manifest(man, q=24, alpha=1.0, deadzone=0.0, notes='identity dump on',
                   diagnostic=dump_diag(args, run_id))
    if not maybe_skip(args, run_id, q=24):
        run_bench(args, run_id, man, test='A04', frames='44')
    run_id2 = 'exp004-ident-g0-nodump'
    man2 = os.path.join(MAN_DIR, run_id2 + '.json')
    write_manifest(man2, q=24, alpha=1.0, deadzone=0.0, notes='identity dump off',
                   diagnostic={'CDNG_ALIGN_SOFT': '1'})
    if not maybe_skip(args, run_id2, q=24):
        run_bench(args, run_id2, man2, test='A04', frames='44')
    return [os.path.join(args.out, 'runs', run_id), os.path.join(args.out, 'runs', run_id2)]


def main():
    os.environ.setdefault('CDNG_ALIGN_SOFT', '1')
    p = argparse.ArgumentParser()
    p.add_argument('--dataset', default=os.path.expanduser('~/Desktop/BMD'))
    p.add_argument('--references', default=os.path.expanduser('~/Desktop/EXR'))
    p.add_argument('--out', default=os.path.join(ROOT, 'out', 'benchmark'))
    p.add_argument('--converter', default=os.path.join(ROOT, 'build', 'worker', 'converter'))
    p.add_argument('--brawdec', default=os.path.join(ROOT, 'tools', 'brawdec', 'brawdec'))
    p.add_argument('--phase', choices=('real', 'synth', 'identity', 'all'), default='all')
    p.add_argument('--skip-existing', action='store_true')
    p.add_argument('--no-cache', action='store_true')
    args = p.parse_args()
    os.makedirs(MAN_DIR, exist_ok=True)
    if args.phase in ('identity', 'all'):
        phase_identity(args)
    if args.phase in ('real', 'all'):
        phase_real(args)
    if args.phase in ('synth', 'all'):
        phase_synth(args)


if __name__ == '__main__':
    main()
