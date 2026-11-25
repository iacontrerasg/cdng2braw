# Experimento: Pocket OG con ganancia unitaria

Rama: `experiment/pocket-og-unity-gain`, creada desde `feat/pocket-og-dynamic-baseline-exposure` (976674e).
Prueba diagnóstica; no es una corrección definitiva ni debe fusionarse tal cual.

## Defecto que se investiga

Con la compensación dinámica de BaselineExposure (commits 9b351d5 y 976674e), los BRAW de la Pocket Cinema
Camera original recortan las altas luces antes de tiempo: el techo lineal ronda 2.1434488 con cualquier ISO de
origen, y bajar la exposición RAW en Resolve no recupera nada. Si no se recupera, la información se perdió al
codificar, no al revelar.

## Hipótesis

`model/pocket_og_cinemadng_signal.json` declara `baseline_exposure_gain = 2.1435469…`, prácticamente igual al
techo observado. En la ruta OG, `ogSourceInfo` calcula `shot.gain = 2^BaselineExposure / baselineGain`;
`sourceInfo` (`native/engine/worker/common.cpp`) lo copia a `info.gain` y `develop` lo aplica **antes** del mapa
de señal. Cualquier ganancia mayor que 1 empuja las luces altas por encima del código 4095, que queda fijado en el
techo del perfil. (La ruta exacta que se ejecuta está en «Trazabilidad en runtime»: es la RGB, no `bayerFields`.)

## Cambio realizado

Solo en `native/engine/signal_source.cpp`, función `ogSourceInfo`:

- Se mantienen sin cambios la lectura de BaselineExposure (tag 50730), el rechazo de valores ausentes o no finitos,
  el cálculo de `gain / m.baselineGain` y el rechazo de resultados no finitos o ≤ 0.
- Al final de ese bloque se fuerza `shot.gain = 1.0`.

`common.cpp` sigue propagando `shot.gain`, de modo que se usa la misma ruta que en la rama de la función.

No se tocaron las matrices de color, el balance de blancos (la inferencia de kelvin/tint sigue usando
`0.18 / m.baselineGain`), el perfil calibrado, `pocket_og_cinemadng_signal.json` ni las rutas Va (Sigma fp) y Pocket.

## Compilación y verificación

- `make package`: correcto; paquete en `out/cdng2braw/cdng2braw.app` (firmado en local, sin notarizar).
- `ctest --test-dir build/native`: 6/6 pruebas superadas.
- El informe de empaquetado da el perfil como `missing`, pero en Camera Support está instalado
  `pocket-og-cinemadng-experimental.json`, con el mismo SHA-256 que `model/pocket_og_cinemadng_camera_support.json`
  (`63a03587…`). Resolve decodifica con el perfil calibrado; el estado `missing` se refiere al registro de
  instalación de la app, no al contenido.

## Qué comprobar

1. Convertir los mismos clips OG con ISO distintos que reproducían el defecto.
2. Medir en Resolve (o con `tools/brawdec`) el valor lineal máximo de las luces altas.
3. Interpretación:
   - Si el techo deja de ser ~2.1434 y las luces se recuperan bajando la exposición, el recorte lo causa aplicar
     la ganancia antes de codificar; la compensación debería ir en los metadatos del clip o tras la decodificación.
   - Si el techo persiste, el límite viene de la calibración o del mapa de señal y hay que buscar en otro sitio.
4. Esperado con este cambio: la exposición aparente vuelve a la de la referencia calibrada (no depende de
   BaselineExposure), así que los clips con otro ISO se verán más claros o más oscuros. Es lo previsto en esta prueba.

## Trazabilidad en runtime (commit cd75430)

### Instrumentación

- `native/CMakeLists.txt`: al configurar, incrusta en `cdng-convert` el commit (`-dirty` si hay cambios sin
  confirmar) y la rama (`CDNG_BUILD_COMMIT`, `CDNG_BUILD_BRANCH`).
- `native/engine/worker/trace.cpp` (nuevo): cada conversión escribe `<salida>.pipeline-trace.txt` junto al BRAW
  (y lo repite por stderr). La cabecera lleva el build, la ruta y el SHA-256 del ejecutable que corre, el SHA-256 del
  `pocket_og_cinemadng_signal.json` cargado y si el destino existía antes. El evento JSON `started` incluye
  `build` y `trace`.
- Primer fotograma legible (`convert.cpp`, solo el hilo que lo procesa):
  - `sourceInfo`/`ogSourceInfo`: ruta, ISO EXIF, BaselineExposure, 2^BE, `baselineGain` del perfil, ganancia
    calculada, `shot.gain` efectivo e `info.gain`. `OgShot` añade tres campos de diagnóstico; el comportamiento no cambia.
  - `develop()`: `info.gain` recibido y resultado de `channelCodec()`. Para cada etapa (`cameraRgb`, `cropSensor`,
    tras la ganancia, `resizeLinear`): mínimo, máximo, percentiles 0.1/1/50/99/99.9/99.99 por canal y muestras
    ≥ 1.0 (blanco del sensor) y ≥ `decode(4095)` (lo que llega al código 4095).
  - Tras `imagePlanes` y `finishPlanes`: Y/Cb/Cr y códigos por canal reconstruidos con `code_from_ycc`, con el
    número de muestras por encima de 4095.
  - Tras `canvasPlanes`, con y sin precompensación antibloques: muestras recortadas a 4095/0 y códigos por canal.

### Verificación del binario

- La app solo ejecuta `Contents/Resources/converter/cdng-convert` de su propio bundle (`app/conversion_job.cpp`) y
  no hay otra copia instalada (ni en `/Applications` ni en `~/Applications`).
- El worker del paquete y `build/worker/bin/cdng-convert` tienen el mismo SHA-256; el binario lleva incrustado
  `cd75430a…` sin `-dirty`.
- Ni la app ni el worker sobrescriben ni reutilizan BRAW: los dos rechazan un destino existente.

### Ruta realmente ejecutada por la Pocket OG

`ogSignalMap` devuelve un `NativeSensor`, que no implementa `channelCodec()` (devuelve `nullptr`). La Pocket OG va
por la **ruta RGB**: `Dng::cameraRgb` (LibRaw AHD, `highlight = Clip`, normalizado de negro a blanco, techo 1.0) →
`cropSensor` → `multiply` (solo si `info.gain != 1`) → `resizeLinear` → `imagePlanes` →
`NativeSensor::mapDouble` (con `sensor_curve`, `SensorCurve::encode`: 1.0 → código 4095.0001) → `finishPlanes`
(solo NR) → `canvasPlanes`/`rounded` (Y recortada a 0…4095). `bayerFields()` no interviene.

### Dónde se produce el recorte

- Sin ganancia, el techo físico está en `Dng::cameraRgb`: LibRaw recorta en el WhiteLevel (51080), es decir, 1.0.
  `SensorCurve::decode(4095) × baselineGain = 2.14354683` es el valor en el que Resolve muestra ese blanco a
  0 EV: el «techo de 2.1434488» es el blanco del sensor visto a través del perfil, con cualquier ISO.
- Con ganancia > 1 (build 976674e), `multiply()` sube la señal sin límite, `SensorCurve::encode` produce códigos
  > 4095 y el recorte efectivo ocurre en `canvasPlanes` → `rounded(y, 0, 4095)` para la luma y, por canal, en el
  decodificador del SDK, que limita la salida a ese mismo blanco (2.14344883). Todo lo que pase de 1/gain en el
  sensor se pierde en el BRAW.

### Resultado medido (C0039, BE 3.1 → ganancia calculada 4.0, `shot.gain` 1.0)

- Traza del fotograma 0: 1 muestra ≥ 1.0 en la fuente; ningún código por encima de 4095 tras el mapa; 65 píxeles
  de Y (0.003 %) en 4095 tras la precompensación antibloques y ninguno sin ella.
- El BRAW de la app del escritorio (`~/Desktop/BRAW/…C0039_3to1.braw`, 14:49) decodificado con el SDK de Resolve es
  equivalente al generado aquí: 0.079 % de píxeles en 2.1434 a 0 EV y, a +2 EV, máximo 8.5738 (= 2^3.1), sin recortar.
- El EXR `UNITY_GAIN_ISO1600_0EV` corresponde al fotograma 43 (correlación 0.9997) y vale ≈ min(BRAW × 4, 2.1434):
  19.94 % de píxeles en el techo frente al 19.90 % medido. Es el BRAW de la build anterior, no el experimental.
- `lsof` muestra que Resolve (PID 18086) tiene abiertos los BRAW **antiguos de la Papelera**
  (`~/.Trash/…C0039_3to1.braw`, 135 293 653 bytes, 14:06), no los nuevos del escritorio (111 395 381 bytes, 14:49).
  Al borrar el fichero, Resolve siguió leyendo el inodo antiguo con el mismo nombre de clip. Las dos medidas del
  informe («anterior» y «Unity Gain») salieron del BRAW antiguo.

## Validación independiente (exportaciones OpenEXR de Resolve, ISO 1600 / BE 3.1)

Repetida con los BRAW nuevos importados de nuevo en Resolve:

- Píxeles en el techo de señal: del 19.9154 % al 0.0829 %.
- Máximo lineal recuperado: 8.573795, frente a 2.143449.
- Error relativo de luminancia media: +0.0696 %.
- PSNR entre el DNG a −3 EV y el BRAW a −1 EV: 41.85 dB. Correlación RGB: 0.99768.

La ganancia unitaria queda como base de la solución: la compensación de BaselineExposure **no** se vuelve a aplicar
sobre el RGB antes de codificar.

## Investigación: exposición no destructiva al abrir el BRAW

Compensación esperada: `BaselineExposure − log2(baselineGain)` = BE − 1.1 EV. Para los clips medidos: BE 0.1 → −1,
1.1 → 0, 2.1 → +1, 3.1 → +2 EV. Las pruebas se hicieron sobre copias del BRAW de C0039 en `out/trace-run/`,
decodificadas con el SDK de Resolve (`tools/brawdec`, `out/research/sidecar_probe`). El motor no se modificó.

### Qué escribe hoy `container.cpp` (ruta OG)

- Por fotograma (`frameMetadata`, bloque `bmdf` de 1024 bytes): `srte` (cadencia), `expo` (float32 BE, copiado
  de la plantilla: 0.0), `isoe` (1250), `wkel`/`wtin` (As Shot inferido), `agpf` (0.0) y `skip`.
- Por clip (`finalizeClip` → `withProfile`/`minimalMetadata`): `camera_type`, `manufacturer`, `viewing_bmdgen`,
  `viewing_gamut` "DCI-P3 D65", `viewing_gamma` "Linear", `analog_gain` (0.0), la geometría y la identidad de la
  toma. `ctrn` lleva la revisión del perfil.

### Atributos del SDK (`brawdec --audit-attributes`, `--frame-info`)

- Fotograma: `expo` float, valor 0, rango −5…+5, escribible; `fiso` lista de solo lectura {1250}; `agpf` float 1;
  `wbkv` 2000…50000; `wbtn` −150…150.
- Clip: `gain` 1 y `agic` 1 sin rango; `hlry` (recuperación de altas luces); `lutm` Embedded/Sidecar/Disabled; tono
  (`tcon`, `tsat`…).
- Metadatos de fotograma del SDK: `exposure` es la entrada `expo` del `bmdf`. También expone `as_shot_kelvin` y
  `as_shot_tint` (0 en nuestros clips), pero no hay `as_shot_exposure`. A nivel de clip no existe `exposure`:
  `SetMetadata` devuelve `0x80000003`.

### Pruebas sobre copias

| Variante | Resultado en el SDK |
| --- | --- |
| `expo` = 2.0 en cada `bmdf` | `expo` efectivo 2; imagen idéntica a decodificar con `--exposure 2` (media 0.867443, máximo 8.5738) |
| `agpf` = 1.0 | sin efecto |
| `agpf` = 2.0 o 4.0 | el SDK revienta (segfault, código 139) |
| `analog_gain` de clip = 4.0 | se lee 4, pero la salida lineal no cambia (media 0.216861) |
| `isoe` = 1600 (fuera de la lista del perfil) | `fiso` efectivo 1600 y el SDK revienta (segfault) |
| Sidecar escrito por `SaveSidecarFile` tras `frame.SetMetadata("exposure", 2)` | `{"exposure": {"07:56:49:00": 2.000000}}`; el BRAW no cambia; al reabrir se adjunta solo y el valor se mantiene en los fotogramas siguientes |
| Sidecar con `"exposure": 2.0` o `"iso": 1600` escalares | `PARSE FAILURE` |
| Sidecar 0.5 sobre `expo` embebido 2.0 | efectivo 0.5: el sidecar **sustituye**, no suma |

### Perfil calibrado

`pocket_og_cinemadng_camera_support.json` (modo «CinemaDNG calibration»):
- `iso_params[0]`: `base_iso` 1250 y `iso_gain_table` con una sola entrada {iso 1250, `pre_gain` 1, `post_gain` 1}.
- `native_iso` 1250; `iso_modification_supported` false.
- `stops_above_mid_grey_at_native_iso` 3.5739: 0.18 · 2^3.5739 = 2.1435 = `baselineGain`.

El SDK solo carga perfiles desde `/Library/Application Support/Blackmagic Design/Blackmagic RAW/Camera Support` (no
hay variable de entorno para otra carpeta): probar una tabla ISO requiere instalar un perfil modificado y reiniciar Resolve.

### Alternativas

1. **`expo` embebido por fotograma** (escribir BE − 1.1 en el `bmdf`).
   - A favor: comprobado en el SDK; no destructivo; sin ficheros extra; no toca el perfil ni la calibración.
   - En contra: es el mismo parámetro que el control «Exposure» del usuario. Resolve lo mostraría como 2.0, no como 0;
     un ajuste del usuario o un sidecar lo sustituye (no se suma), y ocupa parte del rango ±5.
   - Por verificar en Resolve: que «Decode using: Camera metadata» abra con +2, y qué pasa con «Clip» y «Project».
2. **Tabla ISO en el perfil** (una entrada por ISO de la cámara, con la ganancia en `pre_gain`/`post_gain`, e
   `isoe` por clip según BE).
   - A favor: es como tratan la ISO las cámaras Blackmagic: metadato no destructivo, independiente del control
     Exposure. Con `iso_modification_supported` false el usuario no puede cambiarla.
   - En contra: modifica el perfil calibrado (SHA, `profile_sha256`, reinstalación). Toda ISO escrita debe estar en la
     tabla o el SDK revienta. Falta medir qué hacen `pre_gain` y `post_gain` con el techo. Los DNG de la Pocket OG no
     traen ISO en EXIF, así que la ISO se deduciría de BE.
3. **Varias revisiones del perfil** (una por BE, con `stops_above_mid_grey_at_native_iso` distinto, elegida con `ctrn`).
   - A favor: offset independiente del usuario.
   - En contra: multiplica perfiles y calibraciones, y es lo más difícil de mantener.
4. **Sidecar `.sidecar`** generado junto al BRAW con la línea temporal `exposure`.
   - A favor: formato del SDK; no destructivo.
   - En contra: fichero externo que se pierde al mover el BRAW; está en la misma capa que los ajustes del usuario
     (Resolve lo reescribe); no aporta nada sobre la opción 1.
5. **Descartadas por evidencia**: `agpf` ≠ 1 (segfault), `analog_gain` de clip (sin efecto en lineal), `exposure`
   de clip (no admitido), sidecar escalar (no se parsea), ISO fuera del perfil (segfault) y ganancia RGB antes de
   codificar (destructiva).

### Recomendación provisional

- Para que Resolve abra con la exposición correcta sin tocar la calibración, la candidata es la opción 1.
- Si hace falta que la compensación sea independiente del control creativo del usuario, la candidata es la opción 2,
  después de un experimento controlado con un perfil de prueba instalado aparte.
- Siguiente paso propuesto: abrir en Resolve la copia `out/trace-run/exp_expo2.braw` y comprobar el panel RAW con
  «Camera metadata», «Clip» y «Project».
