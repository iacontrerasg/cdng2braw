"""EXP-005b I3: false color periódico en reglas del ColorChecker vs fixture sintético."""
from __future__ import annotations

import json
import os

import numpy as np

from tools.exp005b_sdk import (
    CLIP, FRAME0, H, W, convert_and_decode, crop_ppm, default_encode, rgb_chroma,
)
from tools.exp005b_signal import NativeSensor, camera_equal, camera_from_p3_grey
from tools.fidelity import images, stage_io
from tools.fidelity.metrics import P3_LUMA

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHART = os.path.join(ROOT, 'benchmark', 'rois', 'chart_wall.json')
A04_EXR = 'DNG1600MINUS3_00086731.exr'

# ColorChecker Video: marcas milimétricas a izquierda y derecha de la grilla 6×4.
RULER_ROIS = {
    'cc_ruler_left': [1026, 350, 28, 380],
    'cc_ruler_right': [1280, 350, 30, 380],
}


def load_ruler_rois():
    with open(CHART, encoding='utf-8') as f:
        doc = json.load(f)
    out = {}
    for roi in doc.get('rois', []):
        if roi['name'] in RULER_ROIS:
            out[roi['name']] = roi['rect']
    return out or dict(RULER_ROIS)


def luma_p3(rgb):
    return np.asarray(rgb, dtype=np.float64) @ P3_LUMA


def signed_chroma(rgb):
    rg, bg = rgb_chroma(rgb)
    return rg, bg, np.hypot(rg, bg)


def period_spectrum(field, axis=1):
    """Espectro 1D del campo firmado (media en el eje ortogonal)."""
    a = np.asarray(field, dtype=np.float64)
    if a.ndim == 2:
        prof = a.mean(axis=0 if axis == 1 else 1)
    else:
        prof = np.asarray(a, dtype=np.float64).reshape(-1)
    prof = prof - prof.mean()
    spec = np.abs(np.fft.rfft(prof))
    freq = np.fft.rfftfreq(prof.size)
    spec[0] = 0
    k = int(np.argmax(spec)) if spec.size else 0
    f = float(freq[k]) if 0 <= k < freq.size else 0.0
    period = (1.0 / f) if f > 1e-12 else None
    # potencias cerca de 2 px (4:2:0), 8 px (DCT), 16 px
    def band(p0, p1):
        m = (freq > 0) & (np.where(freq > 0, 1.0 / np.maximum(freq, 1e-12), 0) >= p0) & (
            np.where(freq > 0, 1.0 / np.maximum(freq, 1e-12), 0) < p1)
        return float(spec[m].sum()) if np.any(m) else 0.0
    tot = float(spec.sum()) + 1e-12
    return {
        'n': int(prof.size),
        'peak_period_px': period,
        'peak_freq': f,
        'peak_amp': float(spec[k]) if spec.size else 0.0,
        'frac_period_2_4': band(1.5, 4.5) / tot,
        'frac_period_6_10': band(6.0, 10.0) / tot,
        'frac_period_12_20': band(12.0, 20.0) / tot,
        'ac1': float(np.nan_to_num(np.corrcoef(prof[:-1], prof[1:])[0, 1])) if prof.size > 4 else 0.0,
    }


def roi_report(rgb, rect, ref=None):
    x, y, w, h = [int(v) for v in rect]
    crop = np.asarray(rgb[y:y + h, x:x + w], dtype=np.float64)
    if ref is not None:
        r = np.asarray(ref[y:y + h, x:x + w], dtype=np.float64)
        d = crop - r
    else:
        # residual vs cromaticidad constante de la ROI (meseta local)
        med = np.median(crop.reshape(-1, 3), 0)
        d = crop - med
    rg, bg, mag = signed_chroma(d)
    return {
        'rect': [x, y, w, h],
        'rg_p99': float(np.percentile(np.abs(rg), 99)),
        'bg_p99': float(np.percentile(np.abs(bg), 99)),
        'rg_mean_signed': float(rg.mean()),
        'bg_mean_signed': float(bg.mean()),
        'spectrum_x': period_spectrum(rg, axis=1),
        'spectrum_y': period_spectrum(rg, axis=0),
        'spectrum_bg_x': period_spectrum(bg, axis=1),
        'luma_mean': float(luma_p3(crop).mean()),
    }


def packed_line_rgb(sensor):
    """Líneas acromáticas repetidas: periodos, fases, vertical/inclinada, cámara vs P3."""
    img = np.zeros((H, W, 3), np.float32)
    cam_lo, cam_hi = camera_equal(0.08), camera_equal(0.42)
    p3_lo, p3_hi = camera_from_p3_grey(0.08, sensor), camera_from_p3_grey(0.42, sensor)
    bands = [
        ('cam_v_p4_ph0', cam_lo, cam_hi, 4, 0, 0.0),
        ('cam_v_p8_ph0', cam_lo, cam_hi, 8, 0, 0.0),
        ('cam_v_p8_ph1', cam_lo, cam_hi, 8, 1, 0.0),
        ('cam_v_p16_ph0', cam_lo, cam_hi, 16, 0, 0.0),
        ('p3_v_p8_ph0', p3_lo, p3_hi, 8, 0, 0.0),
        ('p3_v_p8_ph1', p3_lo, p3_hi, 8, 1, 0.0),
        ('cam_tilt_p8', cam_lo, cam_hi, 8, 0, 0.25),
        ('p3_tilt_p8', p3_lo, p3_hi, 8, 0, 0.25),
        ('cam_v_p32', cam_lo, cam_hi, 32, 0, 0.0),
        ('p3_v_p4_ph0', p3_lo, p3_hi, 4, 0, 0.0),
    ]
    bh = H // len(bands)
    meta = []
    yy, xx = np.mgrid[0:bh, 0:W]
    for i, (name, lo, hi, period, phase, tilt) in enumerate(bands):
        y0 = i * bh
        t = (xx + tilt * yy + phase).astype(np.float64)
        mask = (np.floor(t / period).astype(int) % 2) == 0
        band = np.where(mask[..., None], lo, hi).astype(np.float32)
        img[y0:y0 + bh] = band
        meta.append({'name': name, 'y0': y0, 'h': bh, 'period': period, 'phase': phase, 'tilt': tilt,
                     'neutral': 'p3' if name.startswith('p3') else 'camera'})
    return img, meta


def _find_exr(args):
    roots = [
        getattr(args, 'references', None),
        os.path.expanduser('~/Desktop/EXR'),
        os.path.join(getattr(args, 'dataset', ''), '..', 'EXR'),
    ]
    for root in roots:
        if not root:
            continue
        p = os.path.join(os.path.abspath(root), A04_EXR)
        if os.path.isfile(p):
            return p
    return None


def _plane(dump, key):
    if dump and key in dump:
        return np.asarray(dump[key]['array'], dtype=np.float64)
    return None


def stage_chroma_spectrum(dump, rect, luma_shape):
    """S5/S7 en códigos; no se comparan numéricamente con S8 P3."""
    x, y, w, h = [int(v) for v in rect]
    out = {}
    for st, ck, rk in (('S5', 'S5_Cb', 'S5_Cr'), ('S7', 'S7_Cb', 'S7_Cr')):
        cb, cr = _plane(dump, ck), _plane(dump, rk)
        if cb is None or cr is None:
            out[st] = {'present': False}
            continue
        # croma 4:2:0: recorte en sitios
        c = cb[y // 2:(y + h) // 2, x // 2:(x + w) // 2]
        d = cr[y // 2:(y + h) // 2, x // 2:(x + w) // 2]
        out[st] = {
            'present': True,
            'cb_p99': float(np.percentile(np.abs(c), 99)) if c.size else 0,
            'cr_p99': float(np.percentile(np.abs(d), 99)) if d.size else 0,
            'spectrum_cb_x': period_spectrum(c, axis=1),
        }
    s2 = _plane(dump, 'S2_rgb')
    if s2 is not None:
        crop = s2[y:y + h, x:x + w]
        rg, bg, _ = signed_chroma(crop)
        out['S2'] = {'present': True, 'domain': 'camera_rgb',
                     'spectrum_rg_x': period_spectrum(rg, axis=1)}
    else:
        out['S2'] = {'present': False}
    return out


def compare_mechanism(ruler_spec, halo_spec, line_specs):
    """No asume causalidad común. Coincide si el periodo dominante es el mismo bin (2–4 vs 6–10)."""
    def bin_of(s):
        p = s.get('peak_period_px') if isinstance(s, dict) else None
        if not p:
            return 'none'
        if 1.5 <= p < 4.5:
            return 'chroma_siting_2_4'
        if 6 <= p <= 10:
            return 'dct_8'
        if 12 <= p <= 20:
            return 'block_16'
        return 'other_%.2g' % p
    rbin = bin_of(ruler_spec)
    hbin = bin_of(halo_spec)
    line_bins = [bin_of(s) for s in line_specs]
    shared = rbin == hbin and rbin not in ('none',)
    return {
        'ruler_bin': rbin,
        'halo_white_strip_bin': hbin,
        'synthetic_bins': line_bins,
        'same_dominant_bin': shared,
        'causal_claim': False,
        'note': (
            'Misma cubeta espectral ≠ mismo mecanismo. '
            'El halo BUG-002 es un borde de alto contraste; las reglas son un peine espacial.'
        ),
    }


def run_periodic(args, fixt, vis, budget, s8_c0=None):
    sensor = NativeSensor()
    rois = load_ruler_rois()
    dump_a04 = os.path.join(args.out, 'experiments', 'exp005b', 'stages', 'a04')
    s8_a04, m_a04 = convert_and_decode(
        args, 's8-a04-c0',
        default_encode(dump_dir=dump_a04, notes='I3 A04 C0 dump'))
    counted = budget.count(m_a04)
    budget.note('a04-c0', m_a04, counted)

    dump = None
    dump_frame = os.path.join(dump_a04, 'frame_0')
    if os.path.isdir(dump_frame):
        dump = stage_io.load_frame_dir(dump_frame)

    exr_path = _find_exr(args)
    s9 = None
    if exr_path:
        s9, _ = images.read_exr(exr_path)

    white = [640, 520, 120, 80]
    rulers = {}
    for name, rect in rois.items():
        rulers[name] = {
            's8_vs_plateau': roi_report(s8_a04, rect, ref=None),
            's8_vs_s9': roi_report(s8_a04, rect, ref=s9) if s9 is not None else {'missing_s9': True},
            'stages': stage_chroma_spectrum(dump, rect, s8_a04.shape),
        }
        x, y, w, h = rect
        crop_ppm(os.path.join(vis, 'i3_%s_s8_nnx4.ppm' % name), np.clip(s8_a04, 0, 1), x, y, w, h, 4)
        if s9 is not None:
            crop_ppm(os.path.join(vis, 'i3_%s_s9_nnx4.ppm' % name), np.clip(s9, 0, 1), x, y, w, h, 4)

    halo_ws = roi_report(s8_a04, white, ref=s9 if s9 is not None else None)

    rgb_lines, bands = packed_line_rgb(sensor)
    p_rgb = os.path.join(fixt, 'i3_lines.f32')
    images.write_f32(p_rgb, rgb_lines.astype(np.float32))
    dump_ln = os.path.join(args.out, 'experiments', 'exp005b', 'stages', 'i3lines')
    if budget.remaining() < 1:
        lines = {'skipped': 'presupuesto'}
        s8_ln = None
        dump_l = None
    else:
        s8_ln, m_ln = convert_and_decode(
            args, 's8-i3-lines',
            default_encode(inject_rgb=p_rgb, dump_dir=dump_ln, notes='I3 packed lines'))
        budget.count(m_ln)
        budget.note('i3-lines', m_ln, not (m_ln.get('reused_braw') and m_ln.get('reused_decode')))
        dump_l = None
        df = os.path.join(dump_ln, 'frame_0')
        if os.path.isdir(df):
            dump_l = stage_io.load_frame_dir(df)
        lines = {'bands': []}
        for b in bands:
            rect = [0, b['y0'], W, b['h']]
            rep = roi_report(s8_ln, rect, ref=None)
            rep.update(b)
            if dump_l:
                rep['stages'] = stage_chroma_spectrum(dump_l, rect, s8_ln.shape)
            lines['bands'].append(rep)
        crop_ppm(os.path.join(vis, 'i3_lines.ppm'), np.clip(s8_ln, 0, 1), 0, 0, W, min(H, 400), 1)

    line_specs = [b['spectrum_x'] for b in lines.get('bands', [])] if isinstance(lines, dict) else []
    left = (rulers.get('cc_ruler_left') or {}).get('s8_vs_plateau') or {}
    mech = compare_mechanism(left.get('spectrum_x') or {}, halo_ws.get('spectrum_x') or {}, line_specs)

    s9_note = None if s9 is not None else 'S9 EXR ausente (%s); comparación CinemaDNG limitada' % A04_EXR
    return {
        'ruler_rois': rois,
        'rulers': rulers,
        'white_strip': halo_ws,
        's9_exr': exr_path,
        's9_limitation': s9_note,
        'synthetic_lines': lines,
        'mechanism_vs_bug002': mech,
        'domains': {
            'S2': 'camera RGB (no P3)',
            'S5': 'códigos 4:2:0 pre-deblock',
            'S7': 'códigos decodedPlanes NN',
            'S8': 'P3-D65 Linear SDK',
            'S9': 'P3-D65 Linear EXR CinemaDNG (si existe)',
        },
        'no_common_causality_assumed': True,
    }
