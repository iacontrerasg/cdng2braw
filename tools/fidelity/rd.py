"""Análisis rate–distortion EXP-002: identidad desde metadatos, q efectivo del bitstream, curvas SVG."""
import csv
import json
import os
import re
from collections import defaultdict

from . import braw_inspect, identity


PHOTOSITES_1080P = 1920 * 1080
RAW_BYTES_12BIT = PHOTOSITES_1080P * 12 / 8
QMAX = 24

# Variables experimentales autorizadas en EXP-002 (además de PRECOMP/ALPHA/BOUNDED/RATE).
AUTHORIZED_EXP_ENV = frozenset({
    'CDNG_OG_DEBLOCK_PRECOMP', 'CDNG_OG_DEBLOCK_ALPHA', 'CDNG_OG_DEBLOCK_BOUNDED', 'CDNG_RATE',
})


class RdError(RuntimeError):
    pass


def load_run(path):
    metrics = json.load(open(os.path.join(path, 'metrics.json'), encoding='utf-8'))
    run = json.load(open(os.path.join(path, 'run.json'), encoding='utf-8'))
    return {'dir': os.path.abspath(path), 'metrics': metrics, 'run': run,
            'id': os.path.basename(os.path.abspath(path))}


def parse_exp002_id(run_id):
    """Etiqueta del nombre de corrida (solo etiqueta; la identidad real viene de run/metrics).

    Reconoce …-a1- / …-a050- / …-a075- / …-a0- / …-bd- (bounded, no «abd»).
    Expone claves legacy (alpha/q/ratio/mode/tag) y *_label.
    """
    out = {'id': run_id, 'alpha_label': None, 'q_label': None, 'ratio_label': None,
           'mode_label': None, 'tag_label': None}
    m = re.search(r'(?:^|-)(a1|a050|a075|a0|bd)(?:-q|-r|$)', run_id)
    if not m:
        m = re.search(r'-(a1|a050|a075|a0|bd)-', run_id)
    if m:
        tok = m.group(1)
        out['alpha_label'] = {'a1': 1.0, 'a050': 0.5, 'a075': 0.75, 'a0': 0.0, 'bd': None}[tok]
        if tok == 'bd':
            out['tag_label'] = 'bounded'
        elif tok == 'a0':
            out['tag_label'] = 'ablation'
        else:
            out['tag_label'] = 'proportional' if tok != 'a1' else 'control'
    m = re.search(r'-q(\d{2})(?:-|$)', run_id)
    if m:
        out['q_label'] = int(m.group(1))
        out['mode_label'] = 'quality'
    m = re.search(r'-r(\d{2})(?:-|$)', run_id)
    if m:
        out['ratio_label'] = int(m.group(1))
        out['mode_label'] = 'ratio'
    # Alias legacy usados por tests / consumidores previos
    out['alpha'] = out['alpha_label']
    out['q'] = out['q_label']
    out['ratio'] = out['ratio_label']
    out['mode'] = out['mode_label']
    out['tag'] = out['tag_label']
    return out


def alpha_from_env(env):
    env = env or {}
    pre = env.get('CDNG_OG_DEBLOCK_PRECOMP')
    pre_off = pre is not None and str(pre).lower() in ('0', 'off', 'false', 'no')
    bounded = env.get('CDNG_OG_DEBLOCK_BOUNDED')
    bounded_on = bounded is not None and str(bounded).lower() in ('1', 'on', 'true', 'yes')
    if pre_off:
        return 0.0, 'ablation', False
    if bounded_on:
        return None, 'bounded', True
    raw = env.get('CDNG_OG_DEBLOCK_ALPHA')
    if raw is None or raw == '':
        return 1.0, 'control', False
    a = float(raw)
    return a, ('control' if abs(a - 1.0) < 1e-12 else 'proportional'), False


def config_from_result(result):
    """Configuración efectiva de un resultado (setup + compression)."""
    setup = result.get('setup') or {}
    enc = setup.get('encode') or {}
    req = enc.get('request') or {}
    env = enc.get('experimental_env') or {}
    alpha, tag, bounded = alpha_from_env(env)
    mode = req.get('compression')
    q_req = req.get('q') if mode == 'quality' else None
    ratio_req = req.get('ratio') if mode == 'ratio' else None
    unauthorized = sorted(k for k, v in env.items()
                          if k.startswith('CDNG_') and k not in AUTHORIZED_EXP_ENV and v is not None)
    return {
        'mode': mode,
        'q_requested': q_req,
        'ratio_requested': ratio_req,
        'alpha': alpha,
        'tag': tag,
        'bounded': bounded,
        'experimental_env': env,
        'noise_synthesis': req.get('noise_synthesis'),
        'nr_luma': req.get('nr_luma'),
        'nr_chroma': req.get('nr_chroma'),
        'rate_search': env.get('CDNG_RATE') == 'search' or (mode == 'ratio'),
        'decode': setup.get('decode'),
        'iso': setup.get('iso'),
        'inputs': setup.get('inputs'),
        'range': setup.get('range'),
        'frame': result.get('frame'),
        'clip': setup.get('clip'),
        'reference': setup.get('reference'),
        'dng_sha256': setup.get('dng_sha256'),
        'unauthorized_env': unauthorized,
    }


def run_identity(run):
    """Identidad de corrida: worker, SDK, perfiles, dataset."""
    r = run['run']
    conv = r.get('converter') or {}
    dec = r.get('decoder') or {}
    return {
        'cdng_convert_sha256': conv.get('cdng_convert_sha256'),
        'model_sha256': identity.sha256_json(conv.get('model')) if conv.get('model') is not None else None,
        'brawdec_sha256': (dec.get('brawdec_sha256') if isinstance(dec, dict) else None) or (
            (r.get('build') or {}).get('decoder_sha256')),
        'decoder': dec,
        'dataset': r.get('dataset'),
        'references': r.get('references'),
        'config_sha256': (r.get('config') if isinstance(r.get('config'), str) else None) or r.get('config_sha256'),
    }


def cache_root_for(run):
    """Raíz de caché: hermano de runs/ bajo out/benchmark."""
    # run['dir'] = .../out/benchmark/runs/<id>
    return os.path.abspath(os.path.join(run['dir'], os.pardir, os.pardir, 'cache'))


def braw_path_for_result(run, result):
    """Localiza clip.braw de caché alineado con el tramo del resultado."""
    timing = (run['run'].get('timing') or {}).get('conversions') or []
    braw_entries = [c for c in (run['run'].get('cache') or []) if c.get('kind') == 'braw']
    if len(timing) != len(braw_entries):
        # Emparejar por orden de aparición en el tramo [start,count]
        pass
    rng = (result.get('setup') or {}).get('range')
    if not rng or len(rng) != 2:
        raise RdError('%s: setup.range ausente' % run['id'])
    start, count = int(rng[0]), int(rng[1])
    idx = None
    for i, t in enumerate(timing):
        tr = t.get('range')
        if tr and int(tr[0]) == start and int(tr[1]) == count:
            idx = i
            break
    if idx is None:
        # fallback: mismo orden que entradas braw filtrando por test
        test_id = (result.get('test') or (result.get('result') or '').split('_')[0])
        cand = [i for i, t in enumerate(timing) if t.get('test') == test_id]
        if len(cand) == 1:
            idx = cand[0]
        elif len(braw_entries) == len(timing) and cand:
            # primer tramo del test que coincida con range
            for i in cand:
                tr = timing[i].get('range')
                if tr and int(tr[0]) == start:
                    idx = i
                    break
    if idx is None or idx >= len(braw_entries):
        raise RdError('%s: no hay entrada de caché BRAW para range %s' % (run['id'], rng))
    key = braw_entries[idx]['key']
    path = os.path.join(cache_root_for(run), 'braw', key[:24], 'clip.braw')
    if not os.path.isfile(path):
        raise RdError('%s: falta BRAW de caché %s' % (run['id'], path))
    return path, key


def bitstream_frame(run, result):
    """q y tamaños del fotograma evaluado desde el bitstream."""
    path, key = braw_path_for_result(run, result)
    payloads = braw_inspect.frame_payloads(path)
    start, count = result['setup']['range']
    local = int(result['frame']) - int(start)
    if local < 0 or local >= len(payloads):
        raise RdError('%s: índice local %d fuera de %d samples' % (run['id'], local, len(payloads)))
    f = payloads[local]
    payload_bytes = sum(f['slice_bytes'])
    container_bytes = os.path.getsize(path)
    qs = [p['q'] for p in payloads]
    return {
        'braw_path': path,
        'braw_cache_key': key,
        'q_frame': int(f['q']),
        'sample_bytes': int(f['sample_size']),
        'chunk_bytes': int(f['chunk_size']),
        'payload_bytes': int(payload_bytes),
        'bmdf_bytes': braw_inspect.BMDF_SIZE,
        'container_bytes': int(container_bytes),
        'overhead_container_bytes': int(container_bytes - sum(p['sample_size'] for p in payloads)),
        'segment_q': qs,
        'segment_q_min': min(qs),
        'segment_q_max': max(qs),
        'segment_q_mean': sum(qs) / float(len(qs)),
    }


def label_matches_config(label, cfg):
    """Comprueba que la etiqueta del run_id coincida con la config efectiva."""
    errors = []
    if label.get('mode_label') and cfg.get('mode') and label['mode_label'] != cfg['mode']:
        errors.append('mode etiqueta %s ≠ efectivo %s' % (label['mode_label'], cfg['mode']))
    if label.get('q_label') is not None and cfg.get('q_requested') is not None:
        if int(label['q_label']) != int(cfg['q_requested']):
            errors.append('q etiqueta %s ≠ solicitado %s' % (label['q_label'], cfg['q_requested']))
    if label.get('ratio_label') is not None and cfg.get('ratio_requested') is not None:
        if float(label['ratio_label']) != float(cfg['ratio_requested']):
            errors.append('ratio etiqueta %s ≠ solicitado %s' % (label['ratio_label'], cfg['ratio_requested']))
    if label.get('tag_label') == 'bounded' and not cfg.get('bounded'):
        errors.append('etiqueta bd pero bounded inactivo')
    if label.get('tag_label') in ('control', 'proportional') and cfg.get('bounded'):
        errors.append('etiqueta α=%s pero bounded activo' % label.get('alpha_label'))
    if label.get('tag_label') == 'ablation' and cfg.get('tag') != 'ablation':
        errors.append('etiqueta a0 pero no ablación')
    if label.get('tag_label') in ('control', 'proportional') and cfg.get('tag') == 'ablation':
        errors.append('etiqueta α pero ablación efectiva')
    if label.get('alpha_label') is not None and cfg.get('alpha') is not None:
        if abs(float(label['alpha_label']) - float(cfg['alpha'])) > 1e-9:
            errors.append('alpha etiqueta %s ≠ efectivo %s' % (label['alpha_label'], cfg['alpha']))
    elif label.get('alpha_label') is not None and cfg.get('bounded'):
        errors.append('alpha etiqueta %s pero config bounded (sin α)' % label['alpha_label'])
    return errors


def frame_row(run, key, result, *, require_bitstream=True):
    rf = result.get('reference_fidelity') or {}
    n = rf.get('normalized') or {}
    raw_m = rf.get('raw') or {}
    sh = rf.get('sharpness') or {}
    lc = (rf.get('luma_chroma') or {}).get('edges') or {}
    neut = rf.get('neutral') or {}
    eb = rf.get('experimental_block') or {}
    comp = result.get('compression') or {}
    cfg = config_from_result(result)
    label = parse_exp002_id(run['id'])
    mismatch = label_matches_config(label, cfg)
    if cfg['unauthorized_env']:
        raise RdError('%s: experimental_env no autorizado: %s' % (run['id'], cfg['unauthorized_env']))

    bs = None
    if require_bitstream:
        bs = bitstream_frame(run, result)
        q_frame = bs['q_frame']
        sample_bytes = bs['sample_bytes']
        payload_bytes = bs['payload_bytes']
        container_bytes = bs['container_bytes']
        q_seg_min, q_seg_max, q_seg_mean = bs['segment_q_min'], bs['segment_q_max'], bs['segment_q_mean']
    else:
        q_frame = comp.get('q_min') if comp.get('q_min') == comp.get('q_max') else None
        sample_bytes = payload_bytes = container_bytes = None
        q_seg_min = comp.get('q_min')
        q_seg_max = comp.get('q_max')
        q_seg_mean = None

    q_req = cfg['q_requested']
    q_bumped = (q_req is not None and q_frame is not None and q_frame != q_req)
    q_saturated = (q_frame == QMAX) if q_frame is not None else False
    # Fallback CodecRangeError: calidad fija y q efectivo > solicitado
    codec_range_fallback = bool(q_req is not None and q_frame is not None and q_frame > q_req)

    sdk_frame_bytes = comp.get('frame_bytes')
    # Ratios con denominador 12-bit raw explícito; no mezclar con braw_ratio_achieved del worker (tramo).
    ratio_sample = (RAW_BYTES_12BIT / sample_bytes) if sample_bytes else None
    ratio_payload = (RAW_BYTES_12BIT / payload_bytes) if payload_bytes else None
    ratio_sdk = (RAW_BYTES_12BIT / sdk_frame_bytes) if sdk_frame_bytes else None
    bits_per_site_sample = (8.0 * sample_bytes / PHOTOSITES_1080P) if sample_bytes else None
    bits_per_site_payload = (8.0 * payload_bytes / PHOTOSITES_1080P) if payload_bytes else None

    # Almacenamiento: usar sample_bytes (contenido por fotograma en el contenedor) de forma consistente.
    store_bytes = sample_bytes if sample_bytes is not None else sdk_frame_bytes

    return {
        'run': run['id'], 'result': key, 'frame': result.get('frame'),
        'label_mismatch': mismatch,
        'mode': cfg['mode'], 'alpha': cfg['alpha'], 'tag': cfg['tag'], 'bounded': cfg['bounded'],
        'q_requested': q_req, 'ratio_requested': cfg['ratio_requested'],
        'q_frame': q_frame, 'q_segment_min': q_seg_min, 'q_segment_max': q_seg_max,
        'q_segment_mean': q_seg_mean,
        # q_eff = q del fotograma evaluado (bitstream), no (q_min+q_max)/2
        'q_eff': q_frame,
        'q_bumped': q_bumped, 'q_saturated_qmax': q_saturated,
        'codec_range_fallback': codec_range_fallback,
        'psnr': n.get('psnr_db'), 'rmse': n.get('rmse'),
        'psnr_raw': raw_m.get('psnr_db'), 'rmse_raw': raw_m.get('rmse'),
        'duv_edges': lc.get('duv_mean'), 'duv_neutral': neut.get('duv_mean'),
        'grad': sh.get('gradient_energy_ratio_edges'),
        'lap': sh.get('laplacian_energy_ratio_edges'),
        'block_bmi': eb.get('border_minus_interior_mae'),
        'sample_bytes': sample_bytes, 'payload_bytes': payload_bytes,
        'container_bytes': container_bytes,
        'sdk_frame_bytes': sdk_frame_bytes,
        'braw_bytes_segment': comp.get('braw_bytes'),
        'ratio_sample_12bit': ratio_sample,
        'ratio_payload_12bit': ratio_payload,
        'ratio_sdk_12bit': ratio_sdk,
        'ratio_segment_worker': comp.get('braw_ratio_achieved'),
        'bits_per_site_sample': bits_per_site_sample,
        'bits_per_site_payload': bits_per_site_payload,
        # Alias de curvas: bits/ratio/almacenamiento sobre sample (consistente)
        'bits_per_site': bits_per_site_sample,
        'frame_bytes': store_bytes,
        'frame_ratio': ratio_sample,
        'mb_s_24fps': (store_bytes * 24 / 1e6) if store_bytes else None,
        'gb_h_24fps': (store_bytes * 24 * 3600 / 1e9) if store_bytes else None,
        'iso': cfg['iso'],
        'inputs_sha': identity.sha256_json(cfg['inputs']) if cfg.get('inputs') is not None else None,
        'decode': cfg['decode'],
        'candidate_sha256': result.get('candidate_sha256'),
        'worker_sha': (run['run'].get('converter') or {}).get('cdng_convert_sha256'),
    }


def collect(runs, *, require_bitstream=True, require_label_match=True):
    rows = []
    identities = []
    for run in runs:
        rid = run_identity(run)
        identities.append(rid)
        seconds = (run['run'].get('timing') or {}).get('total_seconds')
        fresh = [c for c in (run['run'].get('timing') or {}).get('conversions', []) if not c.get('reused')]
        for key, result in sorted(run['metrics'].get('results', {}).items()):
            row = frame_row(run, key, result, require_bitstream=require_bitstream)
            if require_label_match and row['label_mismatch']:
                raise RdError('%s / %s: etiqueta incompatible: %s' % (run['id'], key, '; '.join(row['label_mismatch'])))
            row['run_seconds'] = seconds
            row['fresh_convert_seconds'] = sum(c.get('seconds') or 0 for c in fresh) or None
            rows.append(row)
    return rows, identities


def assert_comparable_runs(runs):
    """Misma compilación/SDK/dataset/referencias entre corridas del informe."""
    if not runs:
        return
    base = run_identity(runs[0])
    for run in runs[1:]:
        cur = run_identity(run)
        for k in ('cdng_convert_sha256', 'dataset', 'references'):
            if base.get(k) and cur.get(k) and base[k] != cur[k]:
                raise RdError('identidad incompatible en %s: %s %s ≠ %s' % (run['id'], k, base[k], cur[k]))


def coverage_report(rows, expected_q=None, expected_alphas=None, expected_frames=None,
                    expected_ratios=None):
    """Corridas/fotogramas faltantes o duplicados.

    Si expected_q/expected_alphas son None, no se exigen celdas ausentes (solo duplicados
    y fallbacks q0); el llamador del barrido completo A04 debe pasar expected_q=range(25).
    """
    seen_q = defaultdict(list)
    seen_r = defaultdict(list)
    for r in rows:
        if r.get('mode') == 'quality':
            seen_q[(r.get('alpha'), r.get('q_requested'), r.get('frame'))].append(r['run'])
        elif r.get('mode') == 'ratio':
            seen_r[(r.get('alpha'), r.get('ratio_requested'), r.get('frame'))].append(r['run'])
    missing, dupes = [], []
    frames = expected_frames or sorted({r['frame'] for r in rows if r.get('frame') is not None})
    if expected_q is not None:
        alphas = expected_alphas if expected_alphas is not None else [1.0, 0.5, 0.75]
        for a in alphas:
            for q in expected_q:
                for fr in frames:
                    k = (a, q, fr)
                    if k not in seen_q:
                        missing.append({'alpha': a, 'q': q, 'frame': fr, 'mode': 'quality'})
                    elif len(seen_q[k]) > 1:
                        dupes.append({'alpha': a, 'q': q, 'frame': fr, 'runs': seen_q[k]})
    else:
        for k, runs in seen_q.items():
            if len(runs) > 1:
                dupes.append({'alpha': k[0], 'q': k[1], 'frame': k[2], 'runs': runs})
    if expected_ratios is not None:
        alphas = expected_alphas if expected_alphas is not None else [1.0, 0.5, 0.75]
        for a in alphas:
            for ratio in expected_ratios:
                for fr in frames:
                    k = (a, ratio, fr)
                    if k not in seen_r:
                        missing.append({'alpha': a, 'ratio': ratio, 'frame': fr, 'mode': 'ratio'})
                    elif len(seen_r[k]) > 1:
                        dupes.append({'alpha': a, 'ratio': ratio, 'frame': fr, 'runs': seen_r[k]})
    q0_bumps = [r for r in rows if r.get('q_requested') == 0 and r.get('codec_range_fallback')]
    return {'missing': missing, 'duplicates': dupes, 'q0_codec_range_fallbacks': [
        {'run': r['run'], 'frame': r['frame'], 'q_frame': r['q_frame']} for r in q0_bumps
    ]}


def _normalize_row_keys(r):
    """Acepta filas legacy (q/ratio) y nuevas (q_requested/…)."""
    r = dict(r)
    if r.get('q_requested') is None and r.get('q') is not None:
        r['q_requested'] = r['q']
    if r.get('ratio_requested') is None and r.get('ratio') is not None:
        r['ratio_requested'] = r['ratio']
    if r.get('q') is None and r.get('q_requested') is not None:
        r['q'] = r['q_requested']
    if r.get('ratio') is None and r.get('ratio_requested') is not None:
        r['ratio'] = r['ratio_requested']
    return r


def aggregate(rows, keys=('alpha', 'q_requested', 'ratio_requested', 'mode', 'tag')):
    groups = defaultdict(list)
    for r in rows:
        r = _normalize_row_keys(r)
        k = tuple(r.get(x) for x in keys)
        groups[k].append(r)

    def sort_key(kv):
        return tuple((-1 if x is None else x) if not isinstance(x, str) else x for x in kv[0])

    out = []
    for k, rs in sorted(groups.items(), key=sort_key):
        def avg(field):
            vals = [r[field] for r in rs if r.get(field) is not None and r[field] == r[field]]
            return sum(vals) / len(vals) if vals else None

        item = dict(zip(keys, k))
        # Alias legacy para curvas
        item['q'] = item.get('q_requested')
        item['ratio'] = item.get('ratio_requested')
        for f in ('psnr', 'rmse', 'psnr_raw', 'rmse_raw', 'duv_edges', 'duv_neutral', 'grad', 'lap', 'block_bmi',
                  'sample_bytes', 'payload_bytes', 'container_bytes', 'sdk_frame_bytes',
                  'frame_bytes', 'bits_per_site', 'bits_per_site_sample', 'bits_per_site_payload',
                  'ratio_sample_12bit', 'ratio_payload_12bit', 'ratio_sdk_12bit', 'ratio_segment_worker',
                  'frame_ratio', 'q_eff', 'q_segment_mean', 'mb_s_24fps', 'gb_h_24fps',
                  'run_seconds', 'fresh_convert_seconds'):
            item[f] = avg(f)
        item['n'] = len(rs)
        item['n_codec_range_fallback'] = sum(1 for r in rs if r.get('codec_range_fallback'))
        item['n_q_saturated'] = sum(1 for r in rs if r.get('q_saturated_qmax'))
        item['q_frame_values'] = sorted({r['q_frame'] for r in rs if r.get('q_frame') is not None})
        out.append(item)
    return out


def write_csv(path, rows):
    if not rows:
        open(path, 'w').write('')
        return
    keys = sorted({k for r in rows for k in r if k not in ('decode',)})
    with open(path, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=keys, extrasaction='ignore')
        w.writeheader()
        for r in rows:
            flat = {k: r.get(k) for k in keys}
            w.writerow(flat)


def _svg_polyline(points, width, height, pad, color, x_key, y_key):
    xs = [p[x_key] for p in points if p.get(x_key) is not None and p.get(y_key) is not None]
    ys = [p[y_key] for p in points if p.get(x_key) is not None and p.get(y_key) is not None]
    if len(xs) < 1:
        return '<p>sin datos</p>', None
    xmin, xmax = min(xs), max(xs)
    ymin, ymax = min(ys), max(ys)
    if xmax == xmin:
        xmax = xmin + 1
    if ymax == ymin:
        ymax = ymin + 1e-9

    def sx(x):
        return pad + (x - xmin) / (xmax - xmin) * (width - 2 * pad)

    def sy(y):
        return height - pad - (y - ymin) / (ymax - ymin) * (height - 2 * pad)

    pts = ' '.join('%.1f,%.1f' % (sx(x), sy(y)) for x, y in zip(xs, ys))
    circles = ''.join('<circle cx="%.1f" cy="%.1f" r="2.5" fill="%s"/>' % (sx(x), sy(y), color)
                      for x, y in zip(xs, ys))
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d">'
        '<rect width="%d" height="%d" fill="#1a1a1a"/>'
        '<polyline fill="none" stroke="%s" stroke-width="2" points="%s"/>%s'
        '<text x="%d" y="%d" fill="#aaa" font-size="11">%.3g</text>'
        '<text x="%d" y="%d" fill="#aaa" font-size="11">%.3g</text>'
        '<text x="%d" y="%d" fill="#aaa" font-size="11">%.3g</text>'
        '<text x="%d" y="%d" fill="#aaa" font-size="11">%.3g</text>'
        '</svg>' % (
            width, height, width, height, width, height, color, pts, circles,
            pad, height - 4, xmin, width - pad - 40, height - 4, xmax,
            4, pad + 4, ymax, 4, height - pad, ymin,
        )
    )
    return svg, (xmin, xmax, ymin, ymax)


def write_html(path, title, agg):
    by_alpha = defaultdict(list)
    for r in agg:
        if r.get('mode') == 'quality' and r.get('q') is not None and r.get('alpha') is not None:
            by_alpha[r['alpha']].append(r)
    for a in by_alpha:
        by_alpha[a] = sorted(by_alpha[a], key=lambda x: x['q'])

    colors = {1.0: '#66ccff', 0.5: '#ffcc66', 0.75: '#cc66ff', 0.0: '#aaaaaa'}
    metrics_charts = [
        ('PSNR normalizado vs q solicitado', 'q', 'psnr'),
        ('PSNR vs bits/fotosito (sample)', 'bits_per_site', 'psnr'),
        ("Δu'v' bordes vs q", 'q', 'duv_edges'),
        ('Gradiente vs q', 'q', 'grad'),
        ('Laplaciano vs q', 'q', 'lap'),
        ('Error bloques (border−interior) vs q', 'q', 'block_bmi'),
    ]
    sections = []
    for heading, xk, yk in metrics_charts:
        blocks = []
        for a, rows in sorted(by_alpha.items()):
            pts = [{'q': r.get('q'), 'bits_per_site': r.get('bits_per_site'),
                    'psnr': r.get('psnr'), 'duv_edges': r.get('duv_edges'),
                    'grad': r.get('grad'), 'lap': r.get('lap'), 'block_bmi': r.get('block_bmi')}
                   for r in rows]
            # Omitir puntos sin métrica (no pintar como 0)
            pts = [p for p in pts if p.get(xk) is not None and p.get(yk) is not None]
            svg, _ = _svg_polyline(pts, 640, 220, 36, colors.get(a, '#ffffff'), xk, yk)
            blocks.append('<div><h3>α=%s (n=%d pts)</h3>%s</div>' % (a, len(pts), svg))
        sections.append('<div class="card"><h2>%s</h2>%s</div>' % (heading, ''.join(blocks)))

    # Mapa de calor PSNR q × alpha (tabla, sin inventar ceros)
    alphas = sorted(by_alpha.keys())
    qs = sorted({r['q'] for rows in by_alpha.values() for r in rows if r.get('q') is not None})
    heat = ['<div class="card"><h2>Mapa PSNR (q × α)</h2><table><tr><th>q\\\\α</th>']
    heat += ['<th>%s</th>' % a for a in alphas] + ['</tr>']
    lookup = {(r['alpha'], r['q']): r.get('psnr') for r in agg if r.get('mode') == 'quality'}
    for q in qs:
        heat.append('<tr><th>%s</th>' % q)
        for a in alphas:
            v = lookup.get((a, q))
            heat.append('<td>%s</td>' % ('%.3f' % v if v is not None else '—'))
        heat.append('</tr>')
    heat.append('</table></div>')

    table = ['<div class="card"><h2>Tabla agregada</h2><table><tr>',
             '<th>α</th><th>modo</th><th>q_req</th><th>q_eff*</th><th>PSNR</th><th>Δu\'v\'e</th>',
             '<th>grad</th><th>lap</th><th>blockΔ</th><th>sample B</th><th>bits/sitio</th>',
             '<th>ratio sample</th><th>GB/h</th><th>fallback</th></tr>']
    for r in agg:
        mode = r.get('mode') or '—'
        qe = r.get('q_eff')
        table.append(
            '<tr><td>{a}</td><td>{m}</td><td>{qr}</td><td>{qe}</td><td>{psnr}</td><td>{duv}</td>'
            '<td>{g}</td><td>{lap}</td><td>{b}</td><td>{fb}</td><td>{bps}</td><td>{ra}</td>'
            '<td>{gb}</td><td>{fbk}</td></tr>'.format(
                a=r.get('alpha'), m=mode,
                qr=r.get('q_requested') if r.get('q_requested') is not None else r.get('ratio_requested'),
                qe=('%.2f' % qe) if qe is not None else '—',
                psnr=('%.3f' % r['psnr']) if r.get('psnr') is not None else '—',
                duv=('%.5f' % r['duv_edges']) if r.get('duv_edges') is not None else '—',
                g=('%.4f' % r['grad']) if r.get('grad') is not None else '—',
                lap=('%.4f' % r['lap']) if r.get('lap') is not None else '—',
                b=('%.3e' % r['block_bmi']) if r.get('block_bmi') is not None else '—',
                fb=('%d' % r['sample_bytes']) if r.get('sample_bytes') is not None else '—',
                bps=('%.3f' % r['bits_per_site']) if r.get('bits_per_site') is not None else '—',
                ra=('%.3f' % r['ratio_sample_12bit']) if r.get('ratio_sample_12bit') is not None else '—',
                gb=('%.2f' % r['gb_h_24fps']) if r.get('gb_h_24fps') is not None else '—',
                fbk=r.get('n_codec_range_fallback') or 0,
            )
        )
    table.append('</table><p>* q_eff = media del q de bitstream por fotograma agregado.</p></div>')

    html = (
        '<!DOCTYPE html>\n<html lang="es"><head><meta charset="utf-8"><title>' + title + '</title>\n'
        '<style>\n'
        'body{font-family:ui-sans-serif,system-ui,sans-serif;margin:2rem;background:#111;color:#eee}\n'
        'h1,h2,h3{font-weight:600} table{border-collapse:collapse;width:100%;font-size:13px}\n'
        'td,th{border:1px solid #333;padding:4px 8px;text-align:right} th{text-align:left}\n'
        '.card{background:#1a1a1a;padding:1rem;margin:1rem 0;border-radius:8px}\n'
        '</style></head><body>\n'
        '<h1>' + title + '</h1>\n'
        '<p>Informe autocontenido (SVG embebido, sin CDN). '
        'Celdas «—» = métrica ausente (no se sustituye por 0).</p>\n'
        + ''.join(sections) + ''.join(heat) + ''.join(table) +
        '\n</body></html>\n'
    )
    with open(path, 'w', encoding='utf-8') as f:
        f.write(html)


def build_report(run_dirs, dest, analysis_cfg=None, expected=None):
    os.makedirs(dest, exist_ok=True)
    runs = [load_run(d) for d in run_dirs]
    assert_comparable_runs(runs)
    rows, identities = collect(runs, require_bitstream=True, require_label_match=True)
    cov = coverage_report(rows, **(expected or {}))
    agg = aggregate(rows)
    write_csv(os.path.join(dest, 'frames.csv'), rows)
    write_csv(os.path.join(dest, 'aggregate.csv'), agg)
    write_html(os.path.join(dest, 'rd.html'), 'EXP-002 Rate–Distortion', agg)
    with open(os.path.join(dest, 'coverage.json'), 'w', encoding='utf-8') as f:
        json.dump(cov, f, indent=2, sort_keys=True)
    summary = {
        'n_runs': len(runs), 'n_frames': len(rows), 'aggregate': agg, 'coverage': cov,
        'identities': identities[:1],
        'notes': {
            'q_eff': 'q del fotograma en el bitstream (hdr byte 1), no (q_min+q_max)/2',
            'storage_bytes': 'sample_bytes (bmdf+chunk) por fotograma',
            'ratio_sample_12bit': 'RAW_BYTES_12BIT / sample_bytes',
            'q0_fallback': 'si q_requested=0 y q_frame>0 → CodecRangeError / encodeFixed subió de q',
        },
        'storage_1080p24': [
            {'alpha': r.get('alpha'), 'q_requested': r.get('q_requested'), 'ratio_requested': r.get('ratio_requested'),
             'q_eff': r.get('q_eff'), 'mb_s': r.get('mb_s_24fps'), 'gb_h': r.get('gb_h_24fps'),
             'hours_500gb': (500 / r['gb_h_24fps']) if r.get('gb_h_24fps') else None,
             'hours_1tb': (1000 / r['gb_h_24fps']) if r.get('gb_h_24fps') else None,
             'hours_2tb': (2000 / r['gb_h_24fps']) if r.get('gb_h_24fps') else None}
            for r in agg if r.get('frame_bytes')
        ],
    }
    with open(os.path.join(dest, 'summary.json'), 'w', encoding='utf-8') as f:
        json.dump(summary, f, indent=2, sort_keys=True)
    return summary


def verify_run_matches_request(run_dir, *, q=None, ratio=None, alpha=None, bounded=False, ablation=False):
    """Para --skip-existing: la corrida existente debe corresponder a la config pedida."""
    run = load_run(run_dir)
    if not run['metrics'].get('results'):
        raise RdError('%s: metrics.json sin results' % run_dir)
    # Comprueba el primer resultado (todos deben compartir encode)
    key = sorted(run['metrics']['results'])[0]
    cfg = config_from_result(run['metrics']['results'][key])
    if ablation:
        if cfg['tag'] != 'ablation':
            raise RdError('%s: se esperaba ablación' % run_dir)
    elif bounded:
        if not cfg['bounded']:
            raise RdError('%s: se esperaba bounded' % run_dir)
    elif alpha is not None and cfg['alpha'] is not None:
        if abs(cfg['alpha'] - float(alpha)) > 1e-9:
            raise RdError('%s: alpha efectivo %s ≠ %s' % (run_dir, cfg['alpha'], alpha))
    if q is not None:
        if cfg['mode'] != 'quality' or cfg['q_requested'] != int(q):
            raise RdError('%s: q solicitado efectivo %s ≠ %s' % (run_dir, cfg['q_requested'], q))
    if ratio is not None:
        if cfg['mode'] != 'ratio' or float(cfg['ratio_requested']) != float(ratio):
            raise RdError('%s: ratio solicitado efectivo %s ≠ %s' % (run_dir, cfg['ratio_requested'], ratio))
    # Integridad mínima: frames f32
    for rk, res in run['metrics']['results'].items():
        f32 = os.path.join(run['dir'], 'frames', rk + '.f32')
        if not os.path.isfile(f32):
            raise RdError('%s: falta %s' % (run_dir, f32))
        if res.get('candidate_sha256'):
            got = identity.sha256_file(f32)
            if got != res['candidate_sha256']:
                raise RdError('%s: sha f32 ≠ candidate_sha256' % rk)
    return run
