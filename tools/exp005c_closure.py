#!/usr/bin/env python3
"""EXP-005c F–I: corrige PSNR desde f32 cacheados; audita cromática y Camera Support. Sin re-encode."""
from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.exp005c_camera_support import (OG_CAL, OG_ISO, SIGMA, inventory,
                                          og_channelcodec_profile_feasibility, what_determines)
from tools.exp005c_chroma import TRACE, code_from_ycc, codes_from_ycc, ycc_from_codes
from tools.exp005c_metrics import annotate_frame
from tools.exp005c_report import JSON_OUT, build_payload, write_doc
from tools.exp005c_run import JOBS
from tools.fidelity import images

FRAMES = os.path.join(ROOT, 'out', 'benchmark', 'experiments', 'exp005c', 'frames')


def _exr_dir():
    return os.environ.get('CDNG_EXR_DIR') or os.path.expanduser('~/Desktop/EXR')


def correct_smoke(smoke):
    if smoke.get('status') != 'measured':
        return smoke
    refdir = _exr_dir()
    rows = []
    for job, row in zip(JOBS, smoke.get('frames') or []):
        c0p = os.path.join(FRAMES, job['id'] + '_c0.f32')
        byp = os.path.join(FRAMES, job['id'] + '_bayer.f32')
        exr = os.path.join(refdir, job['exr'])
        if not (os.path.isfile(c0p) and os.path.isfile(byp) and os.path.isfile(exr)):
            row = dict(row)
            row['metrics_correction'] = 'missing_f32_or_exr'
            rows.append(row)
            continue
        c0 = images.read_f32(c0p)
        by = images.read_f32(byp)
        ref, _ = images.read_exr(exr)
        rows.append(annotate_frame(row, ref, c0, by, job['exr']))
    smoke = dict(smoke)
    smoke['frames'] = rows
    smoke['note'] = (
        'PSNR raw vs S9 EXR; PSNR exposure-matched iguala luma media. '
        'A04_f044 EXR MINUS3: raw inválido. Halos C0 vs Bayer no dependen de ese PSNR. '
        'No se elige por PSNR global. Sin re-encode.'
    )
    smoke['benchmark_correction'] = 'F: split raw / exposure-matched from cached f32; no full campaign'
    return smoke


def chroma_audit():
    m = code_from_ycc()
    r, g, b = codes_from_ycc(1000.0, 40.0, -20.0)
    y, cb, cr = ycc_from_codes(r, g, b)
    return {
        'code_from_ycc': m,
        'roundtrip_ycc_err': [abs(y - 1000.0), abs(cb - 40.0), abs(cr + 20.0)],
        'trace': TRACE,
        'same_bitstream_algebra': True,
        'same_pre_finish_semantics': False,
        'assumption_avoid_ahd_kills_halos': False,
    }


def camera_audit():
    return {
        'profiles': {
            'og_iso': inventory(OG_ISO),
            'og_calibrated': inventory(OG_CAL),
            'sigma_fp': inventory(SIGMA),
        },
        'determines': what_determines(),
        'og_channelcodec': og_channelcodec_profile_feasibility(),
        'generator': 'tools/make_pocket_og_iso_profile.py deriva ISO desde la revisión calibrada; no toca inv_sqrt ni CSC.',
        'installer': 'app/profile_store.cpp ProfileSpec::pocketOg — no modificado en esta fase.',
    }


def decision():
    cc = og_channelcodec_profile_feasibility()
    return {
        'channelcodec_og': cc['classification'],
        'mosaic_rgb_nativesensor_prototype': 'C',
        'bug002': 'P0',
        'missing_to_prove_viability': cc['gaps'],
        'proposed_smoke_if_implemented': {
            'proceed_now': False,
            'why_not_now': 'Falta ChannelCodec OG y mapeo CFA; no se implementa otra ruta experimental en esta fase.',
            'if_later': (
                'Opt-in solo OG, default AHD intacto. Frames: A04_f049 y A01_f052 '
                '(EXR de exposición nominal). A04_f044 solo con EXR×8 o matched. '
                'q=2 NR/RDOQ OFF. ROI white_strip, cc_ruler_*, bordes. Comparar CinemaDNG / C0 / codec-OG. '
                'Éxito: SDK + colorimetría + no empeorar exposición; BUG-002 se juzga en ROI, no PSNR global.'
            ),
        },
        'no_exp006_007_008': True,
        'no_compression_research': True,
        'no_merge': True,
    }


def main():
    extra = {}
    if os.path.isfile(JSON_OUT):
        with open(JSON_OUT, encoding='utf-8') as f:
            prev = json.load(f)
        extra['mosaic_audit'] = prev.get('mosaic_audit')
        extra['smoke'] = correct_smoke(prev.get('smoke') or {})
        extra['prototype'] = prev.get('prototype')
    extra['chroma_audit'] = chroma_audit()
    extra['camera_support_audit'] = camera_audit()
    extra['architectural_decision'] = decision()
    p = build_payload(extra)
    os.makedirs(os.path.dirname(JSON_OUT), exist_ok=True)
    with open(JSON_OUT, 'w', encoding='utf-8') as f:
        json.dump(p, indent=2, fp=f)
        f.write('\n')
    write_doc(p)
    d = p['architectural_decision']
    print('class_prototype', p['feasibility']['class'])
    print('channelcodec_og', d['channelcodec_og'])
    for r in (p.get('smoke') or {}).get('frames') or []:
        print(r.get('id'), 'raw', r.get('psnr_raw'), 'matched', r.get('psnr_exposure_matched'),
              'valid', r.get('raw_comparison_valid'), 'minus3', r.get('minus3_exr'))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
