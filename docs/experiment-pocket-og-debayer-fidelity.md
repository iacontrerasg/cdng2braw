# EXP-005 — Debayer Fidelity & BUG-002 isolation

**Rama:** `experiment/pocket-og-debayer-fidelity`  
**Base:** `main` tras merge PR #9 (EXP-004). Este trabajo **no** fusiona PRs.  
**Encoder por defecto:** AHD, sin cambios. `CDNG_OG_DEBAYER` ausente ≡ `user_qual=3`.  
**Alcance:** A (aislamiento), B (auditoría LibRaw), C (selector OG), D (campaña preliminar). **No** EXP-006/007/008. **No** FEAT-001. **No** debayer propio. **No** ampliar investigación de compresión.

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

## Recomendación para campaña final

**Clase D:** el halo **persiste sin AHD**. BUG-002 sigue P0. No cambiar defaults. No debayer propio. No EXP-006/007/008 ni FEAT-001.

Campaña final (si se abre): AHD + VNG + AAHD en los mismos fotogramas, q2 y q18, C0, para **zippering / false color en S1**, no como arreglo del halo. Ver `recommendation` en el JSON.
