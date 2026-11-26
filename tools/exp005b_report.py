#!/usr/bin/env python3
"""EXP-005b: informe + JSON de viabilidad (offline). Artefactos pesados → out/."""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import numpy as np

from tools.exp005_color import apply_rgb
from tools.exp005b_oracle import (
    characterize_upsamplers, model_vs_nn_error, reconstruct_p3, run_oracle_suite, solve_o1,
)
from tools.exp005b_signal import (
    NativeSensor, SERIES_A_ASN, analyze_hypothesis_a, bilinear_chroma, camera_equal,
    camera_from_p3_grey, edge_pair, p3_chroma_field, subsample_c0,
)

DOC = os.path.join(ROOT, 'docs', 'experiment-pocket-og-decoder-aware-chroma.md')
JSON_OUT = os.path.join(ROOT, 'benchmark', 'experiments', 'exp005b-results-summary.json')


def classify(oracle, sdk):
    """A–E. No declara imposibilidad matemática global. Con S8, el umbral usa el oráculo SDK."""
    sdk = sdk or {}
    status = sdk.get('status') if isinstance(sdk, dict) else sdk
    red = oracle['reduction_vs_c0']
    if status != 'measured':
        return {
            'class': 'E',
            'reason': (
                'Evidencia insuficiente sobre el SDK (S8). Campaña encode/decode no medida.'
            ),
            'approx_o3_p99_reduction': red.get('o3_p99'),
        }
    o = sdk.get('oracle_s8') or {}
    p99 = float(o.get('o1_p99_reduction') or 0)
    integ = float(o.get('o1_integrated_reduction') or 0)
    if o.get('e2_p99_reduction'):
        p99 = max(p99, float(o['e2_p99_reduction']))
        integ = max(integ, float(o.get('e2_integrated_reduction') or 0))
    j = sdk.get('joint_y') or {}
    jp, ji = float(j.get('p99_reduction') or 0), float(j.get('integrated_reduction') or 0)
    reject = o.get('redistribution_rejected') or o.get('excessive_chroma_loss')
    if p99 >= 0.5 and integ >= 0.5 and not reject:
        return {'class': 'A',
                'reason': 'Reducción ≥50 % p99 e integral con Y fija, verificada S8.',
                'p99_reduction': p99, 'integrated_reduction': integ}
    if (jp >= 0.5 and ji >= 0.5) and not j.get('blur'):
        return {'class': 'B',
                'reason': 'Y conjunta acotada alcanza el umbral en S8 (no es desenfoque).',
                'p99_reduction': jp, 'integrated_reduction': ji}
    best_p, best_i = max(p99, jp), max(integ, ji)
    if 0.15 <= best_p < 0.5 and not reject:
        return {'class': 'C',
                'reason': 'Mejora S8 parcial por debajo del umbral exploratorio.',
                'p99_reduction': best_p, 'integrated_reduction': best_i}
    if reject and best_p > 0:
        return {'class': 'C',
                'reason': 'El p99 baja pero hay redistribución espacial o pérdida cromática; no es eliminación.',
                'p99_reduction': best_p, 'integrated_reduction': best_i}
    if best_p < 0.15:
        return {'class': 'D',
                'reason': 'Sin mejora útil en los candidatos S8 evaluados (no imposibilidad global).',
                'p99_reduction': best_p, 'integrated_reduction': best_i}
    return {'class': 'E',
            'reason': 'S8 medido pero el umbral p99+integral no es concluyente.',
            'p99_reduction': best_p, 'integrated_reduction': best_i}


def build_payload(sdk=None):
    sensor = NativeSensor()
    hypo = analyze_hypothesis_a(sensor)
    char = characterize_upsamplers()
    oracle = run_oracle_suite(sensor, size=32)
    nnerr = model_vs_nn_error()
    sdk = sdk or {'status': 'not_run', 'reason': 'campaña S8 no ejecutada en este informe'}
    decision = classify(oracle, sdk)
    return {
        'experiment': 'EXP-005b',
        'title': 'Decoder-aware Chroma Reconstruction: Feasibility',
        'branch': 'experiment/pocket-og-decoder-aware-chroma',
        'base': 'main@81cc49a (PR #10 merged)',
        'generated_utc': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'defaults_intact': True,
        'aahd_experimental_only': True,
        'ahd_default': True,
        'no_sdk_or_format_change': True,
        'no_exp006_007_008': True,
        'hypothesis_a': hypo,
        'decoder_characterization': char,
        'model_validation': {
            'approx_vs_s7_nn': nnerr,
            'held_out_oracle': {
                'orientation_p99_reduction': oracle['held_out_orientation']['p99_reduction'],
                'saturated_p99_reduction': oracle['held_out_saturated']['p99_reduction'],
            },
            'sdk_model_error': sdk.get('model_error'),
        },
        'oracle': oracle,
        'sdk_campaign': sdk,
        'robustness_f': {
            'status': sdk.get('wb_exposure_sweep', 'not_run') if sdk.get('status') == 'measured' else 'not_run',
            'limitation': (
                'No hay CinemaDNG equivalentes a ±2 EV / WB 3200/6500 / tint en el banco; '
                'un BRAW único decodificado con esos ajustes no tiene referencia DNG par. '
                'No se declara equivalencia. q18/WB/EV no se barrió sin candidato prometedor.'
            ),
        },
        'feasibility': decision,
        'next_if_ab': (
            'Si A/B: experimento de implementación eficiente (predicción local 4:2:0 '
            'condicionada a Y y a la curva, sin oráculo por ROI). No iniciado.'
        ),
        'remaining_if_cde': [
            'Revelado SDK no lineal / no separable no capturado por bilinear.',
            'DCT/QMax residual a q>2.',
            'Cambio de representación (no 4:2:0) — fuera de alcance; no implementado.',
            'Oráculo por iteración encode/decode SDK acotada.',
        ],
    }


def write_doc(p):
    h = p['hypothesis_a']
    o = p['oracle']
    d = p['feasibility']
    lines = [
        '# EXP-005b — Decoder-aware Chroma Reconstruction: Feasibility',
        '',
        '**Rama:** `experiment/pocket-og-decoder-aware-chroma`  ',
        '**Base:** `main` @ `81cc49a` (PR #10 EXP-005 **mergeada**). Este trabajo **no** fusiona PRs.  ',
        '**Encoder por defecto:** AHD, C0, NR/síntesis/RDOQ OFF en las corridas experimentales. Defaults de producción intactos.  ',
        '**Alcance:** viabilidad A–G. **No** algoritmo de producción. **No** EXP-006/007/008. **No** cambio de SDK/formato. **No** investigación extra de compresión.',
        '',
        'Resumen numérico: `benchmark/experiments/exp005b-results-summary.json`. Artefactos voluminosos → `out/`.',
        '',
        'Leyenda: **[código]** en el árbol; **[exp]** medido; **[lim]** limitación.',
        '',
        '---',
        '',
        '## Decisión',
        '',
        '**Clase %s.** %s' % (d['class'], d['reason']),
        '',
        'No se declara imposibilidad matemática global a partir de un oráculo local o un modelo aproximado.',
        '',
        '---',
        '',
        '## A — Neutro en espacio cámara',
        '',
        'Transformaciones: `NativeSensor` = curva del JSON OG + `inv(code_from_ycc)`; `cameraToP3` documentada (EXP-005). AsShotNeutral de la serie A: `%s`.'
        % h['asn'],
        '',
        '| Hecho | Valor |',
        '|-------|-------|',
        '| `camera R=G=B` → croma de código | max `|Cb,Cr|` = %.3g **[exp]** |' % h['camera_equal_code_chroma_max'],
        '| Gris P3 vía `inv(cameraToP3)` vs `∝ AsShotNeutral` (cromaticidad lineal) | max = %.3g |'
        % h['asn_matches_p3_inverse_chromaticity_max'],
        '| Croma de código a lo largo de un rampa P3-gris | rango = %.3g |' % h['p3_grey_code_chroma_range'],
        '| Borde `R=G=B` en P3 (no es neutro de salida) | chroma P3 max = %.4g |'
        % h['camera_equal_edge']['p3_chroma_max'],
        '',
        'Conclusión A (medida, no asumida a priori):',
        '',
        '- `R=G=B` en cámara es acromático en el códec (`Cb=Cr≈0`) y **no** es gris en P3.',
        '- `∝ AsShotNeutral` y `inv(cameraToP3)·gris P3` coinciden en cromaticidad **hasta ~1e-4** (cameraToP3(ASN) solo es gris P3 de forma aproximada, tolerancia Bradford/McCamy). No son idénticos.',
        '- Tras la **curva por canal**, una rampa de gris P3 **tiene Cb/Cr que dependen de la intensidad**. Esa croma espacialmente variable existe ya en el dominio de codificación, antes de 4:2:0/DCT/SDK.',
        '',
        'C0 (media RGB 2×2 y luego el mapa) no cancela esa croma de código. Separación: curva (origen principal en neutros de salida) vs subsampling/DCT/SDK (B, oráculo).',
        '',
        '---',
        '',
        '## B — Caracterización efectiva del decoder',
        '',
        '`CDNG_INJECT_PLANES` (BRWP) sustituye Y/Cb/Cr **después** de `finishPlanes` y **antes** de `canvasPlanes`/encode. Pocket OG. Geometría, VLC, DCT, QMax, ISO y metadata del DNG se conservan. Identidad de caché: `path#sha256` como `CDNG_INJECT_RGB`.',
        '',
        '| Operador | separable | lóbulos neg. | cross-channel | notas |',
        '|----------|-----------|--------------|---------------|-------|',
    ]
    ch = p['decoder_characterization']
    for name in ('s7_nn', 'bilinear_420'):
        c = ch[name]
        lines.append('| %s | %s | %s | %.3g | soporte %d px |' % (
            name, c['linear_separable'], c['has_negative_lobes'],
            c['cross_channel_max'], c['support_px']))
    lines += [
        '',
        'S7 (`decodedPlanes`) **no** interpola croma: copia 2×2. El bilinear es un candidato; **no** se asume que el SDK lo use.',
        '',
        '---',
        '',
        '## C — Oráculo (Y fija)',
        '',
        'ROI %d×%d, upsample = bilinear candidato. C0 = producción. O1 = LS; O2 = LS ponderado por borde/croma; O3 = clip legal de O1 + laplaciano débil (se descarta si suaviza de más).'
        % (o['roi'], o['roi']),
        '',
        '| | p99 halo | integral | luma corr | legal |',
        '|--|----------|----------|-----------|-------|',
        '| C0 | %.4g | %.4g | — | sí |' % (o['c0']['p99'], o['c0']['integrated']),
        '| O1 continuo | %.4g | %.4g | %.4f | %s |' % (
            o['o1_continuous']['p99'], o['o1_continuous']['integrated'],
            o['o1_continuous']['luma_corr'], o['o1_continuous']['chroma_legal']),
        '| O1 cuantizado | %.4g | %.4g | %.4f | %s |' % (
            o['o1_quantized']['p99'], o['o1_quantized']['integrated'],
            o['o1_quantized']['luma_corr'], o['o1_quantized']['chroma_legal']),
        '| O3 | %.4g | %.4g | %.4f | %s |' % (
            o['o3']['p99'], o['o3']['integrated'], o['o3']['luma_corr'], o['o3']['chroma_legal']),
        '',
        'Reducción O3 vs C0: p99 = %.1f %% ; integral = %.1f %% (modelo aproximado, **no** óptimo SDK).'
        % (100 * o['reduction_vs_c0']['o3_p99'], 100 * o['reduction_vs_c0']['o3_integrated']),
        '',
        'Held-out (otra orientación / colores saturados): p99 red. = %.1f %% / %.1f %%.'
        % (100 * o['held_out_orientation']['p99_reduction'],
           100 * o['held_out_saturated']['p99_reduction']),
        '',
        '---',
        '',
        '## D — Y conjunta',
        '',
        o['joint_y'].get('note') or o['joint_y'].get('reason') or json.dumps(o['joint_y']),
        '',
        '---',
        '',
        '## E — Validación',
        '',
        'q2 C0 AHD, NR/síntesis/RDOQ OFF. Fixture P3 + A04_f044 + ROI `white_strip` (BUG-002).',
        '',
        'Campaña SDK: **%s**. %s' % (
            p['sdk_campaign'].get('status'),
            p['sdk_campaign'].get('reason') or p['sdk_campaign'].get('note') or ''),
        '',
        'Resolve puede usarse como oráculo offline; **no** es dependencia de un algoritmo futuro. Cualquier candidato de producción debe derivarse de S2 + metadata CinemaDNG.',
        '',
        'Umbral exploratorio 50 % p99+integral: **no** cierra BUG-002 ni sustituye aceptación visual.',
        '',
        '---',
        '',
        '## F — Robustez del revelado',
        '',
        p['robustness_f']['limitation'],
        '',
        '---',
        '',
        '## S8 (B2/A2/C2) — encode/decode real',
        '',
    ]
    s = p.get('sdk_campaign') or {}
    if s.get('status') == 'measured':
        chs = s.get('characterization') or {}
        uh = s.get('a2_user_hypothesis') or {}
        os8 = s.get('oracle_s8') or {}
        lines += [
            'Impulsos empaquetados en un frame 1920×1080, restando control plano. q=2, clip A04 f44–45. **[exp]**',
            '',
            '| S8 | valor |',
            '|----|-------|',
            '| sitios croma | %s |' % chs.get('n_chroma_sites'),
            '| soporte medio (px) | %.4g |' % float(chs.get('mean_support_px') or 0),
            '| lóbulos negativos | %s |' % chs.get('negative_lobes'),
            '| acoplamiento Y→croma | %.4g |' % float(chs.get('y_to_chroma_coupling') or 0),
            '',
            'No se asume bilinear. S7 sigue siendo NN 2×2.',
            '',
            '### A2 — bordes en S8',
            '',
            'Hipótesis del usuario (halo exclusivo blanco/negro): **%s**. Más fuerte: `%s`. cam_equal_hi p99=%s, p3_hi=%s, asn_hi=%s, isoluminante=%s. corr(contraste, p99)=%.3g; corr(dist ASN, p99)=%.3g.'
            % (uh.get('confirmed_exclusive_wb'), uh.get('strongest'),
               uh.get('cam_equal_hi_p99'), uh.get('p3_hi_p99'), uh.get('asn_hi_p99'),
               uh.get('isoluminant_p99'), float(uh.get('corr_contrast_p99') or 0),
               float(uh.get('corr_asn_dist_p99') or 0)),
            '',
            '### C2 — oráculo S8 (borde P3, Y fija, O1 bilinear en ROI)',
            '',
            'p99 red. = %.1f %%; integral = %.1f %%. Redistribución rechazada: %s. Pérdida cromática excesiva: %s.'
            % (100 * float(os8.get('o1_p99_reduction') or 0),
               100 * float(os8.get('o1_integrated_reduction') or 0),
               os8.get('redistribution_rejected'), os8.get('excessive_chroma_loss')),
            '',
        ]
        if s.get('e2'):
            lines += ['E2 (máscara borde neutro de salida, sin C=0 global): p99 red. %.1f %%.'
                      % (100 * float(s['e2'].get('p99_reduction') or 0)), '']
        if s.get('joint_y'):
            j = s['joint_y']
            lines += ['D2 ΔY rms=%.4g max=%.4g, fracción tocada=%.3g, p99 red. %.1f %%.'
                      % (float(j.get('delta_y_rms') or 0), float(j.get('delta_y_max') or 0),
                         float(j.get('frac_touched') or 0), 100 * float(j.get('p99_reduction') or 0)), '']
        if s.get('a04'):
            lines += ['A04_f044 ROI white_strip p99 croma S8 C0 = %s. %s'
                      % (s['a04'].get('white_strip_p99_chroma'), s['a04'].get('limitation')), '']
    else:
        lines += ['Campaña S8 no medida en este informe.', '']
    lines += [
        '---',
        '',
        '## G — Viabilidad',
        '',
        'Clase **%s**. ' % d['class'] + d['reason'],
        '',
        p['next_if_ab'] if d['class'] in ('A', 'B') else 'Opciones restantes (no implementadas): ' + '; '.join(p['remaining_if_cde']),
        '',
        'BUG-002 sigue P0.',
        '',
    ]
    os.makedirs(os.path.dirname(DOC), exist_ok=True)
    with open(DOC, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    return DOC


def _ppm(path, img01):
    x = np.clip(np.asarray(img01) * 255.0, 0, 255).astype(np.uint8)
    h, w = x.shape[:2]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as f:
        f.write(('P6\n%d %d\n255\n' % (w, h)).encode('ascii'))
        f.write(np.ascontiguousarray(x).tobytes())


def write_visuals(out_dir):
    """Crops 1:1 y NN×4 del oráculo (out/, no se versionan)."""
    sensor = NativeSensor()
    asn = SERIES_A_ASN
    size = 64
    rgb = edge_pair(size, size, camera_from_p3_grey(0.05, sensor, asn),
                    camera_from_p3_grey(0.42, sensor, asn), axis=1)
    ycc = sensor.map_rgb(rgb)
    y, cb0, cr0 = subsample_c0(rgb, sensor)
    cb1, cr1 = solve_o1(ycc[..., 1], ycc[..., 2], bilinear_chroma)
    p3_ref = apply_rgb(rgb, sensor.camera_to_p3(asn))
    cbu0, cru0 = bilinear_chroma(cb0, cr0)
    cbu1, cru1 = bilinear_chroma(cb1, cr1)
    p3_c0 = reconstruct_p3(y, cbu0, cru0, sensor, asn)
    p3_o1 = reconstruct_p3(y, cbu1, cru1, sensor, asn)
    vis = os.path.join(out_dir, 'experiments', 'exp005b', 'visuals')
    gain = 8.0
    for name, img in (
        ('p3_ref', np.clip(p3_ref, 0, 1)),
        ('p3_c0', np.clip(p3_c0, 0, 1)),
        ('p3_o1', np.clip(p3_o1, 0, 1)),
        ('halo_c0', np.clip(np.stack([p3_chroma_field(p3_c0 - p3_ref)] * 3, -1) * gain, 0, 1)),
        ('halo_o1', np.clip(np.stack([p3_chroma_field(p3_o1 - p3_ref)] * 3, -1) * gain, 0, 1)),
        ('cam_equal_edge', np.clip(apply_rgb(
            edge_pair(size, size, camera_equal(0.05), camera_equal(0.42)),
            sensor.camera_to_p3(asn)), 0, 1)),
    ):
        _ppm(os.path.join(vis, name + '.ppm'), img)
        nn4 = np.repeat(np.repeat(img, 4, 0), 4, 1)
        _ppm(os.path.join(vis, name + '_nnx4.ppm'), nn4)
    return vis


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sdk-json', default=None)
    args = ap.parse_args()
    sdk = None
    if args.sdk_json and os.path.isfile(args.sdk_json):
        with open(args.sdk_json, encoding='utf-8') as f:
            sdk = json.load(f)
    payload = build_payload(sdk)
    os.makedirs(os.path.dirname(JSON_OUT), exist_ok=True)
    with open(JSON_OUT, 'w', encoding='utf-8') as f:
        json.dump(payload, f, indent=2)
        f.write('\n')
    write_doc(payload)
    vis = write_visuals(os.path.join(ROOT, 'out', 'benchmark'))
    print('wrote', JSON_OUT)
    print('wrote', DOC)
    print('wrote', vis)
    print('class', payload['feasibility']['class'])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
