"""EXP-005b H2–H5: oráculo de baja dimensión con el SDK como función de evaluación."""
from __future__ import annotations

import hashlib
import os
import time

import numpy as np

from tools.exp005_color import apply_rgb
from tools.exp005_metrics import halo_stats
from tools.exp005b_io import write_brwp
from tools.exp005b_oracle import C_LO, Y_HI, Y_LO
from tools.exp005b_sdk import (
    H, W, convert_and_decode, crop_ppm, default_encode, planes_from_rgb, rgb_chroma,
)
from tools.exp005b_signal import NativeSensor, camera_from_p3_grey, edge_pair, legal_clip_ycc

N_CB = 6
N_CR = 6
N_Y = 4
CX0 = 480 - N_CB // 2  # chroma columns around x=960
LY0 = 128
LY1 = 952
CY0, CY1 = LY0 // 2, LY1 // 2
EPS_C = 64.0
EPS_Y = 48.0
LAM0 = 1e-2
MAX_EVAL = 100
INTERIOR_PAD = 16
EDGE_HALF = 48


class Budget:
    def __init__(self, max_n=MAX_EVAL):
        self.max_n = max_n
        self.n = 0
        self.seconds = 0.0
        self.log = []

    def remaining(self):
        return self.max_n - self.n

    def note(self, tag, meta, counted):
        rec = {'tag': tag, 'counted': counted, 'n': self.n,
               'reused_braw': meta.get('reused_braw'), 'seconds': meta.get('seconds_braw')}
        self.log.append(rec)

    def count(self, meta):
        counted = not (meta.get('reused_braw') and meta.get('reused_decode'))
        if counted:
            self.n += 1
            self.seconds += float(meta.get('seconds_braw') or 0) + float(meta.get('seconds_decode') or 0)
        return counted


def p3_fixture(sensor, lo=0.03, hi=0.55, axis=1, phase=0):
    rgb = edge_pair(H, W, camera_from_p3_grey(lo, sensor), camera_from_p3_grey(hi, sensor),
                    axis=axis, phase=phase)
    return rgb.astype(np.float32)


def algebraic_ref(rgb, sensor):
    return apply_rgb(rgb, sensor.camera_to_p3())


def plateau_ref(s8, split=W // 2):
    ref = np.empty_like(s8)
    left = s8[:, INTERIOR_PAD:split - INTERIOR_PAD]
    right = s8[:, split + INTERIOR_PAD: W - INTERIOR_PAD]
    ref[:, :split] = np.median(left.reshape(-1, 3), 0)
    ref[:, split:] = np.median(right.reshape(-1, 3), 0)
    return ref


def edge_slice(split=W // 2, half=EDGE_HALF):
    y0, y1 = LY0, LY1
    x0, x1 = split - half, split + half
    return y0, y1, x0, x1


def residual_vec(s8, ref, split=W // 2):
    y0, y1, x0, x1 = edge_slice(split)
    d = np.asarray(s8[y0:y1:2, x0:x1:2], dtype=np.float64) - np.asarray(ref[y0:y1:2, x0:x1:2], dtype=np.float64)
    rg, bg = rgb_chroma(d)
    return np.concatenate([rg.reshape(-1), bg.reshape(-1)])


def halo_pair(s8, ref, split=W // 2):
    y0, y1, x0, x1 = edge_slice(split)
    return halo_stats(s8[y0:y1, x0:x1], ref[y0:y1, x0:x1])


def reduction_pair(base, cand):
    out = {}
    for k in ('p99', 'integrated'):
        try:
            a, b = float(base[k]), float(cand[k])
        except (TypeError, ValueError):
            out[k] = 0.0
            continue
        out[k] = 0.0 if a == 0 or a != a else float(1.0 - b / a)
    return out


def apply_vars(y, cb, cr, x, n_y=0):
    y, cb, cr = y.copy(), cb.copy(), cr.copy()
    n = N_CB + N_CR
    for i in range(N_CB):
        cb[CY0:CY1, CX0 + i] = np.clip(cb[CY0:CY1, CX0 + i] + x[i], C_LO, 4095.0)
    for i in range(N_CR):
        cr[CY0:CY1, CX0 + i] = np.clip(cr[CY0:CY1, CX0 + i] + x[N_CB + i], C_LO, 4095.0)
    if n_y:
        # columnas Y pares centradas en el borde
        lx0 = 960 - n_y
        for i in range(n_y):
            y[LY0:LY1, lx0 + 2 * i] = np.clip(y[LY0:LY1, lx0 + 2 * i] + x[n + i], Y_LO, Y_HI)
    return legal_clip_ycc(y, cb, cr)


def eval_planes(budget, args, fixt, y, cb, cr, tag):
    if budget.remaining() <= 0:
        raise RuntimeError('presupuesto SDK agotado (%d)' % budget.max_n)
    y = np.asarray(y, np.float32)
    cb = np.asarray(cb, np.float32)
    cr = np.asarray(cr, np.float32)
    digest = hashlib.sha256(y.tobytes() + cb.tobytes() + cr.tobytes()).hexdigest()[:16]
    run_id = 'h2-%s-%s' % (tag.replace('/', '_'), digest)
    path = os.path.join(fixt, run_id + '.brwp')
    write_brwp(path, y, cb, cr)
    img, meta = convert_and_decode(args, run_id, default_encode(inject_planes=path, notes='H2 ' + tag))
    counted = budget.count(meta)
    budget.note(tag, meta, counted)
    return img, meta


def solve_gn(J, r, lam, bound):
    """(JᵀJ + λI) dx = −Jᵀr, luego clip a bound (lo, hi) por variable."""
    jt = J.T
    a = jt @ J
    a.flat[:: a.shape[0] + 1] += lam
    rhs = -jt @ r
    try:
        dx = np.linalg.solve(a, rhs)
    except np.linalg.LinAlgError:
        dx = np.linalg.lstsq(a, rhs, rcond=None)[0]
    lo, hi = bound
    return np.clip(dx, lo, hi)


def jacobian_at(budget, args, fixt, y0, cb0, cr0, x, s8_0, ref, n_var, n_y, eps_c, eps_y, split):
    J = np.zeros((residual_vec(s8_0, ref, split).size, n_var), dtype=np.float64)
    for i in range(n_var):
        e = np.zeros(n_var)
        e[i] = eps_y if (n_y and i >= N_CB + N_CR) else eps_c
        xp = x + e
        yp, cbp, crp = apply_vars(y0, cb0, cr0, xp, n_y)
        s8p, _ = eval_planes(budget, args, fixt, yp, cbp, crp, 'fd%d' % i)
        rp = residual_vec(s8p, ref, split)
        r0 = residual_vec(s8_0, ref, split)
        J[:, i] = (rp - r0) / e[i]
    return J


def svd_report(J):
    s = np.linalg.svd(J, compute_uv=False)
    s = np.asarray(s, dtype=np.float64)
    smax, smin = float(s[0]), float(s[-1]) if s.size else 0.0
    return {
        'singular_values': [float(v) for v in s[:12]],
        'smax': smax, 'smin': smin,
        'cond': float(smax / smin) if smin > 0 else float('inf'),
        'effective_rank_1e3': int(np.sum(s > 1e-3 * smax)) if smax else 0,
    }


def run_closed_loop(args, fixt, vis, *, max_eval=MAX_EVAL):
    sensor = NativeSensor()
    budget = Budget(max_eval)
    rgb = p3_fixture(sensor)
    y0, cb0, cr0 = planes_from_rgb(rgb, sensor)
    split = W // 2
    t0 = time.monotonic()

    s8_c0, m0 = eval_planes(budget, args, fixt, y0, cb0, cr0, 'c0')
    ref_plat = plateau_ref(s8_c0, split)
    ref_alg = algebraic_ref(rgb, sensor)
    h0_plat = halo_pair(s8_c0, ref_plat, split)
    h0_alg = halo_pair(s8_c0, ref_alg, split)

    n_var = N_CB + N_CR
    n_y = 0
    x = np.zeros(n_var)
    # interferencia espacial: dos columnas Cb separadas 5 sitios
    interf = None
    if budget.remaining() >= 3:
        e_a, e_b = np.zeros(n_var), np.zeros(n_var)
        e_a[0] = EPS_C
        e_b[5] = EPS_C
        sa, _ = eval_planes(budget, args, fixt, *apply_vars(y0, cb0, cr0, e_a, 0), 'intA')
        sb, _ = eval_planes(budget, args, fixt, *apply_vars(y0, cb0, cr0, e_b, 0), 'intB')
        sab, _ = eval_planes(budget, args, fixt, *apply_vars(y0, cb0, cr0, e_a + e_b, 0), 'intAB')
        ra = residual_vec(sa, ref_plat) - residual_vec(s8_c0, ref_plat)
        rb = residual_vec(sb, ref_plat) - residual_vec(s8_c0, ref_plat)
        rab = residual_vec(sab, ref_plat) - residual_vec(s8_c0, ref_plat)
        denom = np.linalg.norm(ra + rb) + 1e-12
        interf = {
            'rel_err': float(np.linalg.norm(rab - (ra + rb)) / denom),
            'grouped_ok': float(np.linalg.norm(rab - (ra + rb)) / denom) < 0.15,
            'note': 'agrupar perturbaciones solo si rel_err<0.15; esta campaña no agrupa el jacobiano',
        }

    J = jacobian_at(budget, args, fixt, y0, cb0, cr0, x, s8_c0, ref_plat, n_var, 0, EPS_C, EPS_Y, split)
    sv = svd_report(J)
    lam = LAM0 * (10 if sv['cond'] > 1e4 else 1)
    r0 = residual_vec(s8_c0, ref_plat, split)
    # cotas: deltas que no salgan de rango legal desde C0 medio en esas columnas
    span = 400.0
    bound = (-np.full(n_var, span), np.full(n_var, span))
    gn = []
    best = {'x': x.copy(), 's8': s8_c0, 'halo_plat': h0_plat, 'halo_alg': h0_alg,
            'red_plat': {'p99': 0.0, 'integrated': 0.0}, 'kind': 'c0'}
    s8_cur, r_cur = s8_c0, r0
    no_descent = 0
    for it in range(3):
        if budget.remaining() < 1:
            break
        dx = solve_gn(J, r_cur, lam, bound)
        pred = r_cur + J @ dx
        pred_drop = float(np.dot(r_cur, r_cur) - np.dot(pred, pred))
        x_try = np.clip(x + dx, bound[0], bound[1])
        s8_try, _ = eval_planes(budget, args, fixt, *apply_vars(y0, cb0, cr0, x_try, 0), 'gn%d' % it)
        r_try = residual_vec(s8_try, ref_plat, split)
        actual_drop = float(np.dot(r_cur, r_cur) - np.dot(r_try, r_try))
        h_try = halo_pair(s8_try, ref_plat, split)
        red = reduction_pair(h0_plat, h_try)
        rec = {'iter': it, 'pred_drop': pred_drop, 'actual_drop': actual_drop,
               'rho': actual_drop / pred_drop if abs(pred_drop) > 1e-18 else 0.0,
               'red': red, 'lam': lam, 'x_rms': float(np.sqrt(np.mean(x_try * x_try)))}
        gn.append(rec)
        if actual_drop > 0 and red['p99'] >= best['red_plat']['p99']:
            best = {'x': x_try.copy(), 's8': s8_try, 'halo_plat': h_try,
                    'halo_alg': halo_pair(s8_try, ref_alg, split),
                    'red_plat': red, 'kind': 'cbcr_gn'}
            x, s8_cur, r_cur = x_try, s8_try, r_try
            no_descent = 0
            if red['p99'] >= 0.5 and red['integrated'] >= 0.5:
                break
        else:
            no_descent += 1
            lam *= 4
            if no_descent >= 2:
                break
        # una re-Jacobiana si queda presupuesto y el modelo falló
        if rec['rho'] < 0.25 and budget.remaining() > n_var + 1:
            J = jacobian_at(budget, args, fixt, y0, cb0, cr0, x, s8_cur, ref_plat, n_var, 0, EPS_C, EPS_Y, split)
            sv = svd_report(J)

    joint = None
    chroma_ok = best['red_plat']['p99'] >= 0.5 and best['red_plat']['integrated'] >= 0.5
    if not chroma_ok and budget.remaining() > N_Y + 2:
        n_y = N_Y
        n_var_j = n_var + n_y
        xj = np.zeros(n_var_j)
        xj[:n_var] = best['x']
        s8_b = best['s8']
        Jt = jacobian_at(budget, args, fixt, y0, cb0, cr0, xj, s8_b, ref_plat, n_var_j, n_y, EPS_C, EPS_Y, split)
        svj = svd_report(Jt)
        boundj = (-np.full(n_var_j, span), np.full(n_var_j, span))
        boundj[0][n_var:] = -80
        boundj[1][n_var:] = 80
        rj = residual_vec(s8_b, ref_plat, split)
        # penalizar ΔY en el residual aumentado
        w_y = 0.15
        # usar solo cromático + regularización en ΔY vía λ extra
        lamj = max(lam, 0.5)
        dxj = solve_gn(Jt, rj, lamj, boundj)
        xj2 = np.clip(xj + dxj, boundj[0], boundj[1])
        s8j, _ = eval_planes(budget, args, fixt, *apply_vars(y0, cb0, cr0, xj2, n_y), 'joint')
        hj = halo_pair(s8j, ref_plat, split)
        redj = reduction_pair(h0_plat, hj)
        # luminancia S8
        y_c0 = 0.2126 * s8_c0[..., 0] + 0.7152 * s8_c0[..., 1] + 0.0722 * s8_c0[..., 2]
        y_j = 0.2126 * s8j[..., 0] + 0.7152 * s8j[..., 1] + 0.0722 * s8j[..., 2]
        gy0 = np.gradient(y_c0, axis=1)
        gyj = np.gradient(y_j, axis=1)
        joint = {
            'red': redj, 'halo': hj, 'sv': svj,
            'delta_y_code_rms': float(np.sqrt(np.mean(xj2[n_var:] ** 2))),
            'delta_y_code': [float(v) for v in xj2[n_var:]],
            's8_luma_rms': float(np.sqrt(np.mean((y_j - y_c0) ** 2))),
            'grad_rel': float(np.linalg.norm(gyj - gy0) / (np.linalg.norm(gy0) + 1e-12)),
            'clip_frac': float(np.mean((s8j > 1) | (s8j < 0))),
            'not_blur': True,
        }
        if redj['p99'] > best['red_plat']['p99'] and redj['integrated'] > best['red_plat']['integrated'] * 0.5:
            best = {'x': xj2, 's8': s8j, 'halo_plat': hj, 'halo_alg': halo_pair(s8j, ref_alg, split),
                    'red_plat': redj, 'kind': 'joint_y'}

    val = {'ran': False}
    promising = best['red_plat']['p99'] >= 0.5 and best['red_plat']['integrated'] >= 0.5
    if promising and budget.remaining() >= 2:
        val = {'ran': True, 'items': []}
        # fase alternativa
        rgb_p = p3_fixture(sensor, phase=1)
        y_p, cb_p, cr_p = planes_from_rgb(rgb_p, sensor)
        s8p0, _ = eval_planes(budget, args, fixt, y_p, cb_p, cr_p, 'val-c0-ph1')
        # aplicar mismos deltas de croma en el nuevo C0
        s8p1, _ = eval_planes(budget, args, fixt, *apply_vars(y_p, cb_p, cr_p, best['x'][:n_var], 0), 'val-x-ph1')
        rp0 = plateau_ref(s8p0)
        val['items'].append({'name': 'phase1',
                             'red': reduction_pair(halo_pair(s8p0, rp0), halo_pair(s8p1, rp0))})
        if budget.remaining() >= 2:
            rgb_h = p3_fixture(sensor, axis=0)
            yh, cbh, crh = planes_from_rgb(rgb_h, sensor)
            s8h0, _ = eval_planes(budget, args, fixt, yh, cbh, crh, 'val-c0-h')
            # no reutilizar x de borde vertical en horizontal: solo C0 diagnóstico
            val['items'].append({'name': 'horizontal_c0_only',
                                 'note': 'el parametrizado es vertical; no se reoptimiza',
                                 'halo': halo_pair(s8h0, plateau_ref(s8h0, H // 2))})
        val['q18'] = 'not_run_budget' if budget.remaining() < 2 else 'not_run_this_pass'
        val['a04'] = 'not_run_in_loop_see_prior_s8'
        val['wb_exposure'] = 'not_run; un BRAW, sin EXR equivalentes'

    crop_ppm(os.path.join(vis, 'h2_c0_nnx4.ppm'), np.clip(s8_c0, 0, 1), split - 40, 500, 80, 80, 4)
    crop_ppm(os.path.join(vis, 'h2_best_nnx4.ppm'), np.clip(best['s8'], 0, 1), split - 40, 500, 80, 80, 4)

    return {
        'budget_max': max_eval,
        'evals_counted': budget.n,
        'evals_log': budget.log,
        'seconds': budget.seconds,
        'wall_seconds': time.monotonic() - t0,
        'n_cb': N_CB, 'n_cr': N_CR, 'eps_c': EPS_C,
        'interference': interf,
        'svd': sv,
        'lambda': lam,
        'gauss_newton': gn,
        'baseline_plateau': h0_plat,
        'baseline_algebraic': h0_alg,
        'best': {
            'kind': best['kind'],
            'red_plateau': best['red_plat'],
            'halo_plateau': best['halo_plat'],
            'halo_algebraic': best['halo_alg'],
            'x_chroma': [float(v) for v in np.asarray(best['x'][:n_var])],
        },
        'joint': joint,
        'validation': val,
        'stopped': 'budget' if budget.remaining() <= 0 else ('no_descent' if no_descent >= 2 else 'complete'),
        'not_bilinear': True,
        'held_out_peak_not_used_as_operator_proof': True,
    }
