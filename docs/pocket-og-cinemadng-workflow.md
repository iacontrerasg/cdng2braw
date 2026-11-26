# FEAT-001 — Native CinemaDNG Workflow Compatibility (fase A–E)

**Rama:** `feat/pocket-og-cinemadng-workflow`  
**Base:** `main` @ `e1d69f3` (PR #12 EXP-005c **mergeada**). Este trabajo **no** fusiona PRs.  
**Alcance:** auditoría A–D aceptada; implementación **E1** (viewing opt-in). **No** cambio de defaults. **No** perfiles Camera Support. **No** EXP-005d/006/007/008. **No** investigación de halos/compresión. **Sin merge.**

Objetivo de producto: que el BRAW Pocket OG se comporte en DaVinci Resolve lo más parecido posible al CinemaDNG nativo, con el mínimo de CST, LUT y ajustes iniciales — **sin hornear** el look en los códigos de sensor.

BUG-002 permanece P0 (véase [backlog.md](backlog.md)). Esta feature **no** lo corrige.

Medición SDK de esta fase: `tools/brawdec` + BlackmagicRawAPI de Resolve, clip OG de caché `out/benchmark/cache/braw/78ee298f0dd9481a75ce2099/clip.braw` (`camera_type` Blackmagic Pocket Cinema Camera, 1920×1080).

---

## Decisión de esta fase

1. Conservar el default histórico: `viewing_gamut` = DCI-P3 D65, `viewing_gamma` = Linear.
2. Opción opt-in **Match original CinemaDNG rendering**: viewing DaVinci Wide Gamut + DaVinci Intermediate; gen 5; LUT **no** embebida.
3. **No** fijar `color_science_gen` = 1: el clip OG solo admite **gen 5**.
4. **No** usar `viewing_gamma` = Blackmagic Design Film como “Gen 1”.
5. LUT embebida: historial Git sin blob sucio; `SetEmbeddedPost3DLUT` / `SetSidecarPost3DLUT` = `0x80000008` en clips OG. Sin escritura en el muxer.
6. Fallback: LUT externa en `LUT/cdng2braw/` + paso de nodo manual.

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

No hay modelo cerrado (matriz + curva) en el repositorio: la equivalencia es **tabular**, calibrada en el dominio **0–1 de DaVinci Intermediate** (codificación logarítmica, **no** Rec.709 SDR).

### Espacios

| | |
|---|---|
| Entrada | **DaVinci Wide Gamut** |
| Gamma de entrada | **DaVinci Intermediate** |
| Salida | look **Blackmagic Design Film Gen 1** del DNG (display-referred en el sentido del decoder Resolve, no un gamut nombrado aparte en el cubo) |
| Gamma de salida | la del revelado DNG “Blackmagic Design” (Film Gen 1), no Linear |

### HDR, clip, fuera de dominio (E4)

- Dominio **0–1 Intermediate**. Valores &lt;0 o &gt;1: el aplicador **recorta al dominio** (estándar `.cube`); no hay extensión HDR/PQ.
- Negro (nodo 0) ≈ **0.035388** (lift Film). Blanco (nodo 1) ≈ **0.85771**. Mín/máx del cubo ≈ 0.03539–0.85771. **Ningún** nodo negativo ni &gt; 1.
- ~28 000 de 35 937 nodos caen en el techo (~0.858): highlights **comprimidos**; no es techo de sensor ni P3 lineal del banco.
- Saturados y altas luces del CinemaDNG que salgan del dominio Intermediate se recortan **antes** de interpolar.
- Exposure / Kelvin / Tint del Camera RAW operan **antes** de esta LUT (E3); un ΔExposure grande empuja más muestras al techo del cubo.
- Equivalencia **visual/display** con Gen 1 en el rango medido; **no** equivalencia escena-lineal con S9 EXR (P3 lineal sin Film).
- **No** se genera una LUT nueva: no hay necesidad demostrada de otro tamaño o dominio.

---

## C — Alternativas de automatización (preferencia)

| # | Alternativa | ¿Compatible Resolve/SDK? | ¿RAW intacto? | Veredicto |
|---|---|---|---|---|
| 1 | Metadata nativa Gen 1 (`csgn=1` o Film Gen 1 de cámara BMD) | **No** para este clip: `csgn` ∈ {5}; Film del SDK ≠ Gen 1 DNG | Sí | **Descartada** como equivalencia Gen 1 |
| 2 | Viewing gamut/gamma DWG + Intermediate | **Sí**: ambos nombres están en las listas; `viewing_*` los escribe el encoder y Resolve los usa al abrir | Sí (CSC de salida, no sensor) | **Camino principal de E** (quita el CST; no quita la LUT) |
| 3 | LUT embebida auto-on (`lutm=Embedded`) | SDK **sí** documenta `SetEmbeddedPost3DLUT` y claves QT `post_3dlut_*`; **no** hay BRAW de referencia local ni persistencia en el encoder | Sí si post-process | **No implementada** (E2) |
| 4 | Sidecar LUT (JSON `.sidecar` con `post_3dlut_sidecar_*`) | Documentado en el manual SDK (ejemplo Identity 33³, `post_3dlut_mode: Sidecar`); **distinto** del sidecar de exposición; Resolve lo reescribe; no viaja solo el `.braw` | Sí | **No implementada** (E5); frágil |
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
| DWG + Intermediate al abrir | Opt-in E1 | Sí | Sí si viewing | Default sigue P3 Linear |
| Film Gen 1 nativo | No | `csgn`≠1 | No | Bloqueado |
| Blackmagic Design Film (gen 5) | No como viewing | Gamma listada | Sí como gamma | ≠ Gen 1 DNG |
| ISO As Shot | Sí | Lista 200–1600 en clip | **Sin** selector | Usar Exposure |
| WB As Shot | Sí | wbkv/wbtn | Sí | |
| LUT embebida | No | lutm Embedded; eml* vacío | — | Hueco |
| LUT sidecar | No | lutm Sidecar; scl* vacío | — | Hueco |
| LUT carpeta Resolve | Instalador perfil | N/A | Lista cdng2braw | Fallback |
| Gen 4/5 tone Film API | N/A | GetToneCurve OK | — | No es Gen 1 DNG |

---

## E1 — Viewing metadata (implementado)

Opción opt-in exclusiva Pocket OG: **Match original CinemaDNG rendering**. Default **desactivado**.

| | Viewing gamut | Viewing gamma | Color science |
|---|---|---|---|
| Desactivada (histórico) | DCI-P3 D65 | Linear | Gen 5 (`viewing_bmdgen` = `OgModel.gen`) |
| Activada | DaVinci Wide Gamut | DaVinci Intermediate | Gen 5 (sin cambio) |

No se toca curva de sensor, ISO, WB, identidad ni payload YCC. Otras rutas (VA / Pocket 4K) rechazan el flag.

Superficies: `request.json` (`match_cinemadng_rendering`), worker `cdng-convert --request`, caché de fidelidad (`encode_request` / `conversion_material`), UI (checkbox avanzado, visible con tomas OG), nombre de fichero `_match` para no chocar con la caché de Resolve.

## E2 — LUT embebida (no viable; E2b agotada)

`eml*` = `0x80000008` en **nuestros** BRAW demuestra **ausencia** de LUT, no incompatibilidad del contenedor.

### E2b-A — Recuperación histórica

| Comprobación | Resultado |
|---|---|
| `git log --all -- model/cam/orig.braw` | Un solo commit: `84a6372` (1.0.0). Blob **5 801 115** bytes, **ya limpio** |
| `tools/clean_template.py` | Entra en el **mismo** commit; el original sucio **nunca** se versionó |
| Git LFS / parent GitHub | Sin LFS; el repo no tiene `parent` upstream |
| Copias locales `orig.braw` | App bundle, `build/worker`, `model/cam` — mismas claves, **sin** `post_3dlut_*` |
| `find` de `.braw` nativos con `post_3dlut_embedded_*` | Ninguno |

No se recupera tipo, endian ni orden RGB del lattice. **No** se reescribe el historial Git.

### E2b-B — Harness SDK (`brawdec --embed-lut identity`)

Clip OG de caché `78ee298f0dd9481a75ce2099` (`Blackmagic Pocket Cinema Camera`, 1920×1080). `SafeArrayCreate` vt=F32 (6), 1 dimensión (el binario rechaza `cDims≠1`). Vtable con `GetISOList` alineada al manual (GetEmbedded en el slot 11 devuelve `S_OK` + puntero nulo).

| Llamada | HRESULT |
|---|---|
| `GetEmbeddedPost3DLUT` / `GetSidecar` / `GetPost3DLUT` | `0` (LUT nula) |
| `SetEmbeddedPost3DLUT` identidad 2³, F32×24 | **`0x80000008`** |
| `SetEmbeddedPost3DLUT` F32×8 (`size³`) | **`0x80000008`** |
| `SetSidecarPost3DLUT` F32×24 | **`0x80000008`** |
| `CreateJobTrim` con attrs | no se alcanzó (el setter falla antes) |
| `SaveSidecarFile` sin LUT | `0`; escribe `{}\n` (3 bytes). **No** es persistencia en el `.braw` |

Los setters existen y se invocan; el SDK **rechaza** escribir LUT embebida o de sidecar sobre estos clips (perfil Camera Support / decoder SDK). No hay fichero recortado que reabrir. No se confunde sidecar de sesión con átomos QT.

### Conclusión E2

**No** se implementa escritura en el muxer: no hay blob de referencia ni persistencia demostrada. Fallback E1: viewing opt-in + LUT externa. **No** equivalencia Gen 1 automática.

Activación por defecto: el UI del SDK lee `lutm`. Presencia de `emld` **no** implica auto-on (gist P6K Pro: cubo embebido con `post_3dlut_mode: Disabled`).

## E3 — Orden de procesado

1. Decode bitstream → planos (sensor / YCC según flujo).
2. Process: ISO (`post_gain`), Exposure, Kelvin, Tint, curva, CSC de **viewing** (gamut/gamma del clip).
3. Post 3D LUT (`CreateJobProcess` buffer LUT; `GetPost3DLUTSizeBytes`) sobre la imagen **ya** en el espacio de visualización.
4. Nodos de Resolve **después** del revelado Camera RAW.

La LUT medida espera **DWG / DaVinci Intermediate**. Con E1 activado, ese es el espacio de entrada. En un proyecto con color management, un CST adicional **más** la LUT **dobla** la transformación: no aplicar CST de entrada si el clip ya llega en DWG/Intermediate. **No** se hornea CST ni LUT en el encoder.

## E5 — Fallback

Sidecar LUT **no** es el sidecar de exposición: el manual muestra JSON con `post_3dlut_mode`, `post_3dlut_sidecar_name/title/size/data` (lattice en texto). Resolve reescribe `.sidecar`; no viaja al copiar solo el `.braw`; un sidecar de exposición existente se **sustituye** al guardar.

Sin un `.sidecar` LUT generado por Resolve para comparar, **no** se emite sidecar desde el conversor.

**Producto de esta fase:** viewing opt-in + LUT **externa** ya instalada en `LUT/cdng2braw/`. Paso manual restante: aplicar `Pocket 2013 DNG Film from DWG Intermediate` en el primer nodo (o OFX). **No** se etiqueta como equivalencia automática Gen 1.

## Limitaciones SDK / Resolve

- Perfil **externo**: sin `GetISOListForAnalogGain` ni lista de ColorScienceGen a nivel Constants → Resolve sin ISO y con errores `0x80000008` / `SetClipAttribute(ColorScienceGen)`.
- Film Gen 1 del CinemaDNG **no** está en el decoder BRAW para esta cámara.
- LUT 33³ dominio 0–1, lift y clip: no HDR; fuera de 0–1 se recorta.
- Sidecar no viaja con el medio si se copia solo el `.braw`.
- Equivalencia Gen 1 es **visual** (LUT medida), no bit-idéntica con S9 P3 lineal.

---

## F — Validación manual en Resolve

El banco S9 (P3 lineal) **no** es referencia Film Gen 1. Exportar EXR **adicionales**. Prioridad: **A01_f052**, **A03_f046**, **A04_f049**.

### Proyecto

1. Proyecto nuevo (`FEAT-001-Gen1`). **DaVinci YRGB** (sin color management). Así no hay CST de proyecto que doble la LUT.
2. Timeline Rec.709-A / Rec.709 (o el mismo espacio en el que se midió la LUT). **No** DaVinci Wide Gamut en el timeline.
3. Decode Using: **Camera Metadata** (clip).
4. Camera RAW: As Shot. No tocar Exposure, Kelvin, Tint, Saturation, Color boost.
5. **No** corregir BUG-002 (halos / false color): anotarlos aparte del revelado.

### Cinco revelados por fotograma

| Etiqueta | Material | Camera RAW | Nodo |
|---|---|---|---|
| CinemaDNG nativo | carpeta DNG | Blackmagic Design / Film Gen 1 (default DNG) | ninguno |
| BRAW histórico | conversión **sin** Match | P3-D65 Linear (metadata) | ninguno |
| BRAW DWG/Inter | conversión **con** Match | DWG / DaVinci Intermediate | ninguno |
| BRAW Match + LUT | el mismo BRAW Match | DWG / Intermediate | LUT `cdng2braw / Pocket 2013 DNG Film from DWG Intermediate` en el **primer** nodo; LUT combinada **on** |
| BRAW Match LUT off | el mismo BRAW Match | DWG / Intermediate | misma LUT **desactivada** (bypass). Debe coincidir con «BRAW DWG/Inter» |

Comprobar: la LUT se puede apagar; ISO/WB As Shot se conservan; el payload (histogramas Camera RAW) no cambia al conmutar la LUT.

### Entrega EXR

Deliver: OpenEXR, RGB 16-bit float, un fotograma, nombre  
`{clip}_{frame}_{etiqueta}.exr`  
p. ej. `A01_f052_DNG_FilmGen1.exr`, `A01_f052_BRAW_P3Linear.exr`, `A01_f052_BRAW_Match.exr`, `A01_f052_BRAW_MatchLUT.exr`, `A01_f052_BRAW_MatchLUToff.exr`.

Misma geometría (recorte de cámara, no timeline crop). Sin denoise, sin rec.709 output transform extra.

### Qué comparar

- Equivalencia de **revelado**: DNG Gen 1 vs BRAW Match+LUT (exposición, contraste Film, colorimetría, saturación, negros/altas).
- Defectos del **encoder** (BUG-002, bloques, banding): no mezclarlos con el veredicto de viewing/LUT.
- Árboles antiguos que asumen P3 Linear **rompen** si se usa Match: por eso el default no cambia.

---

## Entregables de esta PR

- E1 en encoder, request, caché y UI.
- Informe E2–E5 (LUT embebida no prototipada; sidecar LUT no emitido).
- Pruebas `tests/test_feat001.py`, `container_test`.
- Este documento; instrucciones F. Sin merge.
