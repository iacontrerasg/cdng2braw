"""Fase C EXP-002: distorsión incremental BRAW(q) vs BRAW(q_min) a misma precompensación."""
import argparse
import json
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.fidelity import images, metrics as M, rd as RD


DEFAULT_ANALYSIS = {
    'normalize_to_iso': 200, 'psnr_peak': 1.0,
    'error_percentiles': [50, 99],
    'luminance_bins': [0.0, 0.01, 0.1, 1e9],
    'chroma_min_luminance': 0.005,
    'edge_mask': {'percentile': 90, 'dilate': 0, 'min_luminance': 0.002},
    'neutral_mask': {'max_duv': 0.01, 'min_luminance': 0.01},
    'ceiling_margin': 0.9999,
}


def error_cross_term(ref, a, b):
    """Descompone e_total=a-ref y e_inc=b-a: ‖e_total‖² = ‖e_res‖² + ‖e_inc‖² + 2〈e_res,e_inc〉
    donde e_res = a-ref (residual a q_min) y e_inc = b-a."""
    e_res = a.astype(np.float64) - ref.astype(np.float64)
    e_inc = b.astype(np.float64) - a.astype(np.float64)
    e_tot = b.astype(np.float64) - ref.astype(np.float64)
    n = e_tot.size
    return {
        'mse_residual': float(np.mean(e_res * e_res)),
        'mse_incremental': float(np.mean(e_inc * e_inc)),
        'mse_total': float(np.mean(e_tot * e_tot)),
        'cross_term': float(2 * np.mean(e_res * e_inc)),
        'n': int(n),
    }


def analyze_pair(base_dir, cand_dir, ref_exr_by_key, analysis):
    base = RD.load_run(base_dir)
    cand = RD.load_run(cand_dir)
    out = {}
    for key, cres in cand['metrics']['results'].items():
        if key not in base['metrics']['results']:
            continue
        bpath = os.path.join(base['dir'], 'frames', key + '.f32')
        cpath = os.path.join(cand['dir'], 'frames', key + '.f32')
        bimg = images.read_f32(bpath)
        cimg = images.read_f32(cpath)
        inc, _ = M.fidelity(bimg, cimg, analysis, norm=1.0, ceiling=None)
        row = {
            'incremental': inc.get('normalized'),
            'base_sha': base['metrics']['results'][key].get('candidate_sha256'),
            'cand_sha': cres.get('candidate_sha256'),
            'absolute_cand': (cres.get('reference_fidelity') or {}).get('normalized'),
            'absolute_base': (base['metrics']['results'][key].get('reference_fidelity') or {}).get('normalized'),
        }
        # Si hay EXR de referencia alineada en el run candidato, descompone el error.
        ref_meta = (cres.get('setup') or {}).get('reference') or {}
        # Los EXR no están en el run; usamos el absolute ya medido + descomposición b vs c vs no-ref.
        # Descomposición solo entre base/cand sin EXR aquí (residual = base como proxy de q_min).
        row['decomposition_vs_qmin'] = {
            'mse_incremental_only': float(np.mean((cimg.astype(np.float64) - bimg.astype(np.float64)) ** 2)),
            'note': 'Incremental respecto a BRAW(q_min), no CinemaDNG. No restar PSNR.',
        }
        out[key] = row
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', required=True, help='run BRAW(q_min)')
    ap.add_argument('--candidates-glob', required=True)
    ap.add_argument('--dest', required=True)
    args = ap.parse_args()
    import glob
    os.makedirs(args.dest, exist_ok=True)
    cands = sorted(d for d in glob.glob(args.candidates_glob) if os.path.isdir(d))
    report = {}
    for c in cands:
        if os.path.abspath(c) == os.path.abspath(args.base):
            continue
        report[os.path.basename(c)] = analyze_pair(args.base, c, {}, DEFAULT_ANALYSIS)
    with open(os.path.join(args.dest, 'incremental.json'), 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2, sort_keys=True)
    print(args.dest, len(report), 'pairs')


if __name__ == '__main__':
    main()
