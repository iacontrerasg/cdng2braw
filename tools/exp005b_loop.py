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
from tools.fidelity.metrics import P3_LUMA

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
QUANT_RES = 2.0  # códigos: por debajo, DCT q=2 no mueve el payload de forma fiable
W_Y = 0.15
SCALES = (4.0, 16.0, 32.0, 64.0)


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


def luma_p3(rgb):
    return np.asarray(rgb, dtype=np.float64) @ P3_LUMA


def residual_vec(s8, ref, split=W // 2, w_y=0.0):
    """Residuo firmado en las cuatro fases del ROI (todos los píxeles), no solo [::2, ::2]."""
    y0, y1, x0, x1 = edge_slice(split)
    d = np.asarray(s8[y0:y1, x0:x1], dtype=np.float64) - np.asarray(ref[y0:y1, x0:x1], dtype=np.float64)
    rg, bg = rgb_chroma(d)
    parts = [rg.reshape(-1), bg.reshape(-1)]
    if w_y:
        parts.append(float(w_y) * luma_p3(d).reshape(-1))
    return np.concatenate(parts)


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


_EVAL_MEMO = {}


def planes_digest(y, cb, cr):
    y = np.ascontiguousarray(np.asarray(y, np.float32))
    cb = np.ascontiguousarray(np.asarray(cb, np.float32))
    cr = np.ascontiguousarray(np.asarray(cr, np.float32))
    return hashlib.sha256(y.tobytes() + cb.tobytes() + cr.tobytes()).hexdigest()[:16]


def eval_planes(budget, args, fixt, y, cb, cr, tag):
    y = np.asarray(y, np.float32)
    cb = np.asarray(cb, np.float32)
    cr = np.asarray(cr, np.float32)
    digest = planes_digest(y, cb, cr)
    if digest in _EVAL_MEMO:
        img, meta = _EVAL_MEMO[digest]
        budget.note(tag + '/memo', meta, False)
        return img, meta
    if budget.remaining() <= 0:
        raise RuntimeError('presupuesto SDK agotado (%d)' % budget.max_n)
    run_id = 'h2-%s-%s' % (tag.replace('/', '_'), digest)
    path = os.path.join(fixt, run_id + '.brwp')
    write_brwp(path, y, cb, cr)
    img, meta = convert_and_decode(args, run_id, default_encode(inject_planes=path, notes='H2 ' + tag))
    meta = dict(meta, planes_digest=digest)
    counted = budget.count(meta)
    budget.note(tag, meta, counted)
    _EVAL_MEMO[digest] = (img, meta)
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


def jacobian_at(budget, args, fixt, y0, cb0, cr0, x, s8_0, ref, n_var, n_y, eps_c, eps_y, split, w_y=0.0):
    r0 = residual_vec(s8_0, ref, split, w_y=w_y)
    J = np.zeros((r0.size, n_var), dtype=np.float64)
    for i in range(n_var):
        e = np.zeros(n_var)
        e[i] = eps_y if (n_y and i >= N_CB + N_CR) else eps_c
        xp = x + e
        yp, cbp, crp = apply_vars(y0, cb0, cr0, xp, n_y)
        s8p, _ = eval_planes(budget, args, fixt, yp, cbp, crp, 'fd%d' % i)
        rp = residual_vec(s8p, ref, split, w_y=w_y)
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
            step_rms = float(np.sqrt(np.mean(dx * dx)))
            # I1: no inflar λ si el paso ya está bajo la resolución cuantizada.
            if step_rms >= QUANT_RES:
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
        boundj = (-np.full(n_var_j, span), np.full(n_var_j, span))
        boundj[0][n_var:] = -80
        boundj[1][n_var:] = 80
        rj = residual_vec(s8_b, ref_plat, split, w_y=W_Y)
        Jt = jacobian_at(budget, args, fixt, y0, cb0, cr0, xj, s8_b, ref_plat, n_var_j, n_y, EPS_C, EPS_Y, split, w_y=W_Y)
        svj = svd_report(Jt)
        lamj = max(lam, 0.5)
        dxj = solve_gn(Jt, rj, lamj, boundj)
        xj2 = np.clip(xj + dxj, boundj[0], boundj[1])
        s8j, _ = eval_planes(budget, args, fixt, *apply_vars(y0, cb0, cr0, xj2, n_y), 'joint')
        hj = halo_pair(s8j, ref_plat, split)
        redj = reduction_pair(h0_plat, hj)
        y_c0 = luma_p3(s8_c0)
        y_j = luma_p3(s8j)
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


def _x_key(x):
    return tuple(int(np.rint(v)) for v in np.asarray(x).ravel())


def luma_ok(s8_c0, s8, split):
    y0, y1, x0, x1 = edge_slice(split)
    a = luma_p3(s8_c0[y0:y1, x0:x1])
    b = luma_p3(s8[y0:y1, x0:x1])
    ga = np.gradient(a, axis=1)
    gb = np.gradient(b, axis=1)
    rms = float(np.sqrt(np.mean((b - a) ** 2)))
    rel = float(rms / (np.sqrt(np.mean(a * a)) + 1e-12))
    grad_rel = float(np.linalg.norm(gb - ga) / (np.linalg.norm(ga) + 1e-12))
    return {
        'luma_rms': rms, 'luma_rel': rel, 'grad_rel': grad_rel,
        'ok': rel < 0.08 and grad_rel < 0.25,
    }


def accept_real(h0, h, luma, r0, r):
    red = reduction_pair(h0, h)
    drop = float(np.dot(r0, r0) - np.dot(r, r))
    return (drop > 0 and red['p99'] > 0 and red['integrated'] > -0.05
            and luma['ok'] and not (red['p99'] > 0.15 and red['integrated'] < 0)), red, drop


def run_i_campaign(args, fixt, vis, *, max_eval=77):
    """I1–I2: residual a todas las fases, búsqueda discreta, sin rehacer H2 si el payload ya existe."""
    from tools.exp005b_periodic import run_periodic

    sensor = NativeSensor()
    budget = Budget(max_eval)
    rgb = p3_fixture(sensor)
    y0, cb0, cr0 = planes_from_rgb(rgb, sensor)
    split = W // 2
    t0 = time.monotonic()
    seen = set()

    s8_c0, m0 = eval_planes(budget, args, fixt, y0, cb0, cr0, 'c0')
    ref_plat = plateau_ref(s8_c0, split)
    ref_alg = algebraic_ref(rgb, sensor)
    h0 = halo_pair(s8_c0, ref_plat, split)
    r0 = residual_vec(s8_c0, ref_plat, split, w_y=W_Y)
    n_var = N_CB + N_CR
    x0 = np.zeros(n_var)
    seen.add(_x_key(x0))

    fd = []
    for i in range(n_var):
        if budget.remaining() < 1:
            break
        e = np.zeros(n_var)
        e[i] = EPS_C
        s8p, mp = eval_planes(budget, args, fixt, *apply_vars(y0, cb0, cr0, e, 0), 'fd%d' % i)
        dlt = np.asarray(s8p, np.float64) - np.asarray(s8_c0, np.float64)
        rgb_rms = float(np.sqrt(np.mean(dlt * dlt)))
        payload = mp.get('braw_sha256') != m0.get('braw_sha256')
        fd.append({
            'i': i, 'plane': 'cb' if i < N_CB else 'cr',
            'rgb_rms': rgb_rms, 'payload_changed': bool(payload),
            'braw_sha256': mp.get('braw_sha256'),
            'counted': not (mp.get('reused_braw') and mp.get('reused_decode')),
        })
        seen.add(_x_key(e))

    live = [d for d in fd if d['payload_changed'] or d['rgb_rms'] > 1e-8]
    ranked = sorted(live or fd, key=lambda d: -d['rgb_rms'])
    trials = []
    best = {'kind': 'c0', 'x': x0.copy(), 's8' : s8_c0, 'red': {'p99': 0.0, 'integrated': 0.0},
            'halo': h0, 'drop': 0.0}

    def try_x(x, tag):
        x = np.clip(np.asarray(x, dtype=np.float64), -400, 400)
        key = _x_key(x)
        if key in seen:
            return None
        if budget.remaining() < 1:
            return None
        seen.add(key)
        s8, meta = eval_planes(budget, args, fixt, *apply_vars(y0, cb0, cr0, x, 0), tag)
        r = residual_vec(s8, ref_plat, split, w_y=W_Y)
        h = halo_pair(s8, ref_plat, split)
        lu = luma_ok(s8_c0, s8, split)
        ok, red, drop = accept_real(h0, h, lu, r0, r)
        rec = {
            'tag': tag, 'x': [float(v) for v in x], 'red': red, 'drop': drop,
            'luma': lu, 'accepted': ok,
            'payload_changed': meta.get('braw_sha256') != m0.get('braw_sha256'),
            'rgb_rms': float(np.sqrt(np.mean((np.asarray(s8) - np.asarray(s8_c0)) ** 2))),
        }
        trials.append(rec)
        if ok and (red['p99'] > best['red']['p99'] or
                   (red['p99'] == best['red']['p99'] and red['integrated'] > best['red']['integrated'])):
            best.update(kind=tag, x=x.copy(), s8=s8, red=red, halo=h, drop=drop, luma=lu)
        return rec

    # Coordinate / pattern: ±escalas en variables vivas, sin repetir +64 del jacobiano.
    for d in ranked[:8]:
        i = d['i']
        for sc in SCALES:
            for sgn in (-1.0, 1.0):
                e = np.zeros(n_var)
                e[i] = sgn * sc
                try_x(e, 'cd-%s-%d-%s' % (d['plane'], i, int(sgn * sc)))
                if budget.remaining() < 12:
                    break
            if budget.remaining() < 12:
                break

    # Dirección GN a escala cuantizada (residual de todas las fases + luma).
    Jcols = []
    for d in fd:
        e = np.zeros(n_var)
        e[d['i']] = EPS_C
        # residual de FD memoizado
        s8p, _ = eval_planes(budget, args, fixt, *apply_vars(y0, cb0, cr0, e, 0), 'fd%d' % d['i'])
        rp = residual_vec(s8p, ref_plat, split, w_y=W_Y)
        Jcols.append((rp - r0) / EPS_C)
    if Jcols:
        J = np.stack(Jcols, 1)
        bound = (-np.full(n_var, 400.0), np.full(n_var, 400.0))
        dx = solve_gn(J, r0, LAM0, bound)
        nrm = float(np.max(np.abs(dx)))
        if nrm > 1e-9:
            direction = dx / nrm
            for sc in (16.0, 32.0, 64.0):
                try_x(direction * sc, 'gn-scale-%d' % int(sc))
        sv = svd_report(J)
    else:
        sv = {}

    # Un patrón: las 4 mejores columnas al mejor signo de 32.
    if budget.remaining() >= 1 and ranked:
        combo = np.zeros(n_var)
        for d in ranked[:4]:
            combo[d['i']] = 32.0
            signed = [t for t in trials if t['tag'].startswith('cd-%s-%d-' % (d['plane'], d['i']))]
            if signed:
                best_t = max(signed, key=lambda t: t['drop'])
                combo[d['i']] = best_t['x'][d['i']]
        try_x(combo, 'pattern-top4')

    periodic = run_periodic(args, fixt, vis, budget, s8_c0=None)

    crop_ppm(os.path.join(vis, 'i2_c0_nnx4.ppm'), np.clip(s8_c0, 0, 1), split - 40, 500, 80, 80, 4)
    crop_ppm(os.path.join(vis, 'i2_best_nnx4.ppm'), np.clip(best['s8'], 0, 1), split - 40, 500, 80, 80, 4)

    substantial = best['red']['p99'] >= 0.5 and best['red']['integrated'] >= 0.5
    return {
        'budget_max': max_eval,
        'evals_counted': budget.n,
        'seconds': budget.seconds,
        'wall_seconds': time.monotonic() - t0,
        'w_y': W_Y, 'p3_luma': [float(v) for v in P3_LUMA],
        'residual_all_phases': True,
        'quant_res_codes': QUANT_RES,
        'fd_sensitivity': fd,
        'n_live_vars': len(live),
        'svd_all_phases': sv,
        'trials': [{k: t[k] for k in t if k != 's8'} for t in trials],
        'best': {
            'kind': best['kind'],
            'red_plateau': best['red'],
            'x_chroma': [float(v) for v in np.asarray(best['x'])],
            'drop': best['drop'],
            'luma': best.get('luma'),
        },
        'substantial': substantial,
        'periodic': periodic,
        'stopped': 'budget' if budget.remaining() <= 0 else 'complete',
        'not_global_impossibility': True,
        'same_pair_only': True,
    }

