# EXP-003b — Rate-Distortion Optimized Quantization (RDOQ)

**Rama:** `experiment/pocket-og-rdoq`  
**Base:** `3795dea60c1e0bd69ba6afbbfdaa833e0664ddcc` (main tras merge PR #7 / EXP-003)  
**Encoder por defecto:** sin cambios (RDOQ solo con `CDNG_RDOQ`).  
**UI:** sin cambios. **Sin merge** hasta campaña y aceptación.

## Prioridades (contexto)

| Pri | Tema |
|-----|------|
| P0 | Maximum Fidelity (sin ratio obligatorio) |
| P0 | Production ~3:1 |
| P0 | **BUG-002** halos cromáticos (bloqueante; EXP-004) |
| P1 | 5:1–6:1 almacenamiento |
| P2 | 8:1–12:1 no crítico |

RDOQ investiga **fidelidad a igual tasa** y **tasa a fidelidad comparable**. No perseguir ratios altos sacrificando P0. Último experimento principal de compresión antes de EXP-004.

## Hipótesis

`J = D + λ·R` sobre niveles AC legales puede mejorar la frontera RD sin cambiar la semántica del bitstream.  
**Refutación:** sin mejora consistente frente a controles (legacy / DZ uniforme z=0.5) en métricas del banco con cobertura estricta.

---

## Fase A — Auditoría de la codificación AC

Fuentes: `native/engine/codec.cpp`, `codec_tables.{hpp,cpp}`, `deadzone_policy.cpp`, `encode_stats.cpp`, `native/src/entropy.cpp`, `model/vlc-table.json`, `model/quant_steps.json`, `tools/fidelity/braw_inspect.py`, `cdng_bit_budget.h`.

### 1. DCT → niveles cuantizados

- Y: bloques 8×8 → `dct8x8` (ortonormal) → DC `target = (coef[0]/8 − 2048)/yUnit`; AC `ℓ = quantizeAc(coef[ScanY[i]], qY, Y, ScanY[i])`.
- Cb/Cr: bloques 4×8 → `dct4x8`; DC `coef[0]/cUnit`; AC con `ScanC` y `qC`.
- Reconstrucción encoder/vista: `rec = ℓ · step` (`codec.cpp` ~603–621). Escala efectiva = paso plano `qY`/`qC` de `quant_steps` (Y: ley `12.386·(q+4)/12`; C: mediana corregida).
- Deadzone: `ℓ = sign·floor(|c/s| + 0.5 − z)` con z∈[0,0.5]; z=0 ≡ `nearbyint`. DC nunca usa deadzone AC.

### 2. ScanY / ScanC

- `ScanY[63]`: índices `u·8+v` zigzag AC del 8×8 (sin DC).
- `ScanC[31]`: zigzag AC del 4×8 (sin DC). Orden = orden de emisión VLC.

### 3. DC diferencial

- `dcChain`: `D=rint(target)`, `d=D−prev` (primero vs 0); códigos vía `dcCode` (clases 0…12, `|d|≤4095`).
- Estricto: overflow → `CodecRangeError`; no estricto: clamp y la cadena sigue desde el valor recortado (lo que reconstruye el decoder).

### 4. Runs

- Tras prepare: run = índices de scan entre no-ceros: `run = i − previous − 1`.
- El coste de un símbolo depende del **run desde el último no-cero**, no solo de `|ℓ|`.

### 5. Clases de magnitud

- `klass(|v|)` = floor(log2(|v|)) con |v|≥1; clase 0 → |v|=1. Máx. clase 11 (|ℓ|≤4095). `AcClasses=12`.

### 6. Prefijos VLC

- Tabla `ac[(run,k)]` en JSON → código final; si falta, `fallbackBits` construye prefijo (ZRL / relleno) + final.
- Emitido: trozos `prefix` (hasta 4×60 bits) + `final` + `(sign<<k)|mantissa` (k+1 bits).

### 7. ZRL

- Cadena fija (default `11111111001`); usado cuando `run≥16` y existe `(run%16, k)`.

### 8. EOB

- Fijo `"1010"` (4 bits) **siempre** al final de cada bloque (incluso si todos los AC son 0).

### 9. Restricciones de niveles

- Tras clamp: `|ℓ|≤4095`. Clases sin código en run 0 pueden mapearse con `adjust[k]` a un nivel representable **antes** de codificar.

### 10. Clamping

- En `entropy.cpp` `block()`: fuera de ±4095 → clamp a ±4095 y `stats.clamped++`. En modo estricto del codec, clamp AC previo puede abortar el frame.

### 11. Dropping

- Hasta 2 pasadas: si `drop[run*12+klass(ℓ)]`, el coeficiente se pone a 0 (`stats.dropped`). Cambia runs posteriores → **no** es coste aditivo por coeficiente.

### 12. Ajustes y fallbacks

- `adjust[k]`: remapeo de clase sin código propio.
- `fallback` + prefijos largos: `stats.fallback++` si hubo prefijo. Oráculo debe usar las **mismas** tablas `pre/fin/drop/adjust`.

### 13. Alineación y padding

- Macroblock: Y₀, Y₁, Cb, Cr, marcador `0xFF` (8 bits).
- Fin de slice: `BitWriter::finish` rellena a múltiplo de **8 bytes**; `padding_bits = size*8 − coded_bits`.
- Headers de contenedor / FirstSlice / metadata: **fuera** del coste AC local; medición oficial = `sample_bytes` (braw_inspect).

### 14. Dependencias intra-bloque

- Runs, drop, EOB y fallbacks acoplados a la secuencia completa de no-ceros.
- **Prohibido** modelar `R(bloque) = Σ R_i` independientes.

### 15. Dependencias inter-bloque / MB

- DC: cadena `prev` entre bloques del mismo plano/slice.
- Entropía AC: independiente entre bloques salvo marcador MB y padding de slice.
- RDOQ AC **no** debe alterar DC ni el orden MB.

### Decisiones modificables sin cambiar tablas ni reconstrucción del decoder

| Sí (misma semántica bitstream) | No |
|--------------------------------|----|
| Elección de nivel AC entero legal `|ℓ|≤4095` (incl. 0) | Pasos `qY`/`qC`, `yUnit`/`cUnit` |
| Política de deadzone / RDOQ que emite niveles legales | Scan, EOB, ZRL, tabla `ac`, adjust/drop embebidos |
| λ y pesos de D en el encoder | Significado de reconstrucción `ℓ·s` en decoder |
| | Formato contenedor / UI / SDK |

El decoder solo ve niveles ya emitidos; cualquier `ℓ` legal con las tablas actuales es válido.

---

## Fase B — Oráculo de coste

API: `cdng_ac_block_bits` / `cdng_ac_prepare` en `native/include/cdng_ac_cost.h`, implementada en `entropy.cpp` **reutilizando** clamp→adjust→drop→emit (sin segunda VLC).

- Coste local exacto: bits de símbolos AC + EOB (opcionalmente DC aparte).
- Global sample: headers, marcadores MB, padding, metadata — medidos en BRAW real.

Validación: bloques sintéticos + aleatorios reproducibles; reconciliación exacta predicho vs emitido para secuencias legales. Limitación: si `adjust`/`drop` cambian la secuencia, el coste es el de la secuencia **efectiva** post-prepare.

## Fase C — Distorsión

`D(c,k,s) = (c − k·s)²` en dominio DCT, con `s` = paso efectivo del encoder. Pesos experimentales por plano/banda; control = pesos uniformes. No PSNR RGB por candidato; no ROI espacial en el loop.

## Fase D — Candidatos

Por coeficiente AC: legacy, deadzone de control (si aplica), 0, 1–2 vecinos hacia 0, y vecino alejándose solo si baja D o R VLC. Sin duplicados; legales; DC fijo; sin cambiar signo arbitrariamente ni tablas. Legacy ∈ candidatos. A `λ=0`, D del modelo ≤ D(legacy) salvo empates/fallbacks documentados.

## Estado de implementación

| Pieza | Ubicación | Notas |
|-------|-----------|--------|
| Auditoría | este doc § Fase A | |
| Oráculo | `native/include/cdng_ac_cost.h`, `native/src/entropy.cpp` | Misma ruta clamp→adjust→drop→emit |
| Distorsión / candidatos / trellis | `native/engine/rdoq.{hpp,cpp}` | `J=D+λR`; pesos plano/banda |
| Cableado | `codec.cpp` encode + reconstruct | Solo si `CDNG_RDOQ=1` |
| Tests | `native/tests/ac_cost_test.cpp`, `engine_codec_test` | Oráculo + λ=0 |

### Activación experimental

```bash
export CDNG_RDOQ=1
export CDNG_RDOQ_LAMBDA=0.5   # opcional; default 0
# Encoder por defecto (sin estas vars) = legacy / deadzone existente
```

## Fase E — Validación del trellis

Implementación: trellis DP (no coordinate descent). Prueba: `engine_rdoq_test`.

| Comprobación | Resultado esperado |
|--------------|-------------------|
| Optimalidad en candidatos | `J_trellis = J_exhaustivo` en bloques con K≤4 activos |
| `R_estimated` | suma símbolos del DP + EOB (sin prepare) |
| `R_optimized` | alias de `R_estimated` (objetivo del DP) |
| `R_emitted` | `cdng_ac_block_cost` tras clamp/adjust/drop |
| Nulo / EOB / signos / determinismo / rango | cubiertos en test |
| ZRL / drop | trellis evita `(run,k)` con `drop`; post-mod cuantificado |
| adjust | tablas verificadas: `adjust[*]=0` |
| DC | API solo muta el buffer AC |

Con tablas oficiales, `R_estimated == R_emitted` salvo rarezas de drop; el test exige mismatch &lt; 5 % en batería aleatoria y reporta conteos.

## Fases F–H — Integración, controles y barrido λ

### F — Integración

- Default OFF: sin `CDNG_RDOQ` el payload = legacy (identidad de caché distinta si se activa).
- Solo Pocket OG (`convert.cpp` rechaza otras rutas).
- `CDNG_RDOQ` / `CDNG_RDOQ_LAMBDA` en `OUTPUT_ENV`; λ finita ≥ 0; λ sin RDOQ rechazada.
- Config registrada en evento `started` (`rdoq`, `rdoq_lambda`).
- Compatible con DZ/α/NR/synth vía `experimental_env` (NR/synth OFF en G/H).
- q fijo y rate search sin cambios de defaults/VLC/DCT.

### G — Controles A04 (`tools/exp003b_sweep.py --phase g`)

| Id | Config |
|----|--------|
| G0 | α=1, DZ=0, q24 |
| G1 | α=0.75, DZ=0.5, q24 |
| G2 | plane-y40-c50 (series_approved EXP-003), q24 |
| G3 | Max fidelity experimental q=2, α=1, DZ=0 |

NR y synthesis OFF. Artefactos históricos solo con `verify_run_matches_request`.

### H — Barrido λ (A04, α=0.75, DZ=0.5, NR/synth OFF)

Grid: `0, 0.01, 0.1, 1, 10, 30, 50, 70, 100` × q∈{18,24}.  
Informe: `tools/exp003b_report.py` → `benchmark/experiments/exp003b-results-summary.json`.

Hallazgos preliminares (medias A04, `sample_bytes`):

| Punto | Ratio | PSNR | Detail |
|-------|-------|------|--------|
| G1 (sin RDOQ) | **5.055** | **43.23** | 0.744 |
| G0 legacy | 3.47 | 42.43 | 0.790 |
| G3 q2 max-fid | 1.74 | 42.71 | **0.870** |
| RDOQ λ=0 q24 | 3.56 | 42.59 | 0.787 |
| RDOQ λ=30 q24 | 4.07 | 42.74 | 0.781 |
| RDOQ λ=70 q24 | 4.54 | 42.79 | 0.769 |
| RDOQ λ=100 q24 | 4.84 | 42.80 | 0.760 |

- G1 ≡ EXP-002a `a75-dz50-q24` (sample + `candidate_sha256`).
- **λ=0 no preserva la sparsidad del DZ**: minimiza D en el set de candidatos y **aumenta** bytes frente a G1.
- Acercarse a ~5:1 exige λ alta (~100); PSNR/detail aún por debajo de G1 en esta rejilla.
- Candidatos ~3:1: RDOQ λ baja @ q18; ~5:1: G1 o RDOQ λ≈100 @ q24.

### H2 — Rendimiento (wall, A04 completo)

| | w=1 | w=2 | w=4 |
|--|-----|-----|-----|
| RDOQ OFF | ~10.3 s | ~9.7 s | ~10.3 s |
| RDOQ ON λ=1 | ~23.2 s | ~16.9 s | ~17.4 s |

Overhead ≈ **2.3×** a 1 worker; 2 workers mitigan parcialmente. Cuello: trellis por bloque en el hot path de encode.

## Fases I–N — Campaña final y cierre

### I — Igual tasa (±2 % sample bytes)

| Par | Tasa | Veredicto |
|-----|------|-----------|
| RDOQ λ=70 @q18 vs ctrl α0.75 DZ0.5 @q18 | ~igual | **cand_worse** (PSNR/detail) |
| RDOQ λ=130 @q24 vs G1 | ~igual (1.4 %) | **cand_worse** |
| RDOQ λ=0 @q2 vs G3 | igual | **equivalent** (identidad práctica) |

No atribuible a precomp distinta: mismos α/DZ en pares 3:1/5:1.

### J — Límites

- λ∈{130,160,200,250,300}: más compresión degradando detalle; sin mejora útil de frontera vs G1.
- Rate search 3/5/6/8/12: DZ uniforme satura ~**5.06:1** (QMax); RDOQ λ=100 ~**4.84:1**. **No 6:1 ni 8:1** efectivos.

### K — Serie A (cobertura estricta)

| Config | `series_approved` | Ratio | Notas |
|--------|-------------------|-------|-------|
| G0 | sí | 3.49 | |
| G1 | **no** | 5.09 | A01 `quality_rejected` |
| G2 | sí | 4.87 | |
| G3 | sí | 1.75 | |
| RDOQ ~3:1 | sí | 3.10 | |
| RDOQ ~5:1 (λ130) | **sí** | 5.16 | Pasa A01; PSNR&lt;G1 |
| RDOQ hi (λ250) | no | 6.13 | 6/6 detail rejected |

### L — Maximum Fidelity

RDOQ λ=0 @ q0/q2 ≡ control sin RDOQ. Deltas a λ&gt;0 &lt; umbral práctico (0.05 dB / 0.005 detail). **Sin mejora real frente a G3.**

### M — Visual

Hojas a ciegas: `out/benchmark/experiments/exp003b/blind-sheets/` (claves en `benchmark/experiments/exp003b/blind-keys/`). ColorChecker, tela, metal, bordes, card.

### N — Clasificación final

**C — Mejora localizada.**  
RDOQ no gana 3:1 ni 5:1 a igual `sample_bytes` frente a controles pareados; no justifica el overhead ~2.3× para frontera RD. Efecto local: en Serie A, λ≈130 pasa el gate 0.74 en A01 donde G1 falla, a costa de PSNR. Max-fid sin ganancia práctica. **No se amplía la arquitectura del trellis.** Defaults / VLC / DCT / QMax intactos.

| Pregunta | Respuesta |
|----------|-----------|
| ¿Justifica su complejidad? | **No** (para adopción general) |
| ¿Mejora 3:1 o 5:1 a igual tasa? | **No** |
| ¿Alcanza 6:1 / 8:1? | **No** (rate search satura ~5:1) |

### Recomendaciones EXP-004

1. **BUG-002** halos cromáticos: bloqueante P0.  
2. Mantener G1 (α0.75 z=0.5) como referencia ~5:1; G2 si se prioriza gate Serie A.  
3. No basar perfiles Maximum Fidelity / Production 3:1 en RDOQ sin nueva evidencia.  
4. Validación perceptual + grading + estabilidad temporal.

SHA base: `benchmark/experiments/exp003b-base-sha.txt`.  
Resumen: `benchmark/experiments/exp003b-results-summary.json` (schema 2).
