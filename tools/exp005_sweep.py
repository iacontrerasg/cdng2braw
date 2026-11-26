#!/usr/bin/env python3
"""EXP-005: AHD vs algoritmos LibRaw disponibles. C0, q2, α=1, DZ=0, NR/synth/RDOQ OFF."""
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

from tools.exp004_fixtures import build_set, mosaic_rggb, write_fixture
from tools.exp004_sweep import G1_FRAMES
from tools.fidelity import rd as RD

MAN_DIR = os.path.join(ROOT, 'benchmark', 'experiments', 'exp005')

ALGOS = ('ahd', 'vng', 'ppg', 'dht', 'aahd', 'linear')
FRAMES = (('A04', '44'), ('A04', '49'), ('A01', '52'))
FINAL_ALGOS = ('ahd', 'vng', 'aahd')
SERIES_A = G1_FRAMES  # A01–A05, 6 fotogramas con EXR


def write_manifest(path, *, q=2, inject_rgb=None, inject_at=None, diagnostic=None,
                   notes='', debayer=None):
    env = {
        'CDNG_OG_DEBLOCK_PRECOMP': '1',
        'CDNG_OG_DEBLOCK_ALPHA': '1',
        'CDNG_DEADZONE': '0',
        'CDNG_OG_CHROMA_MODE': 'c0',
    }
    if debayer:
        env['CDNG_OG_DEBAYER'] = debayer
    if inject_rgb:
        env['CDNG_INJECT_RGB'] = os.path.abspath(inject_rgb)
        env['CDNG_INJECT_AT'] = inject_at or 'pre_map'
    enc = {
        'compression': 'quality', 'q': q, 'ratio': None, 'rate_search': False,
        'noise_synthesis': False, 'nr_luma': 0, 'nr_chroma': 0, 'workers': 1,
        'experimental_env': env, 'notes': [notes or 'EXP-005'],
    }
    doc = {'base': '../../manifest.json', 'encode': enc}
    if diagnostic:
        doc['encode']['diagnostic_env'] = diagnostic
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(doc, f, indent=2)
        f.write('\n')
    return path


def dump_diag(args, run_id):
    dump = os.path.abspath(os.path.join(args.out, 'experiments', 'exp005', 'stages', run_id))
    stats = os.path.abspath(os.path.join(args.out, 'experiments', 'exp005', 'stats', run_id))
    if not args.skip_existing:
        import shutil
        shutil.rmtree(dump, ignore_errors=True)
    os.makedirs(dump, exist_ok=True)
    os.makedirs(stats, exist_ok=True)
    return {'CDNG_DUMP_STAGES': dump, 'CDNG_DUMP_FRAME': '0',
            'CDNG_BIT_BUDGET': '1', 'CDNG_COEFF_STATS': '1', 'CDNG_STATS_DIR': stats,
            'CDNG_STATS_LABEL': run_id, 'CDNG_ALIGN_SOFT': '1'}


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


def _do(args, run_id, test, frames, *, q=2, debayer=None, inject=None, inject_at=None,
        no_reference=False, notes=''):
    man = os.path.join(MAN_DIR, run_id + '.json')
    write_manifest(man, q=q, inject_rgb=inject, inject_at=inject_at, debayer=debayer,
                   diagnostic=dump_diag(args, run_id), notes=notes)
    if maybe_skip(args, run_id, q=q, alpha=1.0, deadzone=0.0):
        return os.path.join(args.out, 'runs', run_id)
    return run_bench(args, run_id, man, test=test, frames=frames, no_reference=no_reference)


def phase_identity(args):
    """Default (sin CDNG_OG_DEBAYER) vs ahd explícito: deben ser bit-idénticos."""
    args_nc = argparse.Namespace(**vars(args))
    args_nc.no_cache = True
    args_nc.skip_existing = False
    a = _do(args_nc, 'exp005-ident-default', 'A04', '44', notes='default AHD unset')
    b = _do(args_nc, 'exp005-ident-ahd', 'A04', '44', debayer='ahd', notes='explicit ahd')
    return [a, b]


def phase_algos(args):
    out = []
    for test, fr in FRAMES:
        for algo in ALGOS:
            run_id = 'exp005-%s-%s-f%s-q2' % (algo, test.lower(), fr)
            try:
                out.append(_do(args, run_id, test, fr, debayer=algo,
                               notes='EXP-005 %s q2 C0' % algo))
            except SystemExit:
                print('WARN: encode failed for', run_id, file=sys.stderr)
                raise
    return out


def phase_synth(args):
    fxdir = os.path.join(args.out, 'experiments', 'exp005', 'fixtures')
    fx = build_set(1080, 1920)
    names = ['edge_rg_v', 'checker_chroma', 'chirp_chroma', 'flat_bg']
    out = []
    for name in names:
        rgb_path = write_fixture(fxdir, name, fx[name], extra={'ground_truth': True})
        run_id = 'exp005-syn-%s-q2' % name.replace('_', '-')
        out.append(_do(args, run_id, 'A04', '44', inject=rgb_path, inject_at='pre_map',
                       no_reference=True, notes='synthetic GT inject ' + name))
        mosa = mosaic_rggb(fx[name])
        # Mosaic is diagnostic only; inject remains RGB GT (uncompressed DNG patch often unavailable).
        np_path = os.path.join(fxdir, name + '_mosaic.json')
        with open(np_path, 'w', encoding='utf-8') as f:
            json.dump({'name': name, 'mosaic_mean': float(mosa.mean()),
                       'limitation': 'Pocket OG DNGs are compressed; mosaic not patched into DNG'}, f, indent=2)
    return out


def phase_final(args):
    """F: AHD/VNG/AAHD, serie A, q2 y q18. C0 α=1 DZ=0. No persigue borrar el halo."""
    out = []
    for q in (2, 18):
        for test, fr in SERIES_A:
            for algo in FINAL_ALGOS:
                run_id = 'exp005-f-%s-%s-f%s-q%d' % (algo, test.lower(), fr, q)
                out.append(_do(args, run_id, test, fr, q=q, debayer=algo,
                               notes='EXP-005 F %s q%d C0' % (algo, q)))
    return out


def phase_inject_placeholder(args):
    """La inversa S9 se materializa en exp005_report tras leer S2/S9; aquí solo asegura el directorio."""
    os.makedirs(os.path.join(args.out, 'experiments', 'exp005', 'inject'), exist_ok=True)
    return []


def main():
    os.environ.setdefault('CDNG_ALIGN_SOFT', '1')
    p = argparse.ArgumentParser()
    p.add_argument('--dataset', default=os.path.expanduser('~/Desktop/BMD'))
    p.add_argument('--references', default=os.path.expanduser('~/Desktop/EXR'))
    p.add_argument('--out', default=os.path.join(ROOT, 'out', 'benchmark'))
    p.add_argument('--converter', default=os.path.join(ROOT, 'build', 'worker', 'converter'))
    p.add_argument('--brawdec', default=os.path.join(ROOT, 'tools', 'brawdec', 'brawdec'))
    p.add_argument('--phase', choices=('identity', 'algos', 'synth', 'final', 'all'), default='all')
    p.add_argument('--skip-existing', action='store_true')
    p.add_argument('--no-cache', action='store_true')
    args = p.parse_args()
    os.makedirs(MAN_DIR, exist_ok=True)
    if args.phase in ('identity', 'all'):
        phase_identity(args)
    if args.phase in ('algos', 'all'):
        phase_algos(args)
    if args.phase in ('synth', 'all'):
        phase_synth(args)
    if args.phase in ('final', 'all'):
        phase_final(args)
    phase_inject_placeholder(args)


if __name__ == '__main__':
    main()
