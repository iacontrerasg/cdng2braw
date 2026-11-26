# EXP-003 — Adaptive AC Sparsity

**Rama / PR:** `experiment/pocket-og-adaptive-ac-sparsity`  
**Commit de partida:** `4fb9331bd9210712b77862879d869e1d68ce610c`  
**Clasificación final:** **C — Equivalencia práctica con deadzone uniforme** (ligeras mejoras locales de detalle en A01 a costa de tasa; no mejora la frontera RD).  
**Resumen:** `benchmark/experiments/exp003-results-summary.json`

## 1. Resolución de discrepancia de ratios (P1)

| Métrica | Valor (A04_f044, α=0.75, DZ=0.5) |
|---------|----------------------------------|
| `sample_bytes` (braw_inspect) | **615256** |
| `payload_bytes` | **609624** |
| `sdk_frame_bytes` | **638976** (múltiplo de 4096) |
| Ratio sample (3110400/sample) | **5.055:1** |
| Ratio SDK | **≈4.87:1** |

La cifra 5.055:1 de EXP-002a y la 4.87:1 reportada inicialmente en EXP-003 miden cosas distintas. No hay regresión: `candidate_sha256`, `sample_bytes`, `payload_bytes`, `q_eff` y PSNR coinciden con `exp002a-f-a04-a75-dz50-q24`.

Los informes oficiales usan **sample_bytes** vía `tools/fidelity/rd.py` + `braw_inspect`.

## 2. Correcciones de infraestructura

| Tema | Cambio |
|------|--------|
| Medición | `exp003_report.py` reutiliza `RD.frame_row` / `bitstream_frame` |
| Identidad / skip-existing | `verify_run_matches_request(..., deadzone=, policy_sha=)` |
| Política SHA | Worker emite `deadzone_policy_sha256` en `started`; caché `path#sha` |
| Pareto | Tolerancias absolutas (bytes %, dB, grad, lap, detail, Δu′v′); clases `valid` / `aligned_detail_rejected` / error |
| Mutex hot path | `std::atomic` + `shared_ptr` inmutable; `deadzonePolicyEnsureLoaded()` antes de workers |
| Stages | `chunk_built` → `sample_written` (tras `mux.addFrame`) → `conversion_completed`; informes = `sample_written` |
| JSON | Validación estricta C++ y Python (`tools/fidelity/deadzone_policy.py`) |

## 3. Geometría de frecuencias

Normalización **relativa al bloque del plano**: `‖(u/umax,v/vmax)‖/√2`.  
Y: 8×8 (`umax=7`); Cb/Cr: 4×8 (`umax=3`, 4:2:2). El mismo `(u,v)` **no** es equivalente perceptual entre planos.  
Bandas AC: low `[0,0.25)`, mid `[0.25,0.5)`, high `[0.5,1]`; DC fuera. Mapas: `benchmark/experiments/exp003/band-maps.json`.

## 4. Fase E — Presupuesto por frecuencia

`benchmark/experiments/exp003-band-budget.json` + campos `bands_Y` / `bands_Cb` en coeff-stats (energía, energy_q, nz).  
Bits VLC siguen siendo **por plano**; runs/EOB son costes compartidos — no se atribuyen a una sola banda. Reconciliación `attributed_bits == payload_bytes×8` en bit-budget.

## 5. Fase G — Igual tasa (puntos medidos)

A q fijo 24, α=0.75, sobre sample_bytes:

- Uniforme z=0.5: ~615 kB, ratio sample **5.055**, PSNR ~43.23.
- Plano y40/c50 y D1: ~642 kB, ratio ~**4.84–4.85**, PSNR similar o ligeramente inferior; más detail_corr.
- Ninguna política selectiva domina uniforme en (menor sample ∧ PSNR/grad/detail no peores) con las tolerancias definidas.
- Rate search 8:1/12:1 → saturación QMax (~4.63–4.85 sample), sin alcanzar el presupuesto.

## 6. Fase K — Serie A (6 fotogramas)

| Config | Ratio sample medio | PSNR medio | Notas |
|--------|-------------------|------------|-------|
| k-a0 (α1 DZ0) | 3.49 | 42.48 | Control |
| k-a1-u50 | **5.09** | **43.27** | Mejor tasa; A01 `quality_rejected` (detail 0.738) |
| k-a2-nr50 | 5.15 | 43.09 | NR+DZ; A01 rechazado por detalle |
| k-plane-y40-c50 | 4.87 | 43.25 | Pasa gate en toda la serie A |
| k-d1 | 4.88 | 43.20 | Pasa gate en toda la serie A |

Consistencia ISO/exposición dentro de A; **no** generalización a escenas independientes.

## 7. Visual / rendimiento

- Hojas a ciegas: `out/benchmark/experiments/exp003/blind-sheets/` (claves en `benchmark/experiments/exp003/blind-keys/`).
- Encode fresco serie A (~6 conversiones): uniforme ~3.85 s, D1 ~3.91 s, plano ~3.54 s — overhead de política despreciable frente a I/O/decode.

## 8. Clasificación y cierre

**C — Equivalencia práctica con deadzone uniforme.**  
La frontera RD medida sigue dominada por α=0.75 + DZ uniforme 0.5 (~5.05:1 sample). Las políticas adaptativas no mejoran Pareto a igual tasa; pueden conservar mejor el gate de detalle en A01 a costa de ~4 % más bytes. **No se amplía la búsqueda ni se implementa RDOQ en esta PR.**

## 9. Propuesta EXP-003b — RDOQ (solo diseño)

1. Oráculo de coste: presupuesto de bits existente + coste incremental de cambiar un nivel AC (prefijo/final/mag + efecto en run del siguiente).
2. Distorsión: error DCT ponderado por plano/banda; opcional ROI.
3. Candidatos: niveles enteros en rango VLC verificado; DC fijo; sin cambiar Scan/VLC/tablas SDK.
4. Algoritmo acotado: trellis por bloque o coordinate descent en AC no nulos con λ de igual tasa vs uniforme z=0.5.
5. Complejidad: precomputar tablas VLC; prohibido re-parsear política en el loop.
6. Criterio de éxito: mejora sample_bytes a PSNR/detail/grad tolerados, o mejora detail a sample_bytes ±2 %.

## 10. Respuestas 1–12 (actualizadas)

1. Plano vs uniforme: **no** mejora frontera RD (C/D local).  
2. Por frecuencia: igual.  
3. Bits: Y domina; bandas mid/high concentran nz residual (ver band-budget).  
4. Proteger: evidencia débil a favor de low/mid si se prioriza gate; no para tasa.  
5. Croma tolera sparsidad en bits absolutos; no justifica degradar Y agresivamente (D4).  
6. Igual tasa: **no** demostrada a favor de adaptativo.  
7. Mejor ratio sample: ~**5.09–5.15** (uniforme / NR+DZ).  
8–9. 6:1 / 8:1: **no**.  
10. Coste: detail ~0.74 en uniforme; A01 falla gate.  
11. Serie A: uniforme mejor tasa; plano/D1 más estables en gate.  
12. RDOQ: §9.

## 11. Reproducción

```bash
make worker
CDNG_RESOURCES=$PWD ./build/native/lib/engine_codec_test
PYTHON=out/.venv-exr/bin/python tools/exp003_report.py
PYTHON=out/.venv-exr/bin/python tools/exp003_band_budget.py
PYTHON=out/.venv-exr/bin/python tools/exp003_sweep.py --phase k \
  --dataset ~/Desktop/BMD --references ~/Desktop/EXR \
  --converter build/worker/converter --brawdec tools/brawdec/brawdec
```
