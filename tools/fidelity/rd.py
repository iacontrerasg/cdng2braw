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

# Variables experimentales autorizadas en EXP-002.
AUTHORIZED_EXP_ENV = frozenset({
    'CDNG_OG_DEBLOCK_PRECOMP', 'CDNG_OG_DEBLOCK_ALPHA', 'CDNG_OG_DEBLOCK_BOUNDED', 'CDNG_RATE',
    'CDNG_DEADZONE',
    'CDNG_DEADZONE_POLICY',
    'CDNG_RDOQ',
    'CDNG_RDOQ_LAMBDA',
    'CDNG_INJECT_RGB',
    'CDNG_INJECT_AT',
    'CDNG_OG_CHROMA_MODE',
    'CDNG_OG_CHROMA_KERNEL',
    'CDNG_OG_CHROMA_PHASE',
    'CDNG_OG_DEBAYER',
})

# Campos de encode (request o config.encode) que pueden diferir entre puntos RD.
AUTHORIZED_ENCODE_DIFF_KEYS = frozenset({
    'q', 'ratio', 'compression', 'rate_search', 'experimental_env', 'notes',
    'noise_synthesis', 'nr_luma', 'nr_chroma', 'workers', 'diagnostic_env',
})


class RdError(RuntimeError):
    pass


def load_run(path):
    with open(os.path.join(path, 'metrics.json'), encoding='utf-8') as f:
        metrics = json.load(f)
    with open(os.path.join(path, 'run.json'), encoding='utf-8') as f:
        run = json.load(f)
    return {'dir': os.path.abspath(path), 'metrics': metrics, 'run': run,
            'id': os.path.basename(os.path.abspath(path))}


def parse_exp002_id(run_id):
    """Etiqueta del nombre de corrida (solo etiqueta; la identidad real viene de run/metrics).

    Reconoce …-a1- / …-a050- / …-a075- / …-a0- / …-bd- (bounded, no «abd»).
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


def test_id_of(result, result_key=None):
    if result.get('test'):
        return result['test']
    if result_key and '_f' in result_key:
        return result_key.split('_f', 1)[0]
    return None


def config_from_result(result, result_key=None):
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
    ref = setup.get('reference') or {}
    return {
        'test': test_id_of(result, result_key),
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
        'frame': result.get('frame') if result.get('frame') is not None else setup.get('frame'),
        'clip': setup.get('clip'),
        'reference': ref,
        'reference_sha256': ref.get('sha256') if isinstance(ref, dict) else None,
        'dng_sha256': setup.get('dng_sha256'),
        'encode_request': req,
        'unauthorized_env': unauthorized,
    }


def encode_nonexperimental(req):
    """Opciones de codificación que deben coincidir fuera del eje q/ratio."""
    req = req or {}
    return {k: v for k, v in sorted(req.items()) if k not in AUTHORIZED_ENCODE_DIFF_KEYS}


def _require(run_id, name, value, *, empty_ok=False):
    if value is None or (not empty_ok and value == {}) or value == '':
        raise RdError('%s: falta identidad crítica «%s»' % (run_id, name))
    return value


def run_identity(run):
    """Identidad completa de corrida: worker, modelo, SDK, perfiles, análisis, ROI."""
    rid = run['id']
    r = run['run']
    conv = _require(rid, 'converter', r.get('converter'))
    dec = _require(rid, 'decoder', r.get('decoder'))
    cfg = _require(rid, 'config', r.get('config') if isinstance(r.get('config'), dict) else None)
    model = _require(rid, 'converter.model', conv.get('model'))
    cam = _require(rid, 'decoder.camera_support', dec.get('camera_support'))
    analysis = _require(rid, 'config.manifest_analysis', cfg.get('manifest_analysis'))
    rois = cfg.get('rois')
    if rois is None:
        raise RdError('%s: falta identidad crítica «config.rois»' % rid)
    decode_defaults = _require(rid, 'config.decode', cfg.get('decode'))
    encode_cfg = _require(rid, 'config.encode', cfg.get('encode'))
    return {
        'cdng_convert_sha256': _require(rid, 'cdng_convert_sha256', conv.get('cdng_convert_sha256')),
        'model': model,
        'model_sha256': identity.sha256_json(model),
        'brawdec_sha256': _require(rid, 'brawdec_sha256', dec.get('brawdec_sha256')),
        'framework_sha256': _require(rid, 'framework_sha256', dec.get('framework_sha256')),
        'camera_support': cam,
        'camera_support_sha256': identity.sha256_json(cam),
        'manifest_analysis': analysis,
        'analysis_sha256': identity.sha256_json(analysis),
        'rois': rois,
        'rois_sha256': identity.sha256_json(rois),
        'decode_defaults': decode_defaults,
        'decode_defaults_sha256': identity.sha256_json(decode_defaults),
        'encode_nonexperimental': encode_nonexperimental(encode_cfg),
        'encode_nonexperimental_sha256': identity.sha256_json(encode_nonexperimental(encode_cfg)),
        # Rutas de dataset/referencias NO son identidad de contenido; se exponen solo informativas.
        'dataset_path': r.get('dataset'),
        'references_path': r.get('references'),
    }


def cache_root_for(run):
    return os.path.abspath(os.path.join(run['dir'], os.pardir, os.pardir, 'cache'))


def conversion_material_from_result(run, result):
    """Reconstruye el material de caché BRAW a partir de metadatos del resultado (corridas históricas)."""
    setup = result.get('setup') or {}
    enc = setup.get('encode') or {}
    conv = (run['run'].get('converter') or {})
    clip = setup.get('clip')
    rng = setup.get('range')
    inputs = setup.get('inputs')
    req = enc.get('request')
    env = enc.get('experimental_env')
    if not clip:
        raise RdError('%s: setup.clip ausente' % run['id'])
    if not rng or len(rng) != 2:
        raise RdError('%s: setup.range ausente' % run['id'])
    if not inputs:
        raise RdError('%s: setup.inputs ausente' % run['id'])
    if req is None:
        raise RdError('%s: encode.request ausente' % run['id'])
    if env is None:
        raise RdError('%s: experimental_env ausente' % run['id'])
    if not conv.get('cdng_convert_sha256') or conv.get('model') is None:
        raise RdError('%s: converter incompleto para material BRAW' % run['id'])
    return {
        'kind': 'braw', 'info_schema': 2,
        'converter': {'cdng_convert_sha256': conv['cdng_convert_sha256'], 'model': conv['model']},
        'clip': clip,
        'first_frame': int(rng[0]),
        'inputs': inputs,
        'request': req,
        'experimental_env': env,
    }


def expected_conversion_key(run, result):
    return identity.sha256_json(conversion_material_from_result(run, result))


def frame_local_index(result):
    setup = result.get('setup') or {}
    rng = setup.get('range')
    frame = result.get('frame') if result.get('frame') is not None else setup.get('frame')
    if not rng or len(rng) != 2 or frame is None:
        raise RdError('range/frame ausentes para índice local')
    local = int(frame) - int(rng[0])
    count = int(rng[1])
    if local < 0 or local >= count:
        raise RdError('índice local %d fuera de range %s' % (local, rng))
    return local


def binding_spec(run, result, result_key=None):
    """Identificación explícita resultado ↔ conversión ↔ fotograma."""
    setup = result.get('setup') or {}
    test = test_id_of(result, result_key)
    if not test:
        raise RdError('%s: test ID ausente' % run['id'])
    clip = setup.get('clip')
    rng = setup.get('range')
    if not clip or not rng:
        raise RdError('%s/%s: clip o range ausente' % (run['id'], test))
    key = expected_conversion_key(run, result)
    local = frame_local_index(result)
    return {
        'test': test,
        'clip': clip,
        'range': [int(rng[0]), int(rng[1])],
        'conversion_key': key,
        'frame': int(result.get('frame') if result.get('frame') is not None else setup.get('frame')),
        'local_index': local,
        'inputs_sha256': identity.sha256_json(setup.get('inputs')),
        'dng_sha256': setup.get('dng_sha256'),
        'reference_sha256': ((setup.get('reference') or {}).get('sha256')
                             if isinstance(setup.get('reference'), dict) else None),
    }


def _timing_matches(run, spec):
    """Candidatos de timing con el mismo test+range (sin elegir el primero a ciegas)."""
    timing = (run['run'].get('timing') or {}).get('conversions') or []
    hits = []
    for i, t in enumerate(timing):
        tr = t.get('range')
        if (t.get('test') == spec['test'] and tr and len(tr) == 2 and
                int(tr[0]) == spec['range'][0] and int(tr[1]) == spec['range'][1]):
            hits.append((i, t))
    return hits


def resolve_braw_binding(run, result, result_key=None, *, verify_file_sha=True):
    """Asociación inequívoca resultado → BRAW de caché.

    Primario: conversion_key reconstruido desde metadatos (clip, inputs SHA, request, env, worker).
    Histórico: si el key no está en cache[], exige coincidencia única test+range en timing
    alineada 1:1 con entradas braw; múltiples candidatos → error (nunca el primero arbitrario).
    """
    spec = binding_spec(run, result, result_key)
    key = spec['conversion_key']
    braw_entries = [c for c in (run['run'].get('cache') or []) if c.get('kind') == 'braw']
    key_hits = [c for c in braw_entries if c.get('key') == key]

    recovery = 'conversion_key'
    if len(key_hits) > 1:
        raise RdError('%s/%s: múltiples entradas de caché con conversion_key %s' % (
            run['id'], spec['test'], key[:12]))
    if len(key_hits) == 1:
        entry = key_hits[0]
    else:
        # Recuperación histórica inequívoca vía timing test+range
        recovery = 'timing_test_range'
        timing_hits = _timing_matches(run, spec)
        if not timing_hits:
            raise RdError('%s/%s: sin conversion_key %s en caché ni timing (test=%s range=%s)' % (
                run['id'], spec['test'], key[:12], spec['test'], spec['range']))
        if len(timing_hits) > 1:
            raise RdError('%s/%s: timing ambiguo: %d conversiones con test=%s range=%s' % (
                run['id'], spec['test'], len(timing_hits), spec['test'], spec['range']))
        timing = (run['run'].get('timing') or {}).get('conversions') or []
        if len(timing) != len(braw_entries):
            raise RdError('%s/%s: no se puede alinear timing (%d) con braw cache (%d)' % (
                run['id'], spec['test'], len(timing), len(braw_entries)))
        idx = timing_hits[0][0]
        entry = braw_entries[idx]
        if entry.get('key') != key:
            # El material reconstruido no coincide con la entrada alineada → no adivinar
            raise RdError(
                '%s/%s: conversion_key reconstruido %s ≠ caché alineada %s '
                '(test+range únicos pero material inconsistente)' % (
                    run['id'], spec['test'], key[:12], (entry.get('key') or '')[:12]))

    path = os.path.join(cache_root_for(run), 'braw', entry['key'][:24], 'clip.braw')
    if not os.path.isfile(path):
        raise RdError('%s/%s: falta BRAW %s' % (run['id'], spec['test'], path))

    identity_path = os.path.join(os.path.dirname(path), 'identity.json')
    if not os.path.isfile(identity_path):
        raise RdError('%s/%s: falta identity.json del BRAW' % (run['id'], spec['test']))
    with open(identity_path, encoding='utf-8') as f:
        saved = json.load(f)
    material = conversion_material_from_result(run, result)
    if saved.get('material') != material:
        raise RdError('%s/%s: identity.json.material ≠ material reconstruido' % (run['id'], spec['test']))
    expected_sha = saved.get('sha256')
    if not expected_sha:
        raise RdError('%s/%s: identity.json sin sha256 del BRAW' % (run['id'], spec['test']))
    if verify_file_sha:
        got = identity.sha256_file(path)
        if got != expected_sha:
            raise RdError('%s/%s: SHA BRAW %s ≠ identity %s' % (
                run['id'], spec['test'], got[:12], expected_sha[:12]))
    # Timing debe ser único para este test+range (aunque ya hayamos resuelto por key)
    timing_hits = _timing_matches(run, spec)
    if len(timing_hits) > 1:
        raise RdError('%s/%s: timing ambiguo pese a conversion_key' % (run['id'], spec['test']))

    return {
        **spec,
        'braw_path': path,
        'braw_cache_key': entry['key'],
        'braw_sha256': expected_sha,
        'recovery': recovery,
    }


def braw_path_for_result(run, result, result_key=None):
    """Compat: (path, conversion_key) inequívoco."""
    bind = resolve_braw_binding(run, result, result_key)
    return bind['braw_path'], bind['braw_cache_key']


def bitstream_frame(run, result, result_key=None, *, verify_file_sha=True):
    """q y tamaños del fotograma evaluado desde el bitstream (tras verificar identidad BRAW)."""
    bind = resolve_braw_binding(run, result, result_key, verify_file_sha=verify_file_sha)
    payloads = braw_inspect.frame_payloads(bind['braw_path'])
    local = bind['local_index']
    if local < 0 or local >= len(payloads):
        raise RdError('%s: índice local %d fuera de %d samples' % (run['id'], local, len(payloads)))
    f = payloads[local]
    payload_bytes = sum(f['slice_bytes'])
    container_bytes = os.path.getsize(bind['braw_path'])
    qs = [p['q'] for p in payloads]
    return {
        'braw_path': bind['braw_path'],
        'braw_cache_key': bind['braw_cache_key'],
        'braw_sha256': bind['braw_sha256'],
        'conversion_key': bind['conversion_key'],
        'test': bind['test'],
        'clip': bind['clip'],
        'range': bind['range'],
        'local_index': local,
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
        'recovery': bind['recovery'],
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


def frame_row(run, key, result, *, require_bitstream=True, verify_file_sha=True):
    rf = result.get('reference_fidelity') or {}
    n = rf.get('normalized') or {}
    raw_m = rf.get('raw') or {}
    sh = rf.get('sharpness') or {}
    lc = (rf.get('luma_chroma') or {}).get('edges') or {}
    neut = rf.get('neutral') or {}
    eb = rf.get('experimental_block') or {}
    comp = result.get('compression') or {}
    cfg = config_from_result(result, key)
    label = parse_exp002_id(run['id'])
    mismatch = label_matches_config(label, cfg)
    if cfg['unauthorized_env']:
        raise RdError('%s: experimental_env no autorizado: %s' % (run['id'], cfg['unauthorized_env']))

    bs = None
    if require_bitstream:
        bs = bitstream_frame(run, result, key, verify_file_sha=verify_file_sha)
        q_frame = bs['q_frame']
        sample_bytes = bs['sample_bytes']
        payload_bytes = bs['payload_bytes']
        container_bytes = bs['container_bytes']
        q_seg_min, q_seg_max, q_seg_mean = bs['segment_q_min'], bs['segment_q_max'], bs['segment_q_mean']
        braw_sha = bs['braw_sha256']
        conversion_key = bs['conversion_key']
        local_index = bs['local_index']
    else:
        q_frame = comp.get('q_min') if comp.get('q_min') == comp.get('q_max') else None
        sample_bytes = payload_bytes = container_bytes = None
        q_seg_min = comp.get('q_min')
        q_seg_max = comp.get('q_max')
        q_seg_mean = None
        braw_sha = conversion_key = local_index = None

    q_req = cfg['q_requested']
    q_bumped = (q_req is not None and q_frame is not None and q_frame != q_req)
    q_saturated = (q_frame == QMAX) if q_frame is not None else False
    codec_range_fallback = bool(q_req is not None and q_frame is not None and q_frame > q_req)

    sdk_frame_bytes = comp.get('frame_bytes')
    ratio_sample = (RAW_BYTES_12BIT / sample_bytes) if sample_bytes else None
    ratio_payload = (RAW_BYTES_12BIT / payload_bytes) if payload_bytes else None
    ratio_sdk = (RAW_BYTES_12BIT / sdk_frame_bytes) if sdk_frame_bytes else None
    bits_per_site_sample = (8.0 * sample_bytes / PHOTOSITES_1080P) if sample_bytes else None
    bits_per_site_payload = (8.0 * payload_bytes / PHOTOSITES_1080P) if payload_bytes else None
    store_bytes = sample_bytes if sample_bytes is not None else sdk_frame_bytes

    return {
        'run': run['id'], 'result': key, 'test': cfg['test'], 'frame': cfg['frame'],
        'clip': cfg['clip'], 'range': cfg['range'],
        'conversion_key': conversion_key, 'braw_sha256': braw_sha, 'local_index': local_index,
        'label_mismatch': mismatch,
        'mode': cfg['mode'], 'alpha': cfg['alpha'], 'tag': cfg['tag'], 'bounded': cfg['bounded'],
        'q_requested': q_req, 'ratio_requested': cfg['ratio_requested'],
        'q_frame': q_frame, 'q_segment_min': q_seg_min, 'q_segment_max': q_seg_max,
        'q_segment_mean': q_seg_mean, 'q_eff': q_frame,
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
        'bits_per_site': bits_per_site_sample,
        'frame_bytes': store_bytes,
        'frame_ratio': ratio_sample,
        'mb_s_24fps': (store_bytes * 24 / 1e6) if store_bytes else None,
        'gb_h_24fps': (store_bytes * 24 * 3600 / 1e9) if store_bytes else None,
        'iso': cfg['iso'],
        'inputs_sha': identity.sha256_json(cfg['inputs']) if cfg.get('inputs') is not None else None,
        'reference_sha256': cfg.get('reference_sha256'),
        'dng_sha256': cfg.get('dng_sha256'),
        'decode': cfg['decode'],
        'candidate_sha256': result.get('candidate_sha256'),
        'worker_sha': (run['run'].get('converter') or {}).get('cdng_convert_sha256'),
    }


def collect(runs, *, require_bitstream=True, require_label_match=True, verify_file_sha=True):
    rows = []
    identities = []
    for run in runs:
        rid = run_identity(run)
        identities.append(rid)
        seconds = (run['run'].get('timing') or {}).get('total_seconds')
        fresh = [c for c in (run['run'].get('timing') or {}).get('conversions', []) if not c.get('reused')]
        for key, result in sorted(run['metrics'].get('results', {}).items()):
            row = frame_row(run, key, result, require_bitstream=require_bitstream,
                            verify_file_sha=verify_file_sha)
            if require_label_match and row['label_mismatch']:
                raise RdError('%s / %s: etiqueta incompatible: %s' % (run['id'], key, '; '.join(row['label_mismatch'])))
            row['run_seconds'] = seconds
            row['fresh_convert_seconds'] = sum(c.get('seconds') or 0 for c in fresh) or None
            rows.append(row)
    return rows, identities


COMPARABLE_KEYS = (
    'cdng_convert_sha256', 'model_sha256', 'brawdec_sha256', 'framework_sha256',
    'camera_support_sha256', 'analysis_sha256', 'rois_sha256',
    'decode_defaults_sha256', 'encode_nonexperimental_sha256',
)


def assert_comparable_runs(runs):
    """Misma identidad de binarios/análisis/ROI/encode fijo; ausencia = error."""
    if not runs:
        return
    base = run_identity(runs[0])
    for run in runs[1:]:
        cur = run_identity(run)
        for k in COMPARABLE_KEYS:
            if base[k] != cur[k]:
                raise RdError('identidad incompatible en %s: %s distinto' % (run['id'], k))


def coverage_report(rows, expected_q=None, expected_alphas=None, expected_frames=None,
                    expected_ratios=None, expected_test_frames=None):
    """Cobertura por (test_id, frame), no solo número de frame.

    expected_test_frames: lista de (test, frame). Si no se pasa, se deduce de las filas.
    expected_frames (legacy): números de frame; se combina con tests observados.
    """
    seen_q = defaultdict(list)
    seen_r = defaultdict(list)
    for r in rows:
        tf = (r.get('test'), r.get('frame'))
        if r.get('mode') == 'quality':
            seen_q[(r.get('alpha'), r.get('q_requested'), tf)].append(r['run'])
        elif r.get('mode') == 'ratio':
            seen_r[(r.get('alpha'), r.get('ratio_requested'), tf)].append(r['run'])

    if expected_test_frames is not None:
        test_frames = [(t, int(f)) for t, f in expected_test_frames]
    elif expected_frames is not None:
        tests = sorted({r.get('test') for r in rows if r.get('test')})
        test_frames = [(t, int(f)) for t in tests for f in expected_frames]
    else:
        test_frames = sorted({(r.get('test'), r.get('frame')) for r in rows
                              if r.get('test') is not None and r.get('frame') is not None})

    missing, dupes = [], []
    if expected_q is not None:
        alphas = expected_alphas if expected_alphas is not None else [1.0, 0.5, 0.75]
        for a in alphas:
            for q in expected_q:
                for tf in test_frames:
                    k = (a, q, tf)
                    if k not in seen_q:
                        missing.append({'alpha': a, 'q': q, 'test': tf[0], 'frame': tf[1], 'mode': 'quality'})
                    elif len(seen_q[k]) > 1:
                        dupes.append({'alpha': a, 'q': q, 'test': tf[0], 'frame': tf[1], 'runs': seen_q[k]})
    else:
        for k, runs in seen_q.items():
            if len(runs) > 1:
                dupes.append({'alpha': k[0], 'q': k[1], 'test': k[2][0], 'frame': k[2][1], 'runs': runs})
    if expected_ratios is not None:
        alphas = expected_alphas if expected_alphas is not None else [1.0, 0.5, 0.75]
        for a in alphas:
            for ratio in expected_ratios:
                for tf in test_frames:
                    k = (a, ratio, tf)
                    if k not in seen_r:
                        missing.append({'alpha': a, 'ratio': ratio, 'test': tf[0], 'frame': tf[1], 'mode': 'ratio'})
                    elif len(seen_r[k]) > 1:
                        dupes.append({'alpha': a, 'ratio': ratio, 'test': tf[0], 'frame': tf[1],
                                      'runs': seen_r[k]})
    q0_bumps = [r for r in rows if r.get('q_requested') == 0 and r.get('codec_range_fallback')]
    return {'missing': missing, 'duplicates': dupes, 'q0_codec_range_fallbacks': [
        {'run': r['run'], 'test': r.get('test'), 'frame': r['frame'], 'q_frame': r['q_frame']}
        for r in q0_bumps
    ]}


def _normalize_row_keys(r):
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


def _series_bounds(series_list, x_key, y_key):
    xs, ys = [], []
    for pts in series_list:
        for p in pts:
            if p.get(x_key) is not None and p.get(y_key) is not None:
                xs.append(p[x_key])
                ys.append(p[y_key])
    if not xs:
        return None
    xmin, xmax = min(xs), max(xs)
    ymin, ymax = min(ys), max(ys)
    if xmax == xmin:
        xmax = xmin + 1
    if ymax == ymin:
        ymax = ymin + 1e-9
    return xmin, xmax, ymin, ymax


def _svg_overlay(series, width, height, pad, x_key, y_key, colors):
    """Una gráfica con varias series (α) en la misma escala."""
    bounds = _series_bounds([pts for _, pts in series], x_key, y_key)
    if bounds is None:
        return '<p>sin datos</p>'
    xmin, xmax, ymin, ymax = bounds

    def sx(x):
        return pad + (x - xmin) / (xmax - xmin) * (width - 2 * pad)

    def sy(y):
        return height - pad - (y - ymin) / (ymax - ymin) * (height - 2 * pad)

    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d">'
        '<rect width="%d" height="%d" fill="#1a1a1a"/>' % (width, height, width, height, width, height)
    ]
    legend_y = 14
    for label, pts in series:
        color = colors.get(label, '#ffffff')
        valid = [(p[x_key], p[y_key]) for p in pts
                 if p.get(x_key) is not None and p.get(y_key) is not None]
        if not valid:
            continue
        pts_s = ' '.join('%.1f,%.1f' % (sx(x), sy(y)) for x, y in valid)
        parts.append('<polyline fill="none" stroke="%s" stroke-width="2" points="%s"/>' % (color, pts_s))
        parts.append(''.join(
            '<circle cx="%.1f" cy="%.1f" r="2.5" fill="%s"/>' % (sx(x), sy(y), color) for x, y in valid))
        parts.append(
            '<text x="%d" y="%d" fill="%s" font-size="11">α=%s</text>' % (
                width - pad - 70, legend_y, color, label))
        legend_y += 14
    parts.append(
        '<text x="%d" y="%d" fill="#aaa" font-size="11">%.3g</text>'
        '<text x="%d" y="%d" fill="#aaa" font-size="11">%.3g</text>'
        '<text x="%d" y="%d" fill="#aaa" font-size="11">%.3g</text>'
        '<text x="%d" y="%d" fill="#aaa" font-size="11">%.3g</text>'
        '</svg>' % (
            pad, height - 4, xmin, width - pad - 40, height - 4, xmax,
            4, pad + 4, ymax, 4, height - pad, ymin,
        )
    )
    return ''.join(parts)


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
        series = []
        for a, rows in sorted(by_alpha.items()):
            pts = [{'q': r.get('q'), 'bits_per_site': r.get('bits_per_site'),
                    'psnr': r.get('psnr'), 'duv_edges': r.get('duv_edges'),
                    'grad': r.get('grad'), 'lap': r.get('lap'), 'block_bmi': r.get('block_bmi')}
                   for r in rows]
            series.append((a, pts))
        svg = _svg_overlay(series, 720, 280, 40, xk, yk, colors)
        n_pts = sum(1 for _, pts in series for p in pts
                    if p.get(xk) is not None and p.get(yk) is not None)
        sections.append(
            '<div class="card"><h2>%s</h2><p>%d puntos · escala común · curvas superpuestas</p>%s</div>' % (
                heading, n_pts, svg))

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
        'Curvas superpuestas con escala común. Celdas «—» = métrica ausente.</p>\n'
        + ''.join(sections) + ''.join(heat) + ''.join(table) +
        '\n</body></html>\n'
    )
    with open(path, 'w', encoding='utf-8') as f:
        f.write(html)


def build_report(run_dirs, dest, analysis_cfg=None, expected=None, *, verify_file_sha=True):
    os.makedirs(dest, exist_ok=True)
    runs = [load_run(d) for d in run_dirs]
    assert_comparable_runs(runs)
    rows, identities = collect(runs, require_bitstream=True, require_label_match=True,
                               verify_file_sha=verify_file_sha)
    cov = coverage_report(rows, **(expected or {}))
    if cov['duplicates']:
        raise RdError('configuraciones duplicadas antes de agregar: %s' % cov['duplicates'][:3])
    if (expected or {}).get('expected_q') is not None and cov['missing']:
        raise RdError('cobertura incompleta: %d celdas faltantes (p.ej. %s)' % (
            len(cov['missing']), cov['missing'][:3]))
    if (expected or {}).get('expected_ratios') is not None:
        ratio_missing = [m for m in cov['missing'] if m.get('mode') == 'ratio']
        if ratio_missing:
            raise RdError('cobertura de ratios incompleta: %s' % ratio_missing[:3])
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
            'braw_binding': 'conversion_key desde material (test/clip/range/inputs/request/env/worker)',
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


def verify_run_matches_request(run_dir, *, q=None, ratio=None, alpha=None, bounded=False, ablation=False,
                               expected_identity=None, deadzone=None, policy_sha=None,
                               rdoq=None, rdoq_lambda=None):
    """Para --skip-existing: todos los resultados deben coincidir con la config pedida."""
    run = load_run(run_dir)
    results = run['metrics'].get('results') or {}
    if not results:
        raise RdError('%s: metrics.json sin results' % run_dir)
    rid = run_identity(run)  # exige identidades críticas
    if expected_identity:
        for k in COMPARABLE_KEYS:
            if expected_identity.get(k) is not None and rid[k] != expected_identity[k]:
                raise RdError('%s: %s no coincide con la identidad solicitada' % (run_dir, k))

    for rk, res in sorted(results.items()):
        cfg = config_from_result(res, rk)
        env = cfg.get('experimental_env') or {}
        if ablation:
            if cfg['tag'] != 'ablation':
                raise RdError('%s/%s: se esperaba ablación' % (run_dir, rk))
        elif bounded:
            if not cfg['bounded']:
                raise RdError('%s/%s: se esperaba bounded' % (run_dir, rk))
        elif alpha is not None and cfg['alpha'] is not None:
            if abs(cfg['alpha'] - float(alpha)) > 1e-9:
                raise RdError('%s/%s: alpha efectivo %s ≠ %s' % (run_dir, rk, cfg['alpha'], alpha))
        if q is not None:
            if cfg['mode'] != 'quality' or cfg['q_requested'] != int(q):
                raise RdError('%s/%s: q solicitado efectivo %s ≠ %s' % (run_dir, rk, cfg['q_requested'], q))
        if ratio is not None:
            if cfg['mode'] != 'ratio' or float(cfg['ratio_requested']) != float(ratio):
                raise RdError('%s/%s: ratio solicitado efectivo %s ≠ %s' % (
                    run_dir, rk, cfg['ratio_requested'], ratio))
        if deadzone is not None:
            got = env.get('CDNG_DEADZONE')
            if got is None or abs(float(got) - float(deadzone)) > 1e-12:
                raise RdError('%s/%s: CDNG_DEADZONE efectivo %s ≠ %s' % (run_dir, rk, got, deadzone))
        if policy_sha is not None:
            pol = env.get('CDNG_DEADZONE_POLICY') or ''
            if policy_sha not in pol:
                raise RdError('%s/%s: política SHA efectiva %r no contiene %s' % (run_dir, rk, pol, policy_sha))
        if rdoq is not None:
            got = env.get('CDNG_RDOQ')
            if rdoq and got not in ('1', 'on', 'true', 'yes'):
                raise RdError('%s/%s: CDNG_RDOQ efectivo %s ≠ on' % (run_dir, rk, got))
            if not rdoq and got not in (None, '0', 'off', 'false', 'no'):
                raise RdError('%s/%s: se esperaba RDOQ off, got %s' % (run_dir, rk, got))
        if rdoq_lambda is not None:
            got = env.get('CDNG_RDOQ_LAMBDA')
            if got is None or abs(float(got) - float(rdoq_lambda)) > 1e-12:
                raise RdError('%s/%s: CDNG_RDOQ_LAMBDA efectivo %s ≠ %s' % (
                    run_dir, rk, got, rdoq_lambda))
        # Identidades por fotograma: entradas y referencia por SHA, no por ruta
        if not cfg.get('inputs'):
            raise RdError('%s/%s: inputs ausentes' % (run_dir, rk))
        for inp in cfg['inputs']:
            if not inp.get('sha256'):
                raise RdError('%s/%s: input sin sha256' % (run_dir, rk))
        if cfg.get('reference') is not None and not cfg.get('reference_sha256'):
            raise RdError('%s/%s: referencia sin sha256' % (run_dir, rk))
        if not cfg.get('dng_sha256'):
            raise RdError('%s/%s: dng_sha256 ausente' % (run_dir, rk))
        if not cfg.get('decode'):
            raise RdError('%s/%s: decode ausente' % (run_dir, rk))
        # Binding BRAW inequívoco
        resolve_braw_binding(run, res, rk, verify_file_sha=True)
        f32 = os.path.join(run['dir'], 'frames', rk + '.f32')
        if not os.path.isfile(f32):
            raise RdError('%s: falta %s' % (run_dir, f32))
        if res.get('candidate_sha256'):
            got = identity.sha256_file(f32)
            if got != res['candidate_sha256']:
                raise RdError('%s: sha f32 ≠ candidate_sha256' % rk)
    return run
