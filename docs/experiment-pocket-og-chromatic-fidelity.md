# EXP-004 — Chromatic Fidelity & Halo Root Cause Analysis

**Rama:** `experiment/pocket-og-chromatic-fidelity`  
**Base:** `048af3ad6ea147e2e64adbb6bd95620c68ae4127` (main tras merge PR #8 / EXP-003b)  
**Encoder por defecto:** sin cambios. RDOQ experimental y **OFF**. NR y synthesis **OFF** en esta campaña.  
**Alcance:** A–D2 (auditoría) + D3–D4 (integridad/aislamiento) + E (candidatos experimentales C0–C4) + F (comparación preliminar). **No** EXP-005. **No** EXP-007/008. Defaults de producción **sin cambios**.

Leyenda de afirmaciones: **[código]** verificado en el árbol; **[exp]** medido en esta campaña; **[hip]** hipótesis.

---

## A — Auditoría del pipeline Pocket OG

Bifurcación **[código]** `native/engine/worker/common.cpp` `develop()`: si `channelCodec()==nullptr` (Pocket OG / NativeSensor) el camino es RGB LibRaw, **no** el Bayer parcial de Video Assist. `CDNG_CHROMA_FILTER` y `CDNG_RESIZE_CHROMA` **no se ejecutan** en OG.

Cadena:

```
CinemaDNG Bayer u16
  → LibRaw AHD (cameraRgb)          [S1]
  → crop DefaultCrop                (gain=1, skip)
  → Lanczos-3 por canal             [S2]
  → curva sensor + YCbCr            [S3/S4]
  → croma: media 2×2 RGB lineal + mapa  [S5]  4:2:0 centrado
  → NR opcional (OFF aquí)
  → place + réplica; deblock Y; rint    [S6]
  → DCT 8×8 Y / 4×8 C; quant; VLC
  → reconstruct local IDCT              [S7]  ≠ SDK
  → BRAW SDK RGB float                  [S8]
  → referencia Resolve EXR              [S9]
```

### Etapas (hechos)

| Etapa | Dónde | Dimensiones | Tipo / rango | Espacio | Dominio | Notas |
|-------|-------|-------------|--------------|---------|---------|-------|
| Bayer | `dng.cpp` `cameraBayer` ~495 | crop par w×h | float32, (raw−black)/(white−black), **sin clamp** | CFA | lineal | OG **no** usa este buffer para encode; dump S0 es diagnóstico de fuente |
| Debayer | `dng.cpp` `cameraRgb` ~541 | tamaño LibRaw pre-crop | float32 RGB entrelazado | RGB cámara | lineal, γ=1 | AHD `user_qual=3`; WB off; `output_color=0`; highlight **clip**; negro restado después |
| Gain | `signal_source.cpp` ~43 | — | — | — | — | `shot.gain = 1` siempre; ISO via `post_gain` al decodificar |
| Crop | `dng.cpp` `cropSensor` ~763 | DefaultCrop | float32 | RGB cámara | lineal | `trunc` hacia 0; sin padding |
| Resize | `resize.cpp` `resizeLinear` ~124 | W×H pares | float32 | RGB cámara | lineal | recorte centrado `nearbyint`; Lanczos-3 por canal; borde recorta kernel y renormaliza (**no** réplica) |
| Curva | `signal.cpp` `NativeSensor::mapDouble` ~157 | por píxel | códigos ~0…4095 | códigos cámara | **no lineal** | `sensor_curve` gana sobre fórmula sqrt; `encode(0)≈320`; sin clamp en curva |
| YCbCr | misma `mapDouble` × `inverse` de `code_from_ycc` | Y w×h; C teórico full | float | YCbCr **BRAW**, no BT.601/709 | no lineal | **[código]** Y=(R+2G+B)/4, Cb=(B−G)/2, Cr=(R−G)/2 sobre códigos |
| Subsample | `imagePlanes` ~581 | Cb/Cr (w/2)×(h/2) | float | YCbCr | no lineal tras mapa | **media 2×2 del RGB lineal**, luego `mapDouble`. **No** se promedian Cb/Cr full-res |
| Siting | cuarteto (2x,2y)…(2x+1,2y+1) | — | — | — | — | muestra en el **centro** del 2×2 **[código]**. No asumir JPEG/MPEG/ProRes del decoder |
| Canvas | `geometry.cpp`; `canvasPlanes` ~915 | Y 1984×1104 @1920×1080; C mitad | int16 Y 0…4095; C −4096…4095 | YCbCr | códigos | réplica de borde; `nearbyint` even |
| Deblock | `precompensate` ~675 | solo Y | — | — | — | taps (3,10,3)/16; **croma no se toca** **[código]** |
| DCT | `codec.cpp` encode / `codec_tables.cpp` | Y 8×8; C 4×8 | double | — | — | DCT-II ortonormal; DC Y offset 2048 |
| Quant/VLC | `quantizeAc`, `entropy.cpp` | — | niveles ±4095 | — | — | QMax 24 intacto; RDOQ OFF |
| S7 | `Codec::reconstruct` ~601 | canvas | int16 | YCbCr | códigos | IDCT local; **no** upsample SDK |
| S8 | `brawdec` + SDK | 1920×1080 RGB | float32 | P3-D65 Linear (banco) | lineal escena tras CSC | upsample croma **opaco** **[hip]** |
| S9 | EXR Resolve | 1920×1080 RGB | float16/32 | P3-D65 Linear | lineal | referencia CinemaDNG, no BRAW |

### Transferencia × matriz **[código]**

`model/pocket_og_cinemadng_signal.json`: `code_from_ycc` =

```
R = Y − 0.5 Cb + 1.5 Cr
G = Y − 0.5 Cb − 0.5 Cr
B = Y + 1.5 Cb − 0.5 Cr
```

La inversa es la usada al codificar. Interacción no lineal: la caja 2×2 se aplica **antes** de la curva, así que un borde RGB lineal produce Cb/Cr distintos de promediar YCbCr full-res (H2/H3).

### Clipping **[código]**

- LibRaw highlight clip en debayer (RGB).
- Canvas: Y 0…4095, C −4096…4095 tras `nearbyint`.
- Clipping RGB de escena ≠ clipping Cb/Cr (un canal RGB saturado desplaza Cr/Cb).

### CFA × resize **[hip]**

Lanczos independiente R/G/B sobre RGB ya debayerizado. El patrón CFA no entra en OG encode salvo vía artefactos AHD en S1.

---

## B — Instrumentación

| Variable | Clase | Efecto |
|----------|-------|--------|
| `CDNG_DUMP_STAGES` | diagnóstico | directorio `frame_N/*.bin`+sidecar JSON |
| `CDNG_DUMP_FRAME` | diagnóstico | índice (defecto 0) |
| `CDNG_INJECT_RGB` | **OUTPUT** (identidad de caché) | BRWD RGB sustituye `cameraRgb` o el resultado de resize |
| `CDNG_INJECT_AT` | OUTPUT | `camera` \| `pre_map` |

Volcado opt-in: copias de buffers existentes. S3/S4 extra no escriben `Planes`. S7 llama `reconstruct` **después** de encode, sin sustituir el payload.

Prueba nativa: `dumpDoesNotChangePlanes` en `engine_signal_test`. Identidad de `sample_bytes` dump ON/OFF: corrida `exp004-ident-g0` vs `exp004-ident-g0-nodump`.

S8/S9: `brawdec --out` y EXR del banco; no se comparan con S5/S7 sin transformar dominio.

---

## C — Caracterización

### C1 Material real

| Frame | Clip | ISO | ROI (coords `benchmark/rois/chart_wall.json`) |
|-------|------|-----|-----------------------------------------------|
| A04_f044 | C0039 | 1600 | `edge_white_strip_left` [664,550,704,550] w=9; `edge_grey_strip_right`; `edge_patch_top`; `edge_wire_h`; ColorChecker; `text_*`; `metal_specular`; `cloth_black_mesh` |
| A04_f049 | C0039 | 1600 | mismas ROI (cámara fija) |
| A01_f052 | C0036 | 200 | mismas ROI — dependencia ISO/exposición |

u'v' se omite donde Y < `chroma_min_luminance` 0.005 (banco).

### C2 Sintéticos

`tools/exp004_fixtures.py`: edges neutros/gris/R-G/B-Y, diagonal, impulsos, líneas 1/2/4/8, checker, chirps; fases 0–8 px (CFA / 4:2:0 / DCT).  
Ruta RGB: `CDNG_INJECT_RGB` @ `pre_map` → **mismo** `imagePlanes`/`canvasPlanes`/`encode`.  
Ruta Bayer: `patch_dng_mosaic` sobre DNG plantilla (16-bit sin comprimir) + LibRaw AHD; si el template está comprimido, se registra error (no se inventa un debayer).

### C3 Métricas

`tools/exp004_halo.py`: error Cb/Cr firmado, peak/integrated chroma, extensión de halo, overshoot, centroid Y vs C, ESF/LSF, MTF 1D, espectro residual, Δu'v' con umbral de luma. Gates globales **intactos**.

---

## D — Hipótesis

| Id | Hipótesis | Estado inicial | Base |
|----|-----------|----------------|------|
| H1 | Halo ya en AHD | inconcluso / plausible | S1 vs S9; AHD zipper conocido |
| H2 | No lineal / RGB→YCbCr | plausible **[código]** | curva luego matriz; caja en lineal |
| H3 | Aliasing downsampling | plausible **[código]** | solo box 2×2; sin filtro VA |
| H4 | Siting encoder vs SDK | inconcluso | centro 2×2 encoder; SDK opaco |
| H5 | Ringing upsample SDK | inconcluso | S8 vs S7 |
| H6 | Precomp deblock Y/C | **confirmada [exp]** | S6_pre Cb ≡ S6_post Cb; Y cambia |
| H7 | DCT/quant ringing | **parcial [exp]** | MAE Cb S7−S6: 0.90 @q2 vs 2.69 @q24; el halo **no desaparece** a q fino |
| H8 | Clip/resize | plausible | Lanczos por canal; highlight clip LibRaw |
| H9 | Combinación | plausible | default de trabajo |

Controles: G0 (α1 DZ0 q24), G3 (q2), G2 (`plane-y40-c50`). q0 no se trata como lossless.

Mediciones destacadas (A04_f044 salvo sintético):

- Δu′v′ bordes G0: media 0.0152 (banco `luma_chroma.edges`).
- Perfil `edge_white_strip_left`: Δu′v′ media 0.0052, máx 0.037.
- RGB sintético **neutro** @ `pre_map`: S4/S5 Cb=Cr=**0** → la caja 2×2 + curva **no inventan croma** si R=G=B. Los halos en material real no se explican solo con H2/H3 sobre gris perfecto.
- Identidad dump ON/OFF: el BRAW de `exp004-ident-g0-nodump` reutilizó la caché de dump ON (misma identidad de encode).

### D2 Compresibilidad

`CDNG_BIT_BUDGET` / `CDNG_COEFF_STATS` en las mismas corridas. Relacionar energía AC croma y halos es **correlación**, no causalidad. No se toca el cuantizador.

---

## D3 — Integridad experimental

- Dump ON/OFF: `tools/exp004_sweep.py --phase identity` fuerza `--no-cache` (conversiones independientes).
- Identidad de `CDNG_INJECT_RGB`: `path#sha256` en `experimental_env` de caché; el worker recibe solo la ruta (como `CDNG_DEADZONE_POLICY`).
- Dumps: se **rechaza sobrescribir** `.bin` existentes; `stages_index.json` lista etapas escritas, ausentes del encoder (p. ej. S0 si el DNG no es Bayer) y S8/S9 como externas.
- `CDNG_STATS_LABEL` = `run_id` asocia dump ↔ corrida. El dump **no** entra en la identidad de encode.

## D4 — Aislamiento

### Debayer (H1)

S1/S2 son RGB **cámara lineal**; S9 es P3-D65 Linear. No se comparan en crudo. Error cromático **antes** de `imagePlanes`: cromaticidad (R−G, B−G) en S2. No se atribuye el defecto a AHD sin S1 vs verdad de mosaico.

### Siting / SDK (H4/H5)

Fixtures `impulse_even/odd/dct`, `edge_rg_{h,v}`, fases. Centroides en S8 (RGB SDK). C3 solo si `c3_warranted` en `d4.json`. No se asume bilinear/bicubic.

### No linealidad (H2)

`mean(map)` = caja 2×2 de S4; `map(mean)` = S5 C0. MAE/peak en dumps.

### Deblock (H6)

Cb/Cr de canvas **idénticos** pre/post. La cromaticidad **reconstruida** vía `code_from_ycc` puede moverse porque Y cambia (contribución indirecta).

## E — Candidatos (Pocket OG, flags OUTPUT_ENV, default OFF)

| Id | Flag | Qué hace | Y | Dimensiones |
|----|------|----------|---|-------------|
| C0 | ausente o `MODE=c0` | `map(mean RGB 2×2)` histórico | idéntica | 4:2:0 |
| C1 | `MODE=c1` | curva+matriz por píxel; media 2×2 de Cb/Cr | idéntica | 4:2:0 |
| C2 | `MODE=c2` + `KERNEL=b3\|b5\|b7` | AA separable binomial (ganancia DC 1) en croma **codificada**, luego 2×2 | idéntica | 4:2:0 |
| C3 | `MODE=c3` + `PHASE` | muestreo even/odd/center | idéntica | 4:2:0 |
| C4 | `MODE=c4` + kernel + phase | combina AA y fase | idéntica | 4:2:0 |

Kernels (espejo, ganancia DC 1): b3=(1,2,1)/4 → \(H(\omega)=\cos^2(\omega/2)\); b5=(1,4,6,4,1)/16 → \(\cos^4(\omega/2)\); b7=(1,6,15,20,15,6,1)/64 → \(\cos^6(\omega/2)\).

No se toca curva del sensor, matriz YCbCr, VLC, DCT, QMax, ISO, debayer de producción ni post-decode. Sin blur indiscriminado.

## F — Comparación preliminar

A04_f044 / A04_f049 / A01_f052; q2, q18, q24; NR/synth/RDOQ OFF; α=1, DZ=0. Métricas en `exp004-results-summary.json` (`candidate_comparison`). Una bajada de halo que aplaste MTF de croma **no** cuenta como mejora.

---

## Cómo reproducir

```
cmake --build build/native --target cdng-convert engine_signal_test
cp build/native/bin/cdng-convert build/worker/converter/cdng-convert
PYTHON=out/.venv-exr/bin/python python tools/exp004_fixtures.py --out out/benchmark/experiments/exp004/fixtures
PYTHON=out/.venv-exr/bin/python python tools/exp004_sweep.py --phase identity
PYTHON=out/.venv-exr/bin/python python tools/exp004_sweep.py --phase d4 --skip-existing
PYTHON=out/.venv-exr/bin/python python tools/exp004_sweep.py --phase candidates --skip-existing
PYTHON=out/.venv-exr/bin/python python tools/exp004_d4.py
PYTHON=out/.venv-exr/bin/python python tools/exp004_report.py
```

Resumen: `benchmark/experiments/exp004-results-summary.json`.  
Dumps/PNG: `out/benchmark/experiments/exp004/` (no Git).
