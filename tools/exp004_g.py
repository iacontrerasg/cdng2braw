#!/usr/bin/env python3
"""EXP-004 G–H: métricas G0, validación C0/C1/C2-b3, hojas de contacto. C3/C4 inactivos."""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.exp004_halo import (CHROMA_MIN_Y, chroma_error_vs_ref, chroma_esf_mtf, chroma_signed_profile,
                               extract_profile, freq_response_binomial_box, halo_extent_px,
                               luma_chroma_split, profile_metrics, uv_local)
from tools.exp004_sweep import G1_FRAMES, G1_MODES
from tools.fidelity import images, metrics as M, rd as RD, viz
from tools.fidelity import pipeline as P
from tools.fidelity import identity, stage_io

DISPLAY_EV = 2.0  # visualization.display_exposure del manifiesto; común a ref y candidatos.
CHART = os.path.join(ROOT, 'benchmark', 'rois', 'chart_wall.json')
HALO_EDGES = [
    ('edge_white_strip_left', [664, 550, 704, 550], 9),
    ('edge_grey_strip_right', [828, 550, 868, 550], 9),
    ('edge_patch_top', [1258, 336, 1258, 376], 9),
    ('edge_wire_h', [1500, 345, 1500, 400], 9),
]
CROPS = {
    'white_strip': (640, 520, 120, 80),
    'patch_top': (1230, 310, 80, 80),
    'wire': (1470, 320, 80, 90),
}


def _sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def _nan(x):
    return x is None or x == 'nan' or (isinstance(x, float) and not np.isfinite(x))


def run_id(tag, test, frame, q):
    return 'exp004-g1-%s-%s-f%s-q%d' % (tag, test.lower(), frame, q)


def tag_of(mode, kern):
    return mode + (('-' + kern) if kern else '')


def load_pair(run_dir, refs_root):
    if not os.path.isfile(os.path.join(run_dir, 'metrics.json')):
        return None
    run = RD.load_run(run_dir)
    rows = []
    for key, res in (run['metrics'].get('results') or {}).items():
        cand_p = os.path.join(run_dir, 'frames', key + '.f32')
        cand = images.read_f32(cand_p) if os.path.isfile(cand_p) else None
        rf = res.get('reference_fidelity') or {}
        ref = None
        ref_path = None
        if refs_root:
            setup = res.get('setup') or {}
            fn = setup.get('reference')
            if isinstance(fn, dict):
                fn = fn.get('file') or fn.get('path')
            if fn:
                ref_path = os.path.join(refs_root, os.path.basename(str(fn)))
                if os.path.isfile(ref_path):
                    ref, _ = images.read_exr(ref_path)
        try:
            brow = RD.frame_row(run, key, res, require_bitstream=True)
        except Exception as e:
            brow = {'error': str(e)}
        rows.append({
            'key': key, 'cand': cand, 'ref': ref, 'ref_path': ref_path,
            'cand_sha': _sha(cand_p) if cand_p and os.path.isfile(cand_p) else None,
            'rf': rf, 'row': brow, 'res': res,
        })
    return {'run': run, 'dir': run_dir, 'rows': rows}


def y_plane_sha(stages_root, rid):
    p = os.path.join(stages_root, rid, 'frame_0', 'S5_Y.bin')
    if not os.path.isfile(p):
        return None
    return _sha(p)


def analyze_frame(cand, ref, cfg_ymin=CHROMA_MIN_Y):
    out = {'coverage': {}}
    if cand is None:
        out['coverage']['candidate_rgb'] = 'absent'
        return out
    out['coverage']['candidate_rgb'] = 'present'
    if ref is None:
        out['coverage']['reference_exr'] = 'absent'
    else:
        out['coverage']['reference_exr'] = 'present'
        y, c = luma_chroma_split(cand.astype(np.float64))
        yr, cr = luma_chroma_split(ref.astype(np.float64))
        out['chroma_error'] = chroma_error_vs_ref(
            np.linalg.norm(c, axis=-1), np.zeros_like(y),
            np.linalg.norm(cr, axis=-1), np.zeros_like(yr))
        # error cromático: magnitud del residuo RGB vs ref
        dc = c - cr
        mag = np.linalg.norm(dc, axis=-1)
        out['chroma_error'] = {
            'peak_chroma_error': M._f(float(np.max(mag))),
            'integrated_abs_chroma_error': M._f(float(np.sum(mag))),
            'mean_abs_chroma_error': M._f(float(np.mean(mag))),
        }
        out['uv_global'] = uv_local(cand, ref, cfg_ymin)
    rois = []
    for name, line, width in HALO_EDGES:
        item = {'name': name, 'line': line, 'width': width}
        if ref is None:
            item['absent'] = 'no_reference'
            rois.append(item)
            continue
        pc = extract_profile(cand, line, width)
        pr = extract_profile(ref, line, width)
        yc, yr = pc @ M.P3_LUMA, pr @ M.P3_LUMA
        _, cc = luma_chroma_split(pc.reshape(-1, 1, 3))
        _, cr = luma_chroma_split(pr.reshape(-1, 1, 3))
        mag = np.linalg.norm((cc - cr)[:, 0, :], axis=-1)
        item['uv'] = uv_local(pc.reshape(-1, 1, 3), pr.reshape(-1, 1, 3), cfg_ymin)
        item['halo_extent_px'] = halo_extent_px(mag, 0.05 * (float(mag.max()) + 1e-9))
        item['chroma_mtf'] = chroma_esf_mtf(cand, line, width)
        item['chroma_mtf_ref'] = chroma_esf_mtf(ref, line, width)
        item['profile'] = profile_metrics(yc, mag, np.zeros_like(mag), yr, np.zeros_like(mag), np.zeros_like(mag))
        rois.append(item)
    out['edge_rois'] = rois
    return out


def paired_delta(a, b):
    if _nan(a) or _nan(b):
        return 'absent'
    return float(b) - float(a)


def g1_collect(args):
    stages = os.path.join(args.out, 'experiments', 'exp004', 'stages')
    frames = []
    missing = []
    y_ident = []
    for q in (2, 18):
        for test, fr in G1_FRAMES:
            shas = {}
            pack = {}
            for mode, kern, _ in G1_MODES:
                tag = tag_of(mode, kern)
                rid = run_id(tag, test, fr, q)
                rd = os.path.join(args.out, 'runs', rid)
                got = load_pair(rd, args.references)
                if got is None:
                    missing.append({'run': rid, 'reason': 'metrics.json absent'})
                    continue
                pack[tag] = got
                shas[tag] = y_plane_sha(stages, rid)
            if len(pack) == 3:
                y_ident.append({
                    'test': test, 'frame': fr, 'q': q,
                    's5_y_sha': shas,
                    'y_bit_identical': len({s for s in shas.values() if s}) == 1 and all(shas.values()),
                    'absent_y': [k for k, v in shas.items() if not v],
                })
            for tag, got in pack.items():
                for row in got['rows']:
                    extra = analyze_frame(row['cand'], row['ref'])
                    br = row['row'] if isinstance(row['row'], dict) else {}
                    rf = row['rf']
                    lc = (rf.get('luma_chroma') or {})
                    frames.append({
                        'run': os.path.basename(got['dir']),
                        'tag': tag, 'test': test, 'frame': int(fr), 'q': q,
                        'key': row['key'],
                        'cand_sha256': row['cand_sha'],
                        'ref_path': row['ref_path'],
                        'psnr': br.get('psnr'), 'psnr_raw': br.get('psnr_raw'),
                        'duv_edges': br.get('duv_edges') or (lc.get('edges') or {}).get('duv_mean'),
                        'duv_global': (lc.get('global') or lc.get('all') or {}).get('duv_mean') if isinstance(lc, dict) else None,
                        'detail_corr': (rf.get('alignment') or {}).get('detail_correlation'),
                        'sample_bytes': br.get('sample_bytes'),
                        'seconds_convert': ((got['run'].get('run') or {}).get('timing') or {}).get('seconds'),
                        'clipping': rf.get('range') or (rf.get('raw') and None),
                        'channel_stats': (rf.get('candidate') or {}).get('channels') if isinstance(rf.get('candidate'), dict) else None,
                        'halo': extra,
                        'bitstream_error': br.get('error'),
                        'coverage': extra.get('coverage'),
                    })
    # duv global from luma_chroma if present
    return frames, missing, y_ident


def summarize(frames):
    def vals(tag, q, field):
        xs = []
        for f in frames:
            if f['tag'] == tag and f['q'] == q:
                v = f.get(field)
                if not _nan(v):
                    xs.append(float(v))
        return xs

    out = []
    for q in (2, 18):
        for tag in ('c0', 'c1', 'c2-b3'):
            for field in ('psnr', 'psnr_raw', 'duv_edges', 'detail_corr', 'sample_bytes'):
                xs = vals(tag, q, field)
                out.append({
                    'q': q, 'tag': tag, 'metric': field, 'n': len(xs),
                    'mean': float(np.mean(xs)) if xs else 'absent',
                    'worst': (min(xs) if field.startswith('psnr') or field == 'detail_corr' else max(xs)) if xs else 'absent',
                    'missing': 6 - len(xs),
                })
    paired = []
    by = {}
    for f in frames:
        by.setdefault((f['test'], f['frame'], f['q']), {})[f['tag']] = f
    for key, g in by.items():
        if 'c0' not in g:
            continue
        c0 = g['c0']
        for tag in ('c1', 'c2-b3'):
            if tag not in g:
                paired.append({'test': key[0], 'frame': key[1], 'q': key[2], 'tag': tag, 'absent': 'no_candidate'})
                continue
            c = g[tag]
            halo_c0 = ((c0.get('halo') or {}).get('edge_rois') or [{}])[0].get('halo_extent_px')
            halo_c = ((c.get('halo') or {}).get('edge_rois') or [{}])[0].get('halo_extent_px')
            mtf_c0 = ((c0.get('halo') or {}).get('edge_rois') or [{}])[0].get('chroma_mtf') or {}
            mtf_c = ((c.get('halo') or {}).get('edge_rois') or [{}])[0].get('chroma_mtf') or {}
            paired.append({
                'test': key[0], 'frame': key[1], 'q': key[2], 'tag': tag,
                'delta_psnr': paired_delta(c0.get('psnr'), c.get('psnr')),
                'delta_duv_edges': paired_delta(c0.get('duv_edges'), c.get('duv_edges')),
                'delta_detail_corr': paired_delta(c0.get('detail_corr'), c.get('detail_corr')),
                'delta_halo_extent_white': paired_delta(halo_c0, halo_c),
                'delta_chroma_mtf50_white': paired_delta(mtf_c0.get('mtf50'), mtf_c.get('mtf50')),
            })
    return out, paired


def classify(agg, paired, y_ident):
    """A–E. Ausencia de métricas ≠ aprobación."""
    if any(not y.get('y_bit_identical') for y in y_ident):
        y_note = 'Y no bit-identical en alguna pareja; no promocionar'
    else:
        y_note = 'Y bit-identical en dumps S5 presentes'
    c2 = [p for p in paired if p.get('tag') == 'c2-b3' and not p.get('absent')]
    c1 = [p for p in paired if p.get('tag') == 'c1' and not p.get('absent')]
    if len(c2) < 6:
        return {'C1': 'E', 'C2-b3': 'E', 'select_for_exp005': None,
                'reason': 'cobertura G1 incompleta (%d pares C2)' % len(c2), 'y': y_note}

    def mean_f(rows, k):
        xs = [r[k] for r in rows if not _nan(r.get(k)) and r.get(k) != 'absent']
        return float(np.mean(xs)) if xs else None

    def verdict(rows, name):
        dduv = mean_f(rows, 'delta_duv_edges')
        dpsnr = mean_f(rows, 'delta_psnr')
        dmtf = mean_f(rows, 'delta_chroma_mtf50_white')
        dhalo = mean_f(rows, 'delta_halo_extent_white')
        if dduv is None or dpsnr is None:
            return 'E', {'missing_metrics': True}
        halo_down = dhalo is not None and dhalo < -0.5
        mtf_ok = dmtf is None or dmtf > -0.02
        duv_down = dduv < -5e-5
        if duv_down and mtf_ok and (halo_down or dduv < -2e-4) and dpsnr > -0.05:
            cls = 'B' if (dmtf is not None and dmtf < 0) or not halo_down else 'A'
            # Sin evidencia perceptual aún → no A solo por números
            if cls == 'A':
                cls = 'C'
            return cls, {'mean_delta_duv': dduv, 'mean_delta_psnr': dpsnr, 'mean_delta_mtf50': dmtf,
                         'mean_delta_halo': dhalo}
        if duv_down and not mtf_ok:
            return 'D', {'mean_delta_duv': dduv, 'mean_delta_mtf50': dmtf, 'note': 'halo/Δu′v′ a costa de MTF croma'}
        if abs(dduv) < 5e-5 and abs(dpsnr) < 0.05:
            return 'D', {'mean_delta_duv': dduv, 'mean_delta_psnr': dpsnr}
        return 'C', {'mean_delta_duv': dduv, 'mean_delta_psnr': dpsnr, 'mean_delta_mtf50': dmtf,
                     'mean_delta_halo': dhalo}

    v1, e1 = verdict(c1, 'C1')
    v2, e2 = verdict(c2, 'C2-b3')
    pick = None
    if v2 in ('A', 'B'):
        pick = 'C2-b3'
    elif v1 in ('A', 'B'):
        pick = 'C1'
    return {'C1': v1, 'C2-b3': v2, 'C1_evidence': e1, 'C2-b3_evidence': e2,
            'select_for_exp005': pick, 'y': y_note,
            'note': 'Clase A exige hojas G2 de halo desaparecido; PSNR↑ no equivale a halo=0'}


def disp(img):
    return viz.display(np.asarray(img, dtype=np.float64), DISPLAY_EV)


def nn4(img):
    return np.repeat(np.repeat(img, 4, 0), 4, 1)


def write_png(path, rgb8):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    images.write_png(path, rgb8)


def rgb_grade(img, kind):
    x = np.asarray(img, dtype=np.float64)
    y = M.luminance(x)[..., None]
    if kind == 'sat2':
        return y + 2.0 * (x - y)
    if kind == 'contrast':
        return (x - 0.18) * 1.6 + 0.18
    if kind == 'lift_shadows':
        w = np.clip(1.0 - y / 0.08, 0, 1)
        return x + 0.04 * w
    return x


def g2_sheets(args, frames):
    vis = os.path.join(args.out, 'experiments', 'exp004', 'g2')
    os.makedirs(vis, exist_ok=True)
    # A04 f044 q2: C0/C1/C2 + EXR
    wanted = [f for f in frames if f['test'] == 'A04' and f['frame'] == 44 and f['q'] == 2]
    by = {f['tag']: f for f in wanted}
    if 'c0' not in by:
        return {'absent': 'g2 needs G1 A04 f044 q2'}
    imgs = {}
    for tag in ('c0', 'c1', 'c2-b3'):
        if tag not in by:
            continue
        p = os.path.join(args.out, 'runs', by[tag]['run'], 'frames', by[tag]['key'] + '.f32')
        imgs[tag] = images.read_f32(p)
    ref = None
    if by['c0'].get('ref_path') and os.path.isfile(by['c0']['ref_path']):
        ref, _ = images.read_exr(by['c0']['ref_path'])
        imgs['ref'] = ref
    names = [n for n in ('ref', 'c0', 'c1', 'c2-b3') if n in imgs]
    # full + crops, revelado común (display fijo)
    for name in names:
        write_png(os.path.join(vis, 'common_%s_full.png' % name), disp(imgs[name]))
        for crop_n, (x, y, w, h) in CROPS.items():
            crop = imgs[name][y:y + h, x:x + w]
            write_png(os.path.join(vis, 'common_%s_%s_1to1.png' % (name, crop_n)), disp(crop))
            write_png(os.path.join(vis, 'common_%s_%s_nn4.png' % (name, crop_n)), disp(nn4(crop)))
    # RGB post on C0/C2 (no SDK)
    for kind in ('sat2', 'contrast', 'lift_shadows'):
        for tag in ('c0', 'c2-b3', 'ref'):
            if tag not in imgs:
                continue
            g = rgb_grade(imgs[tag], kind)
            write_png(os.path.join(vis, 'rgbpost_%s_%s_full.png' % (kind, tag)), disp(g))
            x, y, w, h = CROPS['white_strip']
            write_png(os.path.join(vis, 'rgbpost_%s_%s_white_1to1.png' % (kind, tag)),
                      disp(g[y:y + h, x:x + w]))
    # SDK ±2 EV / WB: re-decode BRAW of C0 and C2
    sdk = sdk_variants(args, by)
    html = ['<html><meta charset=utf-8><title>EXP-004 G2</title><body>',
            '<h1>Hojas de contacto G2</h1>',
            '<p>Display fijo: 2^%g lineal → clip 0–1 → γ 1/2.2. Sin percentiles por imagen.</p>' % DISPLAY_EV,
            '<p>SDK ±EV/WB separados de sat/contraste/lift RGB.</p>']
    for section, prefix in (('Revelado común', 'common_'), ('RGB post', 'rgbpost_')):
        html.append('<h2>%s</h2><div style="display:flex;flex-wrap:wrap;gap:8px">' % section)
        for p in sorted(glob.glob(os.path.join(vis, prefix + '*.png'))):
            html.append('<figure><img src="%s" width=360><figcaption>%s</figcaption></figure>' %
                        (os.path.basename(p), os.path.basename(p)))
        html.append('</div>')
    html.append('</body></html>')
    open(os.path.join(vis, 'contact.html'), 'w', encoding='utf-8').write('\n'.join(html))
    return {'dir': vis, 'sdk': sdk, 'images': sorted(os.path.basename(p) for p in glob.glob(os.path.join(vis, '*.png')))}


def sdk_variants(args, by):
    """Revelado SDK extra: exposición ±2 EV y kelvin cálido/frío. No cambia el BRAW."""
    out = {'attempts': []}
    try:
        run = RD.load_run(os.path.join(args.out, 'runs', by['c0']['run']))
        key = by['c0']['key']
        res = (run['metrics'].get('results') or {})[key]
        bind = RD.resolve_braw_binding(run, res, key)
        braw = bind['braw_path']
        settings = dict(((res.get('setup') or {}).get('decode')) or run['metrics']['config']['decode'])
        # fill from metrics config
        cfg = (run['metrics'].get('config') or {}).get('decode') or {}
        for k, v in cfg.items():
            settings.setdefault(k, v)
        decoder = run['run']['decoder']
        cache = os.path.join(args.out, 'cache')
        log = P.Log(echo=False)
        base_exp = float(settings.get('exposure') or 0)
        framework = settings.get('framework') or cfg.get('framework') or (run['run'].get('config') or {}).get('decode', {}).get('framework')
        variants = {
            'sdk_ev-2': dict(settings, exposure=base_exp - 2),
            'sdk_ev+2': dict(settings, exposure=base_exp + 2),
            'sdk_wb_warm': dict(settings, kelvin=3200, tint=0),
            'sdk_wb_cool': dict(settings, kelvin=6500, tint=0),
        }
        vis = os.path.join(args.out, 'experiments', 'exp004', 'g2')
        for tag in ('c0', 'c2-b3'):
            if tag not in by:
                continue
            run_t = RD.load_run(os.path.join(args.out, 'runs', by[tag]['run']))
            res_t = (run_t['metrics'].get('results') or {})[by[tag]['key']]
            bind_t = RD.resolve_braw_binding(run_t, res_t, by[tag]['key'])
            bs = RD.bitstream_frame(run_t, res_t, by[tag]['key'])
            braw_sha = bind_t['braw_sha256']
            idx = bs['local_index']
            for vname, st in variants.items():
                try:
                    img, meta = P.decode(args.brawdec, framework,
                                         decoder, bind_t['braw_path'], braw_sha, idx, st, cache, log,
                                         expected_iso=res_t.get('setup', {}).get('iso'))
                    x, y, w, h = CROPS['white_strip']
                    write_png(os.path.join(vis, '%s_%s_full.png' % (vname, tag)), disp(img))
                    write_png(os.path.join(vis, '%s_%s_white_1to1.png' % (vname, tag)), disp(img[y:y + h, x:x + w]))
                    out['attempts'].append({'tag': tag, 'variant': vname, 'ok': True,
                                            'sha256': meta.get('sha256'), 'seconds': meta.get('seconds')})
                except Exception as e:
                    out['attempts'].append({'tag': tag, 'variant': vname, 'ok': False, 'error': str(e)})
    except Exception as e:
        out['error'] = str(e)
    return out


def impulse_bg_subtract(args):
    bg_run = os.path.join(args.out, 'runs', 'exp004-g0-flat-bg-q2')
    frames = glob.glob(os.path.join(bg_run, 'frames', '*.f32'))
    if not frames:
        return {'absent': 'flat_bg decode missing'}
    bg = images.read_f32(frames[0])
    yb, cb = luma_chroma_split(bg.astype(np.float64))
    out = {}
    for name in ('impulse-even', 'impulse-odd', 'impulse-dct'):
        d = os.path.join(args.out, 'runs', 'exp004-d4-%s-q2' % name)
        fs = glob.glob(os.path.join(d, 'frames', '*.f32'))
        if not fs:
            out[name] = {'absent': 'impulse run missing'}
            continue
        rgb = images.read_f32(fs[0])
        y, c = luma_chroma_split(rgb.astype(np.float64))
        dy, dc = y - yb, c - cb
        mag_y = np.abs(dy)
        mag_c = np.linalg.norm(dc, axis=-1)
        yi, xi = np.unravel_index(int(np.argmax(mag_c)), mag_c.shape)
        out[name] = {
            'bg_sha256': _sha(frames[0]),
            'impulse_sha256': _sha(fs[0]),
            'peak_luma_residual': M._f(float(mag_y.max())),
            'peak_chroma_residual': M._f(float(mag_c.max())),
            'chroma_peak_at': [int(yi), int(xi)],
            'luma_at_chroma_peak': M._f(float(dy[yi, xi])),
        }
    return out


def bc_coverage(args):
    man = json.load(open(os.path.join(ROOT, 'benchmark', 'manifest.json'), encoding='utf-8'))
    rows = []
    for t in man['tests']:
        if t.get('series') not in ('B', 'C'):
            continue
        refs = t.get('references') or []
        rows.append({
            'id': t['id'], 'series': t['series'], 'status': t.get('status'),
            'frames': t.get('frames'), 'n_refs': len(refs),
            'coverage': 'present' if refs and t.get('status') == 'complete' else 'absent',
            'note': 'Sin EXR en manifiesto; no se aprueba dependencia exposición/WB de B/C',
        })
    return rows


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--out', default=os.path.join(ROOT, 'out', 'benchmark'))
    p.add_argument('--references', default=os.path.expanduser('~/Desktop/EXR'))
    p.add_argument('--brawdec', default=os.path.join(ROOT, 'tools', 'brawdec', 'brawdec'))
    p.add_argument('--summary', default=os.path.join(ROOT, 'benchmark', 'experiments', 'exp004-results-summary.json'))
    args = p.parse_args()
    frames, missing, y_ident = g1_collect(args)
    agg, paired = summarize(frames)
    # fill duv_global from luma_chroma.global if missing
    kernels = {k: freq_response_binomial_box(k) for k in ('b3', 'b5', 'b7')}
    g2 = g2_sheets(args, frames)
    h = classify(agg, paired, y_ident)
    # perceptual override: if G2 exists, halo still in crops → not A
    if h.get('C2-b3') == 'A':
        h['C2-b3'] = 'C'
        h['note'] = (h.get('note') or '') + '; G2 no demostró desaparición del halo'
    doc = {
        'schema': 1,
        'phase': 'G-H',
        'g0': {
            'esf_mtf': 'LSF FFT / DC; mean not subtracted',
            'display': 'viz.display exposure=%g common' % DISPLAY_EV,
            'impulse_bg': impulse_bg_subtract(args),
            'binomial_box': kernels,
            'c3_c4': 'inactive',
        },
        'g1': {
            'n_rows': len(frames),
            'missing_runs': missing,
            'y_identity': y_ident,
            'aggregates': agg,
            'paired': paired,
            'frames': [{k: v for k, v in f.items() if k not in ('halo',)} | {'halo_edges': (f.get('halo') or {}).get('edge_rois')} for f in frames],
        },
        'g2': g2,
        'bc_coverage': bc_coverage(args),
        'h': h,
        'bug_002': 'open P0',
        'defaults_changed': False,
        'exp005_started': False,
    }
    outp = os.path.join(args.out, 'experiments', 'exp004', 'gh.json')
    os.makedirs(os.path.dirname(outp), exist_ok=True)
    with open(outp, 'w', encoding='utf-8') as f:
        json.dump(doc, f, indent=2)
        f.write('\n')
    # merge into summary
    if os.path.isfile(args.summary):
        summary = json.load(open(args.summary, encoding='utf-8'))
    else:
        summary = {'experiment': 'EXP-004'}
    summary['gh'] = {
        'h': h,
        'g1_aggregates': agg,
        'g1_paired': paired,
        'g1_y_identity': y_ident,
        'g1_missing': missing,
        'g0_impulse_bg': doc['g0']['impulse_bg'],
        'g0_binomial_box_dc': {k: v['dc_combined'] for k, v in kernels.items()},
        'bc_coverage': doc['bc_coverage'],
        'g2_n_images': len(g2.get('images') or []) if isinstance(g2, dict) else 0,
        'bug_002': 'open P0',
        'select_for_exp005': h.get('select_for_exp005'),
    }
    with open(args.summary, 'w', encoding='utf-8') as f:
        json.dump(summary, f, indent=2)
        f.write('\n')
    print('wrote', outp)
    print('H', h)
    print('missing', len(missing), 'rows', len(frames))


if __name__ == '__main__':
    main()
