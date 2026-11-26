# EXP-005b — Decoder-aware Chroma Reconstruction: Feasibility

**Rama:** `experiment/pocket-og-decoder-aware-chroma`  
**Base:** `main` @ `81cc49a` (PR #10 EXP-005 **mergeada**). Este trabajo **no** fusiona PRs.  
**Encoder por defecto:** AHD, C0, NR/síntesis/RDOQ OFF en las corridas experimentales. Defaults de producción intactos.  
**Alcance:** viabilidad A–G. **No** algoritmo de producción. **No** EXP-006/007/008. **No** cambio de SDK/formato. **No** investigación extra de compresión.

Resumen numérico: `benchmark/experiments/exp005b-results-summary.json`. Artefactos voluminosos → `out/`.

Leyenda: **[código]** en el árbol; **[exp]** medido; **[lim]** limitación.

---

## Decisión

**Clase D.** Ningún candidato S8 (mismo par p99+integral) es útil. No es inviabilidad global.

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

S7 (`decodedPlanes`) **no** interpola croma: copia 2×2. El bilinear es un candidato; **no** se asume que el SDK lo use.

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

q2 C0 AHD, NR/síntesis/RDOQ OFF. Fixture P3 + A04_f044 + ROI `white_strip` (BUG-002).

Campaña SDK: **measured**. 

Resolve puede usarse como oráculo offline; **no** es dependencia de un algoritmo futuro. Cualquier candidato de producción debe derivarse de S2 + metadata CinemaDNG.

Umbral exploratorio 50 % p99+integral: **no** cierra BUG-002 ni sustituye aceptación visual.

---

## F — Robustez del revelado

No hay CinemaDNG equivalentes a ±2 EV / WB 3200/6500 / tint en el banco; un BRAW único decodificado con esos ajustes no tiene referencia DNG par. No se declara equivalencia. q18/WB/EV no se barrió sin candidato prometedor.

---

## S8 (B2/A2/C2) — encode/decode real

Impulsos empaquetados en un frame 1920×1080, restando control plano. q=2, clip A04 f44–45. **[exp]**

| S8 | valor |
|----|-------|
| sitios croma | 16 |
| soporte medio (px) | 52.5 |
| lóbulos negativos | True |
| acoplamiento Y→croma | 0.002538 |

No se asume bilinear. S7 sigue siendo NN 2×2.

### A2 — bordes en S8

Hipótesis del usuario (halo exclusivo blanco/negro): **False**. Más fuerte: `isoluminant`. cam_equal_hi p99=0.15980953559709732, p3_hi=0.07020608752276034, asn_hi=0.07020608752276034, isoluminante=1.6970260084993667. corr(contraste, p99)=-0.333; corr(dist ASN, p99)=0.218. Isoluminante en Y de códec: |ΔY|=0.02103091277058411 (lo=1200.3164739480317, hi=1200.3375048608023).

### C2 — oráculo S8 (borde P3, Y fija, O1 bilinear en ROI)

p99 red. = -132.9 %; integral = -496.9 %. Redistribución rechazada: False. Pérdida cromática excesiva: False.

D2 ΔY rms=2.587e-08 max=9.501e-07, fracción tocada=0.00104, p99 red. -132.9 %.

A04_f044 ROI white_strip p99 croma S8 C0 = 0.4850063645645431. ROI BUG-002 medido sobre S8 C0; sin EXR de WB/EV variantes.

### H2 — oráculo de baja dimensión (SDK, no bilinear)

Evaluaciones contadas: 23 / 100. cond(J)=126.57583230100691, rango efectivo@1e-3=12. Interferencia rel=0.0.

Mejor candidato `c0`: p99 red. 0.0 % ; integral 0.0 % (mismo par; mesetas SDK).

Parada: no_descent. Gauss-Newton: [{'iter': 0, 'pred_drop': 0.397453130293285, 'actual_drop': 0.0, 'rho': 0.0, 'red': {'p99': 0.0, 'integrated': 0.0}, 'lam': 0.01, 'x_rms': 0.6690598295395876}, {'iter': 1, 'pred_drop': 0.33327631172089967, 'actual_drop': 0.0, 'rho': 0.0, 'red': {'p99': 0.0, 'integrated': 0.0}, 'lam': 0.04, 'x_rms': 0.450264831469008}].

H3 conjunto: ΔY rms código=0.02018, grad_rel=0, p99 red. 0.0 %.

H4: omitida (sin mejora sustancial; no se barrió fase/horizontal/q18/WB).

### H6 — Cierre (no imposibilidad global)

- **Error irreducible observado (esta parametrización, q2, fixture P3 vertical):** C0 sigue siendo el mejor candidato medido; p99 mesetas=0.03177, p99 vs cameraToP3=0.03367.
- **Parametrización:** 6 columnas Cb + 6 Cr compartidas en una banda vertical; H3 añadió 4 ΔY pares. No cubre filas independientes ni un soporte más ancho.
- **Optimizador:** Gauss-Newton regularizado predijo descenso [0.397453130293285, 0.33327631172089967]; descenso real [0.0, 0.0] (ρ=0). Parada `no_descent` tras intentos acotados.
- **Cuantización:** rms |Δcódigo| propuesto [0.6690598295395876, 0.450264831469008] frente a ε_C=64.0. Por debajo de un código a q=2 el encode/decode no movió el residuo.
- **Condicionamiento:** Cb/Cr cond(J)=126.6, rango efectivo@1e-3=12, smin=0.002502. Conjunto Y cond=807.8, smin=0.0003923. No se invirtió un operador singular sin λ.
- Interferencia espacial rel=0.0 (agrupación de FD no usada). Evaluaciones contadas 23/100, 12.8 s de encode/decode.

---

## G — Viabilidad

Clase **D**. Ningún candidato S8 (mismo par p99+integral) es útil. No es inviabilidad global.

Opciones restantes (no implementadas): Parametrización más rica que 6+6 columnas Cb/Cr compartidas y 4 ΔY pares (filas independientes, soporte más ancho).; Pasos del optimizador por encima del bin de cuantización (esta campaña: rms Δcódigo < 1).; DCT/QMax residual a q>2.; Cambio de representación (no 4:2:0) — fuera de alcance; no implementado.

BUG-002 sigue P0.

