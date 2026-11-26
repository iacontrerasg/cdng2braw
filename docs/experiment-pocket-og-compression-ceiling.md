# EXP-002a — Techo de compresión y asignación de bits

## Control de versiones

| Campo | Valor |
|---|---|
| Rama | `experiment/pocket-og-compression-ceiling` |
| Partida | `e1ade9a5164286b6901433e05d418fa4e1cf870d` (main con EXP-002 / PR #5) |
| Por defecto | Sin cambios de producción |
| Push / merge | Solo con autorización |

Instrumentación: `CDNG_BIT_BUDGET`, `CDNG_COEFF_STATS`, `CDNG_STATS_DIR` (diagnostic_env; no alteran payload). Presupuesto `thread_local` (seguro con workers>1). Corridas instrumentadas: `workers=1` + `--no-cache`.

## 1. Auditoría del códec (CanvasPlanes → sample)

Orquestación (`convert.cpp`): `finishPlanes` (NR) → `canvasPlanes` (place + deblock precomp) → `encodeFixed` / `encodeRateControlled` → `frameChunk` → `StreamMux::addFrame` (BMDF + chunk + pad 8).

### Verificado en código

| Etapa | Ubicación | Notas |
|---|---|---|
| Lienzo Y / Cb / Cr | `signal.cpp` `canvasPlanes` | Y `ws×hs`; Cb/Cr half; Y∈[0,4095], C∈[−4096,4095] |
| Geometría | `geometry.cpp` | `ws=round_up(w+32,32)`, `hs=round_up(h+16,8)`; `rows`≈72; ≤4 strips; ≤32 slices/franja |
| Bloques / slice | `Codec::encode` | Y 8×8 (63 AC); Cb→Cr 4×8 (31 AC); macrobloque: 2Y+Cb+Cr |
| DCT | `dct8x8` / `dct4x8` | DCT-II **ortonormal**; DC Y `(c0/8−2048)/yUnit`, C `c0/cUnit` |
| Orden coef. | `ScanY[63]`, `ScanC[31]` | Orden propietario (pares u,v) — **no** zigzag JPEG clásico |
| Paso AC Y | ley calibrada | `qY = 12.386·(q+4)/12` (no mediana de `y_ac`) |
| Paso AC C | mediana sesgada a q8 | `c_ac[i]·(6.185/refC[i])`, `i=1…31` |
| Cuantización AC | `quantize` | `nearbyint` o deadzone `⌊|c|/s+½−z⌋` (solo AC) |
| DC | `dcChain` | Diferencial; límite **±4095** (clase 12); strict → `CodecRangeError` |
| VLC AC | `entropy.cpp` + `vlc-table.json` | 192 entradas; ZRL; mag/signo `k+1` bits; EOB hardcode `"1010"` |
| Drop / fallback | `drop`/`adjust`/`fallbackBits` | Hasta 2 pases drop; clamp AC a ±4095 |
| Marcador | `0xFF` | 8 bits tras cada macrobloque (coste compartido) |
| Padding | slice y sample | Alineación a **8 bytes** (ceros) |
| Cabecera frame | 120 B @ offset 8 | `hdr[1]=q` (byte 9 del chunk); noiseY/C @20–23; ws/hs/rows; nstrips |
| Tabla slices | @ `0x80` | `u32×n`; primer slice @ **`FirstSlice=0x1200`** |
| Chunk | `braw` + size BE + hdr + tabla + gap | Gap incluye `0x0021×96` @ `0x1080` |
| BMDF | `BmdfSize=1024` | Antepuesto al chunk en el sample |
| Contenedor | QT `mdat`/`moov` | Overhead no identidad de imagen |
| `encodeFixed` | bump q tras `CodecRangeError` | En QMax: encode non-strict (clamp) |
| Rate search | bisección | Saturación QMax si no cabe el presupuesto |

### Hipótesis (formato propietario)

- Semántica completa de keys BMDF no documentada públicamente.
- “Por qué” de la ley `12.386·(q+4)/12` (lectura calibrada, no derivación pública).
- Precompensación antibloques como anulación del filtro decoder `(3,10,3)/16` — el filtro SDK está verificado; la precomp es política de encoder.
- Escalas VA de noise por q; σ decoder ≈ field/`NoiseUnitY=287` y `/600` croma (medido).
- Paridad bit a bit con cámaras BMD no afirmada fuera del corpus.

### Encoder-only vs debe coincidir con el decoder

| Encoder-only (política) | Debe coincidir con decoder |
|---|---|
| Búsqueda de q / `encodeFixed` / rate | DCT, pasos/unidades, ScanY/C, VLC, EOB, ZRL, `0xFF`, pad 8 |
| Deadzone, deblock precomp/α, NR, demosaic | Geometría hdr, `hdr[1]=q`, offsets de slices, FirstSlice |
| Elección del *valor* noiseY/C | *Campos* noiseY/C (afectan revelado) |
| Instrumentación `CDNG_BIT_BUDGET` / coeff-stats | BMDF keys que lee el SDK; payload de slices |

### `CDNG_DEADZONE` (confirmado)

```
z = clamp(atof(CDNG_DEADZONE) o 0, 0, 0.5)   // static, una vez por proceso
ℓ = nearbyint(c/paso)           si z ≤ 0
ℓ = signo·floor(|c|/paso + 0.5 − z)   si z > 0
```

Solo AC (Y y croma). Banco: rechaza z∉[0,0.5]. Nativo: `clamp` silencioso. `OUTPUT_ENV` → identidad de caché.

## 2. Matriz experimental

| Fase | Matriz | Estado A04 |
|---|---|---|
| C | q∈{0,4,9,18,24} α=1 DZ=0 | Completa (2 frames) |
| E | DZ∈{0…0.5} × q∈{18,24} | Completa |
| F | α∈{1,0.75,0.5} × DZ∈{0.3,0.4,0.5} × q∈{18,24} | 17/18; **a50·dz50·q24** falla alineación (corr 0.735\<0.740) |
| G | ratio∈{3,5,8,12} × {dz0·α1, dz50·α1, dz50·α0.5} | Parcial; **dz50·α0.5·r≥5** falla alineación |
| H | NR×synth @ q24 α=1 DZ=0 | Completa: NR≈−0.3 % size; synth no cambia payload |
| D dark | frames:[] en manifiesto | **Pendiente** — no inventar mapeo |

## 3. Presupuestos 1080p12bit

| Ratio | Bytes/frame | Mejor demostrado (sample) | ¿Alcanzado? |
|---:|---:|---:|---|
| 3:1 | 1 036 800 | sí (rate search / q bajo) | Sí |
| 5:1 | 622 080 | **615 256** (α=0.75 DZ=0.5 q=24) | **Sí** (modo quality; no vía rate search a QMax solo) |
| 8:1 | 388 800 | — | No (gap ~37 % vs mejor) |
| 12:1 | 259 200 | — | No |

Referencia techo EXP-002 α=1 DZ=0 q24: sample **895 784** ≈ **3.47:1**.

## 4. Hallazgos de presupuesto (Fase A, q=24 α=1)

Reconciliación: `attributed_bits == payload_bytes×8` y `payload_bytes == slices_payload` en **19/19** corridas CE.

| Componente | % del payload (aprox.) |
|---|---:|
| AC final (run/clase) | **~60 %** |
| AC magnitud+signo | **~28 %** |
| DC | ~6 % |
| EOB | ~4 % |
| Marcadores (compartido) | ~2 % |
| Prefijos AC | ~0.3 % |
| Padding | ~0.03 % |

Por plano (atribuido): **Y ~76 %**, Cb ~10 %, Cr ~12 %.

## 5. Coeficientes (Fase B, A04 q24)

| | DZ=0 | DZ=0.5 |
|---|---:|---:|
| Fracción AC Y nulos | 0.53 | **0.71** |
| ±1 entre no nulos Y | 0.58 | 0.48 |

La sparsidad explica la mayor parte del ahorro; no solo menos ±1 sino más ceros (runs más largos → menos símbolos final+mag).

## 6. Respuestas explícitas

1. **¿Qué consume más bits?**  
   **Confirmada:** códigos AC *final* (~60 %) y magnitud/signo (~28 %), dominados por el plano **Y (~76 %)**. Marcadores/padding son secundarios.

2. **¿Por qué satura ~3.5:1 en A04?**  
   **Confirmada:** a QMax=24 con α=1 DZ=0 el sample ≈896 kB (3.47:1). Rate search pide 5/8/12:1 pero **satura q=24** sin alcanzar el presupuesto. Causa: densidad residual de AC no nulos en Y tras cuantización con tablas actuales — no evidencia de techo del *formato* BRAW en sí.

3. **¿Cuánto afecta el ruido?**  
   **Parcial:** serie D `frames:[]` (falta mapeo). Fase H a q24 α=1: NR luma/chroma=1 reduce sample solo **~0.3 %** (895 784→893 048); synth ON/OFF no cambia el payload (solo metadata/revelado). El deadzone es mucho más efectivo sobre coeficientes pequeños que el NR actual.

4. **¿Cuánto mejora deadzone?**  
   **Confirmada (A04 q24 α=1):** DZ 0→0.5 reduce sample **895 784 → 636 916 (−28.9 %)**, ratio **3.47 → 4.88**.

5. **¿Coste visual?**  
   **Confirmada:** PSNR vs CinemaDNG **sube** (+0.68 dB) al eliminar energía de alta frecuencia; **grad** 1.007→0.955 (−5 %); **lap** 1.016→0.884 (−13 %). A α=0.5×DZ=0.5×q24 el banco **rechaza** por correlación de detalle \<0.74 (acantilado).

6. **¿5:1 alcanzable con herramientas existentes?**  
   **Confirmada en A04:** α=**0.75**, DZ=**0.5**, q=**24** → sample **615 256** ≈ **5.055:1** (≤622 080). Rate search con DZ=0.5 α=1 satura a ~4.88:1 (no etiqueta 5:1). α=0.5×DZ=0.4×q24 ≈4.92:1 sin romper alineación.

7. **¿8:1 o 12:1?**  
   **Descartada con herramientas actuales** en A04: mejor ~615 kB vs 389 k / 259 k. Haría falta ~37–58 % reducción adicional o cambio de representación no admitido sin evidencia de decoder.

8. **¿Mayor potencial futuro?**  
   **Propuesta (Fase I):** sparsidad AC **adaptativa por plano/frecuencia** (extensión controlada de deadzone) + evaluación de **NR→síntesis** para recuperar estadística sin pagar bits de ruido; sin tocar tablas VLC ni ampliar QMax hasta prueba de decoder.

## 7. Fase I — Viabilidad (diseño, sin implementar)

| Estrategia | Beneficio | Restricción | Riesgo | Complejidad |
|---|---|---|---|---|
| A DZ adaptativa por plano | Y domina bits | Decoder ignora z (solo niveles) | Desbalance Y/C | Baja |
| B DZ por frecuencia | Apunta AC caros | Scan fijo | Pérdida de textura selectiva | Media |
| C Supresión selectiva AC | Como drop extendido | Drop ya existe (2 pasadas) | Artefactos / VLC range | Media |
| D Cuantización perceptual | Mejor RD | **No alterar tablas SDK** | Incompatibilidad | Alta — aparcar |
| E Entrópica alternativa | Bits/símbolo | Representaciones VLC fijas | Decoder | Alta — aparcar |
| F NR + síntesis | Menos bits de ruido | Metadata noiseY/C; synth SDK | Textura falsa | Media — EXP-002a H |
| G q por complejidad | Evitar QMax global | Rate search actual | Inestabilidad temporal | Media |

## 8. Clasificación

| Estado | Ítems |
|---|---|
| Confirmadas | Auditoría pipeline; reconciliación bit-budget; Y/AC-final dominan; techo 3.47 α1; DZ −29 % size; 5:1 con α0.75·DZ0.5·q24; rate search satura QMax; q0→q1 |
| Parcialmente | Ruido (H: NR −0.3 %; D sin frames); Pareto perceptual fino |
| Descartadas | 8:1/12:1 con solo QMax/DZ/α actuales; techo como límite *probado* del contenedor BRAW |
| Pendientes | Mapeo serie D; ROI perceptual amplia; EXP-003 sparsidad |

## 9. Reproducción

```bash
git checkout experiment/pocket-og-compression-ceiling
make worker brawdec
PYTHON=out/.venv-exr/bin/python python tools/exp002a_sweep.py --phase all-ce --no-cache
PYTHON=out/.venv-exr/bin/python python tools/exp002a_sweep.py --phase f-alpha --no-cache
PYTHON=out/.venv-exr/bin/python python tools/exp002a_sweep.py --phase g-rate --no-cache
PYTHON=out/.venv-exr/bin/python python tools/exp002a_sweep.py --phase h-nr --no-cache
PYTHON=out/.venv-exr/bin/python python tools/exp002a_report.py
make fidelity-test && make test
```

Resumen: `benchmark/experiments/exp002a-results-summary.json`  
HTML: `out/benchmark/experiments/exp002a/report.html`

## 10. Siguiente experimento propuesto

**EXP-003 — Sparsidad AC controlada:** deadzone (o umbral) adaptativa por plano y banda de frecuencia, con ancla perceptual (grad/lap/correlación detalle ≥ umbral del banco), midiendo sample real vs 5:1/8:1 **sin** modificar tablas VLC ni QMax; incluir NR+síntesis como eje separado tras cerrar H.
