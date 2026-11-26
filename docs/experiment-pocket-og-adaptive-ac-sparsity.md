# EXP-003 — Adaptive AC Sparsity

**Rama:** `experiment/pocket-og-adaptive-ac-sparsity`  
**Commit de partida:** `4fb9331bd9210712b77862879d869e1d68ce610c` (main con EXP-002a)  
**Estado:** implementación + fases A/B/C/D/J en A04; E–I/K y G formal pendientes  
**Resumen numérico:** `benchmark/experiments/exp003-results-summary.json`

## 1. Objetivo

Política experimental de deadzone AC selectiva por plano y frecuencia espacial, compatible con el bitstream y el decoder existentes, para mejorar la frontera rate–distortion hacia 6:1–8:1 sin forzar ratio a costa de textura/bordes/croma.

## 2. Correcciones de infraestructura (EXP-002a)

| Tema | Cambio |
|------|--------|
| `attempt_index` | Se fija en el intento que produce el `EncodedFrame`; no se sobrescribe al seleccionar/descartar. `stats.attempts` sigue siendo el acumulado del rate search. |
| Sample escrito | `encodeStatsPublish(..., "sample_written")` **después** de `frameChunk`, con `chunk_sha256` + `payload_sha256`. Los informes usan `stage=sample_written`. |
| Gate alineación | `geometric_aligned`, `detail_gate_pass`, `quality_rejected` en `check_alignment`. Umbral histórico 0.74 intacto. Variante alineada con detalle degradado ≠ desalineación geométrica. |

## 3. Auditoría del cuantizador

- **Fórmula:** `level = sign(c) · floor(|c|/step + 0.5 − z)`; `z=0` → `nearbyint` (empates históricos).
- **Deadzone:** solo AC vía `quantizeAc(coef, step, plane, dctIndex)`. DC no pasa por esta ruta (cadena DC aparte).
- **Planos:** Y (`ScanY`, bloques 8×8), Cb/Cr (`ScanC`, 4×8). Índice DCT = `u*8+v`.
- **Scan ≠ zigzag JPEG:** ver `codec_tables.cpp`.
- **Clamping/dropping/VLC/noise synthesis:** sin cambios; la política solo elige `z` antes del VLC.
- **Decoder:** pasos de reconstrucción intactos; solo cambian los niveles enteros codificados.

## 4. Arquitectura de política

`z = deadzone(plane, u, v)` con tablas precalculadas 64 entradas/plano.

| Modo | Descripción |
|------|-------------|
| legacy | Sin `CDNG_DEADZONE_POLICY`; `CDNG_DEADZONE` uniforme o 0 |
| uniform | JSON `mode=uniform` |
| per_plane | `z` por Y/Cb/Cr |
| per_band | bandas por frecuencia normalizada |
| per_coefficient | opcional por `(u,v)` |

- DC forzado a `z=0`.
- Solo Pocket OG (`CDNG_DEADZONE_POLICY` rechazado en VA/Pocket 4K).
- Conflicto `CDNG_DEADZONE` ∩ `CDNG_DEADZONE_POLICY` → Error (sin fallback silencioso).
- Caché: `effective[CDNG_DEADZONE_POLICY] = path#sha256`; worker recibe path.

Frecuencia normalizada: `‖(u/umax, v/vmax)‖ / √2`, `umax=7` (Y) / `3` (croma). Bandas: low `[0,0.25)`, mid `[0.25,0.5)`, high `[0.5,1]`. Mapas: `benchmark/experiments/exp003/band-maps.json`.

## 5. Equivalencia (pruebas nativas)

En `engine_codec_test` (sin golden):

- Política off ≡ encoder original.
- Uniforme JSON ≡ `CDNG_DEADZONE` para `z ∈ {0,0.1,0.25,0.5}` (payload idéntico).
- DC fuera de política; bandas/frecuencias coherentes.
- Conflicto de configuración rechazado.

Banco Python: esquema/política/caché SHA, separación geometría/detalle.

## 6. Resultados experimentales (A04, worker experimental)

Controles (media f044+f049):

| Control | α | q | DZ/NR | Ratio frame | Sample B | PSNR | Detail | Grad |
|---------|---|---|-------|-------------|----------|------|--------|------|
| A0 | 1 | 24 | 0 / 0 | 3.39 | 917504 | 42.43 | 0.790 | 1.007 |
| A1 | 0.75 | 24 | 0.5 / 0 | 4.87 | 638976 | 43.23 | 0.744 | 0.931 |
| A2 | 0.75 | 24 | 0.25 / NR50 | 4.87 | 638976 | 43.05 | 0.745 | 0.949 |
| A3 | 0.75 | 18 | 0 / 0 | 3.01 | 1032192 | 42.68 | 0.809 | 0.983 |

- `b-u50` (política uniforme z=0.5) ≡ A1 byte a byte en métricas agregadas → equivalencia operativa confirmada en A04.
- Matriz B (zY×zC) y políticas D1–D5: a **q fijo 24**, ninguna mejora PSNR a tamaño ≤ A1. Configuraciones con más detalle/gradiente suelen **aumentar** bytes (p. ej. `d5`, `b-y00-c50`).
- Mejor ratio frame medido en esta tanda ≈ **4.87:1** (uniforme z=0.5). No 6:1 ni 8:1.

### Fase J (rate search, política D1)

| Pedido | Ratio frame | q | Notas |
|--------|-------------|---|-------|
| 3:1 | 2.92 | 10 | Cabe; buena detail_corr ≈0.81 |
| 5:1 | 4.63 | 24 (QMax) | No cumple presupuesto 5:1 |
| 8:1 | 4.63 | 24 | Saturación QMax |
| 12:1 | 4.63 | 24 | Saturación QMax |

A1 uniforme sigue comprimiendo más (~4.87) que D1 en QMax (~4.63).

## 7. Respuestas (1–12)

1. **¿DZ por plano > uniforme?** **Parcialmente descartada** a q fijo en A04: no mejora PSNR a igual o menor tamaño. Pendiente comparación a igual tasa (fase G).
2. **¿DZ por frecuencia?** **Parcialmente descartada** en D1–D5 a q fijo; D1/D5 no superan A1 en frontera PSNR–bytes. Pendiente refinamiento F/G.
3. **¿Bandas que más bits?** Pendiente instrumentación por banda (fase E). A nivel plano: Y ~76 % (EXP-002a).
4. **¿Qué proteger?** Tentativo: bajas (estructura) y mid de Y para detalle; evidencia formal pendiente E/F.
5. **¿Qué plano tolera sparsidad?** Tentativo: croma tolera más sparsidad en bits absolutos, pero `d4` (Y agresivo) empeora PSNR vs A1; `d5` (croma agresivo) gasta más bits. Pendiente G.
6. **¿Mejora a igual tasa?** **Pendiente** (fase G). A igual q: no demostrada.
7. **Mejor ratio:** ≈ **4.87:1** (A1 / uniforme z0.5). D1 en rate search satura ~4.63:1.
8. **¿6:1?** No.
9. **¿8:1?** No (pedido 8/12 → QMax ≈4.63).
10. **Coste visual:** A1 ya degrada detail_corr (~0.74, en el umbral) y gradiente (~0.93). Políticas más suaves recuperan detalle a costa de ratio.
11. **¿Serie A?** **Pendiente** (fase K).
12. **RDOQ:** hace falta atribución incremental de coste VLC por coeficiente/banda (fase E), λ calibrada a igual tasa, y no alterar tablas/scan/VLC del SDK.

Clasificación: **confirmadas** (infra, política, equivalencia uniforme, A0–A3, J saturación, no-6/8); **parcialmente confirmadas** (plano/banda no ganan a q fijo); **pendientes** (E–I, G formal, K, temporal).

## 8. Reproducción

```bash
git checkout experiment/pocket-og-adaptive-ac-sparsity
make worker
PYTHON=out/.venv-exr/bin/python tools/exp003_sweep.py --phase a \
  --dataset ~/Desktop/BMD --references ~/Desktop/EXR \
  --converter build/worker/converter --brawdec tools/brawdec/brawdec
# fases: a | b | d | j | all
PYTHON=out/.venv-exr/bin/python tools/exp003_report.py
CDNG_RESOURCES=$PWD ./build/native/lib/engine_codec_test
```

## 9. Propuesta EXP-003b — RDOQ

No implementar aún. Base mínima:

1. Coste VLC incremental (run length compartido; no atribuir el run entero a un coef).
2. Distorsión DCT → RGB/ROI acotada.
3. Búsqueda por coeficiente solo en AC no-DC, con `z` o level ∈ permitidos por el formato.
4. Comparación obligatoria a igual tasa frente a uniforme z=0.5 y mejor política EXP-003.
5. Desactivada por defecto; solo Pocket OG.

## 10. Restricciones respetadas

Sin cambios a tablas SDK, DCT, Scan, VLC, QMax, rate search, DC, debayer, NR algorithms, noise synthesis, precompensación, defaults de producción. Sin push/merge.
