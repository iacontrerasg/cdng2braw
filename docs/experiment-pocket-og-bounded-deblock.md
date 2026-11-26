# EXP-001b — Precompensación antibloques proporcional y restringida

## Control de versiones

| Campo | Valor |
|---|---|
| Rama | `experiment/pocket-og-bounded-deblock` |
| Parte de | `main` @ `40e1e547307576492fb6f01c457d36fd4da938d5` (merge PR #3 / EXP-001) |
| Worker de las corridas | `98ce2358f132…` |
| Baseline EXP-001 | Conservado (docs + `out/benchmark/runs/exp001-*`) |
| Por defecto | Idéntico a producción / EXP-001 (`PRECOMP` on, α=1, bounded off) |

## 1. Motivación

Ver EXP-001: la ablación mejora poco el croma de bordes (~5 %) a costa de gradiente (~9 %) y fronteras de bloque; a q=18 no mejora PSNR; la inversa exacta genera overshoot (61 `clamped_high` en A04_f044).

## 2. Infraestructura (antes del experimento)

1. `trace` se recalcula tras `rename` del tmp de conversión nueva; SHA-256 del fichero sellado se verifica al publicar y al exigir trazas.
2. Equivalencia en capas (`braw_inspect.equivalence`): payload; metadata de revelado = payload + `tkhd`/`stsd`/`stsz` (se ignoran UUID/udta/tiempos del moov); RGB float opcional. **mdat igual no basta** si esa metadata de revelado difiere.
3. Encoder/contenedor: sin cambios de determinismo.

## 3. Variables

| Variable | Valores | Rol |
|---|---|---|
| `CDNG_OG_DEBLOCK_PRECOMP` | `1`/`on` (defecto), `0`/`off` | Master |
| `CDNG_OG_DEBLOCK_ALPHA` | `0`, `0.25`, `0.5`, `0.75`, `1` | Proporcional |
| `CDNG_OG_DEBLOCK_BOUNDED` | `0` (defecto), `1` | Pares acotados |

Interacción: `PRECOMP=0` ⊕ `ALPHA`; `BOUNDED` ⊕ `ALPHA`; `PRECOMP=0` ⊕ `BOUNDED=1`. Solo Pocket OG. Cb/Cr intactos. `precompensate()` no se modifica.

## 4. Fase A — Proporcional

\(S_\alpha = S + \alpha(S_\mathrm{comp}-S)\) antes de redondeo/clamp. α=0 ≡ ablación; α=1 ≡ original (SHA de imagen idénticos en A04).

## 5. Fase B — Estrategia **B** (pares acotados)

Elegida frente a A (headroom global) y C (QP en cruces): conserva la inversa exacta si es representable; si no, MC acotados 0…4095 por frontera en el mismo orden H→V. Aproximación: acoplamiento H/V secuencial (como el encoder). Evaluación final con SDK, no solo `decodedPlanes`.

## 6. Sintéticos (modelo interno)

```bash
CDNG_EXP001B_SYNTHETIC=out/benchmark/experiments/exp001b/synthetic/report.json \
  ./build/native/lib/engine_signal_test
```

Hallazgos: bounded anula `math_above`/`clamped_high` en B↔N, impulsos, gradiente y alta frecuencia; en gradiente/alta frecuencia baja el MSE de reconstrucción vs inversa+clamp. En escalones suaves bounded ≡ original.

## 7. A04 @ q=18 (medias f044+f049)

| Variante | PSNR | Δu'v' bordes | Grad. | blockΔ MAE | Y clamped_high f044 |
|---|---:|---:|---:|---:|---:|
| Control (α=1) | 42.520 | 0.014983 | 1.0065 | −9.6e−5 | **61** |
| Ablación / α=0 | 42.512 | 0.014297 | 0.9166 | +1.0e−5 | 0 |
| α=0.25 | 42.672 | 0.014435 | 0.9374 | −1.2e−4 | — |
| **α=0.50** | **42.730** | **0.014600** | **0.9595** | **−1.8e−4** | **9** |
| α=0.75 | 42.676 | 0.014782 | 0.9826 | −1.8e−4 | — |
| Bounded | 42.524 | 0.014983 | 1.0073 | −9.7e−5 | **0** |

Identidades: α=0 ≡ ablación; α=1 ≡ control (SHA candidato idénticos).

## 8. Serie A

### q fijo 18 (6 fotogramas)

| Variante | PSNR | Δu'v' bordes | Grad. | blockΔ |
|---|---:|---:|---:|---:|
| Control | 42.567 | 0.014893 | 1.0068 | −9.5e−5 |
| Ablación | 42.556 | 0.014208 | 0.9181 | +1.0e−5 |
| **α=0.50** | **42.770** | **0.014513** | **0.9604** | **−1.8e−4** |
| Bounded | 42.573 | 0.014893 | 1.0078 | −9.6e−5 |

### Ratio 3:1 (q libre)

| Variante | PSNR | Δu'v' bordes | Grad. | q medio | ratio |
|---|---:|---:|---:|---:|---:|
| Control | 42.567 | 0.014893 | 1.0068 | 17.8 | 3.035 |
| Ablación | 42.593 | 0.014150 | 0.9180 | **14.9** | 3.053 |
| **α=0.50** | **42.784** | **0.014494** | **0.9605** | 16.4 | 3.063 |
| Bounded | 42.573 | 0.014893 | 1.0079 | 17.8 | 3.035 |

A 3:1 la ablación sigue confundida por q↓. α=0.50 mejora PSNR también a q fijo (efecto directo del algoritmo).

## 9. Rendimiento

Conversiones nuevas A04 q18: ~9.3–9.8 s total/variante (2 tramos). Bounded y α no añadieron coste medible frente al control a esta resolución. Complejidad bounded: O(fronteras) con búsqueda constante por par (aristas+esquinas). Memoria: una copia de Y solo en la ruta proporcional α∉{0,1}.

## 10. Artefactos (locales, no en el repo)

- Informes: `out/benchmark/runs/exp001b-*/report.html`
- Comparaciones: `out/benchmark/comparisons/exp001b-*-vs-control/`
- Sintéticos: `out/benchmark/experiments/exp001b/synthetic/report.json`
- Resúmenes: `out/benchmark/experiments/exp001b/*-summary.json`
- Trazas: `out/benchmark/runs/exp001b-a04-*-q18-trace/logs/`

PNG de perfiles/zooms ya generados por el banco en cada run (`reference_fidelity.png`).

## 11. Clasificación

### Confirmadas

1. α=0 ≡ ablación; α=1 ≡ original (identidad de imagen).
2. Parámetros inválidos/contradictorios se rechazan (nativo + banco).
3. Solo Y / solo Pocket OG.
4. Bounded elimina overshoot pre-códec (`clamped_high=0` en A04) sin degradar PSNR ni gradiente frente al control.
5. α=0.50 mejora PSNR a q fijo y reduce clipping (61→9) y Δu'v' parcial, con pérdida de gradiente menor que la ablación y **mejor** error de fronteras 8×8 que el control.

### Parcialmente confirmadas

6. Bounded ≈ control en métricas SDK: el overshoot eliminado no se traduce en ganancia cromática medible en A04/serie A.
7. Frontera de Pareto útil en α∈{0.25,0.50,0.75}; el “mejor compromiso” no es único (α=0.50 maximiza PSNR+bloques; α=0.25 prioriza más croma).

### Descartadas

8. Que bounded por sí solo resuelva el croma de bordes de EXP-001.
9. Que cualquier α domine en todas las métricas a la vez.

### Pendientes

10. Inspección visual sistemática (letras ColorChecker, caja metálica) — PNG disponibles en los runs.
11. Optimización acoplada H+V en cruces (candidato C) si se quiere exprimir bounded.
12. Percepción a ratio constante con α=0.50 (q intermedio 16.4).

## 12. Recomendación

- **Producción:** mantener el algoritmo original por defecto.
- **Candidato experimental más prometedor:** `CDNG_OG_DEBLOCK_ALPHA=0.5` (con `PRECOMP=1`).
- **Bounded:** útil como seguro anti-overshoot / depuración; no sustituye al control en fidelidad SDK.
- **No** promover a producción sin autorización ni inspección visual adicional.

## 13. Reproducción

```bash
make worker brawdec
# A04 barrido α + bounded
make benchmark DATASET=~/Desktop/BMD TEST=A04 SERIES= RUN=exp001b-a04-alpha-0.50-q18 \
  MANIFEST=benchmark/experiments/exp001b-alpha-0.50-q18.json
# Serie A
make benchmark DATASET=~/Desktop/BMD SERIES=A RUN=exp001b-series-a-alpha-0.50-q18 \
  MANIFEST=benchmark/experiments/exp001b-alpha-0.50-q18.json
make benchmark DATASET=~/Desktop/BMD SERIES=A RUN=exp001b-series-a-alpha-0.50-3to1 \
  MANIFEST=benchmark/experiments/exp001b-alpha-0.50-3to1.json
```

Manifiestos: `benchmark/experiments/exp001b-*.json`.

No merge ni push sin autorización.
