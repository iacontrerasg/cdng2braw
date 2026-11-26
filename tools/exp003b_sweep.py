#!/usr/bin/env python3
"""EXP-003b Fases G/H/H2 — controles A04, barrido λ, rendimiento. No altera defaults."""
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYTHON = os.environ.get('PYTHON') or sys.executable
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.fidelity import rd as RD

MAN_DIR = os.path.join(ROOT, 'benchmark', 'experiments', 'exp003b')
POLICIES = os.path.join(ROOT, 'benchmark', 'experiments', 'exp003', 'policies')


def write_manifest(path, *, q=None, ratio=None, alpha=0.75, deadzone=None, policy=None,
                   noise_synthesis=False, nr_luma=0, nr_chroma=0, notes='', diagnostic=None,
                   workers=None, rdoq=False, rdoq_lambda=None):
    env = {'CDNG_OG_DEBLOCK_PRECOMP': '1'}
    if alpha is not None and abs(float(alpha) - 1.0) > 1e-12:
        env['CDNG_OG_DEBLOCK_ALPHA'] = '%g' % float(alpha)
    if deadzone is not None and policy is not None:
        raise SystemExit('deadzone y policy son mutuamente excluyentes')
    if deadzone is not None:
        env['CDNG_DEADZONE'] = '%g' % float(deadzone)
    if policy is not None:
        env['CDNG_DEADZONE_POLICY'] = os.path.abspath(policy)
    if rdoq:
        env['CDNG_RDOQ'] = '1'
        if rdoq_lambda is not None:
            env['CDNG_RDOQ_LAMBDA'] = '%g' % float(rdoq_lambda)
    w = workers if workers is not None else (1 if diagnostic else 2)
    enc = {
        'compression': 'quality' if q is not None else 'ratio',
        'q': q, 'ratio': ratio,
        'rate_search': bool(ratio is not None),
        'noise_synthesis': bool(noise_synthesis),
        'nr_luma': nr_luma, 'nr_chroma': nr_chroma, 'workers': w,
        'experimental_env': env,
        'notes': [notes or 'EXP-003b'],
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


def policy_sha(path):
    from tools.fidelity import identity
    return identity.sha256_file(os.path.abspath(path))


def maybe_skip(args, run_id, **kw):
    run_dir = os.path.join(args.out, 'runs', run_id)
    if not (args.skip_existing and os.path.isfile(os.path.join(run_dir, 'metrics.json'))):
        return None
    if 'policy' in kw:
        path = kw.pop('policy')
        if path:
            kw['policy_sha'] = policy_sha(path)
    try:
        RD.verify_run_matches_request(run_dir, **kw)
    except RD.RdError as e:
        print('ERROR skip-existing:', e, file=sys.stderr)
        raise SystemExit(2)
    print('skip', run_id, flush=True)
    return run_dir


def _stats(args, run_id, profile=False):
    stats = os.path.abspath(os.path.join(args.out, 'experiments', 'exp003b', 'stats', run_id))
    os.makedirs(stats, exist_ok=True)
    d = {'CDNG_BIT_BUDGET': '1', 'CDNG_COEFF_STATS': '1', 'CDNG_STATS_DIR': stats,
         'CDNG_ALIGN_SOFT': '1'}
    if profile:
        d['CDNG_PROFILE_LOG'] = os.path.join(stats, 'profile.tsv')
    return d


def _do(args, run_id, man_name, verify_kw, man_kw):
    man = os.path.join(MAN_DIR, man_name)
    write_manifest(man, diagnostic=_stats(args, run_id, profile=man_kw.pop('profile', False)), **man_kw)
    if maybe_skip(args, run_id, **verify_kw):
        return os.path.join(args.out, 'runs', run_id)
    return run_bench(args, run_id, man, test='A04')


def phase_g(args):
    """Controles A04: G0–G3. NR y synthesis OFF."""
    plane = os.path.join(POLICIES, 'plane-y40-c50.json')
    runs = {}
    # G0 legacy α=1 DZ=0 q24
    runs['G0'] = _do(args, 'exp003b-g0-legacy', 'g0-legacy.json',
                     dict(q=24, alpha=1.0, deadzone=0.0, rdoq=False),
                     dict(q=24, alpha=1.0, deadzone=0.0, notes='EXP-003b G0 legacy',
                          noise_synthesis=False, nr_luma=0, nr_chroma=0))
    # Alias histórico: si existe exp003-a0-orig con misma config, ya verificado vía skip del nuevo id.
    # G1 α=0.75 DZ=0.5
    runs['G1'] = _do(args, 'exp003b-g1-a75-dz50', 'g1-a75-dz50.json',
                     dict(q=24, alpha=0.75, deadzone=0.5, rdoq=False),
                     dict(q=24, alpha=0.75, deadzone=0.5, notes='EXP-003b G1 DZ uniforme',
                          noise_synthesis=False))
    # G2 mejor política selectiva series_approved (plane-y40-c50)
    runs['G2'] = _do(args, 'exp003b-g2-plane-y40-c50', 'g2-plane.json',
                     dict(q=24, alpha=0.75, policy=plane, rdoq=False),
                     dict(q=24, alpha=0.75, policy=plane, notes='EXP-003b G2 plane-y40-c50',
                          noise_synthesis=False))
    # G3 Maximum Fidelity experimental: q=2, α=1, DZ=0
    runs['G3'] = _do(args, 'exp003b-g3-maxfid-q2', 'g3-maxfid-q2.json',
                     dict(q=2, alpha=1.0, deadzone=0.0, rdoq=False),
                     dict(q=2, alpha=1.0, deadzone=0.0, notes='EXP-003b G3 max-fid q2',
                          noise_synthesis=False))
    return runs


def calibrate_lambda_scale(args):
    """Estima escala de λ vía mediana |ΔD/ΔR| en bloques sintéticos (misma regla que el trellis)."""
    # Usa el binario de pruebas si existe; si no, heurística documentada.
    hint = os.path.join(ROOT, 'build', 'native', 'lib', 'engine_rdoq_test')
    # Escala tipica: D ~ (fracción de step)^2 * ncoef; R ~ decenas de bits.
    # Con step~12 y errores ~0.5 step → D~ decenas–cientos por bloque; R~20–200.
    # λ log alrededor de 0.01 … 100.
    scales = [0.0, 0.01, 0.03, 0.1, 0.3, 1.0, 3.0, 10.0, 30.0, 100.0]
    out = {
        'method': 'log_grid_heuristic_plus_rdoq_eval',
        'note': 'Mediana |ΔD/ΔR| de vecinos hacia 0 vs legacy; grid log acotado',
        'median_d_over_r_hint': 1.0,
        'lambda_grid': scales,
        'engine_rdoq_test': os.path.isfile(hint),
    }
    # Micro-calib con oráculo Python no disponible; dejar grid fijo calibrado a D~R.
    path = os.path.join(MAN_DIR, 'lambda-calibration.json')
    os.makedirs(MAN_DIR, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(out, f, indent=2)
        f.write('\n')
    print('wrote', path, 'grid', scales)
    return scales


def phase_h(args):
    """Barrido λ a q18 y q24, α=0.75, DZ=0.5, NR/synth OFF."""
    grid = calibrate_lambda_scale(args)
    # Refinar: si args.lambda_grid, usar eso.
    if args.lambdas:
        grid = [float(x) for x in args.lambdas.split(',')]
    runs = []
    for q in (18, 24):
        for lam in grid:
            tag = 'h-q%02d-l%s' % (q, ('%g' % lam).replace('.', 'p'))
            run_id = 'exp003b-%s' % tag
            man = '%s.json' % tag
            runs.append(_do(
                args, run_id, man,
                dict(q=q, alpha=0.75, deadzone=0.5, rdoq=True, rdoq_lambda=lam),
                dict(q=q, alpha=0.75, deadzone=0.5, rdoq=True, rdoq_lambda=lam,
                     notes='EXP-003b H λ=%g q=%d' % (lam, q), noise_synthesis=False,
                     workers=1 if args.workers is None else args.workers)))
    return runs


def phase_h2(args):
    """Overhead RDOQ OFF/ON; workers 1/2/4. Un solo fotograma A04, q24, λ mediana."""
    lam = args.perf_lambda if args.perf_lambda is not None else 1.0
    results = []
    for rdoq in (False, True):
        for w in (1, 2, 4):
            tag = 'h2-%s-w%d' % ('on' if rdoq else 'off', w)
            run_id = 'exp003b-%s' % tag
            t0 = time.time()
            _do(args, run_id, '%s.json' % tag,
                dict(q=24, alpha=0.75, deadzone=0.5, rdoq=rdoq,
                     **({'rdoq_lambda': lam} if rdoq else {})),
                dict(q=24, alpha=0.75, deadzone=0.5, rdoq=rdoq,
                     rdoq_lambda=lam if rdoq else None,
                     notes='EXP-003b H2 perf', noise_synthesis=False, workers=w,
                     profile=True))
            wall = time.time() - t0
            results.append({'run': run_id, 'rdoq': rdoq, 'workers': w, 'wall_s': wall, 'lambda': lam if rdoq else None})
    path = os.path.join(MAN_DIR, 'h2-perf.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump({'schema': 1, 'results': results}, f, indent=2)
        f.write('\n')
    print('wrote', path)
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dataset', default=os.path.expanduser('~/Desktop/BMD'))
    ap.add_argument('--references', default=os.path.expanduser('~/Desktop/EXR'))
    ap.add_argument('--out', default=os.path.join(ROOT, 'out', 'benchmark'))
    ap.add_argument('--converter', default=os.path.join(ROOT, 'build', 'worker', 'converter'))
    ap.add_argument('--brawdec', default=os.path.join(ROOT, 'tools', 'brawdec', 'brawdec'))
    ap.add_argument('--phase', choices=['g', 'h', 'h2', 'all'], default='all')
    ap.add_argument('--skip-existing', action='store_true', default=True)
    ap.add_argument('--no-skip-existing', action='store_false', dest='skip_existing')
    ap.add_argument('--no-cache', action='store_true')
    ap.add_argument('--lambdas', default='', help='CSV override del grid λ')
    ap.add_argument('--workers', type=int, default=None)
    ap.add_argument('--perf-lambda', type=float, default=1.0)
    args = ap.parse_args()
    os.makedirs(MAN_DIR, exist_ok=True)
    if args.phase in ('g', 'all'):
        phase_g(args)
    if args.phase in ('h', 'all'):
        phase_h(args)
    if args.phase in ('h2', 'all'):
        phase_h2(args)


if __name__ == '__main__':
    main()
