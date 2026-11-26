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

- **q=0 no es lossless**: pasos AC ≈4 códigos DCT; PSNR frente a CinemaDNG ~42.7 dB, no infinito.
- **Ratios 5:1 / 8:1 / 12:1 no se alcanzan** en A04: a q=24 el ratio efectivo ≈ **3.48:1** (α=1). Solo 3:1 es coherente (q≈18, ratio≈3.03).

### Hipótesis / no asumido

- Equivalencia perceptual Q0 comercial BMD ↔ q=0 interno (más allá del mapeo de *esta* app).
- Que un ratio solicitado se consiga siempre: hay que mirar `ratio_achieved` y `q_eff`.

## 2. Metodología

Variables: q o ratio; α ∈ {1, 0.5, 0.75}; ablación/bounded en q∈{0,4,9,18,20,24}.

Revelado validado EXP-001. Scripts: `tools/exp002_sweep.py`, `python -m tools.fidelity rd`, `tools/fidelity/rd_incremental.py`, `tools/exp002_blind_sheet.py`.

## 3. A04 — Barrido q × α (99 corridas / 198 frames)

Curvas: `out/benchmark/experiments/exp002/rd-a04/rd.html`.

### α=1 (control): PSNR vs q (medias f044+f049)

| q | PSNR | bits/fotosito | B/frame | GB/h @24fps |
|---:|---:|---:|---:|---:|
| 0 | 42.72 | 7.33 | 1.90e6 | ~164 |
| 4 | 42.69 | 6.45 | 1.67e6 | ~144 |
| 9 | ~42.64 | ~5.2 | — | — |
| 18 | 42.52 | ~4.1 | — | — |
| 20 | 42.49 | 3.86 | 1.00e6 | ~86 |
| 24 | 42.43 | 3.54 | 0.92e6 | ~79 |

La curva PSNR(q) es **casi monótona decreciente**; la pendiente es suave (~0.3 dB de q0 a q24).

### Interacción con α

En A04, **α=0.50** suele superar a α=1 en PSNR a q fijo (coherente con EXP-001b @ q18). α=0.75 queda entre ambos en PSNR y conserva más gradiente que 0.50.

### Ratios solicitados vs conseguidos (A04)

| α | Pedido | Conseguido | q_eff |
|---|---:|---:|---:|
| 1 | 3:1 | **3.03** | 18 |
| 1 | 5:1 | **3.48** | 24 |
| 1 | 8:1 | **3.48** | 24 |
| 1 | 12:1 | **3.48** | 24 |

Misma saturación a q24 para α=0.50/0.75 (conseguido ≈3.56–3.65).

## 4. Error residual vs incremental (Fase C)

- **Absoluto:** BRAW(q) vs CinemaDNG (métricas del banco).
- **Incremental:** BRAW(q) vs BRAW(q=0) misma α — `out/benchmark/experiments/exp002/incremental-a*/incremental.json`.
- No se resta PSNR; el JSON reporta métricas de fidelidad entre pares de f32.
- q=0 **no** se presenta como lossless.

## 5. Serie A (24 configs × 6 frames)

Informe: `out/benchmark/experiments/exp002/rd-series-a/`. Configs: q∈{0,4,18,24} y ratios 3/5/8/12 × α∈{1,0.5,0.75}.

Conclusión limitada al corpus: la saturación de ratio alto se reproduce fuera de A04; no generalizar a “cualquier escena”.

## 6. Almacenamiento (1080p24, bytes/frame observados)

Ejemplos α=1 A04: q0 ≈164 GB/h; q20 ≈86 GB/h; q24 ≈79 GB/h.  
Capacidad 1 TB: ~6–12 h según q. Detalle en `rd-a04/summary.json` → `storage_1080p24`.

### Sugerencias preliminares (solo corpus Pocket OG evaluado)

| Uso | Candidato | Motivo |
|---|---|---|
| Máster / conservación | q0–q4 | Máxima fidelidad medida |
| Podcast / entrevista | q18–q24 o 3:1 | Buen tamaño; pérdida PSNR pequeña |
| Documental cliente | q9–q18 | Equilibrio |
| Live / clip | 3:1 (verificar `ratio_achieved`) | No prometer 8:1/12:1 en este material |
| Insignia | q0–q4 + α=1 (o α=0.5 experimental) | |

**No** se declara equivalencia con ProRes (EXP posterior).

## 7. Visual / a ciegas

`out/benchmark/experiments/exp002/blind-sheet/` (IDs `V001…` + `key.json` aparte).

## 8. Clasificación

### Confirmadas

1. Intervalo interno q 0…24.
2. q=0 no lossless.
3. Presets Q0/Q1/Q3/Q5 → 0/2/9/20 en esta app.
4. En A04 (y serie A ensayada) ratios ≥5:1 no se alcanzan; techo ~3.5:1 a q24.
5. PSNR(q) suave y casi monótona para α=1.

### Parcialmente confirmadas

6. α=0.50 como mejor PSNR a q fijo (corpus A / A04).
7. Estimaciones GB/h extrapoladas a 1080p24 desde este material.

### Descartadas

8. Etiquetar archivos como 8:1/12:1 solo porque se pidió ese ratio.
9. Tratar q mínimo como referencia lossless.

### Pendientes

10. Ranking visual a ciegas formal.
11. Escenas distintas a la serie A.
12. Comparación ProRes (EXP-003+).
13. Instrumentación densa de coeficientes AC (no hecha en todas las corridas; `braw_inspect` basta para payloads).

## 9. Recomendaciones para EXP-003

1. Tiers perceptuales sobre **q alcanzables** y **ratios ≤ ~3.5:1** en Pocket OG 1080 de este tipo.
2. No anunciar 8:1/12:1 sin medir `ratio_achieved`.
3. Perfil opcional α=0.5 solo tras inspección visual.
4. Comparar con ProRes en experimento dedicado.

## 10. Reproducción

```bash
make worker brawdec
PYTHON=out/.venv-exr/bin/python python tools/exp002_sweep.py --phase all-a04 --skip-existing
PYTHON=out/.venv-exr/bin/python python tools/exp002_sweep.py --phase series-a --skip-existing
python -m tools.fidelity rd --runs-glob 'out/benchmark/runs/exp002-a04-*' \
  --dest out/benchmark/experiments/exp002/rd-a04
make fidelity-test && make test
```

No merge ni push sin autorización. No promover variantes a producción.
