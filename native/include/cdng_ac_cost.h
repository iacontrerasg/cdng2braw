#pragma once
/* Oráculo de coste AC (EXP-003b): misma lógica que cdng_encode_slice / block().
 * No altera el bitstream; solo contabiliza o prepara niveles tras clamp/adjust/drop. */
#include "cdng_native.h"
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    uint64_t ac_prefix_bits;
    uint64_t ac_final_bits;
    uint64_t ac_magnitude_bits;
    uint64_t eob_bits;
    uint64_t total_bits;       /* suma de lo anterior (= coste AC del bloque) */
    uint64_t ac_coded;         /* símbolos AC emitidos (tras drop) */
    uint64_t clamped;
    uint64_t dropped;
    uint64_t fallback;
} CdngAcBlockCost;

/* Copia ac[0..coefficients) aplicando clamp → adjust → drop (hasta 2 pasadas).
 * coefficients debe ser 63 (Y) o 31 (C). ac_out puede ser NULL si solo se quieren stats.
 * Devuelve 0 ok, 1 entrada inválida. */
CDNG_API int cdng_ac_prepare(const int64_t *ac_in, size_t coefficients,
                             const CdngEntropyTables *tables,
                             int64_t *ac_out, CdngEntropyStats *stats);

/* Coste exacto de emitir los AC de un bloque + EOB (sin DC), misma ruta que el encoder.
 * Si ac_effective != NULL, escribe los niveles tras prepare (tamaño coefficients).
 * Devuelve 0 ok, 1 inválido. */
CDNG_API int cdng_ac_block_cost(const int64_t *ac_in, size_t coefficients,
                                const CdngEntropyTables *tables,
                                CdngAcBlockCost *out, int64_t *ac_effective);

/* Coste de un único símbolo AC (run, nivel≠0) + opcionalmente EOB aparte.
 * Útil para trellis; debe coincidir con un put() del encoder para ese (run,ℓ).
 * Devuelve bits (>0) o 0 si el (run,clase) no es codificable (p.ej. drop). */
CDNG_API uint64_t cdng_ac_symbol_bits(size_t run, int64_t level,
                                      const CdngEntropyTables *tables);

CDNG_API uint64_t cdng_ac_eob_bits(const CdngEntropyTables *tables);

#ifdef __cplusplus
}
#endif
