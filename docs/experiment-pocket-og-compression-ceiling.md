# EXP-002a — Techo de compresión y asignación de bits

## Control de versiones

| Campo | Valor |
|---|---|
| Rama | `experiment/pocket-og-compression-ceiling` |
| Partida | `e1ade9a5164286b6901433e05d418fa4e1cf870d` (main con EXP-002 / PR #5) |
| Por defecto | Sin cambios de producción |
| Push / merge | Solo con autorización |

Instrumentación: `CDNG_BIT_BUDGET`, `CDNG_COEFF_STATS`, `CDNG_STATS_DIR`, `CDNG_STATS_LABEL` (diagnostic_env; no alteran payload).  
Publicación: mutex + JSONL atómico por línea; **solo el intento seleccionado** va a `bit-budget.jsonl` / `coeff-stats.jsonl`; descartados → `encode-attempts.jsonl`.  
Campos: `selected`, `attempt_index`, `q_tried`/`q_effective`/`q_requested`, `frame_index`, `source_name`, `payload_sha256`, `slices_payload_bytes`.  
`CDNG_ALIGN_SOFT=1`: el banco registra `gate_failed` sin abortar (umbral 0.74 intacto).

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

## 6. Respuestas EXP-002a (originales)

1. **Más bits:** AC *final* (~60 %) + mag/signo (~28 %); **Y ~76 %**. Confirmada.
2. **Techo ~3.5:1:** QMax α1 DZ0 → 3.47:1; rate search satura. Confirmada.
3. **Ruido (previo):** NR=1 irrelevante (−0.3 %); ver §11 para barrido completo.
4. **Deadzone:** −28.9 % size @ q24 (3.47→4.88). Confirmada.
5. **Coste visual DZ:** PSNR↑, grad/lap↓; α0.5·DZ0.5 falla gate. Confirmada.
6. **5:1:** α0.75·DZ0.5·q24 → 5.055:1. Confirmada.
7. **8:1/12:1:** no con herramientas actuales. Descartada.
8. **Futuro:** sparsidad adaptativa + NR selectivo. Propuesta.

## 6b. Respuestas — final review / NR

1. **¿Los resultados anteriores incluían intentos descartados?**  
   **Sí (confirmado).** `Codec::encode()` escribía JSONL en cada llamada; `encodeFixed` / rate search / non-strict pueden invocarla varias veces. Además, antes de `thread_local`+mutex, workers>1 podían corromper líneas. **Mitigación:** solo el intento `selected` publica en `bit-budget.jsonl`; descartados → `encode-attempts.jsonl`. Las conclusiones de Fase C @ q fijo (sin fallback) siguen siendo válidas; q0 y rate search deben releerse con la instrumentación nueva.

2. **¿Qué intensidad de NR empieza a reducir tamaño de forma significativa?**  
   **~10** (−3 % Y=C); claro desde **25** (−8 %); **50** (−16 %); **100** (−29 %). NR=1 es negligible.

3. **¿Qué proporción del ahorro viene de Y vs C?**  
   A intensidad 100: Y sola ahorra ~193 kB; C sola ~69 kB; ambas ~262 kB → **~74 % Y / ~26 % C** del ahorro combinado (no perfectamente aditivo).

4. **¿A qué intensidad se pierde textura?**  
   Grad/lap empiezan a bajar de forma perceptible hacia **25–50**; a **75–100** el gate de detalle puede fallar (p. ej. NR100+synth, corr 0.738).

5. **¿El ruido sintetizado reproduce el original?**  
   **Payload de slices idéntico** (synth solo cambia metadata noiseY/C → SHA de BRAW distinto). No es recuperación píxel a píxel del sensor; es ruido del SDK en revelado. Determinismo del SDK: mismo BRAW → mismos campos; el ruido añadido al RGB depende del decoder (evaluar con decodificaciones repetidas del mismo sample).

6. **¿corr\<0.74 es desalineación o degradación?**  
   **Degradación / sensibilidad de la métrica, no desplazamiento.** α0.5·DZ0.5·q24: `shift=(0,0)`, correlación de luma multiescala ≈**0.999**, grad_corr≈0.96, detail_corr≈0.735. Clasificación: `geometry_aligned` + `fidelity_degradation_not_geometric` + `E_highpass_detail_metric_sensitive`.

7. **¿NR y deadzone se complementan?**  
   **Sí en tasa, con coste acumulado en detalle.** Ej.: DZ0.25+NR50 @ α1 → 4.97:1 (gate OK, grad 0.972); DZ0.5+NR50 → 5.69:1 pero **gate fail**. No son sustituibles uno por otro: NR reduce energía antes de DCT; DZ anula niveles pequeños tras cuantizar.

8. **¿Mejor ratio sin degradación espacial excesiva (gate OK)?**  
   - Referencia 5.055: α0.75·DZ0.5·NR0.  
   - **Mejor gate-pass en NR-D:** α**0.75** · DZ**0.25** · NR**50** → sample **608 792** ≈ **5.109:1**, grad **0.949**.  
   - Compromiso textura: α1·DZ0.25·NR50 ≈ 4.97:1 con grad 0.972.

9. **¿Prioridad EXP-003?**  
   Sparsidad AC **adaptativa por plano/frecuencia** (controlando el highpass-detail del banco), con NR moderado (≈25–50) como preproceso opcional — **no** NR=100 ni apilar DZ0.5+NR50 sin ancla perceptual. No tocar VLC/QMax/tablas.

## 7. NR-A — Auditoría (código)

| Aspecto | Hecho verificado |
|---|---|
| Rango petición | `nr_luma` / `nr_chroma` ∈ **[0, 100]** (`convert.cpp`); se pasa a `denoisePlanes` como **amount = nr/100** ∈ [0, 1] |
| Algoritmo | Wiener DCT 8×8 solapado (`nr.cpp`); `g = 1 − amount·(1 − s/(s+noise))` |
| amount=1 | Atenuación completa del residuo Wiener (no implica “cero ruido” residual ni imagen flat) |
| Y vs C | Y se filtra primero; croma usa brillo 2×2 de luma **sin filtrar** como guía |
| Orden | `finishPlanes`: NR Y → sharpen → conversión Cb/Cr → NR C → luego `canvasPlanes` (precomp) → DCT |
| ISO | Sin ganancia ISO explícita en NR; el modelo de ruido se estima **por fotograma** (`noiseModel`) |
| Synth | Independiente: escribe `noiseY/C` en cabecera; no cambia slices |

## 7b. Serie D / serie A

- Serie D: `frames: []`, `status: pending` (p. ej. D01 clip `C0057`). **Necesario:** anotar índices de fotogramas dark válidos (y opcionalmente EXR si se quiere fidelidad; no hace falta EXR para stats de ruido/bits).
- Serie A completa de candidatos NR-D: **pendiente de campaña**; no extrapolar A04 automáticamente.

## 8. Fase I — Viabilidad (diseño, sin implementar)

| Estrategia | Beneficio | Restricción | Riesgo | Complejidad |
|---|---|---|---|---|
| A DZ adaptativa por plano | Y domina bits | Decoder ignora z (solo niveles) | Desbalance Y/C | Baja |
| B DZ por frecuencia | Apunta AC caros | Scan fijo | Pérdida de textura selectiva | Media |
| C Supresión selectiva AC | Como drop extendido | Drop ya existe (2 pasadas) | Artefactos / VLC range | Media |
| D Cuantización perceptual | Mejor RD | **No alterar tablas SDK** | Incompatibilidad | Alta — aparcar |
| E Entrópica alternativa | Bits/símbolo | Representaciones VLC fijas | Decoder | Alta — aparcar |
| F NR + síntesis | Menos bits de ruido | Metadata noiseY/C; synth SDK | Textura falsa | Media — EXP-002a H |
| G q por complejidad | Evitar QMax global | Rate search actual | Inestabilidad temporal | Media |

## 9. Clasificación

| Estado | Ítems |
|---|---|
| Confirmadas | Intentos descartados en stats previas; fix selected+mutex; corr\<0.74 = no shift; NR significativo ≥10–25; Y domina ahorro NR; complementaridad NR×DZ; 5.109:1 gate-OK (α0.75·dz0.25·nr50) |
| Parcialmente | Synth vs estadísticas CinemaDNG (payload igual; RGB no evaluado multi-decode exhaustivo); serie A de candidatos |
| Descartadas | NR=1 como palanca de ratio; 8:1/12:1 con stack actual; interpretir gate fail como desalineación geométrica |
| Pendientes | Mapeo frames serie D; campaña serie A; EXP-003 |

## 10. Reproducción

```bash
git checkout experiment/pocket-og-compression-ceiling
make worker brawdec
PYTHON=out/.venv-exr/bin/python python tools/exp002a_sweep.py --phase align-diag --no-cache
PYTHON=out/.venv-exr/bin/python python tools/exp002a_sweep.py --phase nr-b --no-cache
PYTHON=out/.venv-exr/bin/python python tools/exp002a_sweep.py --phase nr-d --no-cache
PYTHON=out/.venv-exr/bin/python python tools/exp002a_report.py
make fidelity-test && make test
```

## 11. Siguiente experimento propuesto

**EXP-003 — Sparsidad AC controlada** con ancla `detail_correlation`/`grad`, NR opcional 25–50, sin VLC/QMax/tablas. Validar antes en serie A el candidato α0.75·DZ0.25·NR50.
