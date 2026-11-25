# EXP-001 — Pocket OG deblocking precompensation ablation

## Control de versiones

| Campo | Valor |
|---|---|
| Rama | `experiment/pocket-og-deblock-ablation` |
| Parte de | `main` @ `e800c70a1ea4a47491eda5c0eaf851c5aee74f06` (merge PR #2, banco schema 2) |
| Baseline congelado | `main-352cfab` (**sin modificar**) |
| Interruptor | `CDNG_OG_DEBLOCK_PRECOMP` ∈ {ausente,`1`,`on`,`true`,`0`,`off`,`false`} |

## 1. Investigación del algoritmo (código)

### Qué problema intenta resolver

**Confirmado (docs/format.md + `decodedPlanes`):** el SDK BRAW mezcla siempre cada píxel junto a una frontera de bloque 8×8 con pesos (3, 10, 3)/16. Los ficheros de cámara no muestran ese suavizado en las fronteras, así que el codificador **precompensa**: altera los píxeles de frontera para que, tras el filtro del decoder, recuperen el valor deseado.

### Matemáticas

En `native/engine/signal.cpp`, `precompensate()` (solo luma Y):

Para cada frontera vertical `i = 8, 16, …` (y luego horizontal), con vecinos `ain, a, b, bin`:

\[
\begin{aligned}
r_a &= 16\,a - 3\,\mathrm{ain},\quad r_b = 16\,b - 3\,\mathrm{bin} \\
a' &= (10\,r_a - 3\,r_b)/(10^2 - 3^2),\quad
b' = (10\,r_b - 3\,r_a)/(10^2 - 3^2)
\end{aligned}
\]

Es la inversa lineal del filtro (3,10,3)/16 aplicado a las dos muestras de frontera. `decodedPlanes` aplica el filtro directo en orden inverso (filas, luego columnas).

### Dominio

Opera sobre **códigos de señal Y** ya mapeados por la curva/matriz de la ruta (valores ~0…4095 en coma flotante), **antes** de `nearbyint` + clamp. No es lineal de cámara RGB ni DCT.

### Luma / croma

**Solo Y.** Cb y Cr solo se colocan en el lienzo y se redondean a −4096…4095 (`canvasPlanes`). Confirmado en prueba: `on.cb == off.cb`.

### Overshoot / undershoot

1. La inversa puede empujar Y **fuera de [0, 4095]** en bordes de alto contraste (**overshoot matemático**).
2. `rounded(y, 0, 4095)` **recorta** → clipping efectivo; se pierde la precompensación exacta en esos píxeles.
3. Hipótesis (no confirmada en decoder propietario): el residual tras clip + DCT/cuantización contribuye a halos al reconstruir.

### Dónde se satura

Único recorte de Y al intervalo representable en esta etapa: `rounded(..., 0, 4095)` dentro de `canvasPlanes`. La croma se satura a ±4096/4095 sin precompensación.

### Artefactos al desactivarla

**Hipótesis:** reaparece el suavizado periódico 8×8 del decoder (bloques visibles), posiblemente menos overshoot/halos. Escenarios A–D en la especificación EXP-001.

### Pocket OG vs otras rutas

`canvasPlanes` es **común** a VA / Pocket 4K / OG. El interruptor `CDNG_OG_DEBLOCK_PRECOMP` **solo cambia el flag en `Route::Og`**; VA y Pocket 4K siguen con precompensación aunque la variable diga `0` (tras validar el valor). Así se evita regresión en SIGMA fp.

## 2. Diferencia respecto al baseline `main-352cfab`

| | Baseline congelado | Control EXP-001 |
|---|---|---|
| Compresión | quality **q 4** | ratio **3:1** (`CDNG_RATE=search`) |
| Noise synthesis | **on** | **off** |
| NR | 0 | 0 |
| Precompensación | on (implícita) | on (`CDNG_OG_DEBLOCK_PRECOMP=1`) explícita |

El baseline **no se sustituye**. El control experimental es una corrida nueva bajo `out/benchmark/runs/exp001-*`.

## 3. Variable experimental

```
CDNG_OG_DEBLOCK_PRECOMP=1|on|true   # producción / control
CDNG_OG_DEBLOCK_PRECOMP=0|off|false # ablación
```

- Registrada en `OUTPUT_ENV` → identidad de caché, setup y `run.json`.
- Overrides ambientales no declarados → error.
- Evento `started` del worker: campo `og_deblock_precomp`.
- Misma binario para ambas variantes.

## 4. Diagnóstico

`encode.diagnostic_env` (p. ej. `CDNG_PIPELINE_TRACE=1`) se pasa al worker **sin** entrar en la clave de caché. Manifiestos `*-trace.json`.

## 5. Revelado

P3 D65, Linear, csgen 5, hlry 0, escala full, WB As Shot, exposure del manifiesto (−3 EV en A04_f044). Atributos críticos ausentes o inconsistentes **detienen** la evaluación. Limitación As Shot: no hay cruce con AsShotNeutral del DNG; sí se exige ISO SDK ≈ ISO del manifiesto cuando el SDK reporta `fiso`.

## 6. Métrica experimental de bloques

`reference_fidelity.experimental_block` / `regression.experimental_block`: MAE/RMSE en fronteras 8×8 vs interior y saltos de luma en costuras. Prueba sintética en `tests/test_fidelity.py`.

## 7. Comandos de reproducción

```bash
# Desde la raíz del repo, worker + brawdec al día
make worker brawdec

# Fase 1 — A04
make benchmark DATASET=~/Desktop/BMD TEST=A04 RUN=exp001-a04-control \
  MANIFEST=benchmark/experiments/exp001-control-3to1.json
make benchmark DATASET=~/Desktop/BMD TEST=A04 RUN=exp001-a04-ablation \
  MANIFEST=benchmark/experiments/exp001-ablation-3to1.json

# Fase 2 — serie A
make benchmark DATASET=~/Desktop/BMD SERIES=A RUN=exp001-series-a-control \
  MANIFEST=benchmark/experiments/exp001-control-3to1.json
make benchmark DATASET=~/Desktop/BMD SERIES=A RUN=exp001-series-a-ablation \
  MANIFEST=benchmark/experiments/exp001-ablation-3to1.json

# Comparación ablación vs control (única diferencia autorizada: experimental_env)
python -m tools.fidelity compare \
  out/benchmark/runs/exp001-series-a-control \
  out/benchmark/runs/exp001-series-a-ablation \
  --allow-setup-differences --dest out/benchmark/comparisons/exp001-ablation-vs-control

# Trazas (no alteran caché de BRAW)
make benchmark DATASET=~/Desktop/BMD TEST=A04 RUN=exp001-a04-control-trace \
  MANIFEST=benchmark/experiments/exp001-control-3to1-trace.json
```

Fase 3 (q fijo): ajustar `q` en `exp001-*-fixed-q.json` al valor típico del control 3:1 y repetir A04.

## 8. Resultados medidos

Worker idéntico en todas las corridas: `17cb31433336…`. Baseline `main-352cfab` no modificado.
Única diferencia de setup autorizada en comparaciones: `encode.experimental_env.CDNG_OG_DEBLOCK_PRECOMP`.

### Serie A @ 3:1 (medias de 6 fotogramas)

| Métrica | Control | Ablación | Diferencia | Interpretación |
|---|---:|---:|---:|---|
| PSNR normalizado | 42.567 | 42.593 | +0.026 | Ligera mejora global (confunde con q↓) |
| RMSE normalizado | 0.007442 | 0.007420 | −0.000022 | Idem |
| Δu'v' en bordes | 0.01489 | 0.01415 | −0.00074 | Menos error cromático en bordes |
| Δu'v' en regiones neutras | 0.01085 | 0.01041 | −0.00044 | Ligera mejora |
| Cromaticidad inválida (bordes) | 0 | 0 | 0 | Sin cambio |
| Overshoot / Y==4095 (traza A04) | 61 px (0.003 %) | 0 | −61 | Sin precomp no hay clip de Y en lienzo |
| Muestras recortadas (techo RGB) | 0.000820 | 0.000753 | −0.000067 | Ligera bajada |
| Error frontera−interior bloques | −9.4e−5 | +2.4e−5 | +1.2e−4 | Ablación empeora fronteras (bloques) |
| Energía de gradiente (ratio) | 1.007 | 0.918 | −0.089 | Ablación más suave en bordes |
| Ratio efectivo | 3.035 | 3.053 | +0.018 | Ambos ~3:1 |
| q medio | 17.8 | 14.8 | −3 | **Confusión:** ablación cuantiza más fino |
| Tiempo total serie A (s) | 33.4 | 29.3 | −4.1 | Menos trabajo de rate search |

### A04 @ q fijo 18 (elimina la confusión de cuantización)

| Métrica | Control | Ablación | Diferencia | Interpretación |
|---|---:|---:|---:|---|
| PSNR normalizado | 42.520 | 42.512 | **−0.008** | Sin q variable, ablación **empeora** levemente |
| RMSE normalizado | 0.007482 | 0.007489 | +0.000007 | Idem |
| Δu'v' bordes | 0.01498 | 0.01430 | −0.00069 | Sigue mejorando el croma de bordes |
| Δu'v' neutras | 0.01086 | 0.01047 | −0.00039 | Ligera mejora |
| Energía de gradiente | 1.006 | 0.917 | −0.090 | Pérdida de nitidez/contraste local |
| Error frontera−interior | −9.6e−5 | +1.0e−5 | +1.1e−4 | Más error en fronteras de bloque |
| Ratio efectivo | 3.03 | 3.28 | +0.25 | Sin precomp cabe peor a mismo q |

Informes HTML:

- `out/benchmark/runs/exp001-series-a-control/report.html`
- `out/benchmark/runs/exp001-series-a-ablation/report.html`
- `out/benchmark/comparisons/exp001-series-a-ablation-vs-control/comparison.html`
- `out/benchmark/comparisons/exp001-a04-q18-ablation-vs-control/comparison.html`
- Trazas: `out/benchmark/experiments/exp001/traces/`

## 9. Conclusiones

### Hipótesis confirmadas

1. La precompensación actúa **solo sobre Y** y puede producir códigos >4095 que `rounded` recorta (61 px en A04 control; 0 en ablación).
2. Desactivarla **cambia el q** elegido a ratio 3:1 (≈18 → ≈15): hay efecto indirecto vía complejidad.
3. A **q fijo**, el PSNR frente a CinemaDNG **no mejora** al quitar la precompensación; el error en fronteras de bloque aumenta y cae el ratio de energía de gradiente.

### Hipótesis parcialmente confirmadas

4. Δu'v' en bordes y neutros baja al ablar (con y sin q fijo) → la precompensación **contribuye** a error cromático en bordes, pero la magnitud es pequeña (~0.0007).
5. Escenario más cercano: **B** (compromiso) con matices de **C**: los halos cromáticos mejoran poco; no justifica eliminar el componente.

### Hipótesis descartadas (con estos datos)

6. Escenario A (mejorar sin coste espacial relevante): descartado a q fijo.
7. Escenario D (empeorar casi todo): descartado; el croma de bordes mejora.

### Abiertas

- ¿Una precompensación **acotada** (clamp antes de redondear, o ganancia <1) retiene el anti-bloque y reduce overshoot?
- ¿El residual de halo tras ablación viene de submuestreo/reconstrucción de croma o del debayer? (BUG-002 / EXP siguiente)
- Percepción en letras del ColorChecker: métricas ROI mixtas; hace falta inspección visual de los PNG del informe.

## 10. Recomendación para el siguiente experimento

**No eliminar** la precompensación en producción. Priorizar:

1. **EXP-001b (opcional):** precompensación con saturación suave / factor <1 sobre Y (sigue siendo ablación paramétrica, no un fix definitivo).
2. **BUG-002 / EXP croma:** submuestreo y reconstrucción de Cb/Cr (la Δu'v' residual tras ablación sigue ~0.014).
3. **EXP-002:** estudio de cuantización/ratio, ahora que sabemos que q confunde ablaciones de señal.

No merge ni push de esta rama sin autorización.
