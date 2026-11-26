# EXP-002 — Caracterización rate–distortion y cuantización

## Control de versiones

| Campo | Valor |
|---|---|
| Rama | `experiment/pocket-og-rate-distortion` |
| Base | EXP-001b @ `a0e2cb19dc1055dcf40fa87c5708e0c408a9a2fd` (PR #4 aún abierto en main al iniciar) |
| Worker corridas | `f265ea91d3b8…` |
| Por defecto | Sin cambios de producción |
| Resumen versionado | `benchmark/experiments/exp002-results-summary.json` |

## 1. Investigación del cuantizador

### Confirmado en código

| Propiedad | Fuente |
|---|---|
| `q ∈ [0, 24]` completo | `QMin`/`QMax` en `codec.hpp`; UI y mensajes del worker |
| DCT Y 8×8 / croma 4×8 | `codec.cpp` |
| Pasos por `q` | `model/quant_steps.json` (25 entradas) |
| Cuantización | `nearbyint(coef/step)`; `CDNG_DEADZONE` opcional (defecto 0) |
| DC en cadena + VLC | Desborde → subir q o clamp en QMax |
| Calidad fija | `encodeFixed` |
| Ratio con search | Bisección: q más fino que cabe en el presupuesto |
| Ratios UI | 3…12 |
| Presets app | Q0→0, Q1→2, Q3→9, Q5→20 |

### Confirmado experimentalmente (A04)

- **q=0 no es lossless**: PSNR frente a CinemaDNG ~42.7 dB.
- **q solicitado 0 → q efectivo 1** en el bitstream (fallback `CodecRangeError` / `encodeFixed`). Además, **q_requested=0 y q_requested=1 producen el mismo BRAW** (mismo `q_frame=1`, mismo SHA de f32).
- **Ratios 5:1 / 8:1 / 12:1 no se alcanzan** en A04: a q=24 el ratio sobre sample ≈ **3.47:1** (α=1; worker ~3.48). Solo 3:1 es coherente (q_eff≈18, ratio_sample≈3.02).

### Hipótesis / no asumido

- Equivalencia perceptual Q0 comercial BMD ↔ q=0 interno (más allá del mapeo de *esta* app).
- Que un ratio solicitado se consiga siempre: hay que mirar `ratio_sample_12bit` / `ratio_segment_worker` y `q_eff` del bitstream.

## 2. Metodología

Variables: q o ratio; α ∈ {1, 0.5, 0.75}; ablación/bounded en q∈{0,4,9,18,20,24}.

Revelado validado EXP-001. Scripts: `tools/exp002_sweep.py`, `python -m tools.fidelity rd`, `tools/fidelity/rd_incremental.py`, `tools/exp002_blind_sheet.py`.

### Identidad y agregación (post code-review)

- El **nombre de corrida es solo etiqueta**; la configuración efectiva sale de `run.json` / `metrics.json` / setup / bitstream.
- `q_eff` = **q del fotograma en el bitstream** (`braw_inspect`, byte 1 de la cabecera), no `(q_min+q_max)/2`.
- Se distinguen: q solicitado, q por fotograma, media/min/max del tramo, fallback CodecRangeError, saturación en QMax=24.
- `--skip-existing` verifica identidad e integridad (no basta con existir `metrics.json`).
- Tamaños: `payload_bytes` (slices), `sample_bytes` (bmdf+chunk), `container_bytes`, overhead; ratios con denominador 12-bit raw explícito.
- Informes HTML **autocontenidos** (SVG embebido, sin CDN); métricas ausentes = «—», no 0.

## 3. A04 — Barrido q × α (99 corridas / 198 frames)

Curvas: `out/benchmark/experiments/exp002/rd-a04/rd.html` (SVG; incluye Laplaciano, error de bloques y mapa PSNR).

### α=1 (control): PSNR vs q (medias f044+f049)

Valores de almacenamiento sobre **sample_bytes** (bitstream), no `sdk_frame_bytes`.

| q_req | q_eff | PSNR | bits/sitio (sample) | sample B | GB/h @24fps |
|---:|---:|---:|---:|---:|---:|
| 0 | **1** | 42.72 | ~7.22 | 1.87e6 | ~162 |
| 1 | 1 | 42.72 | ~7.22 | 1.87e6 | ~162 |
| 4 | 4 | 42.69 | — | — | — |
| 9 | 9 | ~42.64 | — | — | — |
| 18 | 18 | 42.52 | — | — | — |
| 20 | 20 | 42.49 | — | — | — |
| 24 | 24 | 42.43 | ~3.45 | 0.90e6 | ~77 |

La curva PSNR(q_req) es **casi monótona decreciente** salvo el empate q0≡q1; pendiente suave (~0.3 dB de q_eff 1 a 24).

### Interacción con α

En A04, **α=0.50** suele superar a α=1 en PSNR a q fijo (coherente con EXP-001b @ q18). α=0.75 queda entre ambos en PSNR y conserva más gradiente que 0.50.

### Ratios solicitados vs conseguidos (A04, α=1)

| Pedido | ratio_sample_12bit | ratio_worker | q_eff |
|---:|---:|---:|---:|
| 3:1 | **3.02** | 3.03 | 18 |
| 5:1 | **3.47** | 3.48 | 24 |
| 8:1 | **3.47** | 3.48 | 24 |
| 12:1 | **3.47** | 3.48 | 24 |

## 4. Error residual vs incremental (Fase C)

- **Absoluto:** BRAW(q) vs CinemaDNG (métricas del banco).
- **Incremental:** BRAW(q) vs BRAW de **mínima cuantización efectiva** (corrida `q00`, q_eff=1), misma precompensación — no CinemaDNG lossless.
- Normalización ISO/exposición según `benchmark/manifest.json` (`incremental_normalized` vs `incremental_raw`).
- Identidad estricta de entradas/refs/revelado/precomp/SHA f32; fotogramas faltantes **abortan**.
- Artefactos: `out/benchmark/experiments/exp002/incremental-a*/incremental.json`.

## 5. Serie A (24 configs × 6 frames)

Informe: `out/benchmark/experiments/exp002/rd-series-a/`. Configs: q∈{0,4,18,24} y ratios 3/5/8/12 × α∈{1,0.5,0.75}.

Conclusión limitada al corpus: la saturación de ratio alto se reproduce fuera de A04; no generalizar a “cualquier escena”.

## 6. Almacenamiento (1080p24)

Base: **sample_bytes** × 24 fps. Ejemplos α=1 A04: q_eff1 ≈162 GB/h; q24 ≈77 GB/h.  
(Antes del review se usaba `sdk_frame_bytes` ≈1900544 en q0 → ~164 GB/h; la diferencia es el criterio de magnitud, no un re-encode.)

### Sugerencias preliminares (solo corpus Pocket OG evaluado)

| Uso | Candidato | Motivo |
|---|---|---|
| Máster / conservación | q_eff 1–4 (q_req 0/1 son equivalentes aquí) | Máxima fidelidad medida |
| Podcast / entrevista | q18–q24 o 3:1 | Buen tamaño; pérdida PSNR pequeña |
| Documental cliente | q9–q18 | Equilibrio |
| Live / clip | 3:1 (verificar ratio medido) | No prometer 8:1/12:1 en este material |
| Insignia | q bajo + α=1 (o α=0.5 experimental) | |

**No** se declara equivalencia con ProRes (EXP posterior).

## 7. Visual / a ciegas

`out/benchmark/experiments/exp002/blind-sheet/` (IDs `V001…` + `key.json` aparte).

## 8. Clasificación

### Confirmadas

1. Intervalo interno q 0…24.
2. q=0 no lossless; en A04 q_req 0 colapsa a q_eff 1 (= q_req 1).
3. Presets Q0/Q1/Q3/Q5 → 0/2/9/20 en esta app.
4. En A04 (y serie A ensayada) ratios ≥5:1 no se alcanzan; techo ~3.47:1 a q24 (sample).
5. PSNR(q) suave y casi monótona para α=1 (con empate q0≡q1).

### Parcialmente confirmadas

6. α=0.50 como mejor PSNR a q fijo (corpus A / A04).
7. Estimaciones GB/h extrapoladas a 1080p24 desde este material.

### Descartadas

8. Etiquetar archivos como 8:1/12:1 solo porque se pidió ese ratio.
9. Tratar q mínimo solicitado como referencia lossless.

### Pendientes

10. Ranking visual a ciegas formal.
11. Escenas distintas a la serie A.
12. Comparación ProRes (EXP-003+).
13. Instrumentación densa de coeficientes AC (no hecha en todas las corridas; `braw_inspect` basta para payloads/q).

## 9. Recomendaciones para EXP-003

1. Tiers perceptuales sobre **q alcanzables** y **ratios ≤ ~3.5:1** en Pocket OG 1080 de este tipo.
2. No anunciar 8:1/12:1 sin medir el ratio real (sample o worker).
3. Perfil opcional α=0.5 solo tras inspección visual.
4. Comparar con ProRes en experimento dedicado.
5. Tratar q_req 0 y 1 como el mismo punto operativo en este encoder/material.

## 10. Reproducción

```bash
make worker brawdec
PYTHON=out/.venv-exr/bin/python python tools/exp002_sweep.py --phase all-a04 --skip-existing
PYTHON=out/.venv-exr/bin/python python tools/exp002_sweep.py --phase series-a --skip-existing
python -m tools.fidelity rd --runs-glob 'out/benchmark/runs/exp002-a04-*' \
  --dest out/benchmark/experiments/exp002/rd-a04 --expected-q-grid --expected-ratios 3,5,8,12
python tools/fidelity/rd_incremental.py \
  --base out/benchmark/runs/exp002-a04-a1-q00 \
  --candidates-glob 'out/benchmark/runs/exp002-a04-a1-q*' \
  --dest out/benchmark/experiments/exp002/incremental-a1
make fidelity-test && make test
```

No merge ni push sin autorización. No promover variantes a producción.

## 11. Cambios numéricos tras code-review (sin re-sweep)

No se re-ejecutaron las 123 configuraciones. Se reutilizaron corridas con identidad verificada.

| Magnitud | Antes (review) | Después |
|---|---|---|
| `q_eff` | `(q_min+q_max)/2` del runner | q del fotograma en bitstream |
| GB/h q0 α=1 | ~164 (sdk `frame_bytes`) | ~162 (`sample_bytes`) |
| ratio techo α=1 | ~3.48 (worker) | ~3.47 sample / ~3.48 worker |
| Conclusión saturación ≥5:1 | Sin cambio | Sin cambio |
| Conclusión q0 lossless | Rechazada | Rechazada + q0≡q1 efectivo |
