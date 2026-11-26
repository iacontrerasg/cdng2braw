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

### Pendiente (brief truncado tras Fase D)

- Barrido λ / igual tasa vs α=0.75 z=0.5
- Serie A con cobertura estricta y Pareto multidimensional
- Clasificación hipótesis / aceptación
- No implementar perfiles P0 Maximum Fidelity / Production 3:1 / fix BUG-002 aquí

SHA base registrado también en `benchmark/experiments/exp003b-base-sha.txt`.
