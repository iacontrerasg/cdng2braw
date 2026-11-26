#!/usr/bin/env python3
"""EXP-004 D4: aislamiento debayer / siting SDK / no linealidad / deblock Y→RGB."""
from __future__ import annotations

import argparse
import glob
import json
import math
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.fidelity import images, stage_io

# code_from_ycc (model/pocket_og_cinemadng_signal.json)
def ycc_to_codes(y, cb, cr):
    r = y - 0.5 * cb + 1.5 * cr
    g = y - 0.5 * cb - 0.5 * cr
    b = y + 1.5 * cb - 0.5 * cr
    return np.stack([r, g, b], -1)


def chroma_of_rgb(rgb):
    """Cromaticidad cámara lineal: (R−G, B−G) / (R+G+B)."""
    s = np.maximum(np.sum(rgb, axis=-1, keepdims=True), 1e-8)
    rg = (rgb[..., 0] - rgb[..., 1]) / s[..., 0]
    bg = (rgb[..., 2] - rgb[..., 1]) / s[..., 0]
    return rg, bg


def upsample_nn(half):
    return np.repeat(np.repeat(half, 2, 0), 2, 1)


def impulse_centroid(rgb, bg=0.18, window=32):
    mag = np.linalg.norm(rgb.astype(np.float64) - bg, axis=-1)
    peak = float(mag.max())
    y0, x0 = np.unravel_index(int(np.argmax(mag)), mag.shape)
    y0, x0 = int(y0), int(x0)
    y1, y2 = max(0, y0 - window), min(rgb.shape[0], y0 + window + 1)
    x1, x2 = max(0, x0 - window), min(rgb.shape[1], x0 + window + 1)
    patch = mag[y1:y2, x1:x2]
    w = np.clip(patch - np.median(patch), 0, None)
    if w.sum() <= 0:
        return float(y0), float(x0), peak
    yy, xx = np.mgrid[y1:y2, x1:x2]
    return float((yy * w).sum() / w.sum()), float((xx * w).sum() / w.sum()), peak


def esf_mtf(profile):
    from tools.exp004_halo import esf_mtf as _esf
    return _esf(profile)


def analyze_dump(frame_dir):
    dump = stage_io.load_frame_dir(frame_dir)
    out = {'dir': frame_dir, 'keys': sorted(dump.keys())}
    idx = os.path.join(frame_dir, 'stages_index.json')
    if os.path.isfile(idx):
        out['index'] = json.load(open(idx, encoding='utf-8'))
        out['absent_encoder'] = out['index'].get('stages_absent_encoder')
        out['absent_external'] = out['index'].get('stages_absent_external')
    s2 = dump.get('S2_rgb', {}).get('array')
    s4cb = dump.get('S4_Cb', {}).get('array')
    s4cr = dump.get('S4_Cr', {}).get('array')
    s4y = dump.get('S4_Y', {}).get('array')
    s5cb = dump.get('S5_Cb', {}).get('array')
    s5cr = dump.get('S5_Cr', {}).get('array')
    if s2 is not None:
        rg, bg = chroma_of_rgb(s2)
        out['s2_pre_planes'] = {
            'rg_abs_mean': float(np.mean(np.abs(rg))),
            'bg_abs_mean': float(np.mean(np.abs(bg))),
            'rg_peak': float(np.max(np.abs(rg))),
            'note': 'camera-linear RGB chromaticity before curve/YCbCr; not P3/S9',
        }
    if s4cb is not None and s5cb is not None:
        box_cb = s4cb.reshape(s4cb.shape[0] // 2, 2, s4cb.shape[1] // 2, 2).mean((1, 3))
        box_cr = s4cr.reshape(s4cr.shape[0] // 2, 2, s4cr.shape[1] // 2, 2).mean((1, 3))
        out['nonlinearity'] = {
            'mean_map_minus_map_mean_cb_mae': float(np.mean(np.abs(box_cb - s5cb))),
            'mean_map_minus_map_mean_cr_mae': float(np.mean(np.abs(box_cr - s5cr))),
            'mean_map_minus_map_mean_cb_peak': float(np.max(np.abs(box_cb - s5cb))),
            'mean_map_minus_map_mean_cr_peak': float(np.max(np.abs(box_cr - s5cr))),
            'note': 'mean(map(RGB)) is 2×2 of S4; map(mean(RGB)) is C0 S5',
        }
    s6y_pre = dump.get('S6_pre_Y', {}).get('array')
    s6y_post = dump.get('S6_post_Y', {}).get('array')
    s6cb = dump.get('S6_pre_Cb', {}).get('array')
    s6cr = dump.get('S6_pre_Cr', {}).get('array')
    s6cb2 = dump.get('S6_post_Cb', {}).get('array')
    if s6y_pre is not None and s6cb is not None:
        meta = dump.get('S6_pre_Y', {}).get('meta') or {}
        ox, oy = int(meta.get('origin_x') or 0), int(meta.get('origin_y') or 0)
        # Recorte activo 1920×1080 si el lienzo es mayor (Pocket OG 1984×1104).
        h, w = s6y_pre.shape
        y0, x0 = oy, ox
        y1, x1 = min(h, oy + 1080), min(w, ox + 1920)
        cb_u = upsample_nn(s6cb.astype(np.float64))
        cr_u = upsample_nn(s6cr.astype(np.float64))
        rgb_pre = ycc_to_codes(s6y_pre[y0:y1, x0:x1].astype(np.float64),
                               cb_u[y0:y1, x0:x1], cr_u[y0:y1, x0:x1])
        rgb_post = ycc_to_codes(s6y_post[y0:y1, x0:x1].astype(np.float64),
                                cb_u[y0:y1, x0:x1], cr_u[y0:y1, x0:x1])
        rg0, bg0 = chroma_of_rgb(np.maximum(rgb_pre, 1e-3))
        rg1, bg1 = chroma_of_rgb(np.maximum(rgb_post, 1e-3))
        out['deblock_y_to_chromaticity'] = {
            'cb_invariant': bool(s6cb2 is not None and np.array_equal(s6cb, s6cb2)),
            'rgb_chroma_mae': float(np.mean(np.hypot(rg1 - rg0, bg1 - bg0))),
            'rgb_chroma_peak': float(np.max(np.hypot(rg1 - rg0, bg1 - bg0))),
            'note': 'Cb/Cr canvas invariant; reconstructed camera-code chromaticity can still move with Y',
        }
    return out


def analyze_impulse_decode(run_dir):
    frames = glob.glob(os.path.join(run_dir, 'frames', '*.f32'))
    if not frames:
        return None
    rgb = images.read_f32(frames[0])
    cy, cx, peak = impulse_centroid(rgb)
    return {'cy': cy, 'cx': cx, 'peak': peak, 'shape': list(rgb.shape)}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--out', default=os.path.join(ROOT, 'out', 'benchmark'))
    p.add_argument('--json', default=os.path.join(ROOT, 'out', 'benchmark', 'experiments', 'exp004', 'd4.json'))
    args = p.parse_args()
    stages = os.path.join(args.out, 'experiments', 'exp004', 'stages')
    report = {'schema': 1, 'dumps': {}, 'impulses': {}, 'siting': {}}
    for d in sorted(glob.glob(os.path.join(stages, '*', 'frame_0'))):
        rid = os.path.basename(os.path.dirname(d))
        report['dumps'][rid] = analyze_dump(d)
    for d in sorted(glob.glob(os.path.join(args.out, 'runs', 'exp004-d4-*'))):
        rid = os.path.basename(d)
        report['impulses'][rid] = analyze_impulse_decode(d)
    even = report['impulses'].get('exp004-d4-impulse-even-q2')
    odd = report['impulses'].get('exp004-d4-impulse-odd-q2')
    # Fuentes: even (960,540), odd (963,543). El centro del cuarteto 2×2 es (2⌊x/2⌋+0.5, …).
    def cell_center(x, y):
        return (x // 2) * 2 + 0.5, (y // 2) * 2 + 0.5
    even_src, odd_src = (960, 540), (963, 543)
    siting = {'even': even, 'odd': odd, 'even_src': even_src, 'odd_src': odd_src}
    errs = []
    if even and even.get('cx') is not None:
        ecx, ecy = cell_center(*even_src)
        siting['even_vs_cell_center_px'] = [even['cy'] - ecy, even['cx'] - ecx]
        errs.append(abs(even['cy'] - ecy) + abs(even['cx'] - ecx))
    if odd and odd.get('cx') is not None:
        ocx, ocy = cell_center(*odd_src)
        siting['odd_vs_cell_center_px'] = [odd['cy'] - ocy, odd['cx'] - ocx]
        errs.append(abs(odd['cy'] - ocy) + abs(odd['cx'] - ocx))
    mean_l1 = float(np.mean(errs)) if errs else 0.0
    siting['mean_l1_vs_cell_center'] = mean_l1
    yerr = []
    if 'even_vs_cell_center_px' in siting:
        yerr.append(siting['even_vs_cell_center_px'][0])
    if 'odd_vs_cell_center_px' in siting:
        yerr.append(siting['odd_vs_cell_center_px'][0])
    consistent_y = (len(yerr) == 2 and yerr[0] * yerr[1] > 0 and
                    min(abs(yerr[0]), abs(yerr[1])) > 0.5)
    siting['consistent_vertical_bias_px'] = yerr
    # Sesgo vertical ~0.6 px es débil; no basta para C3 de producción. Código C3 existe, F no lo activa.
    siting['c3_warranted'] = False
    siting['c3_weak_vertical_bias'] = bool(consistent_y)
    siting['note'] = ('SDK peaks sit near encoder 2×2 centers; even-vs-odd Δ~2 px is cell spacing. '
                      'Weak consistent vertical bias ~-0.65 px (not C3 in F).')
    report['siting'] = siting
    os.makedirs(os.path.dirname(args.json), exist_ok=True)
    with open(args.json, 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2)
        f.write('\n')
    print('wrote', args.json)


if __name__ == '__main__':
    main()
