"""python -m tools.fidelity {validate,run,freeze,compare,equivalence} — ver docs/image-fidelity-benchmark.md."""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def csv_list(text):
    return [s.strip() for s in text.split(',') if s.strip()] if text else None


def frame_list(text):
    """'52,53' o '40-44' o combinaciones."""
    if not text:
        return None
    out = []
    for part in text.split(','):
        if '-' in part:
            a, b = part.split('-')
            out += list(range(int(a), int(b) + 1))
        elif part.strip():
            out.append(int(part))
    return out


def common(p):
    p.add_argument('--manifest', default=os.path.join(ROOT, 'benchmark', 'manifest.json'))
    p.add_argument('--dataset', help='raíz del dataset (por defecto la del manifiesto)')
    p.add_argument('--references', help='carpeta de los EXR de Resolve (por defecto references_root del manifiesto)')
    p.add_argument('--out', default=os.path.join(ROOT, 'out', 'benchmark'))
    p.add_argument('--run', help='nombre del directorio de la ejecución (por defecto la fecha y hora)')


def tools(p):
    p.add_argument('--converter', default=os.path.join(ROOT, 'build', 'worker', 'converter'), help='worker empaquetado (make worker)')
    p.add_argument('--brawdec', default=os.path.join(ROOT, 'tools', 'brawdec', 'brawdec'))
    p.add_argument('--framework', help='BlackmagicRawAPI (por defecto decode.framework del manifiesto)')
    p.add_argument('--no-cache', action='store_true', help='convertir y revelar de nuevo aunque haya caché verificada')


def main(argv=None):
    ap = argparse.ArgumentParser(prog='python -m tools.fidelity', description='Banco de fidelidad de imagen CinemaDNG → BRAW')
    sub = ap.add_subparsers(dest='cmd')
    v = sub.add_parser('validate', help='valida el manifiesto, las ROI y las dependencias contra el dataset')
    common(v)
    v.add_argument('--all', action='store_true', help='incluir las pruebas pendientes')
    r = sub.add_parser('run', help='convierte, revela, mide e informa')
    common(r); tools(r)
    r.add_argument('--test', type=csv_list, help='ids separados por comas (p. ej. A01,A04)')
    r.add_argument('--series', type=csv_list, help='series separadas por comas (p. ej. A)')
    r.add_argument('--frames', type=frame_list, help='fotogramas (p. ej. 52,53 o 40-44); sustituye a los del manifiesto')
    r.add_argument('--q', type=int, help='modo calidad con este q')
    r.add_argument('--ratio', type=float, help='modo ratio con esta relación (3–12)')
    r.add_argument('--baseline', help='nombre o directorio de un baseline congelado: añade regresión y comparación')
    r.add_argument('--no-reference', action='store_true', help='no comparar con los EXR (solo regresión o dark frames)')
    r.add_argument('--thresholds', default=os.path.join(ROOT, 'benchmark', 'thresholds.json'))
    f = sub.add_parser('freeze', help='congela una ejecución como baseline')
    f.add_argument('run_dir'); f.add_argument('name')
    f.add_argument('--out', default=os.path.join(ROOT, 'out', 'benchmark'))
    f.add_argument('--force', action='store_true')
    c = sub.add_parser('compare', help='compara dos ejecuciones o baselines ya hechos')
    c.add_argument('base'); c.add_argument('candidate')
    c.add_argument('--out', default=os.path.join(ROOT, 'out', 'benchmark'))
    c.add_argument('--thresholds', default=os.path.join(ROOT, 'benchmark', 'thresholds.json'))
    c.add_argument('--dest', help='directorio del informe (por defecto out/benchmark/comparisons/BASE__CAND)')
    c.add_argument('--allow-setup-differences', action='store_true', help='comparar aunque cambie la configuración (p. ej. q)')
    e = sub.add_parser('equivalence', help='brawdec frente al render EXR de Resolve del mismo BRAW')
    common(e); tools(e)
    rd = sub.add_parser('rd', help='EXP-002: agrega corridas rate–distortion y genera curvas HTML/CSV')
    rd.add_argument('--runs-glob', required=True, help="glob de directorios de run (p. ej. 'out/benchmark/runs/exp002-a04-*')")
    rd.add_argument('--dest', required=True, help='directorio del informe')
    rd.add_argument('--expected-q-grid', action='store_true',
                    help='exige cobertura q=0..24 × α∈{1,0.5,0.75} (barrido A04)')
    rd.add_argument('--expected-ratios', help='lista de ratios esperados p.ej. 3,5,8,12')
    args = ap.parse_args(argv)
    if not args.cmd:
        ap.print_help()
        return 2
    try:
        import numpy  # noqa: F401
    except ImportError:
        print('El banco necesita numpy y OpenEXR: make venv (o PYTHON=ruta/a/python con ambos).', file=sys.stderr)
        return 2
    from . import runner, manifest as MF, rois as R
    try:
        if args.cmd == 'validate':
            return validate(args, MF, R)
        if args.cmd == 'run':
            if args.q is not None and args.ratio is not None:
                ap.error('--q y --ratio son excluyentes')
            check_tools(args, needs_converter=True)
            print(runner.run(args))
        elif args.cmd == 'freeze':
            print(runner.freeze(args.run_dir, args.name, args.out, args.force))
        elif args.cmd == 'compare':
            dest = args.dest or os.path.join(args.out, 'comparisons', '%s__%s' % (os.path.basename(os.path.normpath(args.base)),
                                                                                 os.path.basename(os.path.normpath(args.candidate))))
            print(runner.compare_runs(args.base, args.candidate, args.thresholds, dest, args.out, args.allow_setup_differences))
        elif args.cmd == 'equivalence':
            check_tools(args, needs_converter=False)
            print(runner.equivalence(args))
        elif args.cmd == 'rd':
            import glob
            from . import rd as RD
            dirs = sorted(d for d in glob.glob(args.runs_glob) if os.path.isdir(d) and
                          os.path.isfile(os.path.join(d, 'metrics.json')))
            if not dirs:
                raise runner.RunError('ninguna corrida coincide con %s' % args.runs_glob)
            expected = {}
            if args.expected_q_grid:
                expected['expected_q'] = list(range(0, 25))
                expected['expected_alphas'] = [1.0, 0.5, 0.75]
            if args.expected_ratios:
                expected['expected_ratios'] = [float(x) for x in args.expected_ratios.split(',')]
                expected.setdefault('expected_alphas', [1.0, 0.5, 0.75])
            try:
                summary = RD.build_report(dirs, args.dest, expected=expected or None)
            except RD.RdError as e:
                raise runner.RunError(str(e)) from e
            cov = summary.get('coverage') or {}
            print('%s (%d runs, %d frames; missing=%d dupes=%d q0_fallback=%d)' % (
                args.dest, summary['n_runs'], summary['n_frames'],
                len(cov.get('missing') or []), len(cov.get('duplicates') or []),
                len(cov.get('q0_codec_range_fallbacks') or [])))
    except (runner.RunError, MF.ManifestError, R.RoiError) as err:
        print('error: %s' % err, file=sys.stderr)
        return 1
    return 0


def check_tools(args, needs_converter):
    missing = []
    if needs_converter and not os.path.isfile(os.path.join(args.converter, 'cdng-convert')):
        missing.append('worker en %s (make worker)' % args.converter)
    if not os.path.isfile(args.brawdec):
        missing.append('brawdec en %s (make brawdec)' % args.brawdec)
    try:
        import OpenEXR  # noqa: F401
    except ImportError:
        missing.append('paquete OpenEXR de Python (make venv)')
    if missing:
        raise SystemExit('faltan dependencias:\n  ' + '\n  '.join(missing))


def validate(args, MF, R):
    doc = MF.load(args.manifest)
    root, refs = MF.resolve_paths(doc, args.dataset, args.references)
    tests = MF.select(doc, include_pending=args.all)
    print('manifiesto: %s (%d pruebas, %d seleccionadas)' % (args.manifest, len(doc['tests']), len(tests)))
    print('dataset: %s' % root)
    print('referencias: %s' % refs)
    errors, warnings = MF.against_dataset(doc, root, refs, tests)
    for name, s in doc.get('scenes', {}).items():
        try:
            R.load(os.path.join(os.path.dirname(doc['_path']), s['rois']), (1920, 1080))
        except (OSError, ValueError) as err:
            errors.append('escena %s: %s' % (name, err))
    for w in warnings:
        print('aviso: ' + w)
    for e in errors:
        print('ERROR: ' + e)
    print('correcto' if not errors else '%d errores' % len(errors))
    return 1 if errors else 0


if __name__ == '__main__':
    sys.exit(main())
