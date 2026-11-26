# Backlog

La campaña visual EXP-004 / EXP-005 / EXP-005b / EXP-005c está **cerrada**.
Esta lista no autoriza investigación de halos ni de compresión en `feat/pocket-og-cinemadng-workflow`.

## P0 — BUG-002 (abierto)

Defecto residual del revelado Pocket OG frente a CinemaDNG, bloqueante de aptitud de producción. **No** se exige cerrarlo en FEAT-001.

- **Halos cromáticos** en bordes de alto contraste (ROI `white_strip` y similares).
- **False color periódico** en detalle fino (reglas del ColorChecker, `cc_ruler_left` / `cc_ruler_right`).

Cierre documentado de líneas ya evaluadas: compensación RGB post-AHD (EXP-004/005/005b, clase D); mosaico→RGB→NativeSensor (EXP-005c prototipo, clase C). Evitar AHD no basta.

## Investigación posterior (no esta rama)

- **ChannelCodec específico Pocket OG:** EXP-005c I — potencialmente viable; faltan curva OG como `ChannelCodec`, fase CFA GBRG vs sitios RGGB, colorimetría SDK y medición de BUG-002. No reutilizar identidad Sigma fp.
- **AAHD (y otros demosaicos LibRaw):** EXP-005 lo dejó como evaluación de zippering/false color en S1, no como arreglo del halo. Pendiente de una campaña dedicada, no de FEAT-001.
- **Soluciones arquitectónicas posibles:** subsample 4:2:0 no lineal + upsample SDK opaco; siting C0; ChannelCodec OG; demosaic distinto de AHD. Sin imposibilidad matemática global demostrada.

## Producto (otra línea)

- **FEAT-001** Native CinemaDNG workflow compatibility (esta rama: viewing opt-in DWG/Intermediate; LUT embebida no viable). El look objetivo de producto **no** es Gen 1.
- **FEAT-001b** Equivalencia CinemaDNG **Gen 4** / Blackmagic Design / Film frente a BRAW **Gen 5** / Blackmagic Design / Film, con Gamma Controls activos. Entrega A+B: [feat-001b-cinemadng-gen4-match.md](feat-001b-cinemadng-gen4-match.md). Sin LUT nueva, sin cambio de checkbox ni defaults hasta validar.
- Perfiles máxima fidelidad / producción 3:1 y ratios 5:1–12:1 (fuera de alcance aquí).

No EXP-005d / 006 / 007 / 008 en esta rama.
