#!/usr/bin/env python3
"""EXP-005c smoke: AHD C0 vs CDNG_OG_BAYER=1 en A04_f044/f049 y A01_f052."""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.exp005_metrics import halo_stats
from tools.exp005b_periodic import load_ruler_rois
from tools.exp005b_sdk import crop_ppm, decode_settings, ensure_converter
from tools.exp005c_audit import find_frame, inspect_dng
from tools.exp005c_report import JSON_OUT, build_payload, write_doc
from tools.fidelity import identity, images, manifest as MF, metrics as M, pipeline as P

JOBS = [
    {'id': 'A04_f044', 'clip': 'BMPCC_1_2000-03-02_0756_C0039', 'frame': 44, 'iso': 1600,
     'exr': 'DNG1600MINUS3_00086731.exr'},
    {'id': 'A04_f049', 'clip': 'BMPCC_1_2000-03-02_0756_C0039', 'frame': 49, 'iso': 1600,
     'exr': 'DNG1600_00086736.exr'},
    {'id': 'A01_f052', 'clip': 'BMPCC_1_2000-03-02_0756_C0036', 'frame': 52, 'iso': 200,
     'exr': 'DNG200_00086452.exr'},
]
WHITE = [640, 520, 120, 80]


def _args():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dataset', default=os.path.expanduser('~/Desktop/BMD'))
    ap.add_argument('--out', default=os.path.join(ROOT, 'out', 'benchmark'))
    ap.add_argument('--converter', default='')
    ap.add_argument('--brawdec', default=os.path.join(ROOT, 'tools', 'brawdec', 'brawdec'))
    ap.add_argument('--framework',
                    default='/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Frameworks/BlackmagicRawAPI.framework/BlackmagicRawAPI')
    ap.add_argument('--camera-support',
                    default='/Library/Application Support/Blackmagic Design/Blackmagic RAW/Camera Support')
    ap.add_argument('--references', default=os.path.expanduser('~/Desktop/EXR'))
    ap.add_argument('--skip-sdk', action='store_true')
    ap.add_argument('--no-cache', action='store_true')
    return ap.parse_args()


def encode(bayer=False):
    env = {
        'CDNG_OG_DEBLOCK_PRECOMP': '1',
        'CDNG_OG_DEBLOCK_ALPHA': '1',
        'CDNG_DEADZONE': '0',
        'CDNG_OG_CHROMA_MODE': 'c0',
    }
    if bayer:
        env['CDNG_OG_BAYER'] = '1'
    return {
        'compression': 'quality', 'q': 2, 'ratio': None, 'rate_search': False,
        'noise_synthesis': False, 'nr_luma': 0, 'nr_chroma': 0, 'workers': 1,
        'experimental_env': env, 'notes': ['EXP-005c ' + ('bayer' if bayer else 'ahd')],
    }


def run_pair(args, job):
    log = P.Log(os.path.join(args.out, 'experiments', 'exp005c', 'logs', job['id'] + '.log'))
    converter = identity.converter(args.converter)
    dec_id = identity.decoder(args.brawdec, args.framework, args.camera_support)
    clip_dir = MF.clip_dir(args.dataset, job['clip'])
    files = MF.dng_files(clip_dir)
    if len(files) <= job['frame'] + 1:
        return {'id': job['id'], 'decode_fail': True, 'reason': 'clip corto'}
    cache = os.path.join(args.out, 'cache')
    vis = os.path.join(args.out, 'experiments', 'exp005c', 'visuals')
    os.makedirs(vis, exist_ok=True)
    out = {'id': job['id'], 'iso': job['iso']}
    imgs = {}
    for name, bay in (('c0', False), ('bayer', True)):
        conv = P.convert(args.converter, converter, clip_dir, files, job['frame'], 2,
                         encode(bay), cache, log, args.no_cache)
        img, dec = P.decode(args.brawdec, args.framework, dec_id, conv['path'], conv['sha256'], 0,
                            decode_settings(), cache, log, args.no_cache, expected_iso=job['iso'])
        imgs[name] = np.asarray(img, dtype=np.float64)
        out[name + '_braw'] = conv['sha256']
        out[name + '_reused'] = conv['reused'] and dec['reused']
        dest = os.path.join(args.out, 'experiments', 'exp005c', 'frames')
        os.makedirs(dest, exist_ok=True)
        images.write_f32(os.path.join(dest, '%s_%s.f32' % (job['id'], name)), img)
    c0, by = imgs['c0'], imgs['bayer']
    yb, yc0 = float(np.mean(M.luminance(by))), float(np.mean(M.luminance(c0)))
    luma_ratio = (yb / yc0) if yc0 > 0 else None
    ref = None
    exr = os.path.join(args.references, job['exr'])
    if os.path.isfile(exr):
        ref, _ = images.read_exr(exr)
        ref = np.asarray(ref, dtype=np.float64)
    x, y, w, h = WHITE
    def crop(im):
        return im[y:y + h, x:x + w]
    if ref is not None:
        h_c0 = halo_stats(crop(c0), crop(ref))
        h_by = halo_stats(crop(by), crop(ref))
        out['c0_halo_p99'] = h_c0['p99']
        out['bayer_halo_p99'] = h_by['p99']
        a, b = float(h_c0['p99'] or 0), float(h_by['p99'] or 0)
        out['halo_p99_reduction'] = 0.0 if a == 0 else float(1.0 - b / a)
        out['psnr'] = M.basic(ref, by, 1.0).get('psnr_db')
        out['psnr_c0'] = M.basic(ref, c0, 1.0).get('psnr_db')
        yr, yc = float(np.mean(M.luminance(ref))), float(np.mean(M.luminance(by)))
        out['s9_exposure_rel'] = (yc / yr - 1) if yr > 0 else None
        out['luma_bayer_over_c0'] = luma_ratio
        out['exposure_rel'] = out['s9_exposure_rel']
        out['exposure_fail'] = abs((luma_ratio or 1) - 1) > 0.15
    else:
        out['missing_exr'] = job['exr']
        out['luma_bayer_over_c0'] = luma_ratio
        out['exposure_fail'] = abs((luma_ratio or 1) - 1) > 0.15
        h_c0 = halo_stats(crop(c0), np.median(crop(c0).reshape(-1, 3), 0)[None, None])
        h_by = halo_stats(crop(by), np.median(crop(by).reshape(-1, 3), 0)[None, None])
        out['c0_halo_p99'] = h_c0['p99']
        out['bayer_halo_p99'] = h_by['p99']
        a, b = float(h_c0['p99'] or 0), float(h_by['p99'] or 0)
        out['halo_p99_reduction'] = 0.0 if a == 0 else float(1.0 - b / a)
    rois = load_ruler_rois()
    left = rois.get('cc_ruler_left') or [1026, 350, 28, 380]
    def p99_chroma(im, rect):
        xx, yy, ww, hh = rect
        c = im[yy:yy + hh, xx:xx + ww]
        mag = np.hypot(c[..., 0] - c[..., 1], c[..., 2] - c[..., 1])
        return float(np.percentile(mag, 99))
    out['c0_ruler_left_p99'] = p99_chroma(c0, left)
    out['bayer_ruler_left_p99'] = p99_chroma(by, left)
    aa, bb = out['c0_ruler_left_p99'], out['bayer_ruler_left_p99']
    out['ruler_p99_reduction'] = 0.0 if aa == 0 else float(1.0 - bb / aa)
    out['decode_fail'] = False
    crop_ppm(os.path.join(vis, job['id'] + '_c0_white_nnx4.ppm'), np.clip(c0, 0, 1), x, y, w, h, 4)
    crop_ppm(os.path.join(vis, job['id'] + '_bayer_white_nnx4.ppm'), np.clip(by, 0, 1), x, y, w, h, 4)
    lx, ly, lw, lh = left
    crop_ppm(os.path.join(vis, job['id'] + '_c0_ruler_nnx4.ppm'), np.clip(c0, 0, 1), lx, ly, lw, lh, 4)
    crop_ppm(os.path.join(vis, job['id'] + '_bayer_ruler_nnx4.ppm'), np.clip(by, 0, 1), lx, ly, lw, lh, 4)
    return out


def main():
    args = _args()
    if not args.converter:
        args.converter = ensure_converter()
    mosaic = {'status': 'missing_dng'}
    dng = find_frame(args.dataset, JOBS[0]['clip'], JOBS[0]['frame'])
    if dng:
        mosaic = inspect_dng(dng)
        mosaic['status'] = 'ok'
        mosaic['clip'] = JOBS[0]['clip']
        mosaic['frame'] = JOBS[0]['frame']
    smoke = {'status': 'skipped', 'reason': 'skip-sdk or missing tools'}
    if not args.skip_sdk and os.path.isdir(args.dataset) and os.path.isfile(os.path.join(args.converter, 'cdng-convert')):
        frames = []
        for job in JOBS:
            try:
                frames.append(run_pair(args, job))
            except Exception as e:
                frames.append({'id': job['id'], 'decode_fail': True, 'reason': str(e)})
        smoke = {
            'status': 'measured',
            'q': 2, 'nr': 0, 'rdoq': False,
            'frames': frames,
            'note': 'Comparación S9 EXR vs C0 AHD vs CDNG_OG_BAYER. No se elige por PSNR global.',
        }
    extra = {'mosaic_audit': mosaic, 'smoke': smoke,
             'prototype': {'env': 'CDNG_OG_BAYER', 'implemented': True, 'uses_va_finishPlanes': False}}
    p = build_payload(extra)
    os.makedirs(os.path.dirname(JSON_OUT), exist_ok=True)
    with open(JSON_OUT, 'w', encoding='utf-8') as f:
        json.dump(p, f, indent=2)
        f.write('\n')
    write_doc(p)
    print('class', p['feasibility']['class'])
    print('mosaic', mosaic.get('status'), mosaic.get('cfa_pattern'), mosaic.get('compression'))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
