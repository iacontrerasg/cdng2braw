"""EXP-005c F: PSNR raw vs exposure-matched. No re-encode."""
from __future__ import annotations

import math
import os

import numpy as np

from tools.fidelity import metrics as M

MINUS3_EV = 3.0
INVALID_EV = 0.25  # |ΔEV| mayor → PSNR raw no comparable


def luma_mean(img):
    return float(np.mean(M.luminance(np.asarray(img, dtype=np.float64))))


def exposure_ev(cand, ref):
    yc, yr = luma_mean(cand), luma_mean(ref)
    if yr <= 0 or yc <= 0:
        return None
    return math.log2(yc / yr)


def match_luma(ref, cand):
    """Escala la referencia para igualar la luma media del candidato (mismo factor RGB)."""
    yc, yr = luma_mean(cand), luma_mean(ref)
    if yr <= 0:
        return np.asarray(ref, dtype=np.float64), None
    g = yc / yr
    return np.asarray(ref, dtype=np.float64) * g, g


def psnr_pair(ref, cand):
    return M.basic(np.asarray(ref, dtype=np.float64), np.asarray(cand, dtype=np.float64), 1.0).get('psnr_db')


def split_psnr(ref, cand, exr_name=''):
    """PSNR crudo (dominio S9 tal cual) y emparejado por luma. Marca MINUS3."""
    ref = np.asarray(ref, dtype=np.float64)
    cand = np.asarray(cand, dtype=np.float64)
    ev = exposure_ev(cand, ref)
    matched, gain = match_luma(ref, cand)
    minus3 = 'MINUS3' in os.path.basename(exr_name or '')
    out = {
        'exr_name': os.path.basename(exr_name) if exr_name else None,
        'minus3_exr': minus3,
        'ev_offset_cand_over_exr': ev,
        'psnr_raw': psnr_pair(ref, cand),
        'psnr_exposure_matched': psnr_pair(matched, cand) if gain else None,
        'match_gain': gain,
        'raw_comparison_valid': ev is not None and abs(ev) <= INVALID_EV,
    }
    if minus3:
        scaled = ref * (2.0 ** MINUS3_EV)
        out['psnr_exr_times_8'] = psnr_pair(scaled, cand)
        out['ev_after_times_8'] = exposure_ev(cand, scaled)
    return out


def annotate_frame(row, ref, c0, bayer, exr_name):
    """Añade métricas separadas; no borra psnr histórico."""
    row = dict(row)
    s_by = split_psnr(ref, bayer, exr_name)
    s_c0 = split_psnr(ref, c0, exr_name)
    row['psnr_raw'] = s_by['psnr_raw']
    row['psnr_c0_raw'] = s_c0['psnr_raw']
    row['psnr_exposure_matched'] = s_by['psnr_exposure_matched']
    row['psnr_c0_exposure_matched'] = s_c0['psnr_exposure_matched']
    row['exr_name'] = s_by['exr_name']
    row['minus3_exr'] = s_by['minus3_exr']
    row['ev_offset_cand_over_exr'] = s_by['ev_offset_cand_over_exr']
    row['raw_comparison_valid'] = s_by['raw_comparison_valid']
    row['s9_domain_note'] = (
        'A04_f044 EXR es DNG1600MINUS3 (−3 EV). PSNR raw ≈ −1 dB es comparación inválida; '
        'usar psnr_exposure_matched / psnr_exr_times_8.'
        if s_by['minus3_exr'] else
        'EXR de exposición nominal; PSNR raw y matched deben coincidir si |ΔEV|≤0.25.'
    )
    if 'psnr_exr_times_8' in s_by:
        row['psnr_exr_times_8'] = s_by['psnr_exr_times_8']
        row['psnr_c0_exr_times_8'] = s_c0.get('psnr_exr_times_8')
        row['ev_after_times_8'] = s_by.get('ev_after_times_8')
    return row
