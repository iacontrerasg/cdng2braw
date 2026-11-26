# FEAT-001b — CinemaDNG Gen 4 Workflow Match

**Rama:** `feat/pocket-og-cinemadng-workflow` (sigue PR #13). **Sin merge** hasta revisión.  
**Alcance de esta entrega:** caracterización **A** e instrucciones **B**. Sin LUT nueva. Sin cambio de checkbox, defaults, Camera Support ni modelo ISO. Sin LUT embebida. Sin halos/compresión.

## Corrección de producto

El flujo habitual del usuario en Resolve para CinemaDNG Pocket OG **no** es Gen 1:

| | Color Science | Color Space | Gamma |
|---|---|---|---|
| **Objetivo CinemaDNG** | **Gen 4** | Blackmagic Design | Blackmagic Design Film |
| **Objetivo BRAW** | **Gen 5** (única listada en el clip OG) | Blackmagic Design | Blackmagic Design Film |

DaVinci Wide Gamut / DaVinci Intermediate **no** es el revelado obligatorio: en Camera RAW BRAW deja inactivos buena parte de los Gamma Controls. La LUT Gen 1 `Pocket 2013 DNG Film from DWG Intermediate` queda como **alternativa legacy** (FEAT-001). El checkbox *Match original CinemaDNG rendering* **no se modifica** hasta validar la transformación Gen 4.

No hornear ninguna transformación en el BRAW.

---

## A — Caracterización (sin EXR Film nuevos)

Nada de lo siguiente sustituye un par DNG/BRAW revelado en Film. Distingue **nombres**, **API de tono** y **pipeline de generación**.

### Qué controla cada lado

| | CinemaDNG Pocket OG | BRAW Pocket OG (nuestro encoder) |
|---|---|---|
| Color science | Resolve: **Gen 4** (ruta CinemaDNG del banco) | Clip: `csgn` **solo {5}**. `SetClipAttribute(csgn)` a 4 no está en la lista |
| Gamut «Blackmagic Design» | Decoder DNG | SDK: nombre listado en `gamt` (16 opciones) |
| Gamma «Blackmagic Design Film» | Decoder DNG | SDK: nombre listado en `gama` (17 opciones) |
| Viewing al abrir | Default Resolve del DNG (BMD / Film en el flujo del usuario) | Metadata: P3-D65 **Linear**, `viewing_bmdgen` = 5 |
| Gamma Controls (Contrast, Saturation, Midpoint, Highlights, Shadows, Black/White) | Panel CinemaDNG | Activos si `gama` es curva Film/Custom del SDK; **no** si Intermediate/Linear |
| Color Boost / Midtone Detail | Presentes en CinemaDNG | **Ausentes** del panel BRAW |
| Highlight Recovery | Independiente | `hlry`; default OFF en el banco |
| LUT | No hace falta para Film nativo | FEAT-001: cubo DWG→Film Gen 1 (legacy, **otro** look) |

El nombre **Blackmagic Design Film no identifica la misma función** en ambos formatos: es una etiqueta de UI/SDK. El decoder CinemaDNG Gen 4 y el decoder BRAW Gen 5 son productos distintos ([image-fidelity-benchmark.md](image-fidelity-benchmark.md)).

### API de tono (`GetToneCurve`, cámara `Blackmagic Pocket Cinema Camera`)

Medido con `brawdec --tonecurve` (BlackmagicRawAPI de Resolve):

| Gamma | gen | Resultado |
|---|---|---|
| Blackmagic Design Film | 1, 4 y 5 | **Mismos** knobs: contrast 1, saturation 1, midpoint **0.383562**, highlights 1, shadows 1, black 0, white 1, videoBlack 0 |
| Blackmagic Design Custom | 5 | midpoint **0.409008** (distinto de Film) |
| Blackmagic Design Video | 5 | `0x80000003` |
| DaVinci Intermediate | 5 | `0x80000003` |
| Linear | 5 | `0x80000003` |
| Film, `EvaluateToneCurve` | 1/4/5 | `0x80000003` (no se puede volcar la 1D para esta cámara) |

Sigma fp Film gen 4 y 5: mismos knobs que Pocket OG Film. Eso **no** prueba que el CinemaDNG Gen 4 Film sea esa curva: `GetToneCurve` es el API de tono del **BRAW**, no el decoder DNG.

Consecuencia UX: DWG/Intermediate **no** es una curva Film para el SDK (`GetToneCurve` falla) → Resolve deshabilita Gamma Controls. Film/Custom **sí** exponen knobs. Por eso el Match E1 (DWG/Intermediate) perjudica el grading aunque acerque la LUT Gen 1.

### Dónde pueden estar las diferencias (hipótesis ordenadas, no medidas en Film)

El banco S9 ya compara **CinemaDNG Gen 4 / P3-D65 / Linear** frente a **BRAW Gen 5 / P3-D65 / Linear** (~42,5–42,9 dB). Ese residual es sobre todo **encoder** (BUG-002: halos, false color), no el look Film.

Por tanto, si el usuario ve un desajuste grande al pasar a Film, es **posterior** al revelado lineal:

1. **Curva tonal / OETF Film** — misma etiqueta; `EvaluateToneCurve` no entrega la 1D OG; CinemaDNG puede usar otra tabla Gen 4.
2. **Gamut «Blackmagic Design»** — primaries/white del espacio nombrado; puede depender de la generación.
3. **Matriz colorimétrica** — Camera Support `d65_forward_matrix_*` (BRAW) vs matrices del DNG/decoder Gen 4. El banco lineal ya absorbe parte de esto.
4. **Pedestal de negro** — `black_offset` 0,02 en señal OG; Film DNG puede llevar lift (el cubo Gen 1 arranca ~0,035). No extrapolar ese cubo a Gen 4.
5. **Saturación** — knob Film = 1 en ambos APIs; el gen puede aplicar sat extra no expuesto.
6. **Tone mapping / highlight rolloff de generación** — `csgn` 4 vs 5; no se puede igualar el gen en el clip OG.
7. **Otras no linealidades** del decoder DNG (Highlight Recovery, sharpness) si no están a OFF/0.

Hasta tener los EXR de B, **no** se elige curva vs matriz vs LUT 3D vs DCTL. FEAT-001 C (curva → 3×3 → LUT → DCTL) queda para cuando existan referencias Film.

### Qué no hacer todavía

- No generar una LUT por aproximación.
- No poner viewing por defecto a Film.
- No usar DWG/Intermediate como puente Gen 4.
- No atribuir halos al look Film.

---

## B — Export EXR desde Resolve

Los EXR del banco (`DNG200_00086452.exr`, etc.) son **Gen 4 / P3-D65 / Linear**. **No** sirven como referencia Film. Sí sirven para restar el error de encoder al comparar looks.

### Material

| ID | Clip | Frame | TC timeline (manifiesto) | ISO / BE | EXR lineal ya existente |
|---|---|---|---|---|---|
| A01 | `BMPCC_1_2000-03-02_0756_C0036` | **52** | `01:00:02:04` | 200 / 0.1 | `DNG200_00086452.exr` |
| A03 | `BMPCC_1_2000-03-02_0756_C0038` | **46** | `01:00:09:19` | 800 / 2.1 | `DNG800_00086635.exr` |
| A04 | `BMPCC_1_2000-03-02_0756_C0039` | **49** | `01:00:14:00` | 1600 / 3.1 | `DNG1600_00086736.exr` |

**No** exportar A04 frame 44 (−3 EV) para esta comparación: mezcla look Film con un offset de Exposure. BRAW de conversión **histórica** (Match **OFF**, P3 Linear en metadata); en Camera RAW se **fuerza** BMD / Film.

Colocar los EXR nuevos en `~/Desktop/EXR` (o `--references`), **nombres exactos** de abajo. Conservar `timeCode` del fotograma.

### Proyecto

1. Proyecto nuevo `FEAT-001b-Gen4-Film`.
2. Color management: **DaVinci YRGB** (sin Color Managed). Sin CST de timeline que reinterpret Film.
3. Timeline: Rec.709-A, gamma Rec.709 (contenedor de entrega; el look sale de Camera RAW).
4. **Decode Using: Clip** (no Camera Metadata). Si se deja metadata, el BRAW se queda en P3 Linear y no hay par Film.
5. Un timeline DNG y otro BRAW, o el mismo con un clip cada vez. Misma geometría: recorte de cámara, sin timeline crop, sin resize.
6. Nodos vacíos. LUT combinada off. Sin OFX, denoise, ni HDR.

### Camera RAW — pares a igualar

Tras elegir Color Space / Gamma, **Reset** del grupo tonal de **esa** gamma (Film, no Custom). Comprobar que no aparece midpoint ~0,409 (eso es Custom).

| Campo | CinemaDNG | BRAW |
|---|---|---|
| Color Science | **4** | **5** (no hay 4) |
| Color Space | Blackmagic Design | Blackmagic Design |
| Gamma | Blackmagic Design Film | Blackmagic Design Film |
| White Balance | As Shot | As Shot (Kelvin/Tint del clip) |
| Exposure | 0 | 0 |
| Contrast | 1 (default Film) | 1 |
| Saturation | 1 | 1 |
| Midpoint | default Film (~0,384 si Resolve lo muestra) | default Film |
| Shadows / Highlights | default Film (1) | default Film |
| Highlight Recovery | **OFF** | **OFF** |
| Sharpness | **0** | n/a BRAW |
| Color Boost | **0** / default off | **ausente** — no compensar en Saturation |
| Midtone Detail | **0** | **ausente** |
| LUT | ninguna | ninguna |

### Lista de ficheros a exportar

Deliver: OpenEXR, RGB **16-bit float**, un fotograma, sin compresión o PIZ, Data Levels **Full**.

| Archivo | Origen | Camera RAW |
|---|---|---|
| `A01_f052_DNG_G4_BMD_Film.exr` | DNG C0036 frame 52 | Gen 4 / BMD / Film |
| `A01_f052_BRAW_G5_BMD_Film.exr` | BRAW histórico del mismo clip | Gen 5 / BMD / Film |
| `A03_f046_DNG_G4_BMD_Film.exr` | DNG C0038 frame 46 | Gen 4 / BMD / Film |
| `A03_f046_BRAW_G5_BMD_Film.exr` | BRAW histórico | Gen 5 / BMD / Film |
| `A04_f049_DNG_G4_BMD_Film.exr` | DNG C0039 frame 49 | Gen 4 / BMD / Film |
| `A04_f049_BRAW_G5_BMD_Film.exr` | BRAW histórico | Gen 5 / BMD / Film |

Opcional (separar encoder vs look; no bloquean C):

| Archivo | Para qué |
|---|---|
| `A01_f052_BRAW_G5_P3_Linear.exr` | Mismo BRAW, P3-D65 Linear: debe parecerse al EXR lineal del banco, no al Film |

### Qué invalida el par

- Color Managed / CST / timeline DWG.
- Decode Using = Camera Metadata en el BRAW (se queda Linear).
- Match E1 ON (viewing DWG/Intermediate).
- LUT Gen 1 en un nodo.
- Highlight Recovery, Color Boost o Midtone Detail ≠ 0 en el DNG.
- Gamma Custom (midpoint 0,409) en lugar de Film.
- Exposure ≠ 0, WB distinto de As Shot.
- Mezclar el EXR lineal `DNG200_…` con un BRAW Film.
- Corregir halos a mano.

Estos EXR están **codificados Film**, no son escena-lineal como el banco S9. No aplicar la normalización ISO del banco sin un modelo de curva.

### Uso posterior (C, no ahora)

Con los seis EXR: residual DNG Film vs BRAW Film en RGB float; comparar con el residual lineal DNG vs BRAW del banco para no sobreajustar BUG-002. Entonces sí: curva 1D → 3×3 → LUT 3D → DCTL, el modelo más simple. Validar en tomas no usadas para el ajuste.

---

## E — Checkbox (congelado)

El opt-in E1 (DWG/Intermediate) **sigue**; default P3 Linear **sigue**. Se reconsiderará: viewing `Blackmagic Design` + `Blackmagic Design Film` (gen 5) conservaría Gamma Controls y alinearía nombres con el flujo Gen 4. **No implementar** hasta validar C con los EXR de B.

## Fuera de alcance aquí

LUT embebida; Camera Support; ISO; EXP-005d/006/007/008; merge.
