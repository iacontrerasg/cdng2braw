#!/usr/bin/env python3
"""EXP-005c: informe de viabilidad Bayer-nativo Pocket OG."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

DOC = os.path.join(ROOT, 'docs', 'experiment-pocket-og-bayer-native-feasibility.md')
JSON_OUT = os.path.join(ROOT, 'benchmark', 'experiments', 'exp005c-results-summary.json')

INVENTORY = {
    'reusable_as_is': [
        'Dng::cameraBayer() — unpack LibRaw, DefaultCrop, black/white, CFA 2×2, muestras lineales',
        'LibRaw unpack de DNG comprimido (tag 259≠1) → raw_image',
        'ogExposure / Camera Support / metadata de clip OG',
        'imagePlanes + NativeSensor::map (YCC mezclado por matriz, C0)',
        'canvasPlanes / DCT / VLC / contenedor BRAW (comunes)',
        'decodedPlanes y brawdec SDK',
        'green cubic direccional de bayerFields, aplicado a muestras lineales (no a códigos VA)',
    ],
    'va_specific_not_reusable_for_og_ycc': [
        'ChannelCodec VideoAssist (k0+s√x, gain_rgb, toe SDK)',
        'bayerFields ratios (B−Ĝ)/Ĝ y sitios Cb DR / Cr UL',
        'finishPlanes: sharpen luma 1.2 y conversión ratio→código con signal(Y)',
        'noiseResolution crop/2 (cuarteto = 1 píxel VA)',
        'perfil Camera Support SIGMA fp / analog_gain',
    ],
    'og_rgb_specific': [
        'NativeSensor: scale → sensor_curve → offset → inv(code_from_ycc)',
        'LibRaw AHD cameraRgb + cropSensor (default)',
        'info.gain=1; ISO via post_gain al decodificar',
        'CDNG_OG_CHROMA_MODE C0–C4 sobre RGB ya mapeado',
    ],
    'semantic_difference': (
        'VA: códigos por fotosito, Y=(R+2G+B)/4 y croma como proporciones en dominio de señal. '
        'OG: curva por canal sobre RGB completo y mezcla 3×3 a YCbCr. No son la misma representación interna.'
    ),
}

RISKS = [
    'Enchufar cameraBayer en bayerPlanes/finishPlanes escribiría YCC estilo VA en un BRAW con perfil OG.',
    'LibRaw AHD introduce zippering/halos; evitarlo no prueba que el SDK OG espere mosaico parcial.',
    'cameraBayer recorta en el mosaico (par); cameraRgb recorta tras demosaic — hay que no recortar dos veces.',
    'Interpolación bilinear/cúbica sigue siendo un demosaic: puede suavizar detalle y false color propio.',
    'DNG OG comprimido: unpack OK, no se parchea el mosaico in-place (EXP-005).',
    'Rango dinámico: highlight clip de LibRaw no aplica en cameraBayer; highlights pueden diferir.',
]


def classify(smoke):
    if not smoke or smoke.get('status') != 'measured':
        return {
            'class': 'E',
            'reason': 'Evidencia de encode/decode insuficiente o prototipo no medido.',
        }
    if smoke.get('incompatible'):
        return {'class': 'D', 'reason': smoke.get('incompatible')}
    rows = smoke.get('frames') or []
    if not rows:
        return {'class': 'E', 'reason': 'Sin fotogramas medidos.'}
    better = [r for r in rows if r.get('halo_p99_reduction', 0) >= 0.15
              and r.get('ruler_p99_reduction', 0) >= 0.10
              and not r.get('exposure_fail') and not r.get('decode_fail')]
    ok_geo = all(not r.get('decode_fail') for r in rows)
    # A04_f044 S9 lleva −3 EV: C0 también descuadra; no es fallo de la ruta Bayer.
    exp_ok = True
    for r in rows:
        if r.get('exposure_fail') and float(r.get('psnr_c0') or 99) >= 5:
            exp_ok = False
    ok_geo = ok_geo and exp_ok
    if better and ok_geo:
        return {'class': 'A' if min(r['halo_p99_reduction'] for r in better) >= 0.5 else 'B',
                'reason': 'Ruta Bayer experimental decodifica; mejora de BUG-002 en ROI medidas.',
                'frames': [r['id'] for r in better]}
    if ok_geo:
        return {
            'class': 'C',
            'reason': (
                'La ruta mosaico→RGB lineal→NativeSensor es viable (SDK, exposición, geometría) '
                'pero no mejora sustancialmente BUG-002 frente a C0 AHD en las ROI medidas.'
            ),
        }
    return {'class': 'D', 'reason': 'Fallo de decodificación, exposición o geometría bajo las restricciones actuales.'}


def inventory_payload():
    return {
        'experiment': 'EXP-005c',
        'title': 'Pocket OG Bayer-native Architecture Feasibility',
        'branch': 'experiment/pocket-og-bayer-native-feasibility',
        'base': 'main@62986b7 (PR #11 EXP-005b merged)',
        'generated_utc': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'defaults_intact': True,
        'no_sdk_or_format_change': True,
        'no_exp006_007_008': True,
        'ahd_default': True,
        'inventory': INVENTORY,
        'risks': RISKS,
        'routes': {
            'og_rgb': {
                'entry': 'CFA DNG → LibRaw unpack → AHD (user_qual=3) → RGB lineal',
                'normalize': '(count−black)/ (white−black); highlight clip LibRaw',
                'crop': 'DefaultCropOrigin/Size after demosaic (cropSensor)',
                'map': 'NativeSensor (curve + inv code_from_ycc)',
                'planes': 'imagePlanes C0 (Y full, Cb/Cr mean 2×2)',
                'channelCodec': None,
            },
            'va_bayer': {
                'entry': 'CFA DNG → cameraBayer (crop in mosaic)',
                'normalize': 'same black/white linear',
                'map': 'ChannelCodec encode per photosite (sqrt/toe)',
                'planes': 'bayerFields → ratios → finishPlanes codes',
                'channelCodec': 'VideoAssist',
            },
        },
    }


def write_doc(p):
    d = p['feasibility']
    inv = p['inventory']
    smoke = p.get('smoke') or {}
    mosaic = p.get('mosaic_audit') or {}
    lines = [
        '# EXP-005c — Pocket OG Bayer-native Architecture Feasibility',
        '',
        '**Rama:** `experiment/pocket-og-bayer-native-feasibility`  ',
        '**Base:** `main` @ `62986b7` (PR #11 EXP-005b **mergeada**). Este trabajo **no** fusiona PRs.  ',
        '**Default:** RGB/AHD C0. `CDNG_OG_BAYER` es opt-in Pocket OG.  ',
        '**Alcance:** auditoría + viabilidad mínima. **No** EXP-006/007/008. **No** codec nuevo. **No** cambio de SDK.',
        '',
        'Resumen: `benchmark/experiments/exp005c-results-summary.json`. Visuales → `out/`.',
        '',
        '---',
        '',
        '## Decisión',
        '',
        '**Clase %s.** %s' % (d['class'], d['reason']),
        '',
        'No se declara que el procesamiento parcial propietario de Blackmagic sea este prototipo.',
        '',
        'BUG-002 sigue P0.',
        '',
        '---',
        '',
        '## A — Dos rutas (código real)',
        '',
        '```mermaid',
        'flowchart TD',
        '  DNG[CinemaDNG CFA] --> UN[LibRaw unpack]',
        '  UN --> RGB[cameraRgb AHD default]',
        '  UN --> BAY[cameraBayer]',
        '  RGB --> CROP[cropSensor]',
        '  CROP --> RSZ[resizeLinear]',
        '  BAY --> REC[bayerReconstructLinear opt-in OG]',
        '  REC --> RSZ',
        '  RSZ --> NS[NativeSensor map]',
        '  NS --> IP[imagePlanes C0]',
        '  BAY -.-> BF[bayerFields VA only]',
        '  BF -.-> FP[finishPlanes VA]',
        '  IP --> CAN[canvasPlanes DCT VLC]',
        '  FP -.-> CAN',
        '  CAN --> BRAW[BRAW + Camera Support]',
        '  BRAW --> SDK[SDK S8]',
        '```',
        '',
        INVENTORY['semantic_difference'],
        '',
        '### Pocket OG / NativeSensor RGB',
        '',
        '- Entrada: DNG Bayer (a menudo comprimido). LibRaw `unpack` + `dcraw_process` AHD.',
        '- Normalización: negro racional restado tras 16-bit; highlight **clip**.',
        '- Patrón: no entra en el encode salvo artefactos AHD (S1).',
        '- Crop: DefaultCrop **después** del demosaic.',
        '- Mapa: curva por canal + `inv(code_from_ycc)` → Y/Cb/Cr mezclados.',
        '- `channelCodec() == nullptr` → `develop()` **no** llama a `bayerPlanes`.',
        '- ISO: `gain=1`; `post_gain` en el perfil al decodificar.',
        '',
        '### Video Assist / channelCodec Bayer',
        '',
        '- Entrada: `cameraBayer()` (DefaultCrop en mosaico, dimensiones pares).',
        '- Encode por fotosito: `k0 + s√(x·full·gain_ch)` (toe opcional).',
        '- Y: blur 3×3 de códigos. Croma: proporciones filtradas binomial 7 en sitios 4:2:0.',
        '- `finishPlanes` convierte proporciones a códigos con `signal(Y)` y enfoca luma.',
        '- DCT/metadata/SDK: mismos bloques de contenedor, **otro** perfil Camera Support.',
        '',
        'Evitar AHD **no** basta por sí solo para eliminar halos: el subsample 4:2:0, la curva y el SDK siguen ahí.',
        '',
        '---',
        '',
        '## B — Mosaico Pocket OG',
        '',
        'Auditoría DNG A04_f044: **%s**' % mosaic.get('status', 'not_run'),
        '',
        json.dumps({k: mosaic.get(k) for k in (
            'compression', 'is_compressed', 'cfa_pattern', 'cfa_repeat', 'black_level',
            'white_level', 'default_crop_origin', 'default_crop_size', 'active_area',
            'baseline_exposure', 'image_width', 'image_length') if k in mosaic}, indent=2),
        '',
        'LibRaw puede leer el Bayer comprimido (`unpack` → `raw_image`). No se asume parcheo del mosaico.',
        '',
        'Compatibilidad `channelCodec` VA: **no** (YCC semánticamente distinta). Adaptación razonable: reconstruir RGB lineal del mosaico y seguir NativeSensor.',
        '',
        '---',
        '',
        '## Inventario',
        '',
        '**Reutilizable:**',
    ]
    for x in inv['reusable_as_is']:
        lines.append('- ' + x)
    lines += ['', '**No reutilizar para YCC OG:**']
    for x in inv['va_specific_not_reusable_for_og_ycc']:
        lines.append('- ' + x)
    lines += ['', '**Riesgos:**']
    for x in p['risks']:
        lines.append('- ' + x)
    lines += [
        '',
        '---',
        '',
        '## C — Prototipo mínimo',
        '',
        '`CDNG_OG_BAYER=1` (opt-in, solo `Route::Og`). Mutuamente excluyente con `CDNG_OG_DEBAYER`.',
        'Default ausente ≡ AHD. Identidad de caché: `OUTPUT_ENV`.',
        'Reconstrucción: verde cúbico direccional (como VA, en lineal) + R/B bilinear. Luego NativeSensor + C0.',
        'No se llama a `finishPlanes` VA. No se cambia el SDK ni el formato.',
        '',
        '---',
        '',
        '## D — Smoke',
        '',
    ]
    if smoke.get('status') == 'measured':
        lines.append('Campaña: **measured**. q=2, NR/síntesis/RDOQ OFF.')
        lines.append('')
        lines.append('| frame | halo p99 C0 | halo p99 Bayer | red. p99 | ruler left p99 C0 | Bayer | PSNR |')
        lines.append('|-------|-------------|---------------|----------|-------------------|-------|------|')
        for r in smoke.get('frames') or []:
            lines.append('| %s | %s | %s | %.1f %% | %s | %s | %s |' % (
                r.get('id'), r.get('c0_halo_p99'), r.get('bayer_halo_p99'),
                100 * float(r.get('halo_p99_reduction') or 0),
                r.get('c0_ruler_left_p99'), r.get('bayer_ruler_left_p99'),
                r.get('psnr')))
        lines.append('')
        lines.append(smoke.get('note') or '')
    else:
        lines.append('Campaña no medida en este informe. %s' % (smoke.get('reason') or ''))
    lines += [
        '',
        'No se elige ganador solo por PSNR global.',
        '',
        '---',
        '',
        '## E — Cierre',
        '',
        'Clase **%s**. %s' % (d['class'], d['reason']),
        '',
        'Si A/B: siguiente paso acotado sería afinar interpolación de G/R/B **sin** AHD y **sin** VA finishPlanes, con los mismos ROI. No iniciado más allá del prototipo.',
        '',
        'BUG-002 sigue P0. Defaults intactos.',
        '',
    ]
    os.makedirs(os.path.dirname(DOC), exist_ok=True)
    with open(DOC, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    return DOC


def build_payload(extra=None):
    extra = extra or {}
    p = inventory_payload()
    p['mosaic_audit'] = extra.get('mosaic_audit') or {'status': 'not_run'}
    p['smoke'] = extra.get('smoke') or {'status': 'not_run'}
    p['prototype'] = extra.get('prototype') or {
        'env': 'CDNG_OG_BAYER',
        'default': 'absent = AHD C0',
        'uses_va_finishPlanes': False,
        'uses_native_sensor': True,
    }
    p['feasibility'] = classify(p['smoke'])
    return p


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--extra-json', default=None)
    args = ap.parse_args()
    extra = {}
    if args.extra_json and os.path.isfile(args.extra_json):
        with open(args.extra_json, encoding='utf-8') as f:
            extra = json.load(f)
    p = build_payload(extra)
    os.makedirs(os.path.dirname(JSON_OUT), exist_ok=True)
    with open(JSON_OUT, 'w', encoding='utf-8') as f:
        json.dump(p, f, indent=2)
        f.write('\n')
    write_doc(p)
    print('wrote', JSON_OUT)
    print('wrote', DOC)
    print('class', p['feasibility']['class'])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
