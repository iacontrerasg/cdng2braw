# EXP-005 — Debayer Fidelity & BUG-002 isolation

**Rama:** `experiment/pocket-og-debayer-fidelity`  
**Base:** `main` tras merge PR #9 (EXP-004). Este trabajo **no** fusiona PRs.  
**Encoder por defecto:** AHD, sin cambios. `CDNG_OG_DEBAYER` ausente ≡ `user_qual=3`.  
**Alcance:** A–D (preliminar) + E (método) + F (campaña final AHD/VNG/AAHD, serie A, q2/q18) + G (cierre). **No** EXP-006/007/008. **No** FEAT-001. **No** debayer propio. **No** AMaZE/RCD/LMMSE. **No** compensaciones cromáticas nuevas. **No** ampliar investigación de compresión.

Leyenda: **[código]** en el árbol; **[exp]** medido; **[lim]** limitación declarada.

Resumen numérico: `benchmark/experiments/exp005-results-summary.json`.

---

## A — Aislamiento del debayer

### A1 — Camera RGB (S2) vs Resolve (S9)

Transformación **verificable**, no un ajuste que minimice el error:

```
camToXyz = inv(ColorMatrix interpolada en 1/T @ AsShotNeutral)
adapt    = Bradford(white_xyz → D65)
P3       = XyzToP3 · adapt · camToXyz · RGB_cámara
```

**[código]** `cameraToP3` en `native/engine/dng.cpp` (misma Bradford que `cameraTo709`, primaries P3-D65 de `tools/fidelity/metrics.py`). Python: `tools/exp005_color.py`.

| Factor | Tratamiento |
|--------|-------------|
| ColorMatrix1/2 + illuminants | Interpolación McCamy como el encoder |
| AsShotNeutral | Blanco de cámara; **no** se aplica WB de LibRaw (`use_camera_wb=0`) |
| Bradford → D65 | Igual que preview 709 |
| BaselineExposure | Escala documentada `2^BE` **solo** para comparar luminancia; la cromaticidad es invariante de escala |
| Gain ISO | Encoder OG `gain=1`; no se fabrica un ISO fit |
| Black/white | Ya en S2: `(raw−black)/(white−black)` |
| Crop / Lanczos | S2 ya es 1920×1080 post-resize |
| Clipping | LibRaw highlight clip en S1; se cuenta fracción &lt;0 y &gt;1 |
| Revelado Resolve | S9 es EXR CinemaDNG Gen4 P3-D65 Linear (A04_f044 además `decode.exposure=-3` en el manifiesto) — **no** se absorbe en la matriz |

Prueba de la matriz (colores conocidos): el neutro de cámara cae en RGB P3 acromático; roundtrip de rampas y primarias &lt; 1e-9 **[código]** `tests/test_exp005.py`.

Si Δu′v′ entre `cameraToP3(S2)×2^BE` y S9 es grande, eso mide **diferencia de revelado**, no un halo a minimizar con un fit.

### A2 — Inyección de referencia (`CDNG_INJECT_RGB@pre_map`)

1. Validar inversa numérica (S2 → P3 → S2).
2. Aplicar `inv(cameraToP3)` a S9 `/ 2^BE`.
3. Si el revelado Resolve no casa (Δu′v′ media &gt; 0.02), la inyección es **ensayo**, no evidencia concluyente **[lim]**.
4. Comparar a q2 C0: AHD encode vs inject vs EXR vs fixtures sintéticos con verdad conocida.

Criterio de aislamiento: si el halo **desaparece** en el inject fiable, AHD es causa primaria; si **persiste**, el codec/4:2:0/SDK bastan para el defecto. Un inject no fiable no absuelve ni condena AHD.

---

## B — Auditoría de demosaicing (LibRaw 0.22.3 Homebrew `libraw_r`)

Símbolos en `libraw_r.dylib` **[código]**:

| Algoritmo | `user_qual` | En esta build | Notas |
|-----------|-------------|---------------|--------|
| linear | 0 | sí `lin_interpolate` | control |
| VNG | 1 | sí | |
| PPG | 2 | sí | |
| **AHD** | **3** | **sí** | default |
| DCB | 4 | **no** (`dcb_interpolate` ausente) | `LIBRAW_WARN_FALLBACK_TO_AHD` → **rechazo** |
| DHT | 11 | sí | |
| AAHD | 12 | sí | Petrusevich |
| AMaZE | — | no | GPL RawTherapee; candidato posterior, **no** incorporado |
| RCD | — | no | ídem |
| LMMSE | — | no | ídem |

`--q` de dcraw / `params.user_qual` (API-datastruct.html). Qual inválido → AHD + warning; el encoder **aborta** en lugar de sustituir en silencio.

---

## C — Selector experimental (solo Pocket OG)

| Requisito | Cómo |
|-----------|------|
| AHD default | `CDNG_OG_DEBAYER` ausente → `user_qual=3`, resto de `cameraRgb` intacto |
| Default bit-idéntico | identidad `exp005-ident-default` vs `exp005-ident-ahd` |
| Config registrada | evento `started`: `og_debayer`, `og_debayer_from_env` |
| Caché | `CDNG_OG_DEBAYER` en `OUTPUT_ENV` |
| No soportado | parseo C++/Python; DCB fallback → Error |
| Sin tocar WB/exposición/clip/matrices/normalización | solo `user_qual` |
| Otras cámaras | `ogDebayerRejectIfNotOg` si el flag está puesto |
| Verificación de ejecución | SHA de S1 distinto entre algoritmos; fallback AHD prohibido |

---

## D — Campaña preliminar

Fotogramas: A04_f044, A04_f049, A01_f052 + fixtures RGB con GT (inject@pre_map).  
Fijo: **C0, q2, α=1, DZ=0, NR/synthesis/RDOQ OFF**.  
Algoritmos: AHD, VNG, PPG, DHT, AAHD, linear (control). DCB no evaluable en esta build.

Dos niveles: RGB pre-encode (S1/S2) y RGB SDK (S8 vs S9).  
Métricas: PSNR, correlación de detalle, error cromático ROI, pico/integral/extensión de halo, MTF de croma, false color, zippering, aliasing, microtextura, clipping, tiempo. Ejes PCA u′v′ de la referencia. Visualización `viz.display(2.0)` común.

**No** se elige ganador por PSNR.

---

## Resultados **[exp]**

### Identidad (C)

`exp005-ident-default` vs `exp005-ident-ahd` (A04_f044, q2): **S1 idéntico** y **SDK f32 idéntico** (`7c512470…`). AHD ausente ≡ `CDNG_OG_DEBAYER=ahd`.

### A1 — S2 vs S9 (A04_f044)

| Cantidad | Valor |
|----------|--------|
| Roundtrip `cameraToP3` | max abs ~2e-16 |
| Escala documentada | `2^BE × 2^(EXR decode)` = `2^3.1 × 2^(−3)` ≈ 1.072 |
| Ratio mediana Y (diagnóstico, no fit) | **0.999** |
| Residuo RGB (tras escala documentada) | MAE 0.0020, RMSE 0.0047 |
| Δu′v′ media (ROI white_strip, 9600 px) | **0.0072** (máx 0.21: colas, no un fit) |

La matriz **no** se ajustó. A04_f044 exige el `decode.exposure=-3` del manifiesto; sin él el residuo de luma explota (~×8) y se confundiría con un “error de color”.

### A2 — Inyección S9→cámara @ pre_map, q2 C0

Inversa numérica de S9 (con BE y exposición EXR): roundtrip S9 RMSE ~6e-9. Rango inyectado ≈ [0, 1].

| | PSNR vs S9 | Pico halo (Cb/Cr vs ref) |
|--|------------|---------------------------|
| AHD encode | 42.70 dB | 0.455 |
| Inject Resolve-derived | **48.26 dB** (cercanía a S9 en parte tautológica) | **0.347** |

El pico de halo **no cae a la mitad**. La inyección **no absuelve** a AHD como única causa: el camino codec/4:2:0/SDK basta para el defecto. PSNR↑ del inject **no** es evidencia de halo=0.

**[lim]** DNGs Pocket OG comprimidos: no se parcheó mosaico Bayer con GT; los sintéticos entran por inject RGB.

### D — Algoritmos LibRaw (q2 C0, α=1, DZ=0)

S1: **6 SHA distintos** por fotograma (linear/VNG/PPG/AHD/DHT/AAHD). Los algoritmos **sí se ejecutan**.

A04_f044 (chart, EXR −3):

| Algo | PSNR | detail corr | pico halo | integral halo |
|------|------|-------------|-----------|----------------|
| AHD | 42.70 | 0.870 | 0.455 | 1.29e4 |
| VNG | 43.96 | 0.881 | 0.361 | 1.08e4 |
| PPG | 42.36 | 0.885 | 0.564 | 1.25e4 |
| DHT | 43.71 | 0.899 | 0.579 | 1.09e4 |
| AAHD | 43.58 | 0.892 | 0.402 | 1.15e4 |
| linear | 40.51 | 0.682 | 0.528 | 1.24e4 |

A04_f049 (sin −3 en EXR): picos de halo **~10×** mayores (AHD 4.70, VNG 2.87). Nadie elimina el anillo.

No se elige ganador por PSNR. VNG reduce pico/integral frente a AHD **y** no arregla BUG-002.

DCB no evaluable (símbolo ausente). AMaZE/RCD/LMMSE no enlazados; no incorporados.

---

## E — Correcciones metodológicas **[código][exp]**

### Roundtrip algebraico ≠ revelado Resolve

| | Qué mide | A04_f044 |
|--|----------|----------|
| `algebraic_roundtrip` | `inv(cameraToP3)·cameraToP3` sobre S2 | max abs ~2e-16, **fiable** |
| `develop_equivalence` | S9 Resolve vs `cameraToP3(S2)×2^BE×2^decode.exp` | Δu′v′ media **0.0070** (p95 0.023, p99 0.101, n=9370, máscara común) |

El roundtrip **no** prueba que Resolve use esa matriz. La inversa S9→cámara es ensayo de aislamiento del codec, no un oráculo de revelado.

### Máscara u′v′ común

Todas las métricas Δu′v′ / false color / percentiles de halo usan  
`Y_ref > 0.005 AND Y_cand > 0.005 AND u′v′ finitos en ambos` (`tools/exp005_metrics.py` `common_uv_mask`). No se promedian cromas de negros.

### Unidades del halo

| Campo | Unidad |
|-------|--------|
| peak / p95 / p99 / p99.9 | `hypot(Δ(B−G), Δ(R−G))` en **canales P3 lineales** (no ΔE*) |
| integrated | suma sobre píxeles válidos (unidades·píxel) |
| extent_px | píxeles consecutivos en un **perfil de borde** sobre 20 % del pico de ese perfil |
| peak_duv | CIE 1976 Δu′v′, **invariante de escala** |

El máximo solo es un píxel. En A04_f044 AHD: peak 0.455, **p99 0.052**, p99.9 0.149. Extent en perfiles de carta: 4–17 px (antes el extent sobre la media 2D del frame era 0: artefacto).

### Correspondencia espacial

Fracción de píxeles p99-halo que caen en bordes (gradiente log Y p90, dilatados): **0.85** en A04_f044 AHD. El error medido está en los filos, no en un outlier aleatorio de fondo.

### A04_f049 y la exposición

Mismo ISO 1600 / BE 3.1. f044 EXR `decode.exposure=−3`; f049 EXR 0. El peak RGB-diff de AHD pasa de **0.46 → 4.70** (~×10). El p99 **0.052 → 0.40** (~×8, un stop de 3). `peak_duv` **0.174 vs 0.157** (casi igual). El máximo de f049 está en highlight **clipado** (Y alta, `peak_clipped`); excluyendo Y>0.64 el peak cae a **0.63**. El “halo 10×” era en gran parte **escala lineal del EXR + un píxel saturado**, no un demosaic diez veces peor.

### Hashes

A04_f044 AHD: DNG, EXR y SDK f32 coinciden disco ↔ `metrics.json`. Dumps S1/S2 con SHA propios. Inject SHA en la identidad de caché (`path#sha`).

### Identidad payload

Default vs `CDNG_OG_DEBAYER=ahd`: **S1, payload de sample, meta de revelado y SDK f32 idénticos** (`payload_equal`, `decode_relevant_meta_equal`).

### Bayer sintético con GT

**Ausente.** DNGs comprimidos; no se parchea el mosaico. Los fixtures son inject RGB @ pre_map.

---

## F — Campaña final (serie A, q2 y q18)

AHD / VNG / AAHD. C0, α=1, DZ=0, NR/synth/RDOQ OFF. 6 fotogramas × 2 q × 3 algos = **36/36**. Objetivo: artefactos de demosaic, **no** borrar el halo.

Medias serie A (máscara común):

| q | algo | PSNR | detail | Δu′v′ media | halo p99 | false color | zip row | texture | convert s |
|---|------|------|--------|-------------|----------|-------------|---------|---------|-----------|
| 2 | AHD | 42.75 | 0.871 | 0.00661 | 0.168 | 0.072 | 0.0125 | 1.018 | 0.87 |
| 2 | VNG | 44.03 | 0.881 | 0.00538 | 0.150 | 0.045 | 0.0096 | 0.926 | 0.98 |
| 2 | AAHD | 43.61 | 0.893 | 0.00607 | 0.150 | 0.059 | 0.0113 | 0.996 | 1.24 |
| 18 | AHD | 42.57 | 0.812 | 0.00714 | 0.169 | 0.084 | 0.0128 | 1.024 | 0.83 |
| 18 | VNG | 43.79 | **0.794** | 0.00600 | 0.151 | 0.056 | 0.0101 | 0.933 | 0.94 |
| 18 | AAHD | 43.39 | 0.827 | 0.00663 | 0.151 | 0.070 | 0.0117 | 1.001 | 1.18 |

RSS no está instrumentado en el banco. VNG gana PSNR/zip/false color pero **regresa detalle a q18** vs AHD. AAHD baja p99 y false color en q2 y q18 **sin** esa regresión; el pico de halo global sigue en el mismo orden (≈1.7). Memoria: no medida.

Ningún algoritmo elimina el halo visible. PSNR↑ no es criterio de promoción.

---

## G — Cierre

| | |
|--|--|
| BUG-002 | **P0, abierto.** Persiste a q2, persiste sin AHD (inject). |
| Default | **AHD, sin cambio.** |
| Candidato experimental | `CDNG_OG_DEBAYER=aahd` **solo** como flag de estudio (p99/false color/detail). **No** se activa por defecto. **No** cierra el bug. |
| Clase | **D** |
| Limitaciones | Revelado Resolve ≠ cameraToP3; sin Bayer GT; peak RGB-diff depende de exposición EXR; RSS no medido; DCB/AMaZE/RCD/LMMSE fuera de build. |
| Fuera de alcance | EXP-006/007/008, debayer propio, AMaZE/RCD/LMMSE, compensaciones de croma nuevas, investigación de compresión. |

Ver `closure` en `benchmark/experiments/exp005-results-summary.json`.
