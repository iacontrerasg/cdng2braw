#!/usr/bin/env python3
"""Orquesta fases EXP-002a (techo de compresión). No altera defaults del encoder."""
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


def write_manifest(path, *, q=None, ratio=None, alpha=1.0, deadzone=None, ablation=False,
                   noise_synthesis=False, nr_luma=0, nr_chroma=0, notes='', diagnostic=None,
                   workers=None):
    env = {'CDNG_OG_DEBLOCK_PRECOMP': '0' if ablation else '1'}
    if alpha is not None and not ablation and abs(float(alpha) - 1.0) > 1e-12:
        env['CDNG_OG_DEBLOCK_ALPHA'] = '%g' % float(alpha)
    if deadzone is not None:
        env['CDNG_DEADZONE'] = '%g' % float(deadzone)
    # Con instrumentación: 1 worker (JSONL append + thread_local budget limpios).
    w = workers if workers is not None else (1 if diagnostic else 2)
    enc = {
        'compression': 'quality' if q is not None else 'ratio',
        'q': q, 'ratio': ratio,
        'rate_search': bool(ratio is not None),
        'noise_synthesis': bool(noise_synthesis),
        'nr_luma': nr_luma, 'nr_chroma': nr_chroma, 'workers': w,
        'experimental_env': env,
        'notes': [notes or 'EXP-002a'],
    }
    if q is not None:
        enc['ratio'] = None
        enc['rate_search'] = False
    else:
        enc['q'] = None
    doc = {'base': '../../manifest.json', 'encode': enc}
    if diagnostic:
        doc['encode']['diagnostic_env'] = diagnostic
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


def phase_c_a04(args):
    """A04 q∈{0,4,9,18,24} α=1 instrumentado."""
    qs = [0, 4, 9, 18, 24]
    runs = []
    for q in qs:
        run_id = 'exp002a-c-a04-a1-q%02d' % q
        man = os.path.join(ROOT, 'benchmark', 'experiments', 'exp002a', 'c-a1-q%02d.json' % q)
        stats = os.path.abspath(os.path.join(args.out, 'experiments', 'exp002a', 'stats', run_id))
        os.makedirs(stats, exist_ok=True)
        write_manifest(man, q=q, alpha=1.0, notes='EXP-002a Fase C A04 q=%d' % q,
                       diagnostic={'CDNG_BIT_BUDGET': '1', 'CDNG_COEFF_STATS': '1',
                                   'CDNG_STATS_DIR': stats})
        if args.dry_run:
            print('dry', run_id); continue
        skipped = maybe_skip(args, run_id, q=q, alpha=1.0)
        if skipped:
            runs.append(skipped); continue
        runs.append(run_bench(args, run_id, man))
    return runs


def phase_e_deadzone(args):
    zones = [0.0, 0.10, 0.20, 0.25, 0.30, 0.40, 0.50]
    qs = [18, 24]
    runs = []
    for z in zones:
        for q in qs:
            tag = ('dz%02d' % int(round(z * 100))) if z else 'dz00'
            run_id = 'exp002a-e-a04-%s-q%02d' % (tag, q)
            man = os.path.join(ROOT, 'benchmark', 'experiments', 'exp002a',
                               'e-%s-q%02d.json' % (tag, q))
            stats = os.path.abspath(os.path.join(args.out, 'experiments', 'exp002a', 'stats', run_id))
            os.makedirs(stats, exist_ok=True)
            write_manifest(man, q=q, alpha=1.0, deadzone=z,
                           notes='EXP-002a Fase E deadzone=%g q=%d' % (z, q),
                           diagnostic={'CDNG_BIT_BUDGET': '1', 'CDNG_COEFF_STATS': '1',
                                       'CDNG_STATS_DIR': stats})
            if args.dry_run:
                print('dry', run_id); continue
            skipped = maybe_skip(args, run_id, q=q, alpha=1.0)
            if skipped:
                run = RD.load_run(skipped)
                key = sorted(run['metrics']['results'])[0]
                env = run['metrics']['results'][key]['setup']['encode']['experimental_env']
                got = env.get('CDNG_DEADZONE')
                if z == 0:
                    if got not in (None, '0', '0.0', '0.00'):
                        print('ERROR deadzone mismatch', run_id, got, file=sys.stderr)
                        raise SystemExit(2)
                elif got is None or abs(float(got) - z) > 1e-9:
                    print('ERROR deadzone mismatch', run_id, got, '≠', z, file=sys.stderr)
                    raise SystemExit(2)
                runs.append(skipped); continue
            runs.append(run_bench(args, run_id, man))
    return runs


def phase_f_alpha_dz(args):
    """α ∈ {1, 0.75, 0.5} × DZ prometedores × q∈{18,24}."""
    zones = getattr(args, 'dz_list', None) or [0.30, 0.40, 0.50]
    alphas = [1.0, 0.75, 0.50]
    qs = [18, 24]
    runs = []
    for z in zones:
        for a in alphas:
            for q in qs:
                tag = 'dz%02d' % int(round(z * 100))
                run_id = 'exp002a-f-a04-a%02d-%s-q%02d' % (int(round(a * 100)), tag, q)
                man = os.path.join(ROOT, 'benchmark', 'experiments', 'exp002a',
                                   'f-a%02d-%s-q%02d.json' % (int(round(a * 100)), tag, q))
                stats = os.path.abspath(os.path.join(args.out, 'experiments', 'exp002a', 'stats', run_id))
                os.makedirs(stats, exist_ok=True)
                write_manifest(man, q=q, alpha=a, deadzone=z,
                               notes='EXP-002a Fase F α=%g DZ=%g q=%d' % (a, z, q),
                               diagnostic={'CDNG_BIT_BUDGET': '1', 'CDNG_COEFF_STATS': '1',
                                           'CDNG_STATS_DIR': stats})
                if args.dry_run:
                    print('dry', run_id); continue
                skipped = maybe_skip(args, run_id, q=q, alpha=a)
                if skipped:
                    runs.append(skipped); continue
                runs.append(run_bench(args, run_id, man))
    return runs


def phase_g_rate(args):
    """Rate search 3/5/8/12:1 sobre candidatos DZ×α."""
    ratios = [3, 5, 8, 12]
    # Candidatos: DZ=0.5 α=1 y α=0.5 (techo observado); baseline DZ=0 α=1.
    cfgs = [
        (1.0, 0.0, 'dz00-a100'),
        (1.0, 0.5, 'dz50-a100'),
        (0.5, 0.5, 'dz50-a050'),
    ]
    runs = []
    for a, z, tag in cfgs:
        for ratio in ratios:
            run_id = 'exp002a-g-a04-%s-r%02d' % (tag, ratio)
            man = os.path.join(ROOT, 'benchmark', 'experiments', 'exp002a',
                               'g-%s-r%02d.json' % (tag, ratio))
            write_manifest(man, ratio=ratio, alpha=a, deadzone=z if z else None,
                           notes='EXP-002a Fase G ratio=%d:1 α=%g DZ=%g' % (ratio, a, z),
                           workers=2)
            if args.dry_run:
                print('dry', run_id); continue
            skipped = maybe_skip(args, run_id, ratio=ratio, alpha=a)
            if skipped:
                runs.append(skipped); continue
            runs.append(run_bench(args, run_id, man))
    return runs


def phase_h_nr(args):
    """NR / noise synthesis — A04 q=24 α=1 DZ=0 (intensidad 1 histórica)."""
    cases = [
        (0, 0, False, 'nr0-syn0'),
        (1, 1, False, 'nr1-syn0'),
        (1, 1, True, 'nr1-syn1'),
        (0, 0, True, 'nr0-syn1'),
    ]
    runs = []
    for nl, nc, syn, tag in cases:
        run_id = 'exp002a-h-a04-q24-%s' % tag
        man = os.path.join(ROOT, 'benchmark', 'experiments', 'exp002a', 'h-q24-%s.json' % tag)
        write_manifest(man, q=24, alpha=1.0, deadzone=0.0,
                       noise_synthesis=syn, nr_luma=nl, nr_chroma=nc,
                       notes='EXP-002a Fase H %s' % tag, workers=2)
        if args.dry_run:
            print('dry', run_id); continue
        skipped = maybe_skip(args, run_id, q=24, alpha=1.0)
        if skipped:
            runs.append(skipped); continue
        runs.append(run_bench(args, run_id, man))
    return runs


def phase_align_diag(args):
    """Re-evalúa α=0.5 DZ=0.5 q24 con CDNG_ALIGN_SOFT (no baja el umbral)."""
    os.environ['CDNG_ALIGN_SOFT'] = '1'
    run_id = 'exp002a-align-a50-dz50-q24'
    man = os.path.join(ROOT, 'benchmark', 'experiments', 'exp002a', 'align-a50-dz50-q24.json')
    stats = os.path.abspath(os.path.join(args.out, 'experiments', 'exp002a', 'stats', run_id))
    os.makedirs(stats, exist_ok=True)
    write_manifest(man, q=24, alpha=0.5, deadzone=0.5,
                   notes='EXP-002a align soft α=0.5 DZ=0.5 q24',
                   diagnostic={'CDNG_BIT_BUDGET': '1', 'CDNG_COEFF_STATS': '1',
                               'CDNG_STATS_DIR': stats, 'CDNG_ALIGN_SOFT': '1'})
    if args.dry_run:
        print('dry', run_id); return []
    # No skip-existing: queremos métricas con gate_failed
    dest = os.path.join(args.out, 'runs', run_id)
    if os.path.isdir(dest):
        import shutil
        shutil.rmtree(dest)
    return [run_bench(args, run_id, man)]


def phase_nr_intensity(args):
    """NR-B: intensidades 0,1,10,25,50,75,100 — tres ejes sin duplicar nr=0."""
    intensities = [0, 1, 10, 25, 50, 75, 100]
    runs = []
    # C: Y=C
    for v in intensities:
        run_id = 'exp002a-nr-yc%03d-q24' % v
        man = os.path.join(ROOT, 'benchmark', 'experiments', 'exp002a', 'nr-yc%03d-q24.json' % v)
        stats = os.path.abspath(os.path.join(args.out, 'experiments', 'exp002a', 'stats', run_id))
        os.makedirs(stats, exist_ok=True)
        write_manifest(man, q=24, alpha=1.0, deadzone=0.0, nr_luma=v, nr_chroma=v,
                       noise_synthesis=False,
                       notes='EXP-002a NR-B Y=C=%d' % v,
                       diagnostic={'CDNG_BIT_BUDGET': '1', 'CDNG_COEFF_STATS': '1',
                                   'CDNG_STATS_DIR': stats})
        if args.dry_run:
            print('dry', run_id); continue
        skipped = maybe_skip(args, run_id, q=24, alpha=1.0)
        if skipped:
            runs.append(skipped); continue
        runs.append(run_bench(args, run_id, man))
    # A: Y var, C=0 (omitir 0 = ya en yc000)
    for v in intensities:
        if v == 0:
            continue
        run_id = 'exp002a-nr-y%03d-c000-q24' % v
        man = os.path.join(ROOT, 'benchmark', 'experiments', 'exp002a', 'nr-y%03d-c000-q24.json' % v)
        stats = os.path.abspath(os.path.join(args.out, 'experiments', 'exp002a', 'stats', run_id))
        os.makedirs(stats, exist_ok=True)
        write_manifest(man, q=24, alpha=1.0, deadzone=0.0, nr_luma=v, nr_chroma=0,
                       noise_synthesis=False,
                       notes='EXP-002a NR-B Y=%d C=0' % v,
                       diagnostic={'CDNG_BIT_BUDGET': '1', 'CDNG_COEFF_STATS': '1',
                                   'CDNG_STATS_DIR': stats})
        if args.dry_run:
            print('dry', run_id); continue
        skipped = maybe_skip(args, run_id, q=24, alpha=1.0)
        if skipped:
            runs.append(skipped); continue
        runs.append(run_bench(args, run_id, man))
    # B: C var, Y=0
    for v in intensities:
        if v == 0:
            continue
        run_id = 'exp002a-nr-y000-c%03d-q24' % v
        man = os.path.join(ROOT, 'benchmark', 'experiments', 'exp002a', 'nr-y000-c%03d-q24.json' % v)
        stats = os.path.abspath(os.path.join(args.out, 'experiments', 'exp002a', 'stats', run_id))
        os.makedirs(stats, exist_ok=True)
        write_manifest(man, q=24, alpha=1.0, deadzone=0.0, nr_luma=0, nr_chroma=v,
                       noise_synthesis=False,
                       notes='EXP-002a NR-B Y=0 C=%d' % v,
                       diagnostic={'CDNG_BIT_BUDGET': '1', 'CDNG_COEFF_STATS': '1',
                                   'CDNG_STATS_DIR': stats})
        if args.dry_run:
            print('dry', run_id); continue
        skipped = maybe_skip(args, run_id, q=24, alpha=1.0)
        if skipped:
            runs.append(skipped); continue
        runs.append(run_bench(args, run_id, man))
    return runs


def phase_nr_synth(args):
    """NR-C: synth ON/OFF en intensidades representativas."""
    levels = getattr(args, 'nr_levels', None) or [25, 50, 100]
    runs = []
    for v in levels:
        for syn, tag in ((False, 'syn0'), (True, 'syn1')):
            run_id = 'exp002a-nr-yc%03d-q24-%s' % (v, tag)
            if not syn and v in (0, 1, 10, 25, 50, 75, 100):
                # syn0 ya cubierto por nr-yc*; reutilizar si existe
                alt = 'exp002a-nr-yc%03d-q24' % v
                alt_dir = os.path.join(args.out, 'runs', alt)
                if os.path.isfile(os.path.join(alt_dir, 'metrics.json')):
                    print('reuse', alt, 'as', run_id)
                    runs.append(alt_dir)
                    continue
            man = os.path.join(ROOT, 'benchmark', 'experiments', 'exp002a',
                               'nr-yc%03d-q24-%s.json' % (v, tag))
            write_manifest(man, q=24, alpha=1.0, deadzone=0.0, nr_luma=v, nr_chroma=v,
                           noise_synthesis=syn,
                           notes='EXP-002a NR-C Y=C=%d synth=%s' % (v, syn), workers=2)
            if args.dry_run:
                print('dry', run_id); continue
            skipped = maybe_skip(args, run_id, q=24, alpha=1.0)
            if skipped:
                runs.append(skipped); continue
            runs.append(run_bench(args, run_id, man))
    return runs


def phase_nr_dz(args):
    """NR-D: matriz reducida α×DZ×NR @ q24."""
    # Soft align en el proceso del banco (no baja el umbral; evita abortar al combinar NR+DZ).
    os.environ['CDNG_ALIGN_SOFT'] = '1'
    alphas = [1.0, 0.75]
    zones = [0.0, 0.25, 0.5]
    nrs = [0, 25, 50]
    runs = []
    for a in alphas:
        for z in zones:
            for nr in nrs:
                tag = 'a%02d-dz%02d-nr%03d' % (int(round(a * 100)), int(round(z * 100)), nr)
                run_id = 'exp002a-nrd-a04-%s-q24' % tag
                man = os.path.join(ROOT, 'benchmark', 'experiments', 'exp002a',
                                   'nrd-%s-q24.json' % tag)
                stats = os.path.abspath(os.path.join(args.out, 'experiments', 'exp002a', 'stats', run_id))
                os.makedirs(stats, exist_ok=True)
                write_manifest(man, q=24, alpha=a, deadzone=z if z else 0.0,
                               nr_luma=nr, nr_chroma=nr, noise_synthesis=False,
                               notes='EXP-002a NR-D %s' % tag,
                               diagnostic={'CDNG_BIT_BUDGET': '1', 'CDNG_COEFF_STATS': '1',
                                           'CDNG_STATS_DIR': stats, 'CDNG_ALIGN_SOFT': '1'})
                if args.dry_run:
                    print('dry', run_id); continue
                dest = os.path.join(args.out, 'runs', run_id)
                metrics = os.path.join(dest, 'metrics.json')
                if os.path.isfile(metrics):
                    print('skip', run_id); runs.append(dest); continue
                if os.path.isdir(dest):
                    import shutil
                    shutil.rmtree(dest)
                runs.append(run_bench(args, run_id, man))
    return runs


def main():
    ap = argparse.ArgumentParser(description='EXP-002a sweep')
    ap.add_argument('--phase', required=True,
                    choices=['c-a04', 'e-deadzone', 'f-alpha', 'g-rate', 'h-nr',
                             'align-diag', 'nr-b', 'nr-c', 'nr-d',
                             'all-ce', 'all-nr', 'all'])
    ap.add_argument('--dataset', default=os.path.expanduser('~/Desktop/BMD'))
    ap.add_argument('--references', default=os.path.expanduser('~/Desktop/EXR'))
    ap.add_argument('--out', default=os.path.join(ROOT, 'out', 'benchmark'))
    ap.add_argument('--converter', default=os.path.join(ROOT, 'build', 'worker', 'converter'))
    ap.add_argument('--brawdec', default=os.path.join(ROOT, 'tools', 'brawdec', 'brawdec'))
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--skip-existing', action='store_true')
    ap.add_argument('--no-cache', action='store_true')
    args = ap.parse_args()
    phases = {
        'c-a04': [phase_c_a04],
        'e-deadzone': [phase_e_deadzone],
        'f-alpha': [phase_f_alpha_dz],
        'g-rate': [phase_g_rate],
        'h-nr': [phase_h_nr],
        'align-diag': [phase_align_diag],
        'nr-b': [phase_nr_intensity],
        'nr-c': [phase_nr_synth],
        'nr-d': [phase_nr_dz],
        'all-ce': [phase_c_a04, phase_e_deadzone],
        'all-nr': [phase_nr_intensity, phase_nr_synth, phase_nr_dz],
        'all': [phase_c_a04, phase_e_deadzone, phase_f_alpha_dz, phase_g_rate, phase_h_nr,
                phase_align_diag, phase_nr_intensity, phase_nr_synth, phase_nr_dz],
    }
    for fn in phases[args.phase]:
        fn(args)


if __name__ == '__main__':
    main()
