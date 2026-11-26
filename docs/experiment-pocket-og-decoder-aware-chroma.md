# EXP-005b — Decoder-aware Chroma Reconstruction: Feasibility

**Rama:** `experiment/pocket-og-decoder-aware-chroma`  
**Base:** `main` @ `81cc49a` (PR #10 EXP-005 **mergeada**). Este trabajo **no** fusiona PRs.  
**Encoder por defecto:** AHD, C0, NR/síntesis/RDOQ OFF en las corridas experimentales. Defaults de producción intactos.  
**Alcance:** viabilidad A–G. **No** algoritmo de producción. **No** EXP-006/007/008. **No** cambio de SDK/formato. **No** investigación extra de compresión.

Resumen numérico: `benchmark/experiments/exp005b-results-summary.json`. Artefactos voluminosos → `out/`.

Leyenda: **[código]** en el árbol; **[exp]** medido; **[lim]** limitación.

---

## Decisión

**Clase E.** Evidencia insuficiente sobre el SDK (S8). Hay caracterización S7 y un candidato lineal separable; no se midió la respuesta Blackmagic RAW SDK.

No se declara imposibilidad matemática global a partir de un oráculo local o un modelo aproximado.

---

## A — Neutro en espacio cámara

Transformaciones: `NativeSensor` = curva del JSON OG + `inv(code_from_ycc)`; `cameraToP3` documentada (EXP-005). AsShotNeutral de la serie A: `[0.6306, 1.0, 0.7728]`.

| Hecho | Valor |
|-------|-------|
| `camera R=G=B` → croma de código | max `|Cb,Cr|` = 0 **[exp]** |
| Gris P3 vía `inv(cameraToP3)` vs `∝ AsShotNeutral` (cromaticidad lineal) | max = 8.44e-05 |
| Croma de código a lo largo de un rampa P3-gris | rango = 261 |
| Borde `R=G=B` en P3 (no es neutro de salida) | chroma P3 max = 0.4647 |

Conclusión A (medida, no asumida a priori):

- `R=G=B` en cámara es acromático en el códec (`Cb=Cr≈0`) y **no** es gris en P3.
- `∝ AsShotNeutral` y `inv(cameraToP3)·gris P3` coinciden en cromaticidad **hasta ~1e-4** (cameraToP3(ASN) solo es gris P3 de forma aproximada, tolerancia Bradford/McCamy). No son idénticos.
- Tras la **curva por canal**, una rampa de gris P3 **tiene Cb/Cr que dependen de la intensidad**. Esa croma espacialmente variable existe ya en el dominio de codificación, antes de 4:2:0/DCT/SDK.

C0 (media RGB 2×2 y luego el mapa) no cancela esa croma de código. Separación: curva (origen principal en neutros de salida) vs subsampling/DCT/SDK (B, oráculo).

---

## B — Caracterización efectiva del decoder

`CDNG_INJECT_PLANES` (BRWP) sustituye Y/Cb/Cr **después** de `finishPlanes` y **antes** de `canvasPlanes`/encode. Pocket OG. Geometría, VLC, DCT, QMax, ISO y metadata del DNG se conservan. Identidad de caché: `path#sha256` como `CDNG_INJECT_RGB`.

| Operador | separable | lóbulos neg. | cross-channel | notas |
|----------|-----------|--------------|---------------|-------|
| s7_nn | True | False | 0 | soporte 4 px |
| bilinear_420 | True | False | 0 | soporte 16 px |

S7 (`decodedPlanes`) **no** interpola croma: copia 2×2. No es la respuesta SDK. El bilinear es un **candidato** lineal separable; **[lim]** sin S8 no se elige el modelo del SDK.

Dependencia de amplitud en ambos candidatos: error relativo ~0 (lineales). La no linealidad del revelado (curva + WB) aparece al pasar a P3, no en el upsample de códigos.

---

## C — Oráculo (Y fija)

ROI 32×32, upsample = bilinear candidato. C0 = producción. O1 = LS; O2 = LS ponderado por borde/croma; O3 = clip legal de O1 + laplaciano débil (se descarta si suaviza de más).

| | p99 halo | integral | luma corr | legal |
|--|----------|----------|-----------|-------|
| C0 | 0.06166 | 2.715 | — | sí |
| O1 continuo | 0.04105 | 3.62 | 1.0000 | True |
| O1 cuantizado | 0.04101 | 3.748 | 1.0000 | True |
| O3 | 0.03721 | 3.789 | 1.0000 | True |

Reducción O3 vs C0: p99 = 39.6 % ; integral = -39.6 % (modelo aproximado, **no** óptimo SDK).

Held-out (otra orientación / colores saturados): p99 red. = -17.3 % / 31.9 %.

---

## D — Y conjunta

O1–O3 ya operan con Y fija. Un ΔY acotado no se aplica: desenfocar luma para igualar croma está prohibido. Se informa need_joint=true si el umbral exploratorio no se alcanza en el modelo bilinear, no como óptimo SDK.

---

## E — Validación

q2 C0 AHD, NR/síntesis/RDOQ OFF previstos. Fixtures A + A04_f044 / A04_f049 / A01_f052 / ROI BUG-002.

Campaña SDK: **not_run**. campaña S8 no ejecutada en este informe

Resolve puede usarse como oráculo offline; **no** es dependencia de un algoritmo futuro. Cualquier candidato de producción debe derivarse de S2 + metadata CinemaDNG.

Umbral exploratorio 50 % p99+integral: **no** cierra BUG-002 ni sustituye aceptación visual.

---

## F — Robustez del revelado

No hay CinemaDNG equivalentes a ±2 EV / WB 3200/6500 / tint en el banco; un BRAW único decodificado con esos ajustes no tiene referencia DNG par. No se declara equivalencia.

---

## G — Viabilidad

Clase **E**. Evidencia insuficiente sobre el SDK (S8). Hay caracterización S7 y un candidato lineal separable; no se midió la respuesta Blackmagic RAW SDK.

Opciones restantes (no implementadas): Revelado SDK no lineal / no separable no capturado por bilinear.; DCT/QMax residual a q>2.; Cambio de representación (no 4:2:0) — fuera de alcance; no implementado.; Oráculo por iteración encode/decode SDK acotada.

BUG-002 sigue P0.

