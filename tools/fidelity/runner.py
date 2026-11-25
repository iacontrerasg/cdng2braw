"""Ejecución del banco: selección → identidad → conversión → revelado → métricas → visualizaciones → informe."""
import json
import os
import shutil
import sys
import time

import numpy as np

from . import SCHEMA, compare as C, identity, images, manifest as MF, metrics as M, pipeline as P, report, rois as R, viz

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(ROOT, 'out', 'benchmark')


class RunError(RuntimeError):
    pass


def result_key(test_id, frame):
    return '%s_f%03d' % (test_id, frame)


def decode_settings(doc, override=None):
    d = doc['decode']
    s = {'gamut': d['gamut'], 'gamma': d['gamma'], 'color_science_gen': d['color_science_gen'],
         'highlight_recovery': d['highlight_recovery'], 'scale': d.get('scale', 'full'),
         'iso': None, 'exposure': None, 'kelvin': None, 'tint': None}
    for k, v in (override or {}).items():
        s[k] = v
    return s


def exposure_of(settings):
    return float(settings.get('exposure') or 0.0)


def link_or_copy(src, dst):
    try:
        os.link(src, dst)
    except OSError:
        shutil.copyfile(src, dst)


def setup_block(doc, test, frame, inputs, rng, settings, reference, effective_env):
    """Lo que tiene que coincidir para que dos resultados sean comparables (todo salvo la compilación).

    inputs: identidad ordenada de todos los DNG del tramo convertido (no solo el fotograma evaluado).
    experimental_env: mapa completo OUTPUT_ENV → valor|null (identidad de caché).
    """
    req, effective, _, _ = P.encode_request(doc['encode'])
    if effective != effective_env:
        raise RunError('experimental_env inconsistente entre setup y conversión')
    evaluated = next((i for i in inputs if i['index'] == frame), None)
    return json.loads(json.dumps({
        'clip': test['clip'], 'frame': frame,
        'dng_sha256': evaluated['sha256'] if evaluated else None,
        'inputs': inputs, 'range': list(rng),
        'encode': {'request': req, 'experimental_env': effective_env},
        'decode': settings, 'iso': test['iso'], 'reference': reference,
    }))


def decode_override_for(refs, frame):
    """Ajustes de revelado del manifiesto para este fotograma, aunque no se cargue el EXR."""
    ref = refs.get(frame)
    return ref.get('decode') if ref else None


def run(args):
    doc = MF.load(args.manifest)
    thresholds = C.load_thresholds(args.thresholds)
    root, refs_root = MF.resolve_paths(doc, args.dataset, args.references)
    if args.q is not None:
        doc['encode'].update(compression='quality', q=int(args.q), ratio=None)
    if args.ratio is not None:
        doc['encode'].update(compression='ratio', ratio=float(args.ratio), q=None)
    tests = MF.select(doc, args.test, args.series)
    if not tests:
        raise RunError('la selección no contiene pruebas completas')
    frames_override = args.frames
    for t in tests:
        if t['status'] != 'complete' and not frames_override:
            raise RunError('%s está pendiente y no tiene fotogramas: indica --frames' % t['id'])
    # En --no-reference (regresión pura) no se exigen los EXR; los overrides de revelado sí se leen del manifiesto.
    errors, warnings = MF.against_dataset(doc, root, refs_root, tests, require_references=not args.no_reference)
    if errors:
        raise RunError('el manifiesto no coincide con el dataset:\n  ' + '\n  '.join(errors))

    run_id = args.run or time.strftime('%Y%m%d-%H%M%S')
    run_dir = os.path.join(args.out, 'runs', run_id)
    if os.path.exists(run_dir):
        raise RunError('ya existe %s' % run_dir)
    os.makedirs(os.path.join(run_dir, 'frames')); os.makedirs(os.path.join(run_dir, 'png')); os.makedirs(os.path.join(run_dir, 'logs'))
    log = P.Log(os.path.join(run_dir, 'logs', 'pipeline.log'))
    for w in warnings:
        log('aviso: ' + w)
    cache = os.path.join(args.out, 'cache')

    converter_dir = os.path.abspath(args.converter)
    framework = args.framework or doc['decode']['framework']
    camera_support = doc['decode']['camera_support']
    git = identity.git(ROOT)
    conv_id = identity.converter(converter_dir)
    dec_id = identity.decoder(args.brawdec, framework, camera_support)
    try:
        _, effective_env, _, diagnostic = P.encode_request(doc['encode'])
    except P.PipelineError as e:
        raise RunError(str(e)) from e
    log('compilación: commit %s%s, worker %s; brawdec %s; SDK %s' % (
        (git['commit'] or '?')[:12], ' (árbol modificado)' if git['dirty'] else '', conv_id['cdng_convert_sha256'][:12],
        dec_id['brawdec_sha256'][:12], dec_id['framework_sha256'][:12]))
    if git['dirty']:
        log('aviso: el árbol tiene cambios sin commit; la identidad del worker es su sha, no el commit')
    if diagnostic:
        log('diagnóstico activo (no entra en identidad de caché): %s' %
            ', '.join('%s=%s' % kv for kv in sorted(diagnostic.items())))

    analysis = doc['analysis']
    vis = doc['visualization']
    config = {'manifest_analysis': analysis, 'visualization': vis,
              'encode': {k: v for k, v in doc['encode'].items() if k != 'notes'},
              'experimental_env': effective_env,
              'decode': {k: v for k, v in doc['decode'].items() if k != 'notes'},
              'rois': {name: identity.sha256_file(path) for name, path in scene_paths(doc).items()}}
    config_sha = identity.sha256_json(config)
    baseline = load_baseline(args.baseline, args.out) if args.baseline else None
    if baseline and baseline['metrics']['config_sha256'] != config_sha:
        raise RunError('el baseline %s usó otra configuración (codificación, revelado, análisis o ROI); no es comparable' % args.baseline)
    if args.no_reference and not baseline and not any(t.get('category') == 'dark_frame' for t in tests):
        log('aviso: --no-reference sin --baseline: solo se generan imágenes y metadatos, sin métricas de fidelidad')
    scenes = {}
    results, timing, cache_use = {}, {'conversions': [], 'decodes': []}, []
    t_start = time.monotonic()
    for t in tests:
        clip = MF.clip_dir(root, t['clip'])
        files = MF.dng_files(clip)
        frames = frames_override or t['frames']
        refs = {r['frame']: r for r in t['references']}
        for rng in P.ranges(frames, len(files)):
            start, count, fs = rng
            conv = P.convert(converter_dir, conv_id, clip, files, start, count, doc['encode'], cache, log, args.no_cache)
            cache_use.append({'kind': 'braw', 'key': conv['key'], 'reused': conv['reused']})
            timing['conversions'].append({'test': t['id'], 'range': [start, count], 'seconds': conv['seconds'], 'reused': conv['reused']})
            shutil.copyfile(os.path.join(os.path.dirname(conv['path']), 'convert.log'),
                            os.path.join(run_dir, 'logs', 'convert_%s_%03d-%03d.log' % (t['id'], start, start + count - 1)))
            cache_dir = os.path.dirname(conv['path'])
            for name in ('clip.pipeline-trace.txt', 'pipeline-trace.identity.json'):
                src = os.path.join(cache_dir, name)
                if os.path.isfile(src):
                    dest = os.path.join(run_dir, 'logs', '%s_%03d-%03d_%s' % (t['id'], start, start + count - 1, name))
                    shutil.copyfile(src, dest)
            for frame in fs:
                key = result_key(t['id'], frame)
                ref_meta = refs.get(frame)
                settings = decode_settings(doc, decode_override_for(refs, frame))
                img, dec = P.decode(args.brawdec, framework, dec_id, conv['path'], conv['sha256'], frame - start, settings,
                                    cache, log, args.no_cache, expected_iso=t['iso'])
                cache_use.append({'kind': 'decode', 'key': dec['key'], 'reused': dec['reused']})
                timing['decodes'].append({'result': key, 'seconds': dec['seconds'], 'reused': dec['reused']})
                shutil.copyfile(os.path.join(os.path.dirname(dec['path']), 'decode.log'), os.path.join(run_dir, 'logs', 'decode_%s.log' % key))
                link_or_copy(dec['path'], os.path.join(run_dir, 'frames', key + '.f32'))
                reference = None
                ref_img = None
                if not args.no_reference and ref_meta:
                    ref_path = os.path.join(refs_root, ref_meta['file'])
                    ref_img, header = images.read_exr(ref_path)
                    reference = {'file': ref_meta['file'], 'sha256': identity.sha256_file(ref_path),
                                 'timeline_timecode': ref_meta.get('timeline_timecode'),
                                 'exr_time_code': header.get('timeCode')}
                    if ref_meta.get('timeline_timecode') and header.get('timeCode') and header['timeCode'] != ref_meta['timeline_timecode']:
                        raise RunError('%s: el EXR tiene timecode %s y el manifiesto %s' % (key, header['timeCode'], ref_meta['timeline_timecode']))
                res = {'test': t['id'], 'series': t['series'], 'category': t['category'], 'frame': frame,
                       'setup': setup_block(doc, t, frame, conv['inputs'], (start, count), settings, reference, conv['experimental_env']),
                       'candidate_sha256': dec['sha256'],
                       'decode_verification': dec.get('decode_verification'),
                       'compression': compression(conv, dec, img)}
                expo = exposure_of(settings)
                norm = analysis['normalize_to_iso'] / float(t['iso']) * 2.0 ** -expo
                ceiling = doc['camera']['signal_ceiling_iso400'] * t['iso'] / 400.0 * 2.0 ** expo
                if t['category'] == 'dark_frame':
                    res['dark_frame'] = M.dark_frame(img)
                if ref_img is not None:
                    log('%s: métricas frente a %s' % (key, ref_meta['file']))
                    res['reference_fidelity'] = evaluate(ref_img, img, t, doc, norm, ceiling, scenes, run_dir, key, 'ref', vis, analysis)
                if baseline:
                    b = baseline['metrics']['results'].get(key)
                    if not b:
                        raise RunError('%s no está en el baseline %s' % (key, args.baseline))
                    # En regresión, la identidad comparable es entradas + revelado + experimental_env (sin exigir EXR).
                    if regression_setup(b['setup']) != regression_setup(res['setup']):
                        raise RunError('%s: el baseline usó otra configuración o entradas; no es comparable:\n  baseline %s\n  ahora    %s' % (
                            key, json.dumps(regression_setup(b['setup']), sort_keys=True),
                            json.dumps(regression_setup(res['setup']), sort_keys=True)))
                    base_img = images.read_f32(os.path.join(baseline['dir'], 'frames', key + '.f32'))
                    if identity.sha256_file(os.path.join(baseline['dir'], 'frames', key + '.f32')) != b['candidate_sha256']:
                        raise RunError('%s: la imagen congelada del baseline no coincide con su sha' % key)
                    log('%s: regresión frente al baseline %s' % (key, args.baseline))
                    res['regression'] = {'identical': bool(np.array_equal(base_img, img))}
                    res['regression'].update(evaluate(base_img, img, t, doc, norm, ceiling, scenes, run_dir, key, 'base', vis, analysis, align=False))
                results[key] = res
    total = time.monotonic() - t_start

    metrics = {'schema': SCHEMA, 'config_sha256': config_sha,
               'build': {'cdng_convert_sha256': conv_id['cdng_convert_sha256'], 'model_sha256': identity.sha256_json(conv_id['model']),
                         'decoder_sha256': identity.sha256_json(dec_id)},
               'results': results}
    run_meta = {'schema': SCHEMA, 'run_id': run_id, 'started': time.strftime('%Y-%m-%dT%H:%M:%S'), 'command': sys.argv,
                'dataset': root, 'references': refs_root, 'baseline': args.baseline, 'no_reference': bool(args.no_reference),
                'git': git, 'converter': conv_id, 'decoder': dec_id,
                'environment': dict(identity.environment(), experimental_env=effective_env, diagnostic_env=diagnostic),
                'config': config, 'timing': dict(timing, total_seconds=total),
                'cache': cache_use, 'warnings': warnings,
                'frames': {k: {'sha256': r['candidate_sha256']} for k, r in results.items()}}
    write_json(os.path.join(run_dir, 'metrics.json'), metrics)
    write_json(os.path.join(run_dir, 'run.json'), run_meta)
    report.write_csv(os.path.join(run_dir, 'metrics.csv'), metrics)
    comparison = None
    if baseline:
        comparison = C.compare(baseline['metrics'], strip_regression(metrics), thresholds)
        comparison['baseline'] = args.baseline
        write_json(os.path.join(run_dir, 'comparison.json'), comparison)
    report.write_html(os.path.join(run_dir, 'report.html'), metrics, run_meta, comparison, doc)
    log('informe: %s' % os.path.join(run_dir, 'report.html'))
    return run_dir


def regression_setup(setup):
    """Identidad comparable en modo regresión: sin el bloque reference (puede faltar el EXR)."""
    return {k: v for k, v in setup.items() if k != 'reference'}


def strip_regression(metrics):
    """Para comparar con el baseline solo cuentan las métricas frente a la referencia (las de regresión no existen en él)."""
    out = dict(metrics, results={})
    for k, r in metrics['results'].items():
        out['results'][k] = {kk: vv for kk, vv in r.items() if kk != 'regression'}
    return out


def compression(conv, dec, img):
    h, w = img.shape[:2]
    raw = w * h * 12 / 8
    info = dec.get('frame_info') or {}
    fb = info.get('frame_bytes') if isinstance(info, dict) else None
    done = conv['completed']
    return {'frame_bytes': fb, 'frame_ratio': raw / fb if fb else None, 'braw_bytes': done.get('bytes'),
            'braw_frames': done.get('frames'), 'braw_ratio_achieved': done.get('ratio_achieved'),
            'q_min': done.get('q_min'), 'q_max': done.get('q_max')}


def scene_paths(doc):
    base = os.path.dirname(doc['_path'])
    return {name: os.path.join(base, s['rois']) for name, s in doc.get('scenes', {}).items()}


def evaluate(ref, cand, test, doc, norm, ceiling, scenes, run_dir, key, tag, vis, analysis, align=True):
    images.check_pair(ref, cand)
    out = {}
    if align:
        a = analysis['alignment']
        out['alignment'] = images.check_alignment(ref, cand, a['min_detail_correlation'])
    m, edges = M.fidelity(ref, cand, analysis, norm, ceiling)
    out.update(m)
    rn, cn = ref.astype(np.float64) * norm, cand.astype(np.float64) * norm
    scene = test.get('scene')
    doc_rois = None
    if scene:
        if scene not in scenes:
            h, w = ref.shape[:2]
            scenes[scene] = R.load(scene_paths(doc)[scene], (w, h))
        doc_rois = scenes[scene]
        out['rois'], out['roi_summary'] = R.evaluate(rn, cn, doc_rois, analysis)
    out['png'] = pictures(rn, cn, edges, doc_rois, out.get('rois'), run_dir, '%s_%s' % (key, tag), vis, analysis)
    return out


def pictures(rn, cn, edges, doc_rois, roi_metrics, run_dir, prefix, vis, analysis):
    d = os.path.join(run_dir, 'png')
    names = {}

    def put(name, rgb8):
        fname = '%s_%s.png' % (prefix, name)
        viz.save(os.path.join(d, fname), rgb8)
        names[name] = fname
    ref8, cand8 = viz.display(rn, vis['display_exposure']), viz.display(cn, vis['display_exposure'])
    put('reference', ref8)
    put('candidate', cand8)
    abs8 = viz.abs_error_map(rn, cn, vis['abs_error_max'])
    put('abs_error', abs8)
    put('signed_error', viz.signed_error_map(rn, cn, vis['signed_error_max']))
    put('chroma_error', viz.chroma_error_map(rn, cn, vis['chroma_error_max'], analysis['chroma_min_luminance']))
    put('edge_mask', viz.mask_png(edges))
    put('histogram', viz.histogram(rn, cn, vis['histogram_max'], vis['histogram_bins']))
    if doc_rois:
        put('roi_overlay', viz.roi_overlay(ref8, doc_rois))
        size = (rn.shape[1], rn.shape[0])
        for roi in doc_rois['rois']:
            if roi['category'] == 'colorchecker':
                continue
            box = viz.zoom_box(roi, size)
            put('zoom_' + roi['name'], viz.zoom_strip([ref8, cand8, abs8], box, vis['roi_zoom']))
            if roi['type'] == 'edge':
                put('profile_' + roi['name'], viz.profile_plot(roi_metrics[roi['name']]))
    return names


def load_baseline(name, out):
    d = name if os.path.isdir(name) else os.path.join(out, 'baselines', name)
    if not os.path.isfile(os.path.join(d, 'metrics.json')):
        raise RunError('no existe el baseline %s (make benchmark-freeze)' % d)
    with open(os.path.join(d, 'metrics.json'), encoding='utf-8') as f:
        metrics = json.load(f)
    with open(os.path.join(d, 'run.json'), encoding='utf-8') as f:
        meta = json.load(f)
    return {'dir': d, 'metrics': metrics, 'run': meta}


def freeze(run_dir, name, out, force=False):
    """Congela una ejecución como baseline: métricas, metadatos e imágenes reveladas, verificando sus sha."""
    dest = os.path.join(out, 'baselines', name)
    if os.path.exists(dest):
        if not force:
            raise RunError('el baseline %s ya existe (usa --force para sustituirlo)' % name)
        shutil.rmtree(dest)
    with open(os.path.join(run_dir, 'run.json'), encoding='utf-8') as f:
        meta = json.load(f)
    tmp = dest + '.tmp'
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(os.path.join(tmp, 'frames'))
    for key, info in meta['frames'].items():
        src = os.path.join(run_dir, 'frames', key + '.f32')
        if identity.sha256_file(src) != info['sha256']:
            raise RunError('%s no coincide con el sha registrado en la ejecución' % src)
        shutil.copyfile(src, os.path.join(tmp, 'frames', key + '.f32'))
    for name_ in ('metrics.json', 'run.json', 'metrics.csv', 'report.html'):
        shutil.copyfile(os.path.join(run_dir, name_), os.path.join(tmp, name_))
    shutil.copytree(os.path.join(run_dir, 'png'), os.path.join(tmp, 'png'))
    write_json(os.path.join(tmp, 'frozen.json'), {'name': name, 'run_id': meta['run_id'], 'from': os.path.abspath(run_dir),
                                                  'frozen': time.strftime('%Y-%m-%dT%H:%M:%S')})
    os.rename(tmp, dest)
    return dest


def setup_differences(a, b, prefix=''):
    out = []
    for k in sorted(set(a) | set(b)):
        va, vb = a.get(k), b.get(k)
        if isinstance(va, dict) and isinstance(vb, dict):
            out += setup_differences(va, vb, prefix + k + '.')
        elif va != vb:
            out.append({'field': prefix + k, 'base': va, 'candidate': vb})
    return out


def compare_runs(base, cand, thresholds_path, out_dir, out, allow_setup_differences=False):
    """Compara dos ejecuciones (o baselines) ya hechas. Por defecto exige la misma configuración y entradas en los
    resultados comunes; con allow_setup_differences (p. ej. q 4 frente a q 8) las diferencias se listan en el informe."""
    b, c = load_baseline(base, out), load_baseline(cand, out)
    differences = {}
    for key in sorted(set(b['metrics']['results']) & set(c['metrics']['results'])):
        diff = setup_differences(b['metrics']['results'][key]['setup'], c['metrics']['results'][key]['setup'])
        if diff:
            differences[key] = diff
    config_differs = b['metrics']['config_sha256'] != c['metrics']['config_sha256']
    if (differences or config_differs) and not allow_setup_differences:
        detail = '; '.join('%s: %s' % (k, ', '.join(d['field'] for d in v)) for k, v in differences.items()) or 'config_sha256'
        raise RunError('configuración o entradas distintas entre las dos ejecuciones (%s); no son comparables '
                       '(--allow-setup-differences para compararlas igualmente)' % detail)
    comparison = C.compare(strip_regression(b['metrics']), strip_regression(c['metrics']), C.load_thresholds(thresholds_path))
    comparison.update(baseline=b['dir'], candidate=c['dir'], same_build=b['metrics']['build'] == c['metrics']['build'],
                      setup_differences=differences, config_differs=config_differs)
    os.makedirs(out_dir, exist_ok=True)
    write_json(os.path.join(out_dir, 'comparison.json'), comparison)
    report.write_comparison_html(os.path.join(out_dir, 'comparison.html'), comparison, b, c)
    return out_dir


def equivalence(args):
    """¿brawdec reproduce el render de Resolve del mismo BRAW? Compara con las exportaciones EXR de los BRAW."""
    doc = MF.load(args.manifest)
    root, refs_root = MF.resolve_paths(doc, args.dataset, args.references)
    framework = args.framework or doc['decode']['framework']
    dec_id = identity.decoder(args.brawdec, framework, doc['decode']['camera_support'])
    out_dir = os.path.join(args.out, 'equivalence', args.run or time.strftime('%Y%m%d-%H%M%S'))
    os.makedirs(out_dir)
    log = P.Log(os.path.join(out_dir, 'equivalence.log'))
    rows = []
    for e in doc.get('decoder_equivalence', []):
        braw = os.path.expanduser(e['braw'])
        ref_path = os.path.join(refs_root, e['reference'])
        if not os.path.isfile(braw) or not os.path.isfile(ref_path):
            log('omitido: falta %s' % (braw if not os.path.isfile(braw) else ref_path))
            continue
        settings = decode_settings(doc, e.get('decode'))
        img, dec = P.decode(args.brawdec, framework, dec_id, braw, identity.sha256_file(braw), e['frame'], settings,
                            os.path.join(args.out, 'cache'), log, args.no_cache)
        ref, header = images.read_exr(ref_path)
        images.check_pair(ref, img)
        norm = doc['analysis']['normalize_to_iso'] / float(e['iso']) * 2.0 ** -exposure_of(settings)
        rn, cn = ref.astype(np.float64) * norm, img.astype(np.float64) * norm
        d = cn - rn
        row = {'braw': braw, 'frame': e['frame'], 'reference': e['reference'], 'settings': settings,
               'normalized': M.basic(rn, cn), 'max_abs_error': float(np.abs(d).max()), 'signed_mean_error': float(d.mean()),
               'max_rel_error': float((np.abs(d) / np.maximum(np.abs(rn), 1e-3)).max()), 'identical': bool(np.array_equal(ref, img)),
               'exr_header': {k: header.get(k) for k in ('isoSpeed', 'timeCode', 'tech_details_color_science') if k in header}}
        rows.append(row)
        log('%s fotograma %d frente a %s: PSNR norm. %.2f dB, correlación %.7f, error medio %+.2e, máx %.3g, idéntica %s' % (
            os.path.basename(braw), e['frame'], e['reference'], row['normalized']['psnr_db'], row['normalized']['correlation_rgb'],
            row['signed_mean_error'], row['max_abs_error'], row['identical']))
    write_json(os.path.join(out_dir, 'equivalence.json'), {'decoder': dec_id, 'results': rows})
    return out_dir


def write_json(path, value):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(value, f, indent=1, sort_keys=True, ensure_ascii=False)
        f.write('\n')
