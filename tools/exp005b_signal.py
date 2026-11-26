"""Mapa NativeSensor Pocket OG y hipótesis A (neutro cámara vs P3)."""
from __future__ import annotations

import json
import os

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIGNAL_JSON = os.path.join(ROOT, 'model', 'pocket_og_cinemadng_signal.json')

from tools.exp005_color import camera_to_p3, p3_to_camera, apply_rgb

# AsShotNeutral de la serie A (manifiesto A01–A05).
SERIES_A_ASN = np.array([0.6306, 1.0, 0.7728], dtype=np.float64)


def load_og_signal(path=SIGNAL_JSON):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def code_from_ycc_matrix(doc=None):
    doc = doc or load_og_signal()
    return np.asarray(doc['code_from_ycc'], dtype=np.float64)


def ycc_from_code_matrix(doc=None):
    return np.linalg.inv(code_from_ycc_matrix(doc))


class SensorCurve:
    def __init__(self, d):
        self.linear_a = float(d['linear_a'])
        self.linear_b = float(d['linear_b'])
        self.quad_x = float(d['quadratic_x'])
        self.quad_y = float(d['quadratic_y'])
        self.quad_z = float(d['quadratic_z'])
        self.knee = float(d['knee'])
        self.knee_raw = float(d['knee_raw'])

    def encode(self, raw):
        raw = np.asarray(raw, dtype=np.float64)
        lo = raw < self.knee_raw
        disc = np.maximum(self.quad_y ** 2 + 4 * self.quad_x * (raw - self.quad_z), 0.0)
        hi = (-self.quad_y + np.sqrt(disc)) / (2 * self.quad_x)
        return np.where(lo, (raw - self.linear_b) / self.linear_a, hi)

    def decode(self, code):
        code = np.asarray(code, dtype=np.float64)
        return np.where(code < self.knee,
                        self.linear_a * code + self.linear_b,
                        self.quad_x * code * code + self.quad_y * code + self.quad_z)


class NativeSensor:
    """Misma rama hasCurve de NativeSensor::mapDouble (sin rcm_tone en el JSON OG)."""

    def __init__(self, doc=None):
        doc = doc or load_og_signal()
        self.scale = np.asarray(doc.get('input_channel_scale', [1, 1, 1]), dtype=np.float64)
        self.curve = SensorCurve(doc['sensor_curve'])
        self.ycc_from_code = ycc_from_code_matrix(doc)
        self.code_from_ycc = code_from_ycc_matrix(doc)
        fp = doc['dng_fingerprint']
        self.cm1 = np.asarray(fp['CM1'], dtype=np.float64).reshape(9)
        self.cm2 = np.asarray(fp['CM2'], dtype=np.float64).reshape(9)
        self.k1 = float(fp['K1'])
        self.k2 = float(fp['K2'])

    def map_rgb(self, rgb):
        rgb = np.asarray(rgb, dtype=np.float64)
        codes = self.curve.encode(rgb * self.scale)
        return np.einsum('...i,ji->...j', codes, self.ycc_from_code)

    def codes_from_ycc(self, ycc):
        ycc = np.asarray(ycc, dtype=np.float64)
        return np.einsum('...i,ji->...j', ycc, self.code_from_ycc)

    def rgb_from_ycc(self, ycc):
        return self.curve.decode(self.codes_from_ycc(ycc)) / self.scale

    def camera_to_p3(self, asn=SERIES_A_ASN):
        return camera_to_p3(self.cm1, self.cm2, self.k1, self.k2, asn)

    def p3_to_camera(self, asn=SERIES_A_ASN):
        return p3_to_camera(self.cm1, self.cm2, self.k1, self.k2, asn)


def subsample_c0(rgb, sensor):
    """C0: mapa de la media 2×2 del RGB lineal (producción)."""
    rgb = np.asarray(rgb, dtype=np.float64)
    h, w = rgb.shape[:2]
    mean = rgb.reshape(h // 2, 2, w // 2, 2, 3).mean(axis=(1, 3))
    ycc = sensor.map_rgb(rgb)
    y = ycc[..., 0]
    c = sensor.map_rgb(mean)
    return y, c[..., 1], c[..., 2]


def nn_chroma(cb, cr):
    """S7 local: croma por (y//2, x//2)."""
    return np.repeat(np.repeat(cb, 2, 0), 2, 1), np.repeat(np.repeat(cr, 2, 0), 2, 1)


def bilinear_chroma(cb, cr):
    """Upsample 4:2:0 bilinear alineado a sitios pares (candidato lineal separable)."""
    cb = np.asarray(cb, dtype=np.float64)
    cr = np.asarray(cr, dtype=np.float64)
    hh, hw = cb.shape
    y = np.linspace(0, hh - 0.5, hh * 2)
    x = np.linspace(0, hw - 0.5, hw * 2)
    yi = np.clip(y - 0.25, 0, hh - 1)
    xi = np.clip(x - 0.25, 0, hw - 1)

    def samp(p):
        y0 = np.floor(yi).astype(int)
        x0 = np.floor(xi).astype(int)
        y1 = np.minimum(y0 + 1, hh - 1)
        x1 = np.minimum(x0 + 1, hw - 1)
        fy = yi - y0
        fx = xi - x0
        out = np.empty((hh * 2, hw * 2), dtype=np.float64)
        for j in range(hh * 2):
            wy0, wy1 = 1 - fy[j], fy[j]
            row = (p[y0[j]][x0] * (1 - fx) * wy0 + p[y0[j]][x1] * fx * wy0
                   + p[y1[j]][x0] * (1 - fx) * wy1 + p[y1[j]][x1] * fx * wy1)
            out[j] = row
        return out

    return samp(cb), samp(cr)


def legal_clip_ycc(y, cb, cr):
    return (np.clip(y, 0, 4095),
            np.clip(cb, -4096, 4095),
            np.clip(cr, -4096, 4095))


def round_canvas(y, cb, cr):
    y, cb, cr = legal_clip_ycc(y, cb, cr)
    return (np.clip(np.rint(y), 0, 4095),
            np.clip(np.rint(cb), -4096, 4095),
            np.clip(np.rint(cr), -4096, 4095))


def p3_chroma_field(p3):
    p3 = np.asarray(p3, dtype=np.float64)
    return np.hypot(p3[..., 2] - p3[..., 1], p3[..., 0] - p3[..., 1])


def edge_pair(h, w, lo_rgb, hi_rgb, axis=1, phase=0, diagonal=False):
    lo = np.asarray(lo_rgb, dtype=np.float64)
    hi = np.asarray(hi_rgb, dtype=np.float64)
    img = np.empty((h, w, 3), dtype=np.float64)
    if diagonal:
        yy, xx = np.mgrid[0:h, 0:w]
        mask = (xx + yy) < (w + h) // 2 + phase
        img[:] = hi
        img[mask] = lo
        return img
    if axis == 1:
        split = w // 2 + phase
        img[:, :split] = lo
        img[:, split:] = hi
    else:
        split = h // 2 + phase
        img[:split] = lo
        img[split:] = hi
    return img


def camera_equal(g):
    return np.array([g, g, g], dtype=np.float64)


def camera_asn(g, asn=SERIES_A_ASN):
    n = np.asarray(asn, dtype=np.float64)
    return g * n / n[1]


def camera_from_p3_grey(g, sensor, asn=SERIES_A_ASN):
    inv = sensor.p3_to_camera(asn)
    return apply_rgb(np.array([g, g, g], dtype=np.float64), inv)


def saturated_pair():
    return np.array([0.45, 0.04, 0.03], dtype=np.float64), np.array([0.05, 0.08, 0.40], dtype=np.float64)


def analyze_hypothesis_a(sensor=None, asn=SERIES_A_ASN):
    """Diferencia cámara R=G=B vs ∝ AsShotNeutral vs inv(cameraToP3)·gris P3."""
    sensor = sensor or NativeSensor()
    fwd = sensor.camera_to_p3(asn)
    levels = (0.02, 0.08, 0.18, 0.35, 0.55)
    rows = []
    for g in levels:
        kinds = {
            'camera_equal': camera_equal(g),
            'camera_asn': camera_asn(g, asn),
            'p3_grey_inv': camera_from_p3_grey(g, sensor, asn),
        }
        rec = {'g': g}
        for name, rgb in kinds.items():
            ycc = sensor.map_rgb(rgb)
            p3 = apply_rgb(rgb, fwd)
            rec[name] = {
                'rgb': [float(x) for x in rgb],
                'ycc': [float(x) for x in ycc],
                'p3': [float(x) for x in p3],
                'p3_chroma': float(p3_chroma_field(p3)),
                'cb': float(ycc[1]),
                'cr': float(ycc[2]),
                'chroma_code': float(np.hypot(ycc[1], ycc[2])),
            }
        rec['asn_vs_p3_rgb_max'] = float(np.max(np.abs(
            kinds['camera_asn'] / (kinds['camera_asn'][1] + 1e-18)
            - kinds['p3_grey_inv'] / (kinds['p3_grey_inv'][1] + 1e-18))))
        rows.append(rec)

    # Proporcionalidad lineal ASN vs P3 (la curva aún no entra).
    a = camera_asn(0.2, asn)
    b = camera_from_p3_grey(0.2, sensor, asn)
    linear_same_chroma = float(np.max(np.abs(a / a[1] - b / b[1])))

    # Tras la curva: croma de código vs intensidad para un gris P3.
    cb_cr = np.array([sensor.map_rgb(camera_from_p3_grey(g, sensor, asn))[1:] for g in levels])
    chroma_varies = float(np.max(np.ptp(cb_cr, axis=0)))
    eq_chroma = np.array([sensor.map_rgb(camera_equal(g))[1:] for g in levels])
    equal_is_achromatic = float(np.max(np.abs(eq_chroma)))

    # Borde P3-gris: error C0 vs croma full-res en el dominio de código.
    h = w = 64
    lo, hi = camera_from_p3_grey(0.04, sensor, asn), camera_from_p3_grey(0.45, sensor, asn)
    img = edge_pair(h, w, lo, hi, axis=1, phase=0)
    ycc_full = sensor.map_rgb(img)
    y, cb, cr = subsample_c0(img, sensor)
    cb_nn, cr_nn = nn_chroma(cb, cr)
    err_nn = np.hypot(cb_nn - ycc_full[..., 1], cr_nn - ycc_full[..., 2])
    cb_bi, cr_bi = bilinear_chroma(cb, cr)
    err_bi = np.hypot(cb_bi - ycc_full[..., 1], cr_bi - ycc_full[..., 2])

    # Cámara R=G=B: croma de código ~0; el halo P3 es colorimétrico, no de Cb/Cr.
    img_eq = edge_pair(h, w, camera_equal(0.04), camera_equal(0.45), axis=1)
    ycc_eq = sensor.map_rgb(img_eq)
    p3_eq = apply_rgb(img_eq, fwd)

    return {
        'asn': [float(x) for x in asn],
        'levels': rows,
        'asn_matches_p3_inverse_chromaticity_max': linear_same_chroma,
        'p3_grey_code_chroma_range': chroma_varies,
        'camera_equal_code_chroma_max': equal_is_achromatic,
        'hypothesis': {
            'p3_achromatic_is_camera_asn_up_to_scale': linear_same_chroma < 1e-9,
            'p3_achromatic_approx_asn': linear_same_chroma < 2e-3,
            'camera_equal_is_codec_achromatic': equal_is_achromatic < 1e-6,
            'p3_grey_has_intensity_dependent_codec_chroma': chroma_varies > 1.0,
            'not_assumed_a_priori': True,
            'note': (
                'cameraToP3(AsShotNeutral) is only approximately P3-grey (Bradford/McCamy); '
                'inv(cameraToP3)·[g,g,g] is close to ∝ AsShotNeutral, not identical.'
            ),
        },
        'c0_edge_p3_grey': {
            'nn_chroma_mae': float(err_nn.mean()),
            'nn_chroma_max': float(err_nn.max()),
            'bilinear_chroma_mae': float(err_bi.mean()),
            'bilinear_chroma_max': float(err_bi.max()),
            'full_cb_range': float(np.ptp(ycc_full[..., 1])),
            'full_cr_range': float(np.ptp(ycc_full[..., 2])),
        },
        'camera_equal_edge': {
            'code_chroma_max': float(np.max(np.hypot(ycc_eq[..., 1], ycc_eq[..., 2]))),
            'p3_chroma_max': float(np.max(p3_chroma_field(p3_eq))),
        },
    }
