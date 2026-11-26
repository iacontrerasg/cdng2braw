#!/usr/bin/env python3
"""Agrega EXP-005: aislamiento AHD, inyección inversa, matriz LibRaw, pre-encode vs post-SDK."""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import math
import os
import subprocess
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.exp004_g import CROPS, HALO_EDGES, disp, write_png
from tools.exp005_color import (camera_to_p3, compare_camera_to_s9, invert_s9_to_camera,
                                known_color_checks, read_dng_color)
from tools.exp005_metrics import HALO_UNITS, pair_metrics
from tools.exp005_sweep import ALGOS, FINAL_ALGOS, FRAMES, MAN_DIR, SERIES_A, write_manifest
from tools.fidelity import braw_inspect, images, metrics as M, rd as RD, stage_io, viz

SUMMARY_PATH = os.path.join(ROOT, 'benchmark', 'experiments', 'exp005-results-summary.json')


def _sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def _f(x):
    return M._f(x)


LIBRAW_MATRIX = {
    'libraw_version': '0.22.3 (Homebrew bottle, libraw_r)',
    'license_libraw': 'LGPL-2.1-only OR CDDL-1.0',
    'algorithms': [
        {'name': 'linear', 'user_qual': 0, 'symbol': 'lin_interpolate', 'in_this_build': True,
         'notes': 'dcraw linear; control, no candidato'},
        {'name': 'vng', 'user_qual': 1, 'symbol': 'vng_interpolate', 'in_this_build': True},
        {'name': 'ppg', 'user_qual': 2, 'symbol': 'ppg_interpolate', 'in_this_build': True},
        {'name': 'ahd', 'user_qual': 3, 'symbol': 'ahd_interpolate', 'in_this_build': True,
         'notes': 'default histórico; bit-idéntico si CDNG_OG_DEBAYER ausente'},
        {'name': 'dcb', 'user_qual': 4, 'symbol': 'dcb_interpolate', 'in_this_build': False,
         'notes': 'símbolo ausente en libraw_r.dylib; FALLBACK_TO_AHD → rechazo'},
        {'name': 'dht', 'user_qual': 11, 'symbol': 'dht_interpolate', 'in_this_build': True},
        {'name': 'aahd', 'user_qual': 12, 'symbol': 'aahd_interpolate', 'in_this_build': True,
         'notes': 'AHD modificado (Petrusevich)'},
    ],
    'later_candidates_not_in_build': [
        {'name': 'AMaZE', 'license': 'GPL (RawTherapee); no en LibRaw 0.22.3', 'action': 'no incorporar en EXP-005'},
        {'name': 'RCD', 'license': 'típicamente GPL (RawTherapee); símbolo rcd_interpolate ausente',
         'action': 'candidato posterior'},
        {'name': 'LMMSE', 'license': 'Zhang–Wu / implementaciones RT GPL; no enlazado',
         'action': 'candidato posterior'},
    ],
}


def load_stage(args, run_id):
    d = os.path.join(args.out, 'experiments', 'exp005', 'stages', run_id, 'frame_0')
    if not os.path.isdir(d):
        return None
    return stage_io.load_frame_dir(d)


def plane(dump, key):
    if not dump or key not in dump:
        return None
    return dump[key]['array']


def dng_path_from_run(run_dir, dataset):
    m = os.path.join(run_dir, 'metrics.json')
    if not os.path.isfile(m):
        return None
    doc = json.load(open(m, encoding='utf-8'))
    for res in (doc.get('results') or {}).values():
        setup = res.get('setup') or {}
        clip = setup.get('clip') or ''
        ins = setup.get('inputs') or []
        for inp in ins:
            p = inp.get('path') or inp.get('file')
            name = inp.get('name')
            if p and str(p).lower().endswith('.dng') and os.path.isfile(p):
                return p
            if name:
                cand = os.path.join(dataset, clip, name) if clip else os.path.join(dataset, name)
                if os.path.isfile(cand):
                    return cand
                cand2 = os.path.join(dataset, name)
                if os.path.isfile(cand2):
                    return cand2
    return None


def ref_exr(run_dir, refs):
    m = os.path.join(run_dir, 'metrics.json')
    if not os.path.isfile(m):
        return None, 0.0
    doc = json.load(open(m, encoding='utf-8'))
    for res in (doc.get('results') or {}).values():
        setup = res.get('setup') or {}
        fn = setup.get('reference')
        dec = (setup.get('decode') or {}).get('exposure')
        if isinstance(fn, dict):
            if dec is None:
                dec = (fn.get('decode') or {}).get('exposure')
            fn = fn.get('file') or fn.get('path')
        if fn:
            p = os.path.join(refs, os.path.basename(str(fn)))
            if os.path.isfile(p):
                return p, float(dec or 0)
    return None, 0.0


def crop(img, box):
    x, y, w, h = box
    return img[y:y + h, x:x + w]


def analyze_run(args, run_id, test, frame, algo, write_contact=False):
    run_dir = os.path.join(args.out, 'runs', run_id)
    out = {'run_id': run_id, 'test': test, 'frame': frame, 'algo': algo, 'present': False, 'q': None}
    if not os.path.isfile(os.path.join(run_dir, 'metrics.json')):
        return out
    out['present'] = True
    run = RD.load_run(run_dir)
    dump = load_stage(args, run_id)
    s1 = plane(dump, 'S1_rgb')
    s2 = plane(dump, 'S2_rgb')
    if s1 is not None:
        out['s1_sha256'] = hashlib.sha256(np.ascontiguousarray(s1).tobytes()).hexdigest()
        out['s1_shape'] = list(s1.shape)
    if s2 is not None:
        out['s2_sha256'] = hashlib.sha256(np.ascontiguousarray(s2).tobytes()).hexdigest()
    cand = None
    for key, res in (run['metrics'].get('results') or {}).items():
        f32 = os.path.join(run_dir, 'frames', key + '.f32')
        if os.path.isfile(f32):
            cand = images.read_f32(f32)
            rf = res.get('reference_fidelity') or {}
            norm = rf.get('normalized') or {}
            rawb = rf.get('raw') or {}
            align = rf.get('alignment') or {}
            edges = rf.get('edges') or {}
            inside = edges.get('inside') or {}
            out['sdk'] = {
                'psnr': norm.get('psnr_db') or rawb.get('psnr_db'),
                'detail_corr': align.get('detail_correlation'),
                'duv_edges': inside.get('duv_mean') or (rf.get('luma_chroma') or {}).get('duv_edges'),
                'mae': norm.get('mae'),
            }
            out['timing'] = (run['run'].get('timing') or {})
            out['convert_seconds'] = None
            for c in (out['timing'].get('conversions') or []):
                if c.get('seconds') is not None:
                    out['convert_seconds'] = c.get('seconds')
                    break
            out['total_seconds'] = out['timing'].get('total_seconds')
            out['memory'] = {'rss_bytes': None, 'note': 'el banco no instrumenta RSS; solo tiempo de convert/decode'}
            br = res.get('bitstream') or {}
            out['sample_bytes'] = br.get('sample_bytes')
            out['result_key'] = key
            req = ((res.get('setup') or {}).get('encode') or {}).get('request') or {}
            out['q'] = req.get('q')
            out['hashes'] = {
                'dng_metrics': (res.get('setup') or {}).get('dng_sha256'),
                'exr_metrics': ((res.get('setup') or {}).get('reference') or {}).get('sha256'),
                'sdk_metrics': res.get('candidate_sha256'),
            }
            break
    exr_p, dec_exp = ref_exr(run_dir, args.references)
    ref = None
    if exr_p:
        ref, _ = images.read_exr(exr_p)
        out['exr'] = os.path.basename(exr_p)
        out['exr_decode_exposure'] = dec_exp
    dng = dng_path_from_run(run_dir, args.dataset)
    if dng and os.path.isfile(dng):
        out['dng'] = dng
        try:
            out['dng_color'] = {k: v for k, v in read_dng_color(dng).items() if k != 'path'}
        except Exception as e:
            out['dng_color_error'] = str(e)
    if cand is not None and ref is not None:
        out['post_sdk'] = pair_metrics(cand.astype(np.float64), ref.astype(np.float64), HALO_EDGES)
        if write_contact:
            vis = os.path.join(args.out, 'experiments', 'exp005', 'contact', run_id)
            os.makedirs(vis, exist_ok=True)
            write_png(os.path.join(vis, 'sdk_display.png'), disp(cand))
            write_png(os.path.join(vis, 'ref_display.png'), disp(ref))
            write_png(os.path.join(vis, 'residual_abs.png'), viz.abs_error_map(ref, cand, 0.05))
            write_png(os.path.join(vis, 'residual_chroma.png'), viz.chroma_error_map(ref, cand, 0.02, 0.005))
            for name, box in CROPS.items():
                write_png(os.path.join(vis, 'crop_%s.png' % name), disp(crop(cand, box)))
                write_png(os.path.join(vis, 'crop_%s_ref.png' % name), disp(crop(ref, box)))
    if s2 is not None and ref is not None and out.get('dng_color'):
        meta = read_dng_color(dng)
        try:
            out['a1_s2_vs_s9'] = compare_camera_to_s9(s2, ref, meta, exr_decode_exposure=dec_exp,
                                                      roi=CROPS.get('white_strip'))
        except Exception as e:
            out['a1_error'] = str(e)
    if s1 is not None and s2 is not None:
        pre = {'s1_zippering': zippering_only(s1), 's2_clip': clip_only(s2)}
        if s1.shape[0] > 8 and s2.shape == (1080, 1920, 3):
            pre['s1_zippering'] = zippering_only(s1)
        out['pre_encode'] = pre
    hashes = out.setdefault('hashes', {})
    if dng and os.path.isfile(dng):
        hashes['dng_file'] = _sha(dng)
        hashes['dng_match'] = hashes.get('dng_metrics') == hashes['dng_file']
    if exr_p and os.path.isfile(exr_p):
        hashes['exr_file'] = _sha(exr_p)
        hashes['exr_match'] = hashes.get('exr_metrics') == hashes['exr_file']
    f32s = glob.glob(os.path.join(run_dir, 'frames', '*.f32'))
    if f32s:
        hashes['sdk_file'] = _sha(f32s[0])
        hashes['sdk_match'] = hashes.get('sdk_metrics') == hashes['sdk_file']
    if dump:
        for key in ('S1_rgb', 'S2_rgb'):
            if key not in dump:
                continue
            meta = dump[key].get('meta') or {}
            p = meta.get('bin')
            if p and os.path.isfile(p):
                hashes[key + '_dump'] = meta.get('sha256') or _sha(p)
            else:
                hashes[key + '_array'] = hashlib.sha256(
                    np.ascontiguousarray(dump[key]['array']).tobytes()).hexdigest()
    return out


def zippering_only(rgb):
    from tools.exp005_metrics import zippering
    return zippering(rgb)


def clip_only(rgb):
    from tools.exp005_metrics import clipping
    return clipping(rgb)


def _braw_path(run_dir):
    run = RD.load_run(run_dir)
    for key, res in (run['metrics'].get('results') or {}).items():
        bind = RD.resolve_braw_binding(run, res, key, verify_file_sha=False)
        return bind['braw_path']
    return None


def identity_check(args):
    a = os.path.join(args.out, 'runs', 'exp005-ident-default')
    b = os.path.join(args.out, 'runs', 'exp005-ident-ahd')
    out = {'default_present': os.path.isdir(a), 'ahd_present': os.path.isdir(b)}
    fa = glob.glob(os.path.join(a, 'frames', '*.f32'))
    fb = glob.glob(os.path.join(b, 'frames', '*.f32'))
    if fa and fb:
        out['sdk_f32_identical'] = _sha(fa[0]) == _sha(fb[0])
        out['sha_default'] = _sha(fa[0])
        out['sha_ahd'] = _sha(fb[0])
    sa = load_stage(args, 'exp005-ident-default')
    sb = load_stage(args, 'exp005-ident-ahd')
    if sa and sb and 'S1_rgb' in sa and 'S1_rgb' in sb:
        out['s1_identical'] = bool(np.array_equal(sa['S1_rgb']['array'], sb['S1_rgb']['array']))
    try:
        pa, pb = _braw_path(a), _braw_path(b)
        if pa and pb:
            eq = braw_inspect.equivalence(pa, pb, out.get('sha_default'), out.get('sha_ahd'))
            out['payload_equal'] = eq.get('payload_equal')
            out['decode_relevant_meta_equal'] = eq.get('decode_relevant_meta_equal')
            out['payloads_sha256_a'] = braw_inspect.media_fingerprint(pa).get('payloads_sha256')
            out['payloads_sha256_b'] = braw_inspect.media_fingerprint(pb).get('payloads_sha256')
            out['braw_path_a'] = pa
            out['braw_path_b'] = pb
    except Exception as e:
        out['payload_error'] = str(e)
    return out


def ahd_isolation(rows):
    """¿Los S1 de algoritmos distintos difieren? ¿El halo post-SDK persiste en todos?"""
    by = {}
    for r in rows:
        if not r.get('present'):
            continue
        by.setdefault((r['test'], r['frame']), {})[r['algo']] = r
    out = []
    for key, g in by.items():
        shas = {a: g[a].get('s1_sha256') for a in g if g[a].get('s1_sha256')}
        distinct = len(set(shas.values()))
        peaks = {a: ((g[a].get('post_sdk') or {}).get('halo') or {}).get('peak') for a in g}
        out.append({
            'test': key[0], 'frame': key[1],
            's1_sha_by_algo': shas,
            'distinct_s1': distinct,
            'algos_actually_differ': distinct > 1,
            'halo_peak_by_algo': peaks,
        })
    return out


def injection_result(args, rows):
    """Invierte S9→cámara, valida roundtrip, opcionalmente codifica el inject."""
    known = known_color_checks()
    ahd = next((r for r in rows if r.get('algo') == 'ahd' and r.get('test') == 'A04'
                and str(r.get('frame')) == '44' and r.get('present')), None)
    result = {'known_colors': known, 'attempted': False}
    if not ahd or not ahd.get('dng') or 'a1_s2_vs_s9' not in ahd:
        result['limitation'] = 'Falta dump S2 + EXR + DNG de A04_f044 AHD para materializar la inversa'
        return result
    a1 = ahd['a1_s2_vs_s9']
    result['attempted'] = True
    result['roundtrip_s2'] = a1.get('roundtrip_s2')
    result['chromaticity_be'] = a1.get('chromaticity_be')
    reliable = bool(a1.get('roundtrip_reliable'))
    duv = a1.get('chromaticity_be', {}).get('duv_mean')
    ratio = a1.get('diagnostic_median_y_s9_over_p3_be')
    luma_off = (not isinstance(ratio, (int, float))) or ratio <= 0 or abs(math.log2(ratio)) > 0.5
    develop_mismatch = luma_off or (isinstance(duv, float) and duv > 0.02) or duv == 'nan'
    result['inverse_reliable_for_isolation'] = reliable and not develop_mismatch
    result['luma_ratio_ok'] = not luma_off
    result['reason'] = (
        'Roundtrip lineal de cameraToP3 es numérico; el revelado Resolve (WB/tono/clip/ISO/exposición EXR) '
        'no coincide con cameraToP3(S2)×2^BE. La inyección se genera como ensayo, no como prueba concluyente.'
        if develop_mismatch else
        'Roundtrip numérico OK; Δu′v′ S2→P3 vs S9 lo bastante baja para usar la inversa como ensayo.'
    )
    dump = load_stage(args, ahd['run_id'])
    s2 = plane(dump, 'S2_rgb')
    exr_p, _ = ref_exr(os.path.join(args.out, 'runs', ahd['run_id']), args.references)
    if s2 is None or not exr_p:
        return result
    s9, _ = images.read_exr(exr_p)
    meta = read_dng_color(ahd['dng'])
    dec = float(ahd.get('exr_decode_exposure') or 0)
    cam, inv = invert_s9_to_camera(s9, meta, apply_baseline=True, exr_decode_exposure=dec)
    inj_dir = os.path.join(args.out, 'experiments', 'exp005', 'inject')
    os.makedirs(inj_dir, exist_ok=True)
    inj_path = os.path.join(inj_dir, 'a04_f044_s9_to_camera.f32')
    images.write_f32(inj_path, cam)
    result['inject_path'] = inj_path
    result['inject_sha256'] = _sha(inj_path)
    result['inject_range'] = {'min': _f(cam.min()), 'max': _f(cam.max()),
                              'neg_frac': _f(np.mean(cam < 0)), 'above_one': _f(np.mean(cam > 1))}
    # Roundtrip S9 → cam → P3
    fwd = camera_to_p3(meta['cm1'], meta['cm2'], meta['k1'], meta['k2'], meta['as_shot_neutral'])
    back = (cam.astype(np.float64) @ fwd.T) * meta['baseline_gain'] * (2.0 ** dec)
    result['s9_roundtrip_rmse'] = _f(np.sqrt(np.mean((back - s9) ** 2)))
    run_id = 'exp005-inject-s9inv-a04-f44-q2'
    if args.encode_inject:
        man = os.path.join(MAN_DIR, run_id + '.json')
        write_manifest(man, q=2, inject_rgb=inj_path, inject_at='pre_map',
                       notes='A2 Resolve-derived camera RGB',
                       diagnostic={
                           'CDNG_DUMP_STAGES': os.path.abspath(
                               os.path.join(args.out, 'experiments', 'exp005', 'stages', run_id)),
                           'CDNG_DUMP_FRAME': '0', 'CDNG_ALIGN_SOFT': '1',
                       })
        cmd = [sys.executable, '-m', 'tools.fidelity', 'run',
               '--dataset', args.dataset, '--references', args.references,
               '--out', args.out, '--converter', args.converter, '--brawdec', args.brawdec,
               '--run', run_id, '--manifest', man, '--test', 'A04', '--frames', '44']
        if args.no_cache:
            cmd.append('--no-cache')
        print('+', ' '.join(cmd), flush=True)
        r = subprocess.run(cmd, cwd=ROOT)
        result['encode_returncode'] = r.returncode
    else:
        result['encode_skipped'] = True
    result['inject_run'] = run_id
    inj_metrics = os.path.join(args.out, 'runs', run_id, 'metrics.json')
    if os.path.isfile(inj_metrics):
        inj_row = analyze_run(args, run_id, 'A04', '44', 'inject-s9inv')
        result['inject_post_sdk'] = inj_row.get('post_sdk')
        result['inject_sdk'] = inj_row.get('sdk')
        ahd_peak = ((ahd.get('post_sdk') or {}).get('halo') or {}).get('peak')
        inj_peak = ((inj_row.get('post_sdk') or {}).get('halo') or {}).get('peak')
        result['halo_persists_without_ahd'] = {
            'ahd_peak': ahd_peak, 'inject_peak': inj_peak,
            'note': 'Si el pico de halo del inject ≈ AHD, el defecto no es exclusivo de AHD. '
                    'Si el inject no es fiable (develop mismatch), esto no es concluyente.',
        }
    result['inv_matrix'] = inv.reshape(-1).tolist()
    return result


def recommend(rows, isolation, injection):
    present = [r for r in rows if r.get('present')]
    if not present:
        return {'class': 'E', 'select_for_final': None,
                'reason': 'Campaña sin métricas (¿dataset/EXR/converter?)'}
    algos_ran = sorted({r['algo'] for r in present})
    isolation_ok = any(x.get('algos_actually_differ') for x in isolation)
    halo_all = []
    for r in present:
        p = ((r.get('post_sdk') or {}).get('halo') or {}).get('peak')
        if isinstance(p, (int, float)):
            halo_all.append((r['algo'], r['test'], r['frame'], p))
    inj_reliable = injection.get('inverse_reliable_for_isolation')
    persists = injection.get('halo_persists_without_ahd') or {}
    rec = {
        'algos_evaluated': algos_ran,
        's1_hashes_differ': isolation_ok,
        'injection_conclusive': bool(inj_reliable),
        'do_not_pick_by_psnr': True,
        'defaults_unchanged': True,
        'next': 'Campaña final sobre VNG/PPG/DHT/AAHD + AHD, mismos fotogramas, q2 y q18, C0; '
                'no escribir un debayer propio; no EXP-006/007/008 ni FEAT-001.',
    }
    if not isolation_ok:
        rec['class'] = 'E'
        rec['reason'] = 'No se verificó que los algoritmos produzcan S1 distintos'
        rec['select_for_final'] = ['ahd']
        return rec
    # Si el inject fiable elimina el halo, AHD es causa; si no, codec/subsample.
    if inj_reliable and persists.get('inject_peak') is not None and persists.get('ahd_peak') is not None:
        try:
            if float(persists['inject_peak']) < 0.5 * float(persists['ahd_peak']):
                rec['ahd_is_primary_cause'] = True
                rec['class'] = 'C'
                rec['select_for_final'] = [a for a in algos_ran if a != 'linear']
                rec['reason'] = 'Inyección Reduce halo vs AHD; demosaic merece campaña final'
                return rec
        except (TypeError, ValueError):
            pass
        rec['ahd_is_primary_cause'] = False
        rec['class'] = 'D'
        rec['reason'] = (
            'Halo persiste sin AHD (inject de S9→cámara con transformación documentada, Δu′v′ media baja). '
            'BUG-002 no se absuelve cambiando el demosaic. Campaña final: no escribir debayer propio; '
            'si se mira VNG/AAHD es solo por zippering/false color en S1, no como arreglo del halo.'
        )
        rec['select_for_final'] = [a for a in ('ahd', 'vng', 'aahd') if a in algos_ran]
        return rec
    rec['class'] = 'D'
    rec['reason'] = (
        'Inversa S9 no fiable como evidencia concluyente (revelado Resolve ≠ cameraToP3). '
        'Los algoritmos LibRaw se evaluaron; el halo AHD no se absuelve. Campaña final: '
        'comparar artefactos de demosaic en S1/S2, no promover por PSNR SDK.'
    )
    rec['select_for_final'] = [a for a in ('ahd', 'vng', 'aahd') if a in algos_ran]
    rec['ahd_is_primary_cause'] = None
    return rec


def halo_exposure_note(rows):
    """A04_f044 (EXR −3) vs A04_f049 (EXR 0), mismo ISO 1600 / BE 3.1."""
    def pick(test, frame, algo):
        for r in rows:
            if r.get('test') == test and str(r.get('frame')) == str(frame) and r.get('algo') == algo:
                return r
        return None
    out = {'units': HALO_UNITS, 'pairs': []}
    for algo in ('ahd', 'vng', 'aahd'):
        a = pick('A04', '44', algo)
        b = pick('A04', '49', algo)
        if not a or not b:
            continue
        ha = (a.get('post_sdk') or {}).get('halo') or {}
        hb = (b.get('post_sdk') or {}).get('halo') or {}
        out['pairs'].append({
            'algo': algo,
            'f044_decode_exposure': a.get('exr_decode_exposure'),
            'f049_decode_exposure': b.get('exr_decode_exposure'),
            'f044_peak': ha.get('peak'), 'f049_peak': hb.get('peak'),
            'f044_p99': ha.get('p99'), 'f049_p99': hb.get('p99'),
            'f044_peak_duv': ha.get('peak_duv'), 'f049_peak_duv': hb.get('peak_duv'),
            'f044_peak_rel_y': ha.get('peak_rel_y'), 'f049_peak_rel_y': hb.get('peak_rel_y'),
            'f044_peak_at': ha.get('peak_at'), 'f049_peak_at': hb.get('peak_at'),
            'f049_peak_in_highlight': hb.get('peak_in_highlight'),
            'f049_peak_clipped': hb.get('peak_clipped'),
            'f049_peak_excl_highlight': hb.get('peak_excl_highlight'),
            'spatial_f049': hb.get('spatial'),
            'note': 'peak RGB-diff escala con la exposición del EXR; Δu′v′ y peak/Y no.',
        })
    return out


def closure_from_final(final_rows, ident, injection):
    """Seleccionar candidato solo con mejora visual consistente y sin regresiones."""
    bug = {'id': 'BUG-002', 'severity': 'P0', 'status': 'open',
           'note': 'halos cromáticos persisten a q fino y sin AHD (inject)'}
    no_synth = {
        'synthetic_bayer_with_gt': False,
        'reason': 'CinemaDNG Pocket OG comprimidos (tag 259≠1); no se parchea el mosaico. '
                  'GT sintético = inject RGB @ pre_map, no CFA.',
    }
    if not ident.get('payload_equal') and ident.get('sdk_f32_identical'):
        ident_note = 'SDK idéntico pero payload no verificado o distinto'
    elif ident.get('payload_equal') and ident.get('sdk_f32_identical') and ident.get('s1_identical'):
        ident_note = 'default ≡ AHD explícito (S1, payload, SDK f32)'
    else:
        ident_note = ident
    present = [r for r in final_rows if r.get('present')]
    if not present:
        return {'bug002': bug, 'experimental_candidate': None, 'class': 'E',
                'reason': 'campaña F incompleta', 'identity': ident_note,
                'synthetic_bayer': no_synth, 'defaults_unchanged': True}

    def mean_field(algo, q, path):
        xs = []
        for r in present:
            if r.get('algo') != algo or r.get('q') != q:
                continue
            v = r
            for k in path.split('.'):
                v = (v or {}).get(k) if isinstance(v, dict) else None
            if isinstance(v, (int, float)):
                xs.append(float(v))
        return float(np.mean(xs)) if xs else None

    table = []
    for q in (2, 18):
        for algo in FINAL_ALGOS:
            table.append({
                'q': q, 'algo': algo,
                'psnr': mean_field(algo, q, 'sdk.psnr'),
                'detail': mean_field(algo, q, 'sdk.detail_corr'),
                'duv_mean': mean_field(algo, q, 'post_sdk.uv.duv_mean'),
                'halo_p99': mean_field(algo, q, 'post_sdk.halo.p99'),
                'halo_peak': mean_field(algo, q, 'post_sdk.halo.peak'),
                'false_color': mean_field(algo, q, 'post_sdk.false_color.false_color_fraction'),
                'zip_row': mean_field(algo, q, 'post_sdk.zippering.row_rg'),
                'texture': mean_field(algo, q, 'post_sdk.microtexture.texture_ratio'),
                'convert_s': mean_field(algo, q, 'convert_seconds'),
            })
    # Regla: no promover por PSNR; exigir que halo_p99 y false_color no empeoren vs AHD
    # y que no haya caída de detail > 0.01. Mejora visual consistente = halo_p99 y duv_mean
    # bajan en q2 y q18.
    pick = None
    reason = 'Ningún demosaic elimina el halo. AHD permanece default.'
    ahd2 = next((t for t in table if t['algo'] == 'ahd' and t['q'] == 2), None)
    def better(algo):
        ok = True
        notes = []
        for q in (2, 18):
            a = next((t for t in table if t['algo'] == 'ahd' and t['q'] == q), None)
            c = next((t for t in table if t['algo'] == algo and t['q'] == q), None)
            if not a or not c or a['psnr'] is None or c['psnr'] is None:
                return False, ['métricas ausentes']
            if c['detail'] is not None and a['detail'] is not None and c['detail'] < a['detail'] - 0.01:
                ok = False
                notes.append('q%d detail ↓' % q)
            if c['false_color'] is not None and a['false_color'] is not None and c['false_color'] > a['false_color'] + 0.002:
                ok = False
                notes.append('q%d false color ↑' % q)
            if c['halo_p99'] is not None and a['halo_p99'] is not None and c['halo_p99'] > a['halo_p99'] * 1.05:
                notes.append('q%d halo p99 no mejora' % q)
        return ok, notes
    for algo in ('vng', 'aahd'):
        ok, notes = better(algo)
        rows_q = [t for t in table if t['algo'] == algo]
        duv_down = all(
            (t['duv_mean'] is not None and ahd2 and ahd2['duv_mean'] is not None)
            for t in rows_q)
        # no candidate unless both q show lower p99 AND ok regressions
        both_p99 = True
        for q in (2, 18):
            a = next((t for t in table if t['algo'] == 'ahd' and t['q'] == q), None)
            c = next((t for t in table if t['algo'] == algo and t['q'] == q), None)
            if not a or not c or a['halo_p99'] is None or c['halo_p99'] is None:
                both_p99 = False
                break
            if c['halo_p99'] >= a['halo_p99']:
                both_p99 = False
        if ok and both_p99:
            pick = algo
            reason = '%s baja p99 de halo en q2 y q18 sin regresión de detalle/false color; sigue sin resolver BUG-002' % algo
            break
        elif not ok:
            reason = (reason + ' %s: %s.' % (algo, ', '.join(notes))) if notes else reason
    return {
        'bug002': bug,
        'experimental_candidate': pick,
        'class': 'D',
        'reason': reason,
        'identity': ident_note,
        'synthetic_bayer': no_synth,
        'defaults_unchanged': True,
        'do_not_implement_chroma_compensation': True,
        'no_custom_debayer': True,
        'no_amaze_rcd_lmmse': True,
        'series_A_table': table,
        'injection_halo_persists': injection.get('halo_persists_without_ahd'),
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--dataset', default=os.path.expanduser('~/Desktop/BMD'))
    p.add_argument('--references', default=os.path.expanduser('~/Desktop/EXR'))
    p.add_argument('--out', default=os.path.join(ROOT, 'out', 'benchmark'))
    p.add_argument('--converter', default=os.path.join(ROOT, 'build', 'worker', 'converter'))
    p.add_argument('--brawdec', default=os.path.join(ROOT, 'tools', 'brawdec', 'brawdec'))
    p.add_argument('--encode-inject', action='store_true')
    p.add_argument('--no-cache', action='store_true')
    args = p.parse_args()
    t0 = time.time()
    rows = []
    for test, fr in FRAMES:
        for algo in ALGOS:
            run_id = 'exp005-%s-%s-f%s-q2' % (algo, test.lower(), fr)
            contact = algo in ('ahd', 'vng', 'aahd') and test == 'A04' and str(fr) == '44'
            rows.append(analyze_run(args, run_id, test, fr, algo, write_contact=contact))
    ident = identity_check(args)
    iso = ahd_isolation(rows)
    inj = injection_result(args, rows)
    rec = recommend(rows, iso, inj)
    final_rows = []
    for q in (2, 18):
        for test, fr in SERIES_A:
            for algo in FINAL_ALGOS:
                run_id = 'exp005-f-%s-%s-f%s-q%d' % (algo, test.lower(), fr, q)
                contact = q == 2 and test == 'A04' and str(fr) == '44'
                row = analyze_run(args, run_id, test, fr, algo, write_contact=contact)
                row['q'] = row.get('q') or q
                final_rows.append(row)
    summary = {
        'experiment': 'EXP-005',
        'title': 'Pocket OG Debayer Fidelity & BUG-002 isolation',
        'branch': 'experiment/pocket-og-debayer-fidelity',
        'defaults_unchanged': True,
        'encode': {'q': [2, 18], 'chroma': 'C0', 'alpha': 1, 'deadzone': 0, 'nr': 0, 'synthesis': False, 'rdoq': False},
        'halo_units': HALO_UNITS,
        'libraw': LIBRAW_MATRIX,
        'identity_default_vs_ahd': ident,
        'known_color_transform': known_color_checks(),
        'algebraic_vs_develop': 'roundtrip cameraToP3 ≠ equivalencia Resolve S9; ver a1.algebraic_roundtrip vs develop_equivalence',
        'synthetic_bayer_gt': False,
        'runs': rows,
        'ahd_isolation': iso,
        'reference_injection': inj,
        'halo_exposure_a04': halo_exposure_note(rows + final_rows),
        'final_campaign': final_rows,
        'recommendation': rec,
        'closure': closure_from_final(final_rows, ident, inj),
        'elapsed_s': time.time() - t0,
    }
    os.makedirs(os.path.dirname(SUMMARY_PATH), exist_ok=True)
    with open(SUMMARY_PATH, 'w', encoding='utf-8') as f:
        json.dump(summary, f, indent=2)
        f.write('\n')
    dest = os.path.join(args.out, 'experiments', 'exp005-results-summary.json')
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, 'w', encoding='utf-8') as f:
        json.dump(summary, f, indent=2)
        f.write('\n')
    print('wrote', SUMMARY_PATH)


if __name__ == '__main__':
    main()
