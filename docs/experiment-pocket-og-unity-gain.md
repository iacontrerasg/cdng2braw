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

### Pendiente

- Cerrar Resolve (o retirar los clips del proyecto), vaciar la Papelera, importar los BRAW de `~/Desktop/BRAW/` y
  repetir la medida. Lo esperado con ganancia unitaria: a 0 EV la imagen sale 2 EV más oscura que el DNG de ISO 1600
  y con el techo en 2.1434; a +2 EV en el RAW de Resolve, las luces llegan a ≈ 8.57 sin recorte prematuro.
