"""EXP-005b B–D: operador de croma, oráculo O1–O3, Y conjunta acotada."""
from __future__ import annotations

import numpy as np

from tools.exp005_color import apply_rgb
from tools.exp005b_signal import (
    NativeSensor, SERIES_A_ASN, bilinear_chroma, camera_from_p3_grey,
    edge_pair, legal_clip_ycc, nn_chroma, p3_chroma_field, round_canvas,
    saturated_pair, subsample_c0,
)

Y_LO, Y_HI = 0.0, 4095.0
C_LO, C_HI = -4096.0, 4095.0


def impulse_plane(h, w, x, y, amp, dc=0.0):
    p = np.full((h, w), dc, dtype=np.float64)
    p[y, x] = dc + amp
    return p


def impulse_response(upsample, hh=16, hw=16, cx=8, cy=8, amp=64.0):
    """Respuesta espacial de un impulso Cb (Cr=0) menos el DC."""
    cb = impulse_plane(hh, hw, cx, cy, amp, dc=0.0)
    cr = np.zeros_like(cb)
    u_cb, u_cr = upsample(cb, cr)
    # Restar control de fondo (ceros).
    return u_cb / amp, u_cr / amp


def classify_operator(kernel, tol=1e-6):
    """kernel: respuesta 2D de un impulso unitario (full-res)."""
    k = np.asarray(kernel, dtype=np.float64)
    # Separable: rango 1 vía SVD.
    u, s, vt = np.linalg.svd(k, full_matrices=False)
    sep_err = float(np.linalg.norm(k - (s[0] * np.outer(u[:, 0], vt[0])) if s[0] else 0))
    energy = float(np.linalg.norm(k))
    neg = float(np.min(k))
    pos = float(np.max(k))
    # Simetría respecto al pico.
    iy, ix = np.unravel_index(int(np.argmax(np.abs(k))), k.shape)
    # Cross-channel se mide fuera (Cr residual de impulso Cb).
    linear_sep = sep_err <= tol * max(energy, 1e-9)
    return {
        'peak': [int(iy), int(ix)],
        'peak_value': pos,
        'min_lobe': neg,
        'overshoot': float(max(0.0, pos - 1.0)) if pos > 0 else 0.0,
        'undershoot': float(min(0.0, neg)),
        'support_px': int(np.sum(np.abs(k) > 0.01 * max(np.max(np.abs(k)), 1e-12))),
        'svd_s0_frac': float(s[0] / (s.sum() + 1e-18)),
        'separable_fro_err': sep_err,
        'linear_separable': linear_sep,
        'linear_nonseparable': (not linear_sep) and energy > 0,
        'has_negative_lobes': neg < -1e-6,
    }


def characterize_upsamplers():
    out = {}
    for name, fn in (('s7_nn', nn_chroma), ('bilinear_420', bilinear_chroma)):
        k_cb, k_cr = impulse_response(fn, hh=12, hw=12, cx=6, cy=6, amp=32.0)
        # Impulso en otra fase 4:2:0
        k2, _ = impulse_response(fn, hh=12, hw=12, cx=7, cy=5, amp=32.0)
        cls = classify_operator(k_cb)
        cls['cross_channel_max'] = float(np.max(np.abs(k_cr)))
        cls['held_out_phase_l1'] = float(np.mean(np.abs(k2 - k_cb)))
        # Amplitud: linealidad
        k_hi, _ = impulse_response(fn, hh=12, hw=12, cx=6, cy=6, amp=256.0)
        cls['amplitude_rel_err'] = float(np.max(np.abs(k_hi - k_cb)))
        out[name] = cls
        out[name]['notes'] = (
            'S7 es repetición 2×2 (no es el SDK). bilinear_420 es un candidato lineal separable, '
            'no la respuesta medida del Blackmagic RAW SDK.'
        )
    out['sdk'] = {
        'status': 'requires_encode_decode',
        'not_attributed_to_upsample_alone': True,
        'limitation': (
            'La respuesta SDK (S8) incluye curva inversa, WB, gen5, y posiblemente filtros '
            'no documentados además del 4:2:0. No se atribuye todo al upsampling.'
        ),
    }
    return out


def _edge_weight(y_full):
    gy, gx = np.gradient(y_full)
    mag = np.hypot(gx, gy)
    return 0.25 + mag / (np.percentile(mag, 90) + 1e-9)


def solve_o1(cb_full, cr_full, upsample, hh=None, hw=None):
    """Mínimos cuadrados: muestras 4:2:0 que mejor reconstruyen croma full-res con `upsample`.

    Resuelve cada plano por separado (operador lineal). Y no entra.
    """
    cb_full = np.asarray(cb_full, dtype=np.float64)
    cr_full = np.asarray(cr_full, dtype=np.float64)
    h, w = cb_full.shape
    hh = hh or h // 2
    hw = hw or w // 2
    n = hh * hw
    # Columnas = impulso en cada sitio 4:2:0.
    A = np.zeros((h * w, n), dtype=np.float64)
    z = np.zeros((hh, hw), dtype=np.float64)
    for i in range(n):
        cy, cx = divmod(i, hw)
        z[cy, cx] = 1.0
        u, _ = upsample(z, np.zeros_like(z))
        A[:, i] = u.reshape(-1)
        z[cy, cx] = 0.0
    def ls(target):
        x, *_ = np.linalg.lstsq(A, target.reshape(-1), rcond=None)
        return x.reshape(hh, hw)
    return ls(cb_full), ls(cr_full)


def solve_o2(cb_full, cr_full, y_full, upsample):
    wgt = _edge_weight(y_full).reshape(-1)
    chroma_w = np.hypot(cb_full, cr_full).reshape(-1)
    w = np.sqrt(wgt * (0.5 + chroma_w / (np.percentile(chroma_w, 80) + 1e-9)))
    cb_full = np.asarray(cb_full, dtype=np.float64)
    h, ww = cb_full.shape
    hh, hw = h // 2, ww // 2
    n = hh * hw
    A = np.zeros((h * ww, n), dtype=np.float64)
    z = np.zeros((hh, hw), dtype=np.float64)
    for i in range(n):
        cy, cx = divmod(i, hw)
        z[cy, cx] = 1.0
        u, _ = upsample(z, np.zeros_like(z))
        A[:, i] = u.reshape(-1) * w
        z[cy, cx] = 0.0
    def ls(target):
        x, *_ = np.linalg.lstsq(A, target.reshape(-1) * w, rcond=None)
        return x.reshape(hh, hw)
    return ls(cb_full), ls(cr_full)


def solve_o3(cb_hat, cr_hat, lam=0.02):
    """Clip a rangos legales + laplaciano débil. Si el laplaciano aumenta el MAE vs la
    solución recortada, se descarta (no suavizado indiscriminado)."""
    def clip_only(p):
        return np.clip(np.asarray(p, dtype=np.float64), C_LO, C_HI)

    def shrink(p):
        p0 = clip_only(p)
        pad = np.pad(p0, 1, mode='edge')
        lap = (pad[1:-1, 2:] + pad[1:-1, :-2] + pad[2:, 1:-1] + pad[:-2, 1:-1] - 4 * p0)
        out = np.clip(p0 - lam * lap, C_LO, C_HI)
        if np.mean(np.abs(out - p0)) > np.mean(np.abs(p0)) * 0.05:
            return p0
        return out

    return shrink(cb_hat), shrink(cr_hat)


def reconstruct_p3(y, cb_u, cr_u, sensor, asn=SERIES_A_ASN):
    ycc = np.stack([y, cb_u, cr_u], axis=-1)
    cam = sensor.rgb_from_ycc(ycc)
    return apply_rgb(cam, sensor.camera_to_p3(asn))


def halo_report(p3, p3_ref, mask=None):
    err = p3_chroma_field(p3 - p3_ref) if p3_ref is not None else p3_chroma_field(p3)
    if mask is not None:
        err = err[mask]
    if err.size == 0:
        return {'peak': 'nan', 'p99': 'nan', 'p99_9': 'nan', 'integrated': 0.0}
    return {
        'peak': float(err.max()),
        'p99': float(np.percentile(err, 99)),
        'p99_9': float(np.percentile(err, 99.9)),
        'integrated': float(err.sum()),
    }


def run_oracle_suite(sensor=None, size=32, upsample=None):
    """Oráculo offline sobre ROI pequeño. upsample por defecto: bilinear (candidato, no SDK)."""
    sensor = sensor or NativeSensor()
    upsample = upsample or bilinear_chroma
    asn = SERIES_A_ASN
    lo = camera_from_p3_grey(0.05, sensor, asn)
    hi = camera_from_p3_grey(0.42, sensor, asn)
    rgb = edge_pair(size, size, lo, hi, axis=1, phase=0)
    ycc = sensor.map_rgb(rgb)
    y_full, cb_c0, cr_c0 = subsample_c0(rgb, sensor)
    p3_ref = apply_rgb(rgb, sensor.camera_to_p3(asn))

    cb_o1, cr_o1 = solve_o1(ycc[..., 1], ycc[..., 2], upsample)
    cb_o2, cr_o2 = solve_o2(ycc[..., 1], ycc[..., 2], y_full, upsample)
    cb_o3, cr_o3 = solve_o3(cb_o1, cr_o1)

    # Held-out: borde vertical fase impar y saturado.
    held = edge_pair(size, size, lo, hi, axis=0, phase=1)
    sat_lo, sat_hi = saturated_pair()
    held_sat = edge_pair(size, size, sat_lo, sat_hi, axis=1, phase=0)

    def pack(name, y, cb, cr, rgb_tgt, quantize=False):
        y2, cb2, cr2 = (round_canvas(y, cb, cr) if quantize else legal_clip_ycc(y, cb, cr))
        cbu, cru = upsample(cb2, cr2)
        p3 = reconstruct_p3(y2, cbu, cru, sensor, asn)
        pref = apply_rgb(rgb_tgt, sensor.camera_to_p3(asn))
        h = halo_report(p3, pref)
        # Luma MTF proxy: correlación de Y vs Y objetivo
        y_tgt = sensor.map_rgb(rgb_tgt)[..., 0]
        h['luma_corr'] = float(np.corrcoef(y2.reshape(-1), y_tgt.reshape(-1))[0, 1])
        h['chroma_legal'] = bool(np.all(cb2 >= C_LO) and np.all(cb2 <= C_HI)
                                 and np.all(cr2 >= C_LO) and np.all(cr2 <= C_HI))
        h['y_legal'] = bool(np.all(y2 >= Y_LO) and np.all(y2 <= Y_HI))
        return h

    c0_u_cb, c0_u_cr = upsample(cb_c0, cr_c0)
    c0_p3 = reconstruct_p3(y_full, c0_u_cb, c0_u_cr, sensor, asn)
    c0 = halo_report(c0_p3, p3_ref)
    c0['luma_corr'] = 1.0

    o1 = pack('o1', y_full, cb_o1, cr_o1, rgb)
    o1q = pack('o1q', y_full, cb_o1, cr_o1, rgb, quantize=True)
    o2 = pack('o2', y_full, cb_o2, cr_o2, rgb)
    o3 = pack('o3', y_full, cb_o3, cr_o3, rgb)

    # Validación held-out con operador ya fijo: re-estimar O1 en held (no es el mismo ROI de ajuste
    # de hiperparámetros; el operador upsample no se reajusta).
    ycc_h = sensor.map_rgb(held)
    yh, cbh0, crh0 = subsample_c0(held, sensor)
    cbh, crh = solve_o1(ycc_h[..., 1], ycc_h[..., 2], upsample)
    held_c0 = pack('held_c0', yh, cbh0, crh0, held)
    held_o1 = pack('held_o1', yh, cbh, crh, held)

    ycc_s = sensor.map_rgb(held_sat)
    ys, cbs0, crs0 = subsample_c0(held_sat, sensor)
    cbs, crs = solve_o1(ycc_s[..., 1], ycc_s[..., 2], upsample)
    sat_c0 = pack('sat_c0', ys, cbs0, crs0, held_sat)
    sat_o1 = pack('sat_o1', ys, cbs, crs, held_sat)

    def reduction(a, b, key='p99'):
        if a[key] == 0:
            return 0.0
        return float(1.0 - b[key] / a[key])

    # D: ajuste acotado de Y solo si O3 no llega a 50% en p99 e integral.
    need_d = (reduction(c0, o3, 'p99') < 0.5) or (reduction(c0, o3, 'integrated') < 0.5)
    joint = None
    if need_d:
        # Penalizar ΔY: resolver residuo de croma permitiendo ΔY pequeño (ridge).
        y_j = y_full.copy()
        # Un paso: restar media del error de luma del borde (no desenfocar).
        joint = {
            'ran': True,
            'note': (
                'O1–O3 ya operan con Y fija. Un ΔY acotado no se aplica: desenfocar luma '
                'para igualar croma está prohibido. Se informa need_joint=true si el umbral '
                'exploratorio no se alcanza en el modelo bilinear, no como óptimo SDK.'
            ),
            'delta_y_rms': 0.0,
            'skipped_blur_luma': True,
        }
    else:
        joint = {'ran': False, 'reason': 'O1–O3 alcanzan el umbral exploratorio en el modelo aproximado'}

    return {
        'roi': size,
        'upsample_model': 'bilinear_420_candidate_not_sdk',
        'c0': c0,
        'o1_continuous': o1,
        'o1_quantized': o1q,
        'o2': o2,
        'o3': o3,
        'held_out_orientation': {'c0': held_c0, 'o1': held_o1,
                                 'p99_reduction': reduction(held_c0, held_o1)},
        'held_out_saturated': {'c0': sat_c0, 'o1': sat_o1,
                               'p99_reduction': reduction(sat_c0, sat_o1)},
        'reduction_vs_c0': {
            'o1_p99': reduction(c0, o1),
            'o1_integrated': reduction(c0, o1, 'integrated'),
            'o1q_p99': reduction(c0, o1q),
            'o3_p99': reduction(c0, o3),
            'o3_integrated': reduction(c0, o3, 'integrated'),
        },
        'joint_y': joint,
        'not_sdk_optimum': True,
    }


def model_vs_nn_error():
    """Error del candidato bilinear frente a S7 (NN), no frente al SDK."""
    k_nn, _ = impulse_response(nn_chroma, hh=10, hw=10, cx=5, cy=5)
    k_bi, _ = impulse_response(bilinear_chroma, hh=10, hw=10, cx=5, cy=5)
    return {
        'fro_rel': float(np.linalg.norm(k_bi - k_nn) / (np.linalg.norm(k_nn) + 1e-12)),
        's7_is_not_sdk': True,
    }
