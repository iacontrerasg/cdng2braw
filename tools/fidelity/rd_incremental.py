"""Fase C EXP-002: distorsión incremental BRAW(q) vs BRAW(q_min efectivo) a misma precompensación.

La referencia incremental es el BRAW de mínima cuantización efectiva (no CinemaDNG lossless).
Normalización ISO/exposición según el análisis del manifiesto del benchmark.
"""
import argparse
import glob
import json
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.fidelity import identity, images, metrics as M, rd as RD
from tools.fidelity.runner import exposure_of


class IncrementalError(RuntimeError):
    pass


def load_analysis(manifest_path=None):
    path = manifest_path or os.path.join(ROOT, 'benchmark', 'manifest.json')
    doc = json.load(open(path, encoding='utf-8'))
    if 'analysis' not in doc:
        raise IncrementalError('manifiesto sin analysis: %s' % path)
    return doc['analysis'], path


def frame_norm(result, analysis):
    """Factor de normalización ISO/exposición del benchmark (igual que runner.evaluate)."""
    iso = float((result.get('setup') or {}).get('iso') or 200)
    settings = (result.get('setup') or {}).get('decode') or {}
    return float(analysis['normalize_to_iso']) / iso * (2.0 ** -exposure_of(settings))


def precomp_fingerprint(result):
    env = ((result.get('setup') or {}).get('encode') or {}).get('experimental_env') or {}
    return {
        'CDNG_OG_DEBLOCK_PRECOMP': env.get('CDNG_OG_DEBLOCK_PRECOMP'),
        'CDNG_OG_DEBLOCK_ALPHA': env.get('CDNG_OG_DEBLOCK_ALPHA'),
        'CDNG_OG_DEBLOCK_BOUNDED': env.get('CDNG_OG_DEBLOCK_BOUNDED'),
    }


def assert_pair_identity(base_run, cand_run, key):
    """Verifica identidad de entradas/refs/revelado/precomp; solo permite diferir en cuantización."""
    if key not in base_run['metrics']['results']:
        raise IncrementalError('%s: falta fotograma %s en base' % (cand_run['id'], key))
    if key not in cand_run['metrics']['results']:
        raise IncrementalError('%s: falta fotograma %s en candidato' % (cand_run['id'], key))
    b, c = base_run['metrics']['results'][key], cand_run['metrics']['results'][key]
    bcfg, ccfg = RD.config_from_result(b), RD.config_from_result(c)

    for field, bv, cv in (
        ('inputs', bcfg.get('inputs'), ccfg.get('inputs')),
        ('reference', bcfg.get('reference'), ccfg.get('reference')),
        ('frame', bcfg.get('frame'), ccfg.get('frame')),
        ('decode', bcfg.get('decode'), ccfg.get('decode')),
        ('iso', bcfg.get('iso'), ccfg.get('iso')),
        ('dng_sha256', bcfg.get('dng_sha256'), ccfg.get('dng_sha256')),
        ('clip', bcfg.get('clip'), ccfg.get('clip')),
        ('noise_synthesis', bcfg.get('noise_synthesis'), ccfg.get('noise_synthesis')),
        ('nr_luma', bcfg.get('nr_luma'), ccfg.get('nr_luma')),
        ('nr_chroma', bcfg.get('nr_chroma'), ccfg.get('nr_chroma')),
    ):
        if bv != cv:
            raise IncrementalError('%s/%s: %s distinto entre base y candidato' % (cand_run['id'], key, field))

    if precomp_fingerprint(b) != precomp_fingerprint(c):
        raise IncrementalError('%s/%s: precompensación distinta' % (cand_run['id'], key))

    # Solo se autorizan diferencias de cuantización (q o ratio solicitado)
    if bcfg['mode'] != ccfg['mode']:
        raise IncrementalError('%s/%s: modo de compresión distinto' % (cand_run['id'], key))
    if bcfg['mode'] == 'quality':
        if bcfg['q_requested'] is None or ccfg['q_requested'] is None:
            raise IncrementalError('%s/%s: q solicitado ausente' % (cand_run['id'], key))
        if int(bcfg['q_requested']) >= int(ccfg['q_requested']) and bcfg['q_requested'] != ccfg['q_requested']:
            # base debe ser q menor o igual; si iguales, no hay distorsión incremental útil
            pass
        if int(bcfg['q_requested']) > int(ccfg['q_requested']):
            raise IncrementalError('%s/%s: base q=%s > candidato q=%s' % (
                cand_run['id'], key, bcfg['q_requested'], ccfg['q_requested']))
    elif bcfg['mode'] == 'ratio':
        if bcfg['ratio_requested'] != ccfg['ratio_requested']:
            # permitido: distinta ratio
            pass
        else:
            raise IncrementalError('%s/%s: misma ratio solicitada; no hay eje incremental' % (cand_run['id'], key))

    bi, ci = RD.run_identity(base_run), RD.run_identity(cand_run)
    for k in ('cdng_convert_sha256', 'dataset', 'references'):
        if bi.get(k) and ci.get(k) and bi[k] != ci[k]:
            raise IncrementalError('%s: %s de corrida incompatible con base' % (cand_run['id'], k))

    bpath = os.path.join(base_run['dir'], 'frames', key + '.f32')
    cpath = os.path.join(cand_run['dir'], 'frames', key + '.f32')
    for p, res, label in ((bpath, b, 'base'), (cpath, c, 'cand')):
        if not os.path.isfile(p):
            raise IncrementalError('%s/%s: falta f32 %s' % (cand_run['id'], key, label))
        expect = res.get('candidate_sha256')
        if expect:
            got = identity.sha256_file(p)
            if got != expect:
                raise IncrementalError('%s/%s: sha f32 %s ≠ candidate_sha256' % (cand_run['id'], key, label))
    return b, c, bpath, cpath


def analyze_pair(base_dir, cand_dir, analysis):
    base = RD.load_run(base_dir)
    cand = RD.load_run(cand_dir)
    base_keys = set(base['metrics']['results'])
    cand_keys = set(cand['metrics']['results'])
    if base_keys != cand_keys:
        missing_in_cand = sorted(base_keys - cand_keys)
        missing_in_base = sorted(cand_keys - base_keys)
        raise IncrementalError(
            '%s vs %s: fotogramas no coinciden; en candidato faltan %s; en base faltan %s' % (
                cand['id'], base['id'], missing_in_cand, missing_in_base))

    out = {}
    for key in sorted(cand_keys):
        bres, cres, bpath, cpath = assert_pair_identity(base, cand, key)
        bimg = images.read_f32(bpath)
        cimg = images.read_f32(cpath)
        norm = frame_norm(cres, analysis)

        # Normalizado (ISO/exposición del manifiesto) y sin normalizar
        inc_norm, _ = M.fidelity(bimg, cimg, analysis, norm=norm, ceiling=None)
        inc_raw, _ = M.fidelity(bimg, cimg, analysis, norm=1.0, ceiling=None)

        # q efectivo del bitstream cuando hay caché
        try:
            b_bs = RD.bitstream_frame(base, bres)
            c_bs = RD.bitstream_frame(cand, cres)
            q_base, q_cand = b_bs['q_frame'], c_bs['q_frame']
        except RD.RdError as e:
            q_base = q_cand = None
            bitstream_note = str(e)
        else:
            bitstream_note = None
            if q_base is not None and q_cand is not None and q_base > q_cand:
                raise IncrementalError(
                    '%s/%s: q_eff base (%s) > candidato (%s); la referencia incremental debe ser q mínima' % (
                        cand['id'], key, q_base, q_cand))

        e_inc = cimg.astype(np.float64) - bimg.astype(np.float64)
        out[key] = {
            'incremental_normalized': inc_norm.get('normalized'),
            'incremental_raw': inc_raw.get('raw') or inc_raw.get('normalized'),
            'norm_factor': norm,
            'normalize_to_iso': analysis.get('normalize_to_iso'),
            'base_sha': bres.get('candidate_sha256'),
            'cand_sha': cres.get('candidate_sha256'),
            'absolute_cand_normalized': (cres.get('reference_fidelity') or {}).get('normalized'),
            'absolute_cand_raw': (cres.get('reference_fidelity') or {}).get('raw'),
            'absolute_base_normalized': (bres.get('reference_fidelity') or {}).get('normalized'),
            'absolute_base_raw': (bres.get('reference_fidelity') or {}).get('raw'),
            'q_requested_base': RD.config_from_result(bres).get('q_requested'),
            'q_requested_cand': RD.config_from_result(cres).get('q_requested'),
            'q_eff_base': q_base,
            'q_eff_cand': q_cand,
            'bitstream_note': bitstream_note,
            'decomposition_vs_qmin': {
                'mse_incremental_only': float(np.mean(e_inc * e_inc)),
                'note': 'Incremental respecto a BRAW(q_min efectivo), no CinemaDNG. No restar PSNR.',
            },
            'reference_kind': 'braw_min_effective_q',
        }
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', required=True, help='run BRAW(q_min efectivo)')
    ap.add_argument('--candidates-glob', required=True)
    ap.add_argument('--dest', required=True)
    ap.add_argument('--manifest', default=os.path.join(ROOT, 'benchmark', 'manifest.json'),
                    help='manifiesto con analysis (ISO/exposición)')
    args = ap.parse_args()
    analysis, man_path = load_analysis(args.manifest)
    os.makedirs(args.dest, exist_ok=True)
    cands = sorted(d for d in glob.glob(args.candidates_glob) if os.path.isdir(d))
    report = {'_meta': {
        'analysis_manifest': man_path,
        'normalize_to_iso': analysis.get('normalize_to_iso'),
        'base': os.path.abspath(args.base),
        'reference_kind': 'braw_min_effective_q',
    }}
    errors = []
    for c in cands:
        if os.path.abspath(c) == os.path.abspath(args.base):
            continue
        try:
            report[os.path.basename(c)] = analyze_pair(args.base, c, analysis)
        except IncrementalError as e:
            errors.append(str(e))
            print('ERROR', e, file=sys.stderr)
    with open(os.path.join(args.dest, 'incremental.json'), 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2, sort_keys=True)
    if errors:
        with open(os.path.join(args.dest, 'incremental-errors.json'), 'w', encoding='utf-8') as f:
            json.dump(errors, f, indent=2)
        raise SystemExit('%d errores de identidad incremental' % len(errors))
    print(args.dest, len(report) - 1, 'pairs')


if __name__ == '__main__':
    main()
