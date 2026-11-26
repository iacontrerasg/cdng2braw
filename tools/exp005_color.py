#!/usr/bin/env python3
"""EXP-005 A1/A2: S2 camera RGB ↔ P3-D65 Linear. Misma Bradford/ColorMatrix que cameraToP3 C++.

No ajusta una matriz para minimizar el error (eso ocultaría halos). La única escala documentada es
2**BaselineExposure. Un ratio de luminancia medido se informa como diagnóstico, no se aplica al residuo
oficial. Decode.exposure del EXR de Resolve se anota, no se absorbe en la matriz.
"""
from __future__ import annotations

import json
import os
import struct
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.fidelity import metrics as M

BRADFORD = np.array([[0.8951, 0.2664, -0.1614],
                     [-0.7502, 1.7135, 0.0367],
                     [0.0389, -0.0685, 1.0296]], dtype=np.float64)
D65 = np.array([0.95047, 1.0, 1.08883], dtype=np.float64)
P3_TO_XYZ = M.P3_TO_XYZ.astype(np.float64)
XYZ_TO_P3 = np.linalg.inv(P3_TO_XYZ)

ILLUMINANT_K = {
    17: 2856.0, 18: 3200.0, 19: 5500.0, 20: 6500.0,
    21: 6504.0, 23: 5003.0, 1: 5500.0, 2: 4874.0, 3: 2856.0,
}


def _lu_inv(a):
    """Inversa 3×3 con el mismo LU de dng_math.hpp (pivoteo parcial)."""
    a = np.array(a, dtype=np.float64).reshape(3, 3).copy()
    b = np.eye(3, dtype=np.float64)
    for k in range(3):
        p = k + int(np.argmax(np.abs(a[k:, k])))
        if a[p, k] == 0:
            raise ValueError('Singular matrix')
        if p != k:
            a[[k, p]] = a[[p, k]]
            b[[k, p]] = b[[p, k]]
        r = 1.0 / a[k, k]
        for i in range(k + 1, 3):
            a[i, k] *= r
            a[i, k + 1:] -= a[i, k] * a[k, k + 1:]
    x = np.zeros_like(b)
    for c in range(3):
        y = b[:, c].copy()
        for i in range(1, 3):
            for k in range(i):
                y[i] -= a[i, k] * y[k]
        for i in range(2, -1, -1):
            for k in range(i + 1, 3):
                y[i] -= a[i, k] * y[k]
            y[i] /= a[i, i]
        x[:, c] = y
    return x


def bradford(src_white, dst_white):
    s = BRADFORD @ np.asarray(src_white, dtype=np.float64)
    d = BRADFORD @ np.asarray(dst_white, dtype=np.float64)
    diag = np.diag(d / s)
    return _lu_inv(BRADFORD) @ diag @ BRADFORD


def matrix_for_neutral(cm1, cm2, k1, k2, neutral):
    cm1 = np.asarray(cm1, dtype=np.float64).reshape(3, 3)
    cm2 = np.asarray(cm2, dtype=np.float64).reshape(3, 3)
    n = np.asarray(neutral, dtype=np.float64)
    if k1 == k2:
        return cm2.copy(), k2
    order = k1 < k2
    lo, hi = (k1, k2) if order else (k2, k1)
    cm_lo, cm_hi = (cm1, cm2) if order else (cm2, cm1)
    t = hi
    cm = cm_hi.copy()
    for _ in range(12):
        w = (1 / t - 1 / hi) / (1 / lo - 1 / hi)
        w = min(max(w, 0.0), 1.0)
        cm = w * cm_lo + (1 - w) * cm_hi
        xyz = _lu_inv(cm) @ n
        s = xyz.sum()
        x, y = xyz[0] / s, xyz[1] / s
        nn = (x - 0.3320) / (y - 0.1858)
        mccamy = -449 * nn ** 3 + 3525 * nn ** 2 - 6823.3 * nn + 5520.33
        t_new = min(max(mccamy, lo), hi)
        if abs(t_new - t) < 0.5:
            t = t_new
            break
        t = t_new
    return cm, t


def camera_to_p3(cm1, cm2, k1, k2, neutral):
    cm, _ = matrix_for_neutral(cm1, cm2, k1, k2, neutral)
    cam_to_xyz = _lu_inv(cm)
    white_xyz = cam_to_xyz @ np.asarray(neutral, dtype=np.float64)
    adapt = bradford(white_xyz, D65)
    return XYZ_TO_P3 @ adapt @ cam_to_xyz


def p3_to_camera(cm1, cm2, k1, k2, neutral):
    return _lu_inv(camera_to_p3(cm1, cm2, k1, k2, neutral))


def apply_rgb(img, mat):
    m = np.asarray(mat, dtype=np.float64)
    return np.einsum('...i,ji->...j', np.asarray(img, dtype=np.float64), m)


def roundtrip_error(rgb, fwd, inv):
    back = apply_rgb(apply_rgb(rgb, fwd), inv)
    d = back - np.asarray(rgb, dtype=np.float64)
    return {'max_abs': float(np.max(np.abs(d))), 'rmse': float(np.sqrt(np.mean(d * d)))}


# --- TIFF/DNG mínimo (IFD0, última aparición como Dng::color) ---

def _u16(buf, off, le):
    return struct.unpack_from('<H' if le else '>H', buf, off)[0]


def _u32(buf, off, le):
    return struct.unpack_from('<I' if le else '>I', buf, off)[0]


def read_dng_color(path):
    buf = open(path, 'rb').read()
    if buf[:2] == b'II':
        le = True
    elif buf[:2] == b'MM':
        le = False
    else:
        raise ValueError('not TIFF: %s' % path)
    if _u16(buf, 2, le) != 42:
        raise ValueError('not TIFF42')
    tags = {}
    off = _u32(buf, 4, le)
    while off:
        n = _u16(buf, off, le)
        for i in range(n):
            e = off + 2 + i * 12
            tag, typ, cnt = _u16(buf, e, le), _u16(buf, e + 2, le), _u32(buf, e + 4, le)
            val_off = e + 8
            tags[tag] = (typ, cnt, val_off, _u32(buf, val_off, le))
        next_off = off + 2 + n * 12
        off = _u32(buf, next_off, le)

    def srats(tag):
        typ, cnt, val_off, ptr = tags[tag]
        start = ptr if cnt > 1 or typ in (5, 10) else val_off
        if cnt == 1 and typ in (5, 10):
            start = ptr
        out = []
        def i32(u):
            u = int(u) & 0xffffffff
            return u - 0x100000000 if u >= 0x80000000 else u
        for i in range(cnt):
            at = start + 8 * i
            a, b = _u32(buf, at, le), _u32(buf, at + 4, le)
            if typ == 10:
                a, b = i32(a), i32(b)
            if b == 0:
                raise ValueError('division by zero tag %s' % tag)
            out.append(a / b)
        return out

    def illum(tag, fallback):
        if tag not in tags:
            return fallback
        typ, cnt, val_off, ptr = tags[tag]
        code = _u16(buf, val_off, le)
        return ILLUMINANT_K.get(code, fallback)

    cm1 = srats(50721)
    k1 = illum(50778, 2856.0)
    if 50722 in tags:
        cm2 = srats(50722)
        k2 = illum(50779, 6504.0)
    else:
        cm2, k2 = cm1, k1
    asn = srats(50728)
    be = 0.0
    if 50730 in tags:
        be = srats(50730)[0]
    black = None
    white = None
    if 50714 in tags:
        try:
            black = srats(50714)[0] if tags[50714][0] in (5, 10) else None
        except Exception:
            black = None
    iso = None
    if 34855 in tags:
        typ, cnt, val_off, ptr = tags[34855]
        iso = int(_u16(buf, val_off, le))
    return {
        'cm1': cm1, 'cm2': cm2, 'k1': k1, 'k2': k2, 'as_shot_neutral': asn,
        'baseline_exposure': be, 'baseline_gain': float(2 ** be),
        'black': black, 'iso': iso, 'path': os.path.abspath(path),
    }


def documented_scale(meta, exr_decode_exposure=0.0):
    """Escala documentada (no ajustada): 2^BaselineExposure. El EXR decode.exposure se anota aparte."""
    return {
        'baseline_gain': meta['baseline_gain'],
        'exr_decode_exposure': float(exr_decode_exposure),
        'exr_decode_gain': float(2 ** float(exr_decode_exposure)),
        'applied_to_s2_for_luma_compare': meta['baseline_gain'] * float(2 ** float(exr_decode_exposure)),
        'note': 'No se aplica un ajuste de mínimos cuadrados. Chromaticidad es invariante de escala.',
    }


def chroma_axes_from_ref(ref_p3):
    """Ejes de proyección cromática comunes: PCA 2D en (u′,v′) de la referencia (píxeles con Y>min)."""
    y = M.luminance(ref_p3)
    ok = y > M.P3_LUMA[1] * 0  # placeholder
    ok = y > 0.005
    u, v = M.uv_prime(ref_p3)
    pts = np.stack([u[ok], v[ok]], -1)
    pts = pts[np.isfinite(pts).all(-1)]
    if pts.shape[0] < 16:
        return {'ok': False, 'mean': [float('nan'), float('nan')], 'axes': [[1, 0], [0, 1]]}
    mean = pts.mean(0)
    x = pts - mean
    _, _, vt = np.linalg.svd(x, full_matrices=False)
    return {'ok': True, 'mean': [float(mean[0]), float(mean[1])],
            'axes': [[float(vt[0, 0]), float(vt[0, 1])], [float(vt[1, 0]), float(vt[1, 1])]]}


def project_uv_residual(cand_p3, ref_p3, axes):
    uc, vc = M.uv_prime(cand_p3)
    ur, vr = M.uv_prime(ref_p3)
    du, dv = uc - ur, vc - vr
    a0 = np.array(axes['axes'][0])
    a1 = np.array(axes['axes'][1])
    p0 = du * a0[0] + dv * a0[1]
    p1 = du * a1[0] + dv * a1[1]
    finite = np.isfinite(p0) & np.isfinite(p1)
    return {
        'pc1_rmse': float(np.sqrt(np.mean(p0[finite] ** 2))) if finite.any() else 'nan',
        'pc2_rmse': float(np.sqrt(np.mean(p1[finite] ** 2))) if finite.any() else 'nan',
        'duv_rmse': float(np.sqrt(np.mean((p0[finite] ** 2 + p1[finite] ** 2)))) if finite.any() else 'nan',
    }


def compare_camera_to_s9(s2_cam, s9_p3, meta, *, exr_decode_exposure=0.0, roi=None):
    """Compara S2 transformado (documentado) con S9. roi = (x,y,w,h) o None."""
    fwd = camera_to_p3(meta['cm1'], meta['cm2'], meta['k1'], meta['k2'], meta['as_shot_neutral'])
    inv = _lu_inv(fwd)
    s2 = np.asarray(s2_cam, dtype=np.float64)
    s9 = np.asarray(s9_p3, dtype=np.float64)
    if s2.shape != s9.shape:
        raise ValueError('S2 %s ≠ S9 %s' % (s2.shape, s9.shape))
    p3_unscaled = apply_rgb(s2, fwd)
    p3_be = p3_unscaled * meta['baseline_gain'] * (2.0 ** float(exr_decode_exposure))
    rt = roundtrip_error(s2, fwd, inv)
    y_s9 = M.luminance(s9)
    y_be = M.luminance(p3_be)
    valid = (y_s9 > 0.005) & (y_be > 0.005)
    diag_ratio = float(np.median(y_s9[valid] / np.maximum(y_be[valid], 1e-12))) if valid.any() else float('nan')
    axes = chroma_axes_from_ref(s9)
    sl = np.s_[:, :]
    if roi:
        x, y, w, h = roi
        sl = np.s_[y:y + h, x:x + w]
    d_be = p3_be[sl] - s9[sl]
    d_un = p3_unscaled[sl] - s9[sl]
    u_be, v_be = M.uv_prime(p3_be[sl])
    u9, v9 = M.uv_prime(s9[sl])
    duv = np.hypot(u_be - u9, v_be - v9)
    duv = duv[np.isfinite(duv)]
    return {
        'roundtrip_s2': rt,
        'roundtrip_reliable': rt['max_abs'] < 1e-6,
        'documented_scale': documented_scale(meta, exr_decode_exposure),
        'diagnostic_median_y_s9_over_p3_be': diag_ratio,
        'residual_rgb_be': {
            'mae': float(np.mean(np.abs(d_be))),
            'rmse': float(np.sqrt(np.mean(d_be * d_be))),
            'max_abs': float(np.max(np.abs(d_be))),
        },
        'residual_rgb_unscaled': {
            'mae': float(np.mean(np.abs(d_un))),
            'rmse': float(np.sqrt(np.mean(d_un * d_un))),
        },
        'chromaticity_be': {
            'duv_mean': float(duv.mean()) if duv.size else 'nan',
            'duv_max': float(duv.max()) if duv.size else 'nan',
            'n': int(duv.size),
        },
        'projection': project_uv_residual(p3_be[sl], s9[sl], axes) if axes['ok'] else {'ok': False},
        'clip_s2': {
            'neg_frac': float(np.mean(s2 < 0)),
            'above_one': float(np.mean(s2 > 1)),
        },
        'clip_s9': {
            'neg_frac': float(np.mean(s9 < 0)),
            'above_one': float(np.mean(s9 > 1)),
        },
        'matrix_fwd': fwd.reshape(-1).tolist(),
        'limitation': (
            'S9 es revelado Resolve (Gen4 / P3-D65 Linear), no cameraToP3(S2). '
            'Una Δu′v′ grande implica que la inversa de S9 no es evidencia concluyente de AHD.'
        ),
    }


def invert_s9_to_camera(s9_p3, meta, *, apply_baseline=True, exr_decode_exposure=0.0):
    inv = p3_to_camera(meta['cm1'], meta['cm2'], meta['k1'], meta['k2'], meta['as_shot_neutral'])
    s9 = np.asarray(s9_p3, dtype=np.float64)
    scale = 1.0
    if apply_baseline:
        scale *= meta['baseline_gain']
    scale *= (2.0 ** float(exr_decode_exposure))
    if scale != 0:
        s9 = s9 / scale
    cam = apply_rgb(s9, inv)
    return cam.astype(np.float32), inv


def known_color_checks():
    """Colores conocidos: neutro de cámara → RGB P3 acromático; rampa; roundtrip."""
    cm1 = [1.0, -0.2, -0.05, -0.4, 1.2, 0.15, -0.06, 0.18, 0.6]
    cm2 = [0.8, -0.15, -0.07, -0.45, 1.3, 0.12, -0.09, 0.21, 0.7]
    n = [0.55, 1.0, 0.65]
    fwd = camera_to_p3(cm1, cm2, 2856, 6504, n)
    p3n = fwd @ np.array(n)
    ratio_rg = float(p3n[0] / p3n[1])
    ratio_bg = float(p3n[2] / p3n[1])
    ramp = np.stack([np.linspace(0, 1, 32)] * 3, -1).reshape(1, 32, 3)
    ramp[..., 1] *= 0.4
    ramp[..., 2] *= 0.3
    rt = roundtrip_error(ramp, fwd, _lu_inv(fwd))
    primaries = np.eye(3, dtype=np.float64).reshape(1, 3, 3)
    back = apply_rgb(apply_rgb(primaries, fwd), _lu_inv(fwd))
    return {
        'neutral_p3_rg': ratio_rg,
        'neutral_p3_bg': ratio_bg,
        'neutral_achromatic': abs(ratio_rg - 1) < 2e-3 and abs(ratio_bg - 1) < 2e-3,
        'ramp_roundtrip': rt,
        'primaries_roundtrip_max': float(np.max(np.abs(back - primaries))),
        'dynamic_range': {'in_min': 0.0, 'in_max': 1.0, 'out_has_neg': bool((apply_rgb(primaries, fwd) < 0).any())},
    }


if __name__ == '__main__':
    print(json.dumps(known_color_checks(), indent=2))
