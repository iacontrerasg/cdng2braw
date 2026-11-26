#!/usr/bin/env python3
"""Diagnóstico de correlación de detalle (EXP-002a) sin bajar el umbral del banco.

Clasifica rechazo de alineación como desplazamiento geométrico vs degradación de detalle.
No modifica criterios de aceptación.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.fidelity import images as IMG
from tools.fidelity import rd as RD


def _luma(img):
    return np.log2(np.maximum(img.mean(-1), 1e-4))


def multiscale_corr(ref, cand, scales=(1, 2, 4, 8)):
    out = {}
    a0, b0 = _luma(ref), _luma(cand)
    for s in scales:
        a = a0[::s, ::s]
        b = b0[::s, ::s]
        out[str(s)] = float(np.corrcoef(a.ravel(), b.ravel())[0, 1])
    return out


def gradient_corr(ref, cand):
    def g(x):
        gy, gx = np.gradient(x)
        return np.hypot(gx, gy)
    a, b = g(_luma(ref)), g(_luma(cand))
    return float(np.corrcoef(a.ravel(), b.ravel())[0, 1])


def band_energy(img):
    """Energía relativa por bandas de frecuencia (FFT 2D de luma)."""
    a = _luma(img).astype(np.float64)
    a -= a.mean()
    F = np.fft.fftshift(np.abs(np.fft.fft2(a)) ** 2)
    h, w = F.shape
    cy, cx = h // 2, w // 2
    yy, xx = np.ogrid[:h, :w]
    r = np.sqrt((yy - cy) ** 2 + (xx - cx) ** 2)
    rmax = min(cy, cx)
    bands = {}
    edges = [0, 0.1, 0.3, 0.6, 1.0]
    labels = ['dc_low', 'low', 'mid', 'high']
    for lab, lo, hi in zip(labels, edges[:-1], edges[1:]):
        m = (r >= lo * rmax) & (r < hi * rmax)
        bands[lab] = float(F[m].sum())
    tot = sum(bands.values()) or 1.0
    return {k: v / tot for k, v in bands.items()}


def diagnose(ref, cand, min_corr=0.74):
    shift, cc = IMG.alignment(ref, cand)
    gate = {
        'shift': list(shift),
        'detail_correlation': cc,
        'gate_pass': shift == (0, 0) and cc >= min_corr,
        'gate_min_correlation': min_corr,
    }
    # Contraste / medias
    rl, cl = ref.mean(-1), cand.mean(-1)
    contrast = {
        'ref_std': float(rl.std()),
        'cand_std': float(cl.std()),
        'std_ratio': float(cl.std() / max(rl.std(), 1e-12)),
        'mean_delta': float(cl.mean() - rl.mean()),
    }
    be_ref, be_cand = band_energy(ref), band_energy(cand)
    classification = []
    if shift != (0, 0):
        classification.append('A_spatial_shift')
    else:
        classification.append('geometry_aligned')
    if cc < min_corr:
        # Pérdida HF en espectro completo
        if be_cand['high'] / max(be_ref['high'], 1e-12) < 0.85:
            classification.append('B_high_frequency_loss')
        if abs(contrast['std_ratio'] - 1.0) > 0.08:
            classification.append('C_contrast_change')
        # Si shift=0 → no es desalineación geométrica
        if shift == (0, 0):
            classification.append('fidelity_degradation_not_geometric')
            # La métrica del banco usa highpass de log-luma; puede fallar con
            # correlación de luma ~1.0 y sin desplazamiento (umbral 0.74 justo).
            ms1 = multiscale_corr(ref, cand).get('1')
            if ms1 is not None and ms1 > 0.99 and cc < min_corr:
                classification.append('E_highpass_detail_metric_sensitive')
        if not any(x.startswith('B_') or x.startswith('C_') or x.startswith('E_') for x in classification):
            classification.append('E_metric_or_other')
    return {
        'gate': gate,
        'multiscale_luma_corr': multiscale_corr(ref, cand),
        'gradient_corr': gradient_corr(ref, cand),
        'contrast': contrast,
        'band_energy_ref': be_ref,
        'band_energy_cand': be_cand,
        'band_energy_ratio': {k: be_cand[k] / max(be_ref[k], 1e-12) for k in be_ref},
        'classification': classification,
    }


def load_pair_from_run(run_dir, result_key):
    run = RD.load_run(run_dir)
    res = run['metrics']['results'][result_key]
    # Imágenes: el runner guarda PNG; preferimos f32 si existen
    ref_path = os.path.join(run_dir, 'png', result_key + '_ref.f32')
    cand_path = os.path.join(run_dir, 'png', result_key + '_cand.f32')
    if not (os.path.isfile(ref_path) and os.path.isfile(cand_path)):
        # intentar vía decode cache — no siempre disponible
        raise SystemExit('no hay pares f32 en %s; re-ejecuta fidelidad o pasa --ref/--cand' % run_dir)
    # sin shape en f32 raw no podemos leer — usar alignment sobre lo que haya en metrics
    raise SystemExit('usar --ref-npy/--cand-npy o generar con runner')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ref-npy', required=True, help='RGB float64 .npy referencia')
    ap.add_argument('--cand-npy', required=True, help='RGB float64 .npy candidata')
    ap.add_argument('--out', required=True)
    ap.add_argument('--min-corr', type=float, default=0.74)
    args = ap.parse_args()
    ref = np.load(args.ref_npy)
    cand = np.load(args.cand_npy)
    doc = diagnose(ref, cand, args.min_corr)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or '.', exist_ok=True)
    with open(args.out, 'w', encoding='utf-8') as f:
        json.dump(doc, f, indent=2)
        f.write('\n')
    print(json.dumps(doc['classification']))
    print('detail_corr', doc['gate']['detail_correlation'], 'shift', doc['gate']['shift'])


if __name__ == '__main__':
    main()
