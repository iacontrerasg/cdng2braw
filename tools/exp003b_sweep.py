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


def _do(args, run_id, man_name, verify_kw, man_kw, *, test='A04', series=None):
    man = os.path.join(MAN_DIR, man_name)
    write_manifest(man, diagnostic=_stats(args, run_id, profile=man_kw.pop('profile', False)), **man_kw)
    if maybe_skip(args, run_id, **verify_kw):
        return os.path.join(args.out, 'runs', run_id)
    return run_bench(args, run_id, man, test=test, series=series)


def mean_sample_bytes(run_dir):
    run = RD.load_run(run_dir)
    vals = []
    for k, res in run['metrics'].get('results', {}).items():
        try:
            row = RD.frame_row(run, k, res, require_bitstream=True)
        except RD.RdError:
            continue
        if row.get('sample_bytes') is not None:
            vals.append(row['sample_bytes'])
    return sum(vals) / len(vals) if vals else None


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


def phase_i(args):
    """Comparación a igual tasa (±2 % sample bytes) y controles pareados sin RDOQ."""
    # Control ~3:1: q18 α0.75 DZ0.5 sin RDOQ (mismo precomp/α/DZ que el brazo RDOQ).
    ctrl3 = _do(args, 'exp003b-i-ctrl-q18-dz50', 'i-ctrl-q18-dz50.json',
                dict(q=18, alpha=0.75, deadzone=0.5, rdoq=False),
                dict(q=18, alpha=0.75, deadzone=0.5, notes='EXP-003b I ctrl ~3:1',
                     noise_synthesis=False))
    # RDOQ alrededor de 3:1 (λ baja @ q18) — ya existe h-q18-l0..; añadir λ=0.3 si falta.
    rdoq3 = _do(args, 'exp003b-i-rdoq-q18-l0', 'i-rdoq-q18-l0.json',
                dict(q=18, alpha=0.75, deadzone=0.5, rdoq=True, rdoq_lambda=0.0),
                dict(q=18, alpha=0.75, deadzone=0.5, rdoq=True, rdoq_lambda=0.0,
                     notes='EXP-003b I RDOQ ~3:1', noise_synthesis=False))
    # Control 5:1 = G1 (reusar).
    ctrl5 = _do(args, 'exp003b-g1-a75-dz50', 'g1-a75-dz50.json',
                dict(q=24, alpha=0.75, deadzone=0.5, rdoq=False),
                dict(q=24, alpha=0.75, deadzone=0.5, notes='EXP-003b G1', noise_synthesis=False))
    # RDOQ intentando igualar sample de G1: λ altas (también en J).
    target = mean_sample_bytes(ctrl5)
    print('I target G1 sample_bytes', target)
    best = None
    for lam in (130, 160, 200, 250, 300):
        run_id = 'exp003b-i-rdoq-q24-l%s' % (('%g' % lam).replace('.', 'p'))
        path = _do(args, run_id, run_id.replace('exp003b-', '') + '.json',
                   dict(q=24, alpha=0.75, deadzone=0.5, rdoq=True, rdoq_lambda=lam),
                   dict(q=24, alpha=0.75, deadzone=0.5, rdoq=True, rdoq_lambda=lam,
                        notes='EXP-003b I match G1 λ=%g' % lam, noise_synthesis=False))
        sb = mean_sample_bytes(path)
        if sb is None or target is None:
            continue
        pct = abs(sb - target) / target
        print('I λ=%g sample=%.0f delta%%=%.2f' % (lam, sb, 100 * pct))
        if pct <= 0.02 and (best is None or pct < best[0]):
            best = (pct, run_id, sb)
        # Parar si bytes caen claramente bajo el objetivo con degradación (λ ya demasiado alta).
        if sb < target * 0.95:
            print('I stop: sample claramente bajo G1')
            break
    # Anclar también h-q24-l100 como candidato 5:1 cercano.
    out = {
        'ctrl_3to1': os.path.basename(ctrl3),
        'rdoq_3to1': os.path.basename(rdoq3),
        'ctrl_5to1': os.path.basename(ctrl5),
        'best_match_5to1': best[1] if best else None,
        'tol_pct': 2.0,
        'target_5to1_sample': target,
    }
    path = os.path.join(MAN_DIR, 'phase-i-equal-rate.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(out, f, indent=2)
        f.write('\n')
    print('wrote', path, out)
    return out


def phase_j(args):
    """λ>100 acotado + rate search 3/5/6/8/12 (sin cambiar QMax ni el buscador)."""
    # Hasta 5 λ adicionales >100 (si I ya corrió 130..300, skip-existing las reutiliza).
    for lam in (130, 160, 200, 250, 300):
        run_id = 'exp003b-j-q24-l%s' % (('%g' % lam).replace('.', 'p'))
        # Alias: reutilizar runs de I si existen con mismo verify.
        alias = 'exp003b-i-rdoq-q24-l%s' % (('%g' % lam).replace('.', 'p'))
        if args.skip_existing and os.path.isfile(os.path.join(args.out, 'runs', alias, 'metrics.json')):
            print('j reuse', alias)
            continue
        _do(args, run_id, run_id.replace('exp003b-', '') + '.json',
            dict(q=24, alpha=0.75, deadzone=0.5, rdoq=True, rdoq_lambda=lam),
            dict(q=24, alpha=0.75, deadzone=0.5, rdoq=True, rdoq_lambda=lam,
                 notes='EXP-003b J λ=%g' % lam, noise_synthesis=False))
    # Rate search: control DZ uniforme y RDOQ λ=100 representativo.
    for ratio in (3, 5, 6, 8, 12):
        for tag, rdoq, lam in (
            ('dz50', False, None),
            ('rdoq-l100', True, 100.0),
        ):
            run_id = 'exp003b-j-r%02d-%s' % (ratio, tag)
            kw = dict(ratio=float(ratio), alpha=0.75, deadzone=0.5, notes='EXP-003b J r=%d %s' % (ratio, tag),
                      noise_synthesis=False, rdoq=rdoq, rdoq_lambda=lam)
            vkw = dict(ratio=float(ratio), alpha=0.75, deadzone=0.5, rdoq=rdoq)
            if rdoq:
                vkw['rdoq_lambda'] = lam
            t0 = time.time()
            path = _do(args, run_id, run_id.replace('exp003b-', '') + '.json', vkw, kw)
            wall = time.time() - t0
            sb = mean_sample_bytes(path)
            print('J rate-search', run_id, 'sample', sb, 'wall', round(wall, 2))
    return True


def phase_k(args):
    """Serie A: controles + mejores RDOQ 3:1 / 5:1."""
    plane = os.path.join(POLICIES, 'plane-y40-c50.json')
    configs = [
        ('k-g0', dict(q=24, alpha=1.0, deadzone=0.0), dict(q=24, alpha=1.0, deadzone=0.0, rdoq=False)),
        ('k-g1', dict(q=24, alpha=0.75, deadzone=0.5), dict(q=24, alpha=0.75, deadzone=0.5, rdoq=False)),
        ('k-g2', dict(q=24, alpha=0.75, policy=plane), dict(q=24, alpha=0.75, policy=plane, rdoq=False)),
        ('k-g3', dict(q=2, alpha=1.0, deadzone=0.0), dict(q=2, alpha=1.0, deadzone=0.0, rdoq=False)),
        # Mejor ~3:1 RDOQ: q18 λ=0
        ('k-rdoq-3to1', dict(q=18, alpha=0.75, deadzone=0.5, rdoq=True, rdoq_lambda=0.0),
         dict(q=18, alpha=0.75, deadzone=0.5, rdoq=True, rdoq_lambda=0.0)),
        # Mejor ~5:1 RDOQ: q24 λ=100 (o el match de I si existe)
        ('k-rdoq-5to1', dict(q=24, alpha=0.75, deadzone=0.5, rdoq=True, rdoq_lambda=100.0),
         dict(q=24, alpha=0.75, deadzone=0.5, rdoq=True, rdoq_lambda=100.0)),
        # Compresión agresiva representativa
        ('k-rdoq-hi', dict(q=24, alpha=0.75, deadzone=0.5, rdoq=True, rdoq_lambda=250.0),
         dict(q=24, alpha=0.75, deadzone=0.5, rdoq=True, rdoq_lambda=250.0)),
    ]
    # Preferir match I si quedó dentro de ±2 %.
    i_path = os.path.join(MAN_DIR, 'phase-i-equal-rate.json')
    if os.path.isfile(i_path):
        doc = json.load(open(i_path, encoding='utf-8'))
        if doc.get('best_match_5to1'):
            # Extraer λ del run id …-lNNN
            rid = doc['best_match_5to1']
            try:
                lam = float(rid.split('-l')[-1].replace('p', '.'))
                configs[5] = (
                    'k-rdoq-5to1',
                    dict(q=24, alpha=0.75, deadzone=0.5, rdoq=True, rdoq_lambda=lam),
                    dict(q=24, alpha=0.75, deadzone=0.5, rdoq=True, rdoq_lambda=lam),
                )
                print('K using matched 5:1 λ', lam)
            except ValueError:
                pass
    runs = []
    for tag, man_kw, vkw in configs:
        run_id = 'exp003b-%s' % tag
        man_kw = dict(man_kw, notes='EXP-003b K ' + tag, noise_synthesis=False)
        runs.append(_do(args, run_id, '%s.json' % tag, vkw, man_kw, test=None, series='A'))
    return runs


def phase_l(args):
    """Maximum Fidelity: q fino ± RDOQ vs G3."""
    g3 = _do(args, 'exp003b-g3-maxfid-q2', 'g3-maxfid-q2.json',
             dict(q=2, alpha=1.0, deadzone=0.0, rdoq=False),
             dict(q=2, alpha=1.0, deadzone=0.0, notes='G3', noise_synthesis=False))
    # q0 y q2 con RDOQ λ=0 (max D en candidatos) y λ pequeña.
    for q in (0, 2):
        for lam in (0.0, 0.1, 1.0):
            run_id = 'exp003b-l-q%d-l%s' % (q, ('%g' % lam).replace('.', 'p'))
            _do(args, run_id, run_id.replace('exp003b-', '') + '.json',
                dict(q=q, alpha=1.0, deadzone=0.0, rdoq=True, rdoq_lambda=lam),
                dict(q=q, alpha=1.0, deadzone=0.0, rdoq=True, rdoq_lambda=lam,
                     notes='EXP-003b L maxfid', noise_synthesis=False))
    # Control q0 sin RDOQ.
    _do(args, 'exp003b-l-ctrl-q0', 'l-ctrl-q0.json',
        dict(q=0, alpha=1.0, deadzone=0.0, rdoq=False),
        dict(q=0, alpha=1.0, deadzone=0.0, notes='EXP-003b L ctrl q0', noise_synthesis=False))
    return g3


def phase_m(args):
    """Hojas de contacto a ciegas (reutiliza exp002_blind_sheet)."""
    dest_root = os.path.join(ROOT, 'out', 'benchmark', 'experiments', 'exp003b', 'blind-sheets')
    key_root = os.path.join(MAN_DIR, 'blind-keys')
    os.makedirs(dest_root, exist_ok=True)
    os.makedirs(key_root, exist_ok=True)
    runs = [
        'exp003b-g0-legacy', 'exp003b-g1-a75-dz50', 'exp003b-g2-plane-y40-c50',
        'exp003b-g3-maxfid-q2', 'exp003b-i-ctrl-q18-dz50', 'exp003b-i-rdoq-q18-l0',
        'exp003b-h-q24-l100',
    ]
    # Añadir mejor match 5:1 si existe.
    i_path = os.path.join(MAN_DIR, 'phase-i-equal-rate.json')
    if os.path.isfile(i_path):
        bm = json.load(open(i_path, encoding='utf-8')).get('best_match_5to1')
        if bm:
            runs.append(bm)
    run_dirs = [os.path.join(args.out, 'runs', r) for r in runs
                if os.path.isfile(os.path.join(args.out, 'runs', r, 'metrics.json'))]
    for png_key, sub in (
        ('zoom_card_black', 'zoom_card'),
        ('zoom_cloth_black_mesh', 'cloth'),
        ('candidate', 'candidate'),
    ):
        dest = os.path.join(dest_root, sub)
        cmd = [PYTHON, os.path.join(ROOT, 'tools', 'exp002_blind_sheet.py'),
               '--dest', dest, '--png-key', png_key, '--seed', '303',
               '--runs', *run_dirs]
        print('+', ' '.join(cmd), flush=True)
        r = subprocess.run(cmd, cwd=ROOT)
        if r.returncode:
            print('WARN blind sheet', sub, 'rc', r.returncode, file=sys.stderr)
            continue
        # Mover clave fuera de la hoja pública.
        key_src = os.path.join(dest, 'key.json')
        if os.path.isfile(key_src):
            shutil_dst = os.path.join(key_root, sub + '_key.json')
            os.replace(key_src, shutil_dst)
            print('key ->', shutil_dst)
    return dest_root


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dataset', default=os.path.expanduser('~/Desktop/BMD'))
    ap.add_argument('--references', default=os.path.expanduser('~/Desktop/EXR'))
    ap.add_argument('--out', default=os.path.join(ROOT, 'out', 'benchmark'))
    ap.add_argument('--converter', default=os.path.join(ROOT, 'build', 'worker', 'converter'))
    ap.add_argument('--brawdec', default=os.path.join(ROOT, 'tools', 'brawdec', 'brawdec'))
    ap.add_argument('--phase', choices=['g', 'h', 'h2', 'i', 'j', 'k', 'l', 'm', 'final', 'all'],
                    default='all')
    ap.add_argument('--skip-existing', action='store_true', default=True)
    ap.add_argument('--no-skip-existing', action='store_false', dest='skip_existing')
    ap.add_argument('--no-cache', action='store_true')
    ap.add_argument('--lambdas', default='', help='CSV override del grid λ')
    ap.add_argument('--workers', type=int, default=None)
    ap.add_argument('--perf-lambda', type=float, default=1.0)
    args = ap.parse_args()
    os.makedirs(MAN_DIR, exist_ok=True)
    # El gate de detalle se evalúa en el proceso del banco (no solo en el worker).
    os.environ['CDNG_ALIGN_SOFT'] = '1'
    if args.phase in ('g', 'all'):
        phase_g(args)
    if args.phase in ('h', 'all'):
        phase_h(args)
    if args.phase in ('h2', 'all'):
        phase_h2(args)
    if args.phase in ('i', 'final'):
        phase_i(args)
    if args.phase in ('j', 'final'):
        phase_j(args)
    if args.phase in ('k', 'final'):
        phase_k(args)
    if args.phase in ('l', 'final'):
        phase_l(args)
    if args.phase in ('m', 'final'):
        phase_m(args)


if __name__ == '__main__':
    main()
