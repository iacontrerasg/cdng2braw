"""EXP-005b: encode/decode real (worker + Blackmagic RAW SDK) y análisis S8."""
from __future__ import annotations

import json
import os
import shutil

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from tools.exp005_color import apply_rgb
from tools.exp005_metrics import halo_stats
from tools.exp005b_io import write_brwp
from tools.exp005b_oracle import classify_operator, solve_o1
from tools.exp005b_signal import (
    NativeSensor, SERIES_A_ASN, bilinear_chroma, camera_asn, camera_equal,
    camera_from_p3_grey, legal_clip_ycc, nn_chroma, p3_chroma_field, round_canvas,
    subsample_c0,
)
from tools.fidelity import identity, images, manifest as MF, pipeline as P

W, H = 1920, 1080
Y_LO, Y_HI = 1000.0, 2400.0
AMP_LO, AMP_HI = 80.0, 240.0
CLIP = 'BMPCC_1_2000-03-02_0756_C0039'  # A04
FRAME0, FRAME1 = 44, 45


def ensure_converter(native_bin=None):
    native_bin = native_bin or os.path.join(ROOT, 'build', 'native', 'bin', 'cdng-convert')
    dst = os.path.join(ROOT, 'build', 'native', 'converter')
    os.makedirs(dst, exist_ok=True)
    binary = os.path.join(dst, 'cdng-convert')
    shutil.copy2(native_bin, binary)
    os.chmod(binary, 0o755)
    model_dst = os.path.join(dst, 'model')
    model_src = os.path.join(ROOT, 'model')
    if os.path.islink(model_dst) or os.path.isdir(model_dst):
        pass
    else:
        os.symlink(model_src, model_dst)
    return dst


def default_encode(q=2, inject_planes=None, inject_rgb=None, dump_dir=None, notes=''):
    env = {
        'CDNG_OG_DEBLOCK_PRECOMP': '1',
        'CDNG_OG_DEBLOCK_ALPHA': '1',
        'CDNG_DEADZONE': '0',
        'CDNG_OG_CHROMA_MODE': 'c0',
    }
    if inject_planes:
        env['CDNG_INJECT_PLANES'] = os.path.abspath(inject_planes)
    if inject_rgb:
        env['CDNG_INJECT_RGB'] = os.path.abspath(inject_rgb)
        env['CDNG_INJECT_AT'] = 'pre_map'
    enc = {
        'compression': 'quality', 'q': q, 'ratio': None, 'rate_search': False,
        'noise_synthesis': False, 'nr_luma': 0, 'nr_chroma': 0, 'workers': 1,
        'experimental_env': env, 'notes': [notes or 'EXP-005b S8'],
    }
    if dump_dir:
        os.makedirs(dump_dir, exist_ok=True)
        enc['diagnostic_env'] = {'CDNG_DUMP_STAGES': os.path.abspath(dump_dir), 'CDNG_DUMP_FRAME': '0'}
    return enc


def decode_settings():
    return {
        'gamut': 'DCI-P3 D65', 'gamma': 'Linear', 'color_science_gen': 5,
        'highlight_recovery': 0, 'scale': 'full',
        'iso': None, 'exposure': None, 'kelvin': None, 'tint': None,
    }


def convert_and_decode(args, run_id, encode, *, iso=1600):
    log = P.Log(os.path.join(args.out, 'experiments', 'exp005b', 'logs', run_id + '.log'))
    converter = identity.converter(args.converter)
    framework = args.framework
    dec_id = identity.decoder(args.brawdec, framework, args.camera_support)
    clip_dir = MF.clip_dir(args.dataset, CLIP)
    files = MF.dng_files(clip_dir)
    if len(files) <= FRAME1:
        raise RuntimeError('clip A04 incompleto: %s' % clip_dir)
    cache = os.path.join(args.out, 'cache')
    conv = P.convert(args.converter, converter, clip_dir, files, FRAME0, 2, encode, cache, log, args.no_cache)
    img, dec = P.decode(args.brawdec, framework, dec_id, conv['path'], conv['sha256'], 0, decode_settings(),
                        cache, log, args.no_cache, expected_iso=iso)
    dest = os.path.join(args.out, 'experiments', 'exp005b', 'frames')
    os.makedirs(dest, exist_ok=True)
    images.write_f32(os.path.join(dest, run_id + '.f32'), img)
    return np.asarray(img, dtype=np.float64), {'run_id': run_id, 'braw_sha256': conv['sha256'],
                                               'reused_braw': conv['reused'], 'reused_decode': dec['reused'],
                                               'seconds_braw': conv['seconds'], 'seconds_decode': dec['seconds']}


def impulse_sites():
    """Sitios 4:2:0 interiores, separados ≥96 muestras croma. Dos bandas de Y, fases DCT, polaridad."""
    sites = []
    # banda Y_LO: cy < 270
    even = [(80, 80), (176, 80), (272, 80), (368, 80), (464, 80), (560, 80)]
    odd = [(660, 82), (756, 82), (660, 178), (756, 178)]
    specs = [
        ('cb', AMP_LO, 'even'), ('cb', -AMP_LO, 'even'), ('cb', AMP_HI, 'even'),
        ('cr', AMP_LO, 'even'), ('cr', -AMP_LO, 'even'), ('cr', AMP_HI, 'even'),
        ('cb', AMP_LO, 'odd'), ('cr', AMP_LO, 'odd'),
        ('cb', -AMP_HI, 'odd'), ('cr', -AMP_HI, 'odd'),
    ]
    for (cx, cy), (plane, amp, phase) in zip(even + odd, specs):
        sites.append({'cx': cx, 'cy': cy, 'plane': plane, 'amp': amp, 'phase': phase,
                      'band': 'ylo', 'split': 'train' if cx < 400 else 'held'})
    # banda Y_HI
    even_hi = [(80, 360), (176, 360), (272, 360), (368, 360), (464, 360), (560, 360)]
    specs_hi = [
        ('cb', AMP_LO, 'even'), ('cb', -AMP_LO, 'even'), ('cr', AMP_LO, 'even'),
        ('cr', -AMP_LO, 'even'), ('cb', AMP_HI, 'even'), ('cr', AMP_HI, 'even'),
    ]
    for (cx, cy), (plane, amp, phase) in zip(even_hi, specs_hi):
        sites.append({'cx': cx, 'cy': cy, 'plane': plane, 'amp': amp, 'phase': phase,
                      'band': 'yhi', 'split': 'train' if cx < 400 else 'held'})
    y_imp = [
        {'lx': 900, 'ly': 200, 'amp': 200.0, 'band': 'ylo', 'plane': 'y'},
        {'lx': 1100, 'ly': 200, 'amp': -200.0, 'band': 'ylo', 'plane': 'y'},
        {'lx': 900, 'ly': 760, 'amp': 200.0, 'band': 'yhi', 'plane': 'y'},
        {'lx': 1100, 'ly': 760, 'amp': -200.0, 'band': 'yhi', 'plane': 'y'},
    ]
    return sites, y_imp


def build_control_planes():
    y = np.empty((H, W), np.float32)
    y[: H // 2] = Y_LO
    y[H // 2:] = Y_HI
    cb = np.zeros((H // 2, W // 2), np.float32)
    cr = np.zeros_like(cb)
    return y, cb, cr


def build_impulse_planes():
    y, cb, cr = build_control_planes()
    sites, y_imp = impulse_sites()
    for s in sites:
        if s['plane'] == 'cb':
            cb[s['cy'], s['cx']] = s['amp']
        else:
            cr[s['cy'], s['cx']] = s['amp']
    for s in y_imp:
        y[s['ly'], s['lx']] += np.float32(s['amp'])
    return y, cb, cr, sites, y_imp


def rgb_chroma(rgb):
    rgb = np.asarray(rgb, dtype=np.float64)
    return rgb[..., 0] - rgb[..., 1], rgb[..., 2] - rgb[..., 1]


def extract_kernel(resid, lx, ly, amp, radius=28):
    h, w = resid.shape[:2]
    y0, y1 = max(0, ly - radius), min(h, ly + radius)
    x0, x1 = max(0, lx - radius), min(w, lx + radius)
    patch = resid[y0:y1, x0:x1] / float(amp)
    return patch, (y0, x0, ly - y0, lx - x0)


def signed_channel_stats(k):
    """Lóbulos sobre el kernel firmado, no sobre hypot."""
    k = np.asarray(k, dtype=np.float64)
    pos, neg = float(np.max(k)), float(np.min(k))
    amp = max(abs(pos), abs(neg), 1e-12)
    return {
        'max': pos, 'min': neg,
        'has_negative_lobes': neg < -0.05 * amp,
        'neg_energy_frac': float(np.sum(np.clip(-k, 0, None)) / (np.sum(np.abs(k)) + 1e-12)),
        'support_pos': int(np.sum(k > 0.01 * amp)),
        'support_neg': int(np.sum(k < -0.01 * amp)),
    }


def kernel_stats(patch, peak_yx):
    rg, bg = rgb_chroma(patch) if patch.ndim == 3 else (patch, np.zeros_like(patch))
    mag = np.hypot(rg, bg) if patch.ndim == 3 else np.abs(patch)
    py, px = peak_yx
    cls = classify_operator(mag)
    srg, sbg = signed_channel_stats(rg), signed_channel_stats(bg)
    cls['has_negative_lobes'] = bool(srg['has_negative_lobes'] or sbg['has_negative_lobes'])
    cls['signed_rg'] = srg
    cls['signed_bg'] = sbg
    cls['min_lobe'] = min(srg['min'], sbg['min'])
    cls['cross_rg_at_peak'] = float(rg[py, px]) if rg.shape == mag.shape else 0.0
    cls['cross_bg_at_peak'] = float(bg[py, px]) if bg.shape == mag.shape else 0.0
    cls['rgb_peak'] = [float(v) for v in patch[py, px]] if patch.ndim == 3 else float(patch[py, px])
    return cls


def characterize_s8(ctrl, imp, sites, y_imp):
    resid = np.asarray(imp, dtype=np.float64) - np.asarray(ctrl, dtype=np.float64)
    rows = []
    train_cb = []
    for s in sites:
        lx, ly = 2 * s['cx'], 2 * s['cy']
        patch, (y0, x0, py, px) = extract_kernel(resid, lx, ly, s['amp'])
        st = kernel_stats(patch, (py, px))
        st.update({k: s[k] for k in ('plane', 'amp', 'phase', 'band', 'split', 'cx', 'cy')})
        rows.append(st)
        if s['split'] == 'train' and s['plane'] == 'cb' and s['band'] == 'ylo' and s['amp'] == AMP_LO:
            train_cb.append(patch)
    y_rows = []
    for s in y_imp:
        patch, (y0, x0, py, px) = extract_kernel(resid, s['lx'], s['ly'], s['amp'])
        st = kernel_stats(patch, (py, px))
        st.update(s)
        y_rows.append(st)
    # linealidad: kernel +amp vs −amp (mismo plano/fase/banda)
    lin = []
    by = {}
    for r in rows:
        key = (r['plane'], r['phase'], r['band'], abs(r['amp']))
        by.setdefault(key, []).append(r)
    amp_dep = []
    for r in rows:
        if abs(r['amp']) != AMP_LO:
            continue
        pair = [x for x in rows if x['plane'] == r['plane'] and x['phase'] == r['phase']
                and x['band'] == r['band'] and x['amp'] == -r['amp']]
        if pair:
            lin.append({'plane': r['plane'], 'band': r['band'],
                        'peak_pos': r['peak_value'], 'peak_neg': pair[0]['peak_value']})
    for r in rows:
        if abs(r['amp']) != AMP_LO or r['amp'] < 0:
            continue
        hi = [x for x in rows if x['plane'] == r['plane'] and x['phase'] == r['phase']
              and x['band'] == r['band'] and x['amp'] == AMP_HI]
        if hi:
            amp_dep.append({'plane': r['plane'], 'band': r['band'],
                            'peak_lo': r['peak_value'], 'peak_hi': hi[0]['peak_value'],
                            'ratio': hi[0]['peak_value'] / max(r['peak_value'], 1e-12)})
    mean_support = float(np.mean([r['support_px'] for r in rows])) if rows else 0.0
    neg = bool(any(r['has_negative_lobes'] for r in rows))
    y_cross = float(np.mean([abs(r.get('cross_rg_at_peak', 0)) + abs(r.get('cross_bg_at_peak', 0))
                             for r in y_rows])) if y_rows else 0.0
    return {
        'n_chroma_sites': len(rows),
        'n_y_sites': len(y_rows),
        'mean_support_px': mean_support,
        'negative_lobes': neg,
        'sites': rows,
        'y_impulses': y_rows,
        'polarity': lin,
        'amplitude': amp_dep,
        'y_to_chroma_coupling': y_cross,
        'not_assumed_bilinear': True,
    }


def mean_kernel(rows, plane, band='ylo', amp=AMP_LO):
    chosen = [r for r in rows if r['plane'] == plane and r['band'] == band
              and r['amp'] == amp and r['split'] == 'train']
    return chosen


def stripe_layout():
    names = [
        'cam_equal_hi', 'cam_equal_lo', 'asn_hi', 'p3_hi', 'p3_lo',
        'saturated', 'isoluminant', 'asn_lo', 'cam_equal_phase1', 'p3_phase1',
    ]
    return [(n, i * 192, 192) for i, n in enumerate(names)]


def match_codec_y(sensor, rgb, y_tgt, iters=12, tol=0.5):
    """Iguala Y del mapa NativeSensor (post-curva), no una escala lineal RGB."""
    rgb = np.asarray(rgb, dtype=np.float64).copy()
    hist = []
    for _ in range(iters):
        y = float(sensor.map_rgb(rgb)[0])
        hist.append(y)
        if abs(y - y_tgt) <= tol:
            break
        if abs(y) < 1e-9:
            break
        rgb *= y_tgt / y
    return rgb, {'y_final': float(sensor.map_rgb(rgb)[0]), 'y_target': float(y_tgt),
                 'iters': len(hist), 'y_path': hist}


def scale_to_y(sensor, rgb, y_tgt):
    rgb, _ = match_codec_y(sensor, rgb, y_tgt)
    return rgb


def build_edge_rgb(sensor):
    img = np.zeros((H, W, 3), np.float32)
    asn = SERIES_A_ASN
    pairs = {
        'cam_equal_hi': (camera_equal(0.02), camera_equal(0.55)),
        'cam_equal_lo': (camera_equal(0.10), camera_equal(0.22)),
        'asn_hi': (camera_asn(0.02, asn), camera_asn(0.55, asn)),
        'asn_lo': (camera_asn(0.10, asn), camera_asn(0.22, asn)),
        'p3_hi': (camera_from_p3_grey(0.02, sensor, asn), camera_from_p3_grey(0.55, sensor, asn)),
        'p3_lo': (camera_from_p3_grey(0.10, sensor, asn), camera_from_p3_grey(0.22, sensor, asn)),
        'saturated': (np.array([0.45, 0.05, 0.04]), np.array([0.05, 0.06, 0.42])),
        'isoluminant': (scale_to_y(sensor, [0.40, 0.10, 0.06], 1200),
                        scale_to_y(sensor, [0.06, 0.12, 0.38], 1200)),
        'cam_equal_phase1': (camera_equal(0.02), camera_equal(0.55)),
        'p3_phase1': (camera_from_p3_grey(0.02, sensor, asn), camera_from_p3_grey(0.55, sensor, asn)),
    }
    for name, x0, w in stripe_layout():
        lo, hi = pairs[name]
        split = w // 2 + (1 if 'phase1' in name else 0)
        img[:, x0:x0 + split] = lo
        img[:, x0 + split:x0 + w] = hi
    return img, pairs


def stripe_metrics(s8, sensor, pairs):
    out = []
    asn = SERIES_A_ASN
    n = np.asarray(asn, dtype=np.float64)
    n = n / n[1]
    for name, x0, w in stripe_layout():
        lo, hi = pairs[name]
        split = w // 2 + (1 if 'phase1' in name else 0)
        edge = x0 + split
        sl = np.s_[:, x0:x0 + w]
        band = s8[sl]
        # referencia: croma de un bloque plano a cada lado (sin el borde ±8)
        left = band[:, : max(8, split - 12)]
        right = band[:, split + 12:]
        ref = band.copy()
        ref[:, :split] = np.median(left.reshape(-1, 3), axis=0)
        ref[:, split:] = np.median(right.reshape(-1, 3), axis=0)
        h_spatial = halo_stats(band, ref)  # error vs mesetas SDK (croma constante de cada lado)
        alg_ref = np.empty_like(band)
        alg_ref[:, :split] = apply_rgb(lo, sensor.camera_to_p3())
        alg_ref[:, split:] = apply_rgb(hi, sensor.camera_to_p3())
        h_alg = halo_stats(band, alg_ref)
        mid = band[H // 4: 3 * H // 4, max(0, split - 10): split + 10]
        mag_abs = p3_chroma_field(mid)
        y_lo = float(sensor.map_rgb(lo)[0])
        y_hi = float(sensor.map_rgb(hi)[0])
        dist = float(np.mean(np.abs(lo / (lo[1] + 1e-12) - n) + np.abs(hi / (hi[1] + 1e-12) - n)))
        out.append({
            'name': name, 'x0': x0, 'edge': edge,
            'halo_p99': h_spatial['p99'], 'halo_peak': h_spatial['peak'],
            'halo_integrated': h_spatial['integrated'],
            'spatial_vs_plateau': h_spatial,
            'error_vs_algebraic_p3': h_alg,
            'absolute_chroma_p99': float(np.percentile(mag_abs, 99)),
            'codec_y_lo': y_lo, 'codec_y_hi': y_hi,
            'luma_contrast': abs(y_hi - y_lo),
            'isoluminant_codec_y_err': abs(y_hi - y_lo) if name == 'isoluminant' else None,
            'dist_to_asn': dist,
            'cam_equal': name.startswith('cam_equal'),
            'p3_neutral': name.startswith('p3'),
        })
    return out


def planes_from_rgb(rgb, sensor):
    y, cb, cr = subsample_c0(rgb, sensor)
    return y.astype(np.float32), cb.astype(np.float32), cr.astype(np.float32)


def o1_from_full_chroma(rgb, sensor, upsample=None, roi=None):
    """O1 en un ROI par (por defecto franja del borde vertical). Y = C0 a cuadro completo."""
    upsample = upsample or bilinear_chroma
    y, cb0, cr0 = subsample_c0(rgb, sensor)
    if roi is None:
        x0, y0, rw, rh = W // 2 - 32, H // 2 - 64, 64, 128
    else:
        x0, y0, rw, rh = roi
    x0 -= x0 % 2
    y0 -= y0 % 2
    rw += rw % 2
    rh += rh % 2
    patch = np.asarray(rgb[y0:y0 + rh, x0:x0 + rw], dtype=np.float64)
    ycc = sensor.map_rgb(patch)
    cb, cr = solve_o1(ycc[..., 1], ycc[..., 2], upsample)
    cb0 = cb0.copy()
    cr0 = cr0.copy()
    cb0[y0 // 2:y0 // 2 + cb.shape[0], x0 // 2:x0 // 2 + cb.shape[1]] = cb
    cr0[y0 // 2:y0 // 2 + cr.shape[0], x0 // 2:x0 // 2 + cr.shape[1]] = cr
    y, cb0, cr0 = legal_clip_ycc(y, cb0, cr0)
    return y.astype(np.float32), cb0.astype(np.float32), cr0.astype(np.float32)


def localized_neutral_mask(rgb, sensor, grad_thr=None):
    """Bordes de alta |∇Y| y cromaticidad cámara cerca de AsShotNeutral (no R=G=B)."""
    ycc = sensor.map_rgb(rgb)
    y = ycc[..., 0]
    gy, gx = np.gradient(y)
    mag = np.hypot(gx, gy)
    thr = grad_thr if grad_thr is not None else float(np.percentile(mag, 85))
    rgb = np.asarray(rgb, dtype=np.float64)
    g = np.maximum(rgb[..., 1], 1e-8)
    rg, bg = rgb[..., 0] / g, rgb[..., 2] / g
    n = SERIES_A_ASN / SERIES_A_ASN[1]
    dist = np.hypot(rg - n[0], bg - n[2])
    near = dist < 0.12
    equal = (np.abs(rg - 1) < 0.04) & (np.abs(bg - 1) < 0.04)
    return (mag > thr) & near & (~equal), mag, dist


def apply_e2(y, cb, cr, rgb, sensor):
    """Compensación local: O1 solo bajo máscara de borde neutro de salida; no recorta Y ni fuerza C=0 global."""
    mask, _, _ = localized_neutral_mask(rgb, sensor)
    y1, cb1, cr1 = o1_from_full_chroma(rgb, sensor)
    m4 = mask[::2, ::2][: cr.shape[0], : cr.shape[1]]
    cb_o = np.where(m4, cb1, cb)
    cr_o = np.where(m4, cr1, cr)
    return y, cb_o.astype(np.float32), cr_o.astype(np.float32)


def joint_delta_y(y, rgb, sensor, scale=0.04):
    """ΔY local acotado en el borde (no blur): refuerzo del gradiente existente, clip legal."""
    ycc = sensor.map_rgb(rgb)
    y_tgt = ycc[..., 0]
    gy, gx = np.gradient(y_tgt)
    mag = np.hypot(gx, gy)
    m = mag > np.percentile(mag, 90)
    dy = scale * (y_tgt - y)
    dy = np.where(m, dy, 0.0)
    out = np.clip(y + dy, 0, 4095)
    return out.astype(np.float32), {
        'delta_y_rms': float(np.sqrt(np.mean(dy * dy))),
        'delta_y_max': float(np.max(np.abs(dy))),
        'frac_touched': float(np.mean(m)),
        'not_blur': True,
    }


def crop_ppm(path, rgb, x, y, w, h, nn=1):
    c = np.clip(np.asarray(rgb[y:y + h, x:x + w]), 0, 1)
    if nn > 1:
        c = np.repeat(np.repeat(c, nn, 0), nn, 1)
    hh, ww = c.shape[:2]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    u8 = (c * 255).astype(np.uint8)
    with open(path, 'wb') as f:
        f.write(('P6\n%d %d\n255\n' % (ww, hh)).encode('ascii'))
        f.write(np.ascontiguousarray(u8).tobytes())
