#!/usr/bin/env python3
"""EXP-005b: campaña S8 acotada + informe. Artefactos → out/. No cambia defaults."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.exp005_metrics import halo_stats
from tools.exp005b_io import write_brwp
from tools.exp005b_oracle import bilinear_chroma
from tools.exp005b_report import JSON_OUT, build_payload, write_doc, write_visuals
from tools.exp005b_sdk import (
    CLIP, FRAME0, FRAME1, H, W, apply_e2, build_control_planes, build_edge_rgb, build_impulse_planes,
    characterize_s8, convert_and_decode, crop_ppm, default_encode, ensure_converter,
    joint_delta_y, o1_from_full_chroma, planes_from_rgb, stripe_metrics,
)
from tools.exp005b_signal import NativeSensor, camera_from_p3_grey, edge_pair
from tools.fidelity import images, stage_io
from tools.fidelity.images import write_f32

MAN_DIR = os.path.join(ROOT, 'benchmark', 'experiments', 'exp005b')
FIXT = None  # set in main


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
    ap.add_argument('--skip-sdk', action='store_true')
    ap.add_argument('--no-cache', action='store_true')
    return ap.parse_args()


def ready(args):
    missing = []
    if args.skip_sdk:
        missing.append('skip-sdk')
    if not os.path.isdir(args.dataset):
        missing.append('dataset')
    if not os.path.isfile(os.path.join(args.converter, 'cdng-convert')):
        missing.append('converter')
    if not os.path.isfile(args.brawdec):
        missing.append('brawdec')
    if not os.path.exists(args.framework):
        missing.append('sdk_framework')
    return missing


def run_sdk(args):
    os.makedirs(FIXT, exist_ok=True)
    vis = os.path.join(args.out, 'experiments', 'exp005b', 'visuals')
    sensor = NativeSensor()
    y_c, cb_c, cr_c = build_control_planes()
    y_i, cb_i, cr_i, sites, y_imp = build_impulse_planes()
    p_ctrl = os.path.join(FIXT, 'planes_ctrl.brwp')
    p_imp = os.path.join(FIXT, 'planes_imp.brwp')
    write_brwp(p_ctrl, y_c, cb_c, cr_c)
    write_brwp(p_imp, y_i, cb_i, cr_i)
    dump_imp = os.path.join(args.out, 'experiments', 'exp005b', 'stages', 'imp')
    shutil.rmtree(dump_imp, ignore_errors=True)
    ctrl, m_ctrl = convert_and_decode(args, 's8-ctrl', default_encode(inject_planes=p_ctrl, notes='S8 control'))
    imp, m_imp = convert_and_decode(
        args, 's8-imp',
        default_encode(inject_planes=p_imp, dump_dir=dump_imp, notes='S8 impulses packed'))
    char = characterize_s8(ctrl, imp, sites, y_imp)
    s7 = None
    dump_frame = os.path.join(dump_imp, 'frame_0')
    if os.path.isdir(dump_frame):
        buf = stage_io.load_frame_dir(dump_frame)
        s7 = {
            'has_s7': 'S7_Y' in buf,
            'has_s5': 'S5_Y' in buf,
            'note': 'S7 es decodedPlanes local (croma NN); no interpola. S8 es SDK.',
        }
        if 'S7_Cb' in buf and 'S5_Cb' in buf:
            s7a = buf['S7_Cb']['array'].astype(np.float64)
            s5a = buf['S5_Cb']['array'].astype(np.float64)
            ox = int(buf['S7_Cb']['meta'].get('origin_x') or 0)
            oy = int(buf['S7_Cb']['meta'].get('origin_y') or 0)
            hh, ww = s5a.shape
            if s7a.shape != s5a.shape:
                s7a = s7a[oy:oy + hh, ox:ox + ww]
            if s7a.shape == s5a.shape:
                s7['s7_vs_s5_cb_mae'] = float(np.mean(np.abs(s7a - s5a)))
            else:
                s7['s7_vs_s5_cb_mae'] = None
                s7['s7_shape'] = list(buf['S7_Cb']['array'].shape)
                s7['s5_shape'] = list(s5a.shape)

    rgb_edges, pairs = build_edge_rgb(sensor)
    p_rgb = os.path.join(FIXT, 'edges.f32')
    write_f32(p_rgb, rgb_edges.astype(np.float32))
    s8_edges, m_edges = convert_and_decode(
        args, 's8-edges', default_encode(inject_rgb=p_rgb, notes='A2 packed edges'))
    a2 = stripe_metrics(s8_edges, sensor, pairs)
    # correlación contraste vs halo y distancia ASN vs halo
    contrast = np.array([r['luma_contrast'] for r in a2], dtype=np.float64)
    p99 = np.array([r['halo_p99'] for r in a2], dtype=np.float64)
    dist = np.array([r['dist_to_asn'] for r in a2], dtype=np.float64)
    def corr(a, b):
        if a.std() < 1e-12 or b.std() < 1e-12:
            return 0.0
        return float(np.corrcoef(a, b)[0, 1])
    bw = [r for r in a2 if r['name'] in ('cam_equal_hi', 'asn_hi', 'p3_hi')]
    user_hyp = {
        'white_black_not_exclusive': True,
        'strongest': max(a2, key=lambda r: r['halo_p99'])['name'],
        'cam_equal_hi_p99': next(r['halo_p99'] for r in a2 if r['name'] == 'cam_equal_hi'),
        'p3_hi_p99': next(r['halo_p99'] for r in a2 if r['name'] == 'p3_hi'),
        'asn_hi_p99': next(r['halo_p99'] for r in a2 if r['name'] == 'asn_hi'),
        'isoluminant_p99': next(r['halo_p99'] for r in a2 if r['name'] == 'isoluminant'),
        'corr_contrast_p99': corr(contrast, p99),
        'corr_asn_dist_p99': corr(dist, p99),
        'note': (
            'Si cam_equal (Cb=Cr=0 en códec) tiene halo S8, el SDK introduce croma desde luma. '
            'Si p3/asn dominan, el origen principal es croma de código por la curva.'
        ),
    }
    user_hyp['white_black_not_exclusive'] = not (
        user_hyp['cam_equal_hi_p99'] > 1.5 * max(user_hyp['p3_hi_p99'], user_hyp['asn_hi_p99'])
        and user_hyp['isoluminant_p99'] < 0.5 * user_hyp['cam_equal_hi_p99'])
    user_hyp['confirmed_exclusive_wb'] = (
        user_hyp['cam_equal_hi_p99'] >= user_hyp['p3_hi_p99']
        and user_hyp['cam_equal_hi_p99'] >= user_hyp['asn_hi_p99']
        and user_hyp['isoluminant_p99'] < 0.4 * user_hyp['cam_equal_hi_p99'])

    # C2: fixture P3 full-frame
    p3 = edge_pair(H, W, camera_from_p3_grey(0.03, sensor), camera_from_p3_grey(0.50, sensor), axis=1)
    p_p3 = os.path.join(FIXT, 'p3_edge.f32')
    write_f32(p_p3, p3.astype(np.float32))
    s8_c0, m_c0 = convert_and_decode(args, 's8-p3-c0', default_encode(inject_rgb=p_p3, notes='C2 C0 P3 edge'))
    y, cb, cr = planes_from_rgb(p3, sensor)
    y1, cb1, cr1 = o1_from_full_chroma(p3, sensor, bilinear_chroma)
    p_o1 = os.path.join(FIXT, 'p3_o1.brwp')
    write_brwp(p_o1, y1, cb1, cr1)
    s8_o1, m_o1 = convert_and_decode(args, 's8-p3-o1', default_encode(inject_planes=p_o1, notes='C2 O1 bilinear'))
    # referencia: cada mitad plana
    split = W // 2
    ref = s8_c0.copy()
    ref[:, :split] = np.median(s8_c0[:, : split - 16].reshape(-1, 3), 0)
    ref[:, split:] = np.median(s8_c0[:, split + 16:].reshape(-1, 3), 0)
    pad = 64
    band = np.s_[:, split - pad:split + pad]
    h_c0 = halo_stats(s8_c0[band], ref[band])
    h_o1 = halo_stats(s8_o1[band], ref[band])

    def red(a, b, k):
        try:
            av, bv = float(a[k]), float(b[k])
        except (TypeError, ValueError):
            return 0.0
        if av == 0 or av != av or bv != bv:
            return 0.0
        return float(1.0 - bv / av)

    red_p99 = red(h_c0, h_o1, 'p99')
    red_int = red(h_c0, h_o1, 'integrated')
    redistrib = red_p99 > 0.15 and red_int < 0.0
    chroma_loss = float(np.mean(np.hypot(*[
        s8_o1[..., 0] - s8_o1[..., 1], s8_o1[..., 2] - s8_o1[..., 1]
    ]))) < 0.25 * float(np.mean(np.hypot(*[
        s8_c0[..., 0] - s8_c0[..., 1], s8_c0[..., 2] - s8_c0[..., 1]
    ])))

    e2_meta = None
    s8_e2 = None
    if user_hyp['confirmed_exclusive_wb'] or user_hyp['p3_hi_p99'] >= user_hyp['cam_equal_hi_p99']:
        ye, cbe, cre = apply_e2(y, cb, cr, p3, sensor)
        p_e2 = os.path.join(FIXT, 'p3_e2.brwp')
        write_brwp(p_e2, ye, cbe, cre)
        s8_e2, m_e2 = convert_and_decode(args, 's8-p3-e2', default_encode(inject_planes=p_e2, notes='E2 masked O1'))
        h_e2 = halo_stats(s8_e2, ref)
        e2_meta = {'halo': h_e2, 'p99_reduction': red(h_c0, h_e2, 'p99'),
                   'integrated_reduction': red(h_c0, h_e2, 'integrated'),
                   'braw': m_e2, 'no_global_desat': True, 'no_wb_lock': True}

    joint_meta = None
    promising = (red_p99 >= 0.5 and red_int >= 0.5 and not redistrib and not chroma_loss)
    if not promising:
        yj, jinfo = joint_delta_y(y1, p3, sensor)
        p_j = os.path.join(FIXT, 'p3_joint.brwp')
        write_brwp(p_j, yj, cb1, cr1)
        s8_j, m_j = convert_and_decode(args, 's8-p3-joint', default_encode(inject_planes=p_j, notes='D2 joint Y'))
        h_j = halo_stats(s8_j[band], ref[band])
        joint_meta = dict(jinfo, halo=h_j, p99_reduction=red(h_c0, h_j, 'p99'),
                          integrated_reduction=red(h_c0, h_j, 'integrated'), braw=m_j)

    # F2 A04 C0
    s8_a04, m_a04 = convert_and_decode(args, 's8-a04-c0', default_encode(notes='F2 A04 C0'))
    a04_roi = {'rect': [640, 520, 120, 80], 'name': 'white_strip'}
    x, y0, w, h = a04_roi['rect']
    crop = s8_a04[y0:y0 + h, x:x + w]
    a04_chroma = float(np.percentile(np.hypot(crop[..., 0] - crop[..., 1], crop[..., 2] - crop[..., 1]), 99))

    crop_ppm(os.path.join(vis, 's8_ctrl.ppm'), np.clip(ctrl, 0, 1), 0, 0, 320, 180, 1)
    crop_ppm(os.path.join(vis, 's8_imp_nnx4.ppm'), np.clip(np.abs(imp - ctrl) * 8, 0, 1), 140, 140, 80, 80, 4)
    crop_ppm(os.path.join(vis, 's8_edges.ppm'), np.clip(s8_edges, 0, 1), 0, 400, 1920, 200, 1)
    crop_ppm(os.path.join(vis, 's8_p3_c0_nnx4.ppm'), np.clip(s8_c0, 0, 1), split - 40, 500, 80, 80, 4)
    crop_ppm(os.path.join(vis, 's8_p3_o1_nnx4.ppm'), np.clip(s8_o1, 0, 1), split - 40, 500, 80, 80, 4)
    crop_ppm(os.path.join(vis, 's8_a04_white_nnx4.ppm'), np.clip(s8_a04, 0, 1), x, y0, w, h, 4)

    held = [s for s in char['sites'] if s['split'] == 'held']
    train = [s for s in char['sites'] if s['split'] == 'train']
    model_err = {
        'held_n': len(held),
        'train_n': len(train),
        'held_mean_peak': float(np.mean([s['peak_value'] for s in held])) if held else None,
        'train_mean_peak': float(np.mean([s['peak_value'] for s in train])) if train else None,
        's7_vs_s8': 'S8 medido en RGB SDK; S7 es NN de códigos. No se asume bilinear.',
        'bilinear_not_assumed': True,
    }

    oracle_s8 = {
        'c0': h_c0, 'o1': h_o1,
        'o1_p99_reduction': red_p99, 'o1_integrated_reduction': red_int,
        'redistribution_rejected': redistrib,
        'excessive_chroma_loss': bool(chroma_loss),
        'upsample_used': 'bilinear_candidate_for_O1; S8 operator from impulses used for characterization not inverted in this pass if ill-conditioned',
        'sdk_calls': 4 + (1 if e2_meta else 0) + (1 if joint_meta else 0) + 1,
    }
    return {
        'status': 'measured',
        'clip': CLIP, 'frames': [FRAME0, FRAME1], 'q': 2,
        'characterization': char,
        's7': s7,
        'model_validation': model_err,
        'a2_stripes': a2,
        'a2_user_hypothesis': user_hyp,
        'oracle_s8': oracle_s8,
        'e2': e2_meta,
        'joint_y': joint_meta,
        'a04': {'white_strip_p99_chroma': a04_chroma, 'braw': m_a04,
                'limitation': 'ROI BUG-002 medido sobre S8 C0; sin EXR de WB/EV variantes.'},
        'meta': {'ctrl': m_ctrl, 'imp': m_imp, 'edges': m_edges, 'p3_c0': m_c0, 'p3_o1': m_o1},
        'q18': 'not_run_no_promising_production_candidate' if not promising else 'not_run_this_pass',
        'wb_exposure_sweep': 'not_run; no EXR equivalentes',
    }


def main():
    global FIXT
    args = _args()
    FIXT = os.path.join(args.out, 'experiments', 'exp005b', 'fixtures')
    if not args.converter:
        args.converter = ensure_converter()
    missing = ready(args)
    if missing:
        sdk = {'status': 'skipped', 'reason': 'faltan: ' + ', '.join(missing)}
    else:
        sdk = run_sdk(args)
    os.makedirs(MAN_DIR, exist_ok=True)
    payload = build_payload(sdk)
    os.makedirs(os.path.dirname(JSON_OUT), exist_ok=True)
    with open(JSON_OUT, 'w', encoding='utf-8') as f:
        json.dump(payload, f, indent=2)
        f.write('\n')
    write_doc(payload)
    write_visuals(args.out)
    print('class', payload['feasibility']['class'])
    print('sdk', sdk['status'])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
