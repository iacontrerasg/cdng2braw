#!/usr/bin/env python3
"""Agrega EXP-004: dumps, hipótesis, hojas de contacto. Artefactos voluminosos en out/."""
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

from tools.exp004_halo import (chroma_error_vs_ref, extract_profile, profile_metrics,
                               residual_spectrum, uv_local)

CROP = (640, 520, 120, 80)  # x,y,w,h around white-strip edge
from tools.fidelity import braw_inspect, images, metrics as M, rd as RD, stage_io

CHART_EDGES = [
    ('edge_white_strip_left', [664, 550, 704, 550], 9),
    ('edge_grey_strip_right', [828, 550, 868, 550], 9),
    ('edge_patch_top', [1258, 336, 1258, 376], 9),
    ('edge_wire_h', [1500, 345, 1500, 400], 9),
]


def _sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def load_stage_frame(stages_root, run_id):
    d = os.path.join(stages_root, run_id, 'frame_0')
    if not os.path.isdir(d):
        return None
    return stage_io.load_frame_dir(d)


def plane(dump, key):
    if not dump or key not in dump:
        return None
    return dump[key]['array']


def analyze_dump(dump):
    out = {'stages_present': sorted(dump.keys()) if dump else []}
    if not dump:
        return out
    s4y, s4cb, s4cr = plane(dump, 'S4_Y'), plane(dump, 'S4_Cb'), plane(dump, 'S4_Cr')
    s5y, s5cb, s5cr = plane(dump, 'S5_Y'), plane(dump, 'S5_Cb'), plane(dump, 'S5_Cr')
    if s4cb is not None:
        out['s4_chroma'] = {
            'cb_abs_mean': float(np.mean(np.abs(s4cb))),
            'cr_abs_mean': float(np.mean(np.abs(s4cr))),
            'cb_peak': float(np.max(np.abs(s4cb))),
            'cr_peak': float(np.max(np.abs(s4cr))),
        }
    if s4cb is not None and s5cb is not None:
        box = s4cb.reshape(s4cb.shape[0] // 2, 2, s4cb.shape[1] // 2, 2).mean(axis=(1, 3))
        out['h3_box_vs_s5'] = {
            'cb_mae': float(np.mean(np.abs(box - s5cb))),
            'note': 'S5 is map(mean RGB 2x2), not mean of S4 YCbCr — MAE>0 is expected (H3/H2 mix)',
        }
    s6a, s6b = plane(dump, 'S6_pre_Cb'), plane(dump, 'S6_post_Cb')
    if s6a is not None and s6b is not None:
        out['h6_chroma_precomp'] = {
            'cb_identical': bool(np.array_equal(s6a, s6b)),
            'cr_identical': bool(np.array_equal(plane(dump, 'S6_pre_Cr'), plane(dump, 'S6_post_Cr'))),
            'y_changed': not np.array_equal(plane(dump, 'S6_pre_Y'), plane(dump, 'S6_post_Y')),
        }
    s6y, s7y = plane(dump, 'S6_post_Y'), plane(dump, 'S7_Y')
    if s6y is not None and s7y is not None:
        vis = np.abs(s6y.astype(np.float64) - s7y.astype(np.float64))
        out['h7_quant_luma_mae'] = float(vis.mean())
        out['h7_quant_cb_mae'] = float(np.mean(np.abs(
            plane(dump, 'S6_post_Cb').astype(np.float64) - plane(dump, 'S7_Cb').astype(np.float64))))
    return out


def roi_report(ref, cand, cfg_ymin=0.005):
    rows = []
    for name, line, width in CHART_EDGES:
        pr = extract_profile(ref, line, width)
        pc = extract_profile(cand, line, width)
        yr, yc = pr @ M.P3_LUMA, pc @ M.P3_LUMA
        # RGB residual as proxy chroma in display domain (S8 vs S9).
        d = pc - pr
        mag = np.linalg.norm(d - np.mean(d, axis=-1, keepdims=True), axis=-1)
        rows.append({
            'name': name, 'line': line, 'width': width,
            'uv': uv_local(pc.reshape(-1, 1, 3), pr.reshape(-1, 1, 3), cfg_ymin),
            'fringe_max': float(np.max(mag)),
            'profile': profile_metrics(yc, d[:, 0], d[:, 2]),
        })
    return rows


def vis_residual(ref, cand, path, scale=0.02):
    d = cand - ref
    chroma = d - np.mean(d, axis=-1, keepdims=True)
    vis = np.clip(chroma / scale * 0.5 + 0.5, 0, 1)
    rgb8 = (vis * 255).astype(np.uint8)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, 'wb').write(images.png_bytes(rgb8))


def hypothesis_matrix(dump_stats, q2_vs_q24, synth_neutral):
    def status(name, state, evidence):
        return {'id': name, 'status': state, 'evidence': evidence}

    rows = []
    h6 = next((d.get('h6_chroma_precomp') for d in dump_stats.values() if d.get('h6_chroma_precomp')), None)
    if h6:
        rows.append(status('H6', 'confirmed' if h6.get('cb_identical') else 'rejected',
                           'Canvas chroma identical with/without luma precomp; luma changes=%s' % h6.get('y_changed')))
    else:
        rows.append(status('H6', 'inconclusive', 'missing S6 dumps'))
    h7 = [d for d in dump_stats.values() if 'h7_quant_cb_mae' in d]
    if h7:
        mae = h7[0]['h7_quant_cb_mae']
        rows.append(status('H7', 'confirmed' if mae > 1.0 else 'inconclusive',
                           'S7 vs S6_post Cb MAE=%.3f codes (quant/IDCT); halo may preexist DCT' % mae))
    else:
        rows.append(status('H7', 'inconclusive', 'missing S7'))
    rows.append(status('H1', 'inconclusive' if not dump_stats else 'plausible',
                       'S0/S1 dumps exist; AHD false color is a known class — quantify vs S9 in RGB domain'))
    rows.append(status('H2', 'refined',
                       'Mechanism exists (curve then YCbCr; box on linear RGB) but synthetic R=G=B yields S4/S5 chroma=0'))
    rows.append(status('H3', 'refined',
                       'Box 2×2 is the only OG AA; it does not create chroma on perfect greys — aliasing needs existing RGB mismatch'))
    rows.append(status('H4', 'inconclusive',
                       'OG siting is centered 2×2; SDK upsample kernel is opaque — compare S7 vs S8'))
    rows.append(status('H5', 'inconclusive', 'Requires S8 vs S7 edge overshoot; SDK is closed'))
    rows.append(status('H8', 'plausible', 'Lanczos-3 independent per RGB channel on linear camera RGB'))
    rows.append(status('H9', 'plausible', 'Defect likely combination: AHD + nonlinear box-chroma + SDK upsample'))
    if synth_neutral:
        rows.append(status('synth_neutral_chroma', 'measured', synth_neutral))
    return rows


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--out', default=os.path.join(ROOT, 'out', 'benchmark'))
    p.add_argument('--summary', default=os.path.join(ROOT, 'benchmark', 'experiments', 'exp004-results-summary.json'))
    args = p.parse_args()
    runs_glob = os.path.join(args.out, 'runs', 'exp004-*')
    stages_root = os.path.join(args.out, 'experiments', 'exp004', 'stages')
    vis_root = os.path.join(args.out, 'experiments', 'exp004', 'viz')
    os.makedirs(vis_root, exist_ok=True)

    run_rows = []
    dump_stats = {}
    for d in sorted(glob.glob(runs_glob)):
        if not os.path.isfile(os.path.join(d, 'metrics.json')):
            continue
        run = RD.load_run(d)
        rid = os.path.basename(d)
        dump = load_stage_frame(stages_root, rid)
        dump_stats[rid] = analyze_dump(dump)
        edges = None
        for key, res in (run['metrics'].get('results') or {}).items():
            cand_path = os.path.join(d, 'frames', key + '.f32')
            cand = images.read_f32(cand_path) if os.path.isfile(cand_path) else None
            ref = None
            rf = (res.get('reference_fidelity') or {})
            # EXR del banco: no está copiado en el run; el informe usa métricas ya calculadas.
            lc = rf.get('luma_chroma') or {}
            if cand is not None:
                # Mapa de residuo vs gris local (sin EXR): desviación RGB del candidato.
                grey = np.mean(cand, axis=-1, keepdims=True)
                vis_residual(np.repeat(grey, 3, -1), cand, os.path.join(vis_root, rid + '_' + key + '_chroma_dev.png'), 0.05)
                if rid.startswith('exp004-cand-') and key.endswith('f044'):
                    x, y, cw, ch = CROP
                    crop = cand[y:y + ch, x:x + cw]
                    nn = np.repeat(np.repeat(crop, 4, 0), 4, 1)
                    lo, hi = np.percentile(cand, 0.5), np.percentile(cand, 99.5)
                    def disp(im):
                        t = np.clip((im - lo) / max(hi - lo, 1e-8), 0, 1)
                        return (t * 255).astype(np.uint8)
                    images.write_png(os.path.join(vis_root, rid + '_' + key + '_full.png'), disp(cand))
                    images.write_png(os.path.join(vis_root, rid + '_' + key + '_crop.png'), disp(crop))
                    images.write_png(os.path.join(vis_root, rid + '_' + key + '_crop_nn4.png'), disp(nn))
            try:
                row = RD.frame_row(run, key, res, require_bitstream=True)
            except Exception:
                row = {'key': key, 'error': 'no bitstream'}
            run_rows.append({
                'run': rid, 'key': key,
                'sample_bytes': row.get('sample_bytes') if isinstance(row, dict) else None,
                'q_eff': row.get('q_eff') if isinstance(row, dict) else None,
                'ratio_sample': row.get('ratio_sample') if isinstance(row, dict) else None,
                'psnr': row.get('psnr') if isinstance(row, dict) else None,
                'duv_edges_mean': (lc.get('edges') or {}).get('duv_mean') or row.get('duv_edges'),
                'grad': row.get('grad') if isinstance(row, dict) else None,
                'detail_corr': (rf.get('alignment') or {}).get('detail_correlation'),
                'dump': dump_stats[rid],
            })

    ident = {}
    a = os.path.join(args.out, 'runs', 'exp004-ident-g0')
    b = os.path.join(args.out, 'runs', 'exp004-ident-g0-nodump')
    if os.path.isdir(a) and os.path.isdir(b):
        def ident_one(run_dir):
            run = RD.load_run(run_dir)
            out = {}
            for k, res in (run['metrics'].get('results') or {}).items():
                try:
                    row = RD.frame_row(run, k, res, require_bitstream=True)
                    fp = None
                    rgb_sha = None
                    try:
                        spec = RD.resolve_braw_binding(run, res, k)
                        fp = braw_inspect.media_fingerprint(spec['braw_path'])
                    except Exception as e:
                        fp = {'error': str(e)}
                    f32 = os.path.join(run_dir, 'frames', k + '.f32')
                    if os.path.isfile(f32):
                        rgb_sha = _sha(f32)
                    out[k] = {
                        'sample_bytes': row.get('sample_bytes'), 'q_eff': row.get('q_eff'),
                        'payload_sha256': (fp or {}).get('payloads_sha256') if isinstance(fp, dict) else None,
                        'rgb_sha256': rgb_sha,
                        'fingerprint': fp if isinstance(fp, dict) and 'error' in fp else {
                            'payloads_sha256': (fp or {}).get('payloads_sha256'),
                            'mdat_sha256': (fp or {}).get('mdat_sha256'),
                        },
                    }
                except Exception as e:
                    out[k] = {'error': str(e)}
            return out
        on, off = ident_one(a), ident_one(b)
        ident = {
            'dump_on': on, 'dump_off': off,
            'independent_conversions': True,
            'payload_match': all(
                on.get(k, {}).get('payload_sha256') == off.get(k, {}).get('payload_sha256')
                and on.get(k, {}).get('payload_sha256')
                for k in set(on) | set(off)),
            'rgb_match': all(
                on.get(k, {}).get('rgb_sha256') == off.get(k, {}).get('rgb_sha256')
                and on.get(k, {}).get('rgb_sha256')
                for k in set(on) | set(off)),
        }

    synth_neutral = None
    syn = os.path.join(args.out, 'experiments', 'exp004', 'stages', 'exp004-syn-edge-neutral-v-q2', 'frame_0')
    if os.path.isdir(syn):
        dump = stage_io.load_frame_dir(syn)
        cb = plane(dump, 'S5_Cb')
        if cb is not None:
            synth_neutral = {
                's5_cb_abs_mean': float(np.mean(np.abs(cb))),
                's5_cr_abs_mean': float(np.mean(np.abs(plane(dump, 'S5_Cr')))),
                's4_cb_abs_mean': float(np.mean(np.abs(plane(dump, 'S4_Cb')))) if plane(dump, 'S4_Cb') is not None else None,
            }

    base = open(os.path.join(ROOT, 'benchmark', 'experiments', 'exp004-base-sha.txt')).read().strip()
    hypo = hypothesis_matrix(dump_stats, None, synth_neutral)
    first_stage = {
        'luma_ringing_lanczos': 'S2 (hypothesis: resize)',
        'ahd_false_color': 'S1 (hypothesis)',
        'nonlinear_chroma_box': 'S5 vs S4 (verified mechanism in imagePlanes)',
        'deblock_luma_only': 'S6_post Y; chroma unchanged (H6)',
        'dct_quant': 'S7',
        'sdk_upsample_csc': 'S8 (not substituted by S7)',
    }
    cand_cmp = []
    by_run = {r['run']: r for r in run_rows}
    for q in (2, 18, 24):
        base_id = 'exp004-cand-c0-a04-q%d' % q
        base_row = by_run.get(base_id)
        if not base_row:
            continue
        for tag in ('c1', 'c2-b3', 'c2-b5', 'c2-b7'):
            rid = 'exp004-cand-%s-a04-q%d' % (tag, q)
            row = by_run.get(rid)
            if not row:
                continue
            duv0, duv1 = base_row.get('duv_edges_mean'), row.get('duv_edges_mean')
            p0, p1 = base_row.get('psnr'), row.get('psnr')
            g0, g1 = base_row.get('grad'), row.get('grad')
            cand_cmp.append({
                'q': q, 'candidate': tag, 'vs': 'c0',
                'duv_edges_base': duv0, 'duv_edges': duv1,
                'delta_duv_edges': (duv1 - duv0) if None not in (duv0, duv1) else None,
                'psnr_base': p0, 'psnr': p1,
                'delta_psnr': (p1 - p0) if None not in (p0, p1) else None,
                'grad_base': g0, 'grad': g1,
                'sample_bytes_base': base_row.get('sample_bytes'),
                'sample_bytes': row.get('sample_bytes'),
            })
    verdicts = []
    for tag in ('c1', 'c2-b3', 'c2-b5', 'c2-b7'):
        rows = [c for c in cand_cmp if c['candidate'] == tag and c.get('delta_duv_edges') is not None]
        if not rows:
            continue
        mean_duv = float(np.mean([c['delta_duv_edges'] for c in rows]))
        mean_psnr = float(np.mean([c['delta_psnr'] for c in rows if c.get('delta_psnr') is not None] or [0]))
        mean_grad = float(np.mean([(c['grad'] or 0) - (c['grad_base'] or 0) for c in rows]))
        # Halo win only if Δu'v' down without collapsing gradient (chroma/luma edge energy).
        halo_down = mean_duv < -1e-4
        detail_ok = mean_grad > -0.02
        verdicts.append({
            'candidate': tag, 'mean_delta_duv_edges': mean_duv, 'mean_delta_psnr': mean_psnr,
            'mean_delta_grad': mean_grad,
            'class': 'promising' if halo_down and detail_ok else (
                'resolution_loss_suspect' if halo_down and not detail_ok else (
                    'regression' if mean_duv > 1e-4 or mean_psnr < -0.15 else 'neutral')),
        })
    d4_path = os.path.join(args.out, 'experiments', 'exp004', 'd4.json')
    d4 = json.load(open(d4_path, encoding='utf-8')) if os.path.isfile(d4_path) else None
    c3_ok = bool(d4 and d4.get('siting', {}).get('c3_warranted'))
    if d4 and d4.get('siting'):
        delta = (d4['siting'].get('even_odd_centroid_delta_px') or [None, None])
        for h in hypo:
            if h['id'] == 'H4':
                h['status'] = 'not_supported' if not c3_ok else 'plausible'
                h['evidence'] = 'SDK impulse even/odd centroid Δpx=%s; C3 %s' % (
                    delta, 'warranted' if c3_ok else 'not warranted')
    # Residuos C0 vs C2-b3 con escala común (q24).
    p0 = os.path.join(args.out, 'runs', 'exp004-cand-c0-a04-q24', 'frames')
    p2 = os.path.join(args.out, 'runs', 'exp004-cand-c2-b3-a04-q24', 'frames')
    f0 = glob.glob(os.path.join(p0, '*044*.f32'))
    f2 = glob.glob(os.path.join(p2, '*044*.f32'))
    if f0 and f2:
        a, b = images.read_f32(f0[0]), images.read_f32(f2[0])
        vis_residual(a, b, os.path.join(vis_root, 'c0_vs_c2b3_q24_residual.png'), 0.02)
        x, y, cw, ch = CROP
        vis_residual(a[y:y + ch, x:x + cw], b[y:y + ch, x:x + cw],
                     os.path.join(vis_root, 'c0_vs_c2b3_q24_crop_residual.png'), 0.02)
    candidates = [
        {'id': 'C0', 'implemented': True, 'flag': 'CDNG_OG_CHROMA_MODE=c0 or absent',
         'status': 'baseline bit-identical when flags absent'},
        {'id': 'C1', 'implemented': True, 'flag': 'CDNG_OG_CHROMA_MODE=c1',
         'status': 'per-pixel curve+matrix then 2×2 mean Cb/Cr; Y unchanged'},
        {'id': 'C2', 'implemented': True, 'flag': 'MODE=c2 KERNEL=b3|b5|b7',
         'status': 'separable binomial AA (DC gain 1) on coded chroma then 2×2',
         'kernels': {
             'b3': {'taps': [0.25, 0.5, 0.25], 'H_w': 'cos^2(w/2)'},
             'b5': {'taps': [1, 4, 6, 4, 1], 'norm': 16, 'H_w': 'cos^4(w/2)'},
             'b7': {'taps': [1, 6, 15, 20, 15, 6, 1], 'norm': 64, 'H_w': 'cos^6(w/2)'},
         }},
        {'id': 'C3', 'implemented': True, 'flag': 'MODE=c3 PHASE=even|odd|center',
         'status': 'code present; F campaign skipped unless D4 c3_warranted',
         'c3_warranted': c3_ok},
        {'id': 'C4', 'implemented': True, 'flag': 'MODE=c4 KERNEL+PHASE',
         'status': 'combination path; enable only if C2 and C3 independently help'},
    ]
    doc = {
        'schema': 2,
        'experiment': 'EXP-004',
        'base_commit': base,
        'branch': 'experiment/pocket-og-chromatic-fidelity',
        'rdoq': 'experimental OFF default; not used here',
        'instrumentation': {
            'env': ['CDNG_DUMP_STAGES', 'CDNG_DUMP_FRAME', 'CDNG_INJECT_RGB', 'CDNG_INJECT_AT',
                    'CDNG_OG_CHROMA_MODE', 'CDNG_OG_CHROMA_KERNEL', 'CDNG_OG_CHROMA_PHASE'],
            'stages': ['S0', 'S1', 'S2', 'S3', 'S4', 'S5', 'S6_pre', 'S6_post', 'S7', 'S8', 'S9'],
            'payload_identity': ident,
        },
        'n_runs': len({r['run'] for r in run_rows}),
        'runs': run_rows,
        'hypothesis_matrix': hypo,
        'first_stage_by_component': first_stage,
        'corrective_candidates': candidates,
        'candidate_comparison': cand_cmp,
        'candidate_verdicts': verdicts,
        'gh_recommendation': {
            'next': 'G–H: C2-b3 first (promising Δu′v′ without gradient collapse); C1 optional; C2-b7 not',
            'do_not': ['EXP-005', 'EXP-007', 'EXP-008', 'production default change', 'merge C3 without stronger siting'],
            'c3': 'code present, F skipped; weak ~0.65 px vertical SDK bias vs 2×2 center',
        },
        'compute_cost': {
            'C0': 'baseline imagePlanes (Y per-pixel + map of 2×2 RGB mean)',
            'C1': 'no extra map of means; reuse per-pixel Cb/Cr + 2×2 (cheaper than C0 chroma)',
            'C2': 'two separable convolutions on 1920×1080 Cb and Cr then 2×2; b7 widest',
            'C3': 'point sample, negligible',
        },
        'd4': d4,
        'synthetic_neutral': synth_neutral,
        'notes': [
            'S7 is local encoder reconstruct, not SDK.',
            'Do not compare buffers across incompatible domains.',
            'Global fidelity gates unchanged.',
            'Halo reduction that destroys useful chroma resolution is not a win.',
            'EXP-007/008 deferred; EXP-005 not started.',
        ],
    }
    os.makedirs(os.path.dirname(args.summary) or '.', exist_ok=True)
    with open(args.summary, 'w', encoding='utf-8') as f:
        json.dump(doc, f, indent=2)
        f.write('\n')
    print('wrote', args.summary)
    print('hypothesis', [(h['id'], h['status']) for h in hypo])
    print('identity', ident.get('payload_match'))


if __name__ == '__main__':
    main()
