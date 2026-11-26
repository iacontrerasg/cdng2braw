# FEAT-001 — Native CinemaDNG Workflow Compatibility (fase A–D)

**Rama:** `feat/pocket-og-cinemadng-workflow`  
**Base:** `main` @ `e1d69f3` (PR #12 EXP-005c **mergeada**). Este trabajo **no** fusiona PRs.  
**Alcance:** auditoría de revelado y metadata. **No** implementación E. **No** cambio de defaults. **No** perfiles instalados. **No** EXP-005d/006/007/008. **No** investigación de halos/compresión.

Objetivo de producto: que el BRAW Pocket OG se comporte en DaVinci Resolve lo más parecido posible al CinemaDNG nativo, con el mínimo de CST, LUT y ajustes iniciales — **sin hornear** el look en los códigos de sensor.

BUG-002 permanece P0 (véase [backlog.md](backlog.md)). Esta feature **no** lo corrige.

Medición SDK de esta fase: `tools/brawdec` + BlackmagicRawAPI de Resolve, clip OG de caché `out/benchmark/cache/braw/78ee298f0dd9481a75ce2099/clip.braw` (`camera_type` Blackmagic Pocket Cinema Camera, 1920×1080).

---

## Decisión de esta fase

**Estrategia recomendada (para E, tras revisión):**

1. Conservar el default histórico: `viewing_gamut` = DCI-P3 D65, `viewing_gamma` = Linear (equivalencia con DNG sin gestión de color / banco S9).
2. Añadir, solo si se aprueba E, una opción experimental **CinemaDNG / Gen 1 Match** que **no** recodifica el sensor: cambia los **defaults de visualización** a DaVinci Wide Gamut + DaVinci Intermediate (espacio de entrada de la LUT ya medida) y documenta/aplica la LUT de equivalencia.
3. **No** fijar `color_science_gen` = 1: el clip OG solo admite **gen 5**.
4. **No** usar `viewing_gamma` = Blackmagic Design Film como “Gen 1”: esa curva en el SDK **no** es Film Gen 1 del CinemaDNG (README, `container.cpp`).
5. LUT embebida (`lutm` Embedded / `eml*`) y sidecar LUT (`scl*`): el SDK **lista** Embedded/Sidecar/Disabled, pero en un BRAW actual `eml*` y `scl*` devuelven `0x80000008` (no hay LUT en el fichero). **No demostrado** que el encoder pueda escribir un cubo embebido sin el SDK de escritura. Queda como investigación de E, no como camino de primera.
6. Fallback ya existente: LUT externa en `LUT/cdng2braw/`.

---

## A — Auditoría de revelado

### Quién controla qué

| Superficie | Controla | No controla |
|---|---|---|
| **Encoder** (`finalizeClip`, `frameMetadata`) | `camera_type`, `viewing_bmdgen`/`viewing_gamut`/`viewing_gamma`, `ctrn`, `isoe`/`expo`/`agpf`/`wkel`/`wtin`, `analog_gain`, identidad de toma, geometría, bitstream YCC | Listas de gen/gamut/gamma del SDK; selector ISO de Resolve; Film Gen 1 nativo; LUT embebida (hoy no se escribe) |
| **Camera Support** (JSON instalado) | Identidad manufacturer/model/revision, `inv_sqrt`, CSC D65, r_gain/b_gain, tabla ISO `pre_gain`/`post_gain`, `chroma_alignment`, niveles | ChannelCodec; generación de color; LUTs de look; `is_bmd_camera` |
| **SDK** | Revelado RAW (curva, CSC, WB, ISO `post_gain`), listas de `gamt`/`gama` (16/17 nombres), `lutm`, tone controls | Gen 1 como opción de clip OG (`csgn` solo 5); `GetISOListForAnalogGain` para perfiles externos (`0x80000008` → Resolve sin selector ISO) |
| **Resolve** | Importación, Decode Using (Clip / Camera metadata / Project), árbol de nodos, carpeta LUT, sidecars que reescribe | Capacidades que el SDK no expone para Camera Support |

### Default actual (histórico)

- Clip: `viewing_gamut` **DCI-P3 D65**, `viewing_gamma` **Linear**, `viewing_bmdgen` = **5** (`OgModel.gen`).
- Camera RAW al abrir: esos viewing (medido: `gamt`/`gama` efectivos coinciden).
- `color_science_gen` efectivo **5**, lista **solo 5**.
- `lutm` efectivo **Disabled**.
- ISO 200–1600 vía `isoe` + `post_gain`; `expo` = 0; `agpf` = 1.

Esto iguala el DNG **sin** “Blackmagic Design” (Film Gen 1). El DNG nativo en Resolve usa por defecto esa look Gen 1, que el decoder BRAW **no** ofrece como generación de color.

### Color science / gamut / gamma (SDK, clip OG)

`csgn`: **{5}** únicamente. `GetClipProcessingAttributeList(ColorScienceGen)` por `camera_type` externo ya era `0x80000008` (EXP ISO). El clip abierto sí lista gen 5.

Gamuts (16): Blackmagic Design, Rec.709, Rec.2020, DCI-P3 D65, DCI-P3 Theater, ACES AP0/AP1, CIE XYZ D65/D50, V-Gamut, Canon Cinema Gamut, ARRI WG 3/4, **DaVinci Wide Gamut**, S-Gamut3.Cine, S-Gamut3.

Gammas (17): Blackmagic Design Film / Video / Extended Video / Custom, Linear, HLG, PQ, Rec.709, ACEScct, V-Log, Canon Log2, ARRI LogC3, N-Log, **DaVinci Intermediate**, L-Log, ARRI LogC4, S-Log3.

`IBlackmagicRawToneCurve::GetToneCurve("Blackmagic Pocket Cinema Camera", "Blackmagic Design Film", gen)` **acepta gen 1, 4 y 5** y devuelve los **mismos** parámetros (midpoint 0.383562, contrast/sat 1, …) que Sigma fp Film gen 4. Eso **no** prueba equivalencia con CinemaDNG Film Gen 1: es una curva genérica del API de tono, y el clip **no** puede seleccionar gen 1. `GetToneCurve` Linear / DaVinci Intermediate → `0x80000003` (no son curvas Film). Video gen 5 para OG → `0x80000003`.

**No se asume soporte nativo de Gen 1** para el revelado del clip.

### LUT embebida y sidecars

| Atributo | En clip OG actual |
|---|---|
| `lutm` | Disabled; opciones Embedded / Sidecar / Disabled |
| `emln` `emlt` `emls` `emld` | `0x80000008` (sin LUT embebida) |
| `scln` `sclt` `scls` `scld` | `0x80000008` (sin LUT de sidecar) |

El encoder **no** escribe cubos en el BRAW. Sidecar de revelado (`.sidecar`, JSON temporal `{"exposure": {"TC": v}}`) está documentado en EXP ISO: sustituye, no suma; Resolve lo reescribe; se pierde al mover el fichero. No hay sidecar de LUT generado hoy.

LUTs de producto: instaladas en `…/DaVinci Resolve/LUT/cdng2braw/` (macOS) o `…/Support/LUT/cdng2braw/` (Windows) por `ProfileStore`, **fuera** del BRAW.

### Comportamiento al importar (Resolve)

- Abre en P3-D65 Linear (metadata viewing).
- Sin selector ISO (Camera Support).
- Color science no listada como Gen 1–4 para esta cámara.
- Look Film Gen 1 del DNG exige hoy: Camera RAW → DaVinci Wide Gamut / DaVinci Intermediate **y** LUT `Pocket 2013 DNG Film from DWG Intermediate` (pasos manuales).

---

## B — LUT `Pocket 2013 DNG Film from DWG Intermediate`

**Fichero:** `model/pocket_og_dng_film_from_dwg_intermediate.cube`  
SHA-256 anclado en `ProfileSpec::pocketOg` (`5fbd4bae…`).  
**Título IRIDAS:** `Pocket Cinema Camera (2013) DNG Blackmagic Design Film, from DaVinci WG/Intermediate`.

### Procedencia

Medida en Resolve sobre el **decoder CinemaDNG** de la Pocket OG: entrada DaVinci Wide Gamut / DaVinci Intermediate, salida look por defecto **Blackmagic Design** (Film Gen 1). No es un look creativo. Segunda LUT (`Sigma fp to Pocket 2013…`) es un puente de cámara distinta; **no** forma parte del Match OG.

### Matemática (cubo, no analítica)

| Propiedad | Valor |
|---|---|
| Tipo | LUT 3D IRIDAS `.cube`, lattice regular |
| Tamaño | **33³** = 35937 muestras |
| `DOMAIN_MIN` / `DOMAIN_MAX` | **0,0,0** / **1,1,1** (RGB del espacio de entrada, no escena lineal infinita) |
| Interpolación | la de Resolve (típicamente tetraédrica); el encoder no la aplica |
| Negro (nodo 0) | ≈ **0.035388** en R,G,B (lift; Film no arranca en 0) |
| Blanco (nodo 1,1,1) | ≈ **0.85771** (techo; no 1.0) |
| Rango de salida | min ≈ 0.03539, max ≈ 0.85771; muchos nodos saturan cerca de 0.858 |

No hay modelo cerrado (matriz + curva) en el repositorio: la equivalencia es **tabular**, calibrada en un dominio SDR 0–1 de Intermediate.

### Espacios

| | |
|---|---|
| Entrada | **DaVinci Wide Gamut** |
| Gamma de entrada | **DaVinci Intermediate** |
| Salida | look **Blackmagic Design Film Gen 1** del DNG (display-referred en el sentido del decoder Resolve, no un gamut nombrado aparte en el cubo) |
| Gamma de salida | la del revelado DNG “Blackmagic Design” (Film Gen 1), no Linear |

### HDR, clip, fuera de dominio

- Dominio **0–1**. Valores &lt;0 o &gt;1: el aplicador **recorta al dominio** (estándar `.cube`); no hay extensión HDR/PQ.
- Highlights del cubo **comprimidos** (~0.86): no es techo de sensor 4095 ni P3 lineal del banco.
- Equivalencia **visual/display** con Gen 1 en el rango en que se midió; **no** equivalencia matemática escena-lineal con S9 EXR (esos EXR son P3 lineal sin Film).

---

## C — Alternativas de automatización (preferencia)

| # | Alternativa | ¿Compatible Resolve/SDK? | ¿RAW intacto? | Veredicto |
|---|---|---|---|---|
| 1 | Metadata nativa Gen 1 (`csgn=1` o Film Gen 1 de cámara BMD) | **No** para este clip: `csgn` ∈ {5}; Film del SDK ≠ Gen 1 DNG | Sí | **Descartada** como equivalencia Gen 1 |
| 2 | Viewing gamut/gamma DWG + Intermediate | **Sí**: ambos nombres están en las listas; `viewing_*` los escribe el encoder y Resolve los usa al abrir | Sí (CSC de salida, no sensor) | **Camino principal de E** (quita el CST; no quita la LUT) |
| 3 | LUT embebida auto-on (`lutm=Embedded`) | **No demostrado**: `eml*` ausentes; no hay escritor SDK; átomos no implementados | Sí si post-process | Investigación E; no primera entrega |
| 4 | Sidecar auto (`.sidecar` o `scl*`) | Sidecar de **exposure** sí; LUT sidecar `scl*` = `0x80000008`; Resolve reescribe sidecars | Sí | Frágil; no recomendado como primario |
| 5 | LUT externa `LUT/cdng2braw/` | **Sí, ya instalada** | Sí | **Fallback**; sigue pidiendo un nodo (o OFX) |

Reglas: no hornear Film en YCC; no cambiar `sensor_curve` / ISO / `camera_type` / revisión.

**Árboles de nodos existentes** que asumen P3-D65 Linear **romperían** si el default viewing pasa a DWG/Inter. Por eso E debe ser **opt-in**.

---

## D — Preservación de metadata

| Campo DNG / toma | En BRAW OG | Clase |
|---|---|---|
| Identidad de cámara | `camera_type` «Blackmagic Pocket Cinema Camera», `manufacturer` Blackmagic, `ctrn` revisión CISO | **Preservada** (perfil, no número de serie DNG) |
| Nº de serie / UniqueCameraModel | no se escribe | **No disponible** (no fabricar) |
| ISO | `isoe` = ISO inferida {200,400,800,1600} | **Inferida** de BaselineExposure |
| BaselineExposure | no viaja como BE; `post_gain` = 2^BE / 2^1.1 | **Inferida** (tabla); BE crudo **no almacenado** |
| AsShotNeutral (triple) | no se guarda | **Inferida** → kelvin/tint (`inferAsShot`); triple **no disponible** |
| Kelvin / tint | `wkel` / `wtin` | **Inferidos** (dominio perfil 3000–10000 K, tint −30…30) |
| Timecode | pista tmcd; `finish(timecode)` desde tag 51043 | **Preservado** si el DNG lo trae; si no, 0 |
| Frame rate | `srte` num/den | **Preservado** (secuencia) |
| Resolución | geometría crop/encode | **Preservada** (o reducida si el usuario pide otra) |
| Nombre de clip | `clip_number` = nombre de carpeta | **Inferido** (no UniqueCameraModel) |
| Fecha | `date_recorded` DateTimeOriginal o DateTime IFD0 | **Preservado** si hay fecha válida |
| `camera_id` | UUID v4 **nuevo** | **Generado** (no hay serial) |
| Objetivo | `lens_type` solo con gyro fpSup | **No disponible** en OG típico |
| Audio WAV | pista PCM si hay WAV | **Preservado** si existe |
| Gyro | pistas mogy/moac | N/A Pocket OG típica |

No se fabrica metadata ausente (serial, BE crudo, AsShotNeutral triple).

---

## Matriz de capacidades (clip OG, SDK Resolve 21)

| Capacidad | Encoder hoy | SDK lista | Resolve UI | Nota |
|---|---|---|---|---|
| P3-D65 Linear al abrir | Sí (viewing) | Sí | Sí | Default histórico |
| DWG + Intermediate al abrir | No (viewing distinto) | Sí | Sí si viewing | Propuesta E |
| Film Gen 1 nativo | No | `csgn`≠1 | No | Bloqueado |
| Blackmagic Design Film (gen 5) | No como viewing | Gamma listada | Sí como gamma | ≠ Gen 1 DNG |
| ISO As Shot | Sí | Lista 200–1600 en clip | **Sin** selector | Usar Exposure |
| WB As Shot | Sí | wbkv/wbtn | Sí | |
| LUT embebida | No | lutm Embedded; eml* vacío | — | Hueco |
| LUT sidecar | No | lutm Sidecar; scl* vacío | — | Hueco |
| LUT carpeta Resolve | Instalador perfil | N/A | Lista cdng2braw | Fallback |
| Gen 4/5 tone Film API | N/A | GetToneCurve OK | — | No es Gen 1 DNG |

---

## Limitaciones SDK / Resolve

- Perfil **externo**: sin `GetISOListForAnalogGain` ni lista de ColorScienceGen a nivel Constants → Resolve sin ISO y con errores `0x80000008` / `SetClipAttribute(ColorScienceGen)`.
- Film Gen 1 del CinemaDNG **no** está en el decoder BRAW para esta cámara.
- LUT 33³ dominio 0–1, lift y clip: no HDR; fuera de 0–1 se recorta.
- Sidecar no viaja con el medio si se copia solo el `.braw`.
- Equivalencia Gen 1 es **visual** (LUT medida), no bit-idéntica con S9 P3 lineal.

---

## Propuesta E / F (no implementada)

### E — Opción experimental Pocket OG

- UI/request: **CinemaDNG / Gen 1 Match** vs **Native BRAW** (histórico). Default = histórico.
- Match: escribir `viewing_gamut` = `DaVinci Wide Gamut`, `viewing_gamma` = `DaVinci Intermediate`; **no** tocar curva, ISO, identidad, YCC.
- Identidad de caché: incluir el flag (como `OUTPUT_ENV` / notes de encode).
- Otras cámaras: sin cambio.
- LUT: seguir instalando el cubo; **no** embed hasta probar `eml*` con un prototipo de contenedor (fuera de la primera E si el riesgo es alto).
- Texto Resolve: un paso restante = aplicar LUT (si embed no está).

### F — Validación (cuando exista E)

- Comparar CinemaDNG **Blackmagic Design** vs BRAW Match + LUT (y vs histórico P3 Linear).
- El banco S9 actual **no** es referencia Gen 1: habría que exportar EXR **adicionales** (DNG Film Gen 1 y, si hace falta, BRAW DWG/Inter±LUT) — no reutilizar `DNG1600MINUS3` sin emparejar EV.
- Evaluar: exposición inicial, WB, gamma/contraste, colorimetría, saturación, negros/altas, ΔExposure RAW, ΔWB/tint, rotura de árboles P3 Linear.
- Distinguir equivalencia visual (LUT) vs matemática (códigos).
- No gate de BUG-002.

---

## Entregables de esta PR

- Este informe; [backlog.md](backlog.md).
- Pruebas de cabecera del cubo (`tests/test_feat001.py`).
- Sin código de encode E. Sin merge.
