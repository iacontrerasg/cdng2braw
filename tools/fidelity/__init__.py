"""Banco de fidelidad de imagen: CinemaDNG → BRAW (worker) → RGB float lineal (brawdec) frente a referencias.

Dos modalidades: fidelidad frente a la exportación OpenEXR del CinemaDNG en DaVinci Resolve, y regresión frente a la
salida congelada de una compilación de referencia. Ver docs/image-fidelity-benchmark.md.
"""
# 2: setup incluye inputs[] del tramo, experimental_env efectivo, verificación de revelado
#    (solicitado/aplicado/no verificable) y conteos de cromaticidad válida/inválida.
SCHEMA = 2
