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
`sourceInfo` (`native/engine/worker/common.cpp`) lo copia a `info.gain`, y `develop` → `bayerPlanesSized` →
`bayerFields` (`native/engine/signal.cpp`) multiplica cada muestra por ese factor **antes** del mapa `sqrt`
(`encoding_max = 4095`). Después, los códigos se recortan al rango del plano. Cualquier ganancia mayor que 1
empuja las luces altas por encima del rango calibrado y se recortan en el código, que queda fijado en el techo
del perfil.

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
- El informe de empaquetado indica que el perfil de cámara no está instalado en `/Library/Application Support/…`
  en esta máquina; para probar en Resolve hay que instalarlo desde la app.

## Qué comprobar

1. Convertir los mismos clips OG con ISO distintos que reproducían el defecto.
2. Medir en Resolve (o con `tools/brawdec`) el valor lineal máximo de las luces altas.
3. Interpretación:
   - Si el techo deja de ser ~2.1434 y las luces se recuperan bajando la exposición, el recorte lo causa aplicar
     la ganancia antes de codificar; la compensación debería ir en los metadatos del clip o tras la decodificación.
   - Si el techo persiste, el límite viene de la calibración o del mapa de señal y hay que buscar en otro sitio.
4. Esperado con este cambio: la exposición aparente vuelve a la de la referencia calibrada (no depende de
   BaselineExposure), así que los clips con otro ISO se verán más claros o más oscuros. Es lo previsto en esta prueba.
