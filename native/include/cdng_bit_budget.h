#pragma once
/* Instrumentación opcional del presupuesto de bits (EXP-002a).
 * No altera el payload: solo contabiliza lo que ya escribe cdng_encode_slice.
 * Activar con CDNG_BIT_BUDGET=1 (o cualquier valor distinto de vacío/0). */
#include "cdng_native.h"
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

enum { CDNG_PLANE_Y = 0, CDNG_PLANE_CB = 1, CDNG_PLANE_CR = 2, CDNG_PLANES = 3 };

typedef struct {
    uint64_t dc_bits[CDNG_PLANES];
    uint64_t ac_prefix_bits[CDNG_PLANES];   /* códigos de prefijo VLC AC */
    uint64_t ac_final_bits[CDNG_PLANES];    /* código final (run/clase) */
    uint64_t ac_magnitude_bits[CDNG_PLANES];/* magnitud + signo (k+1 bits) */
    uint64_t eob_bits[CDNG_PLANES];
    uint64_t marker_bits;                  /* 0xff entre grupos de 2Y+Cb+Cr */
    uint64_t padding_bits;                 /* alineación a 8 bytes del slice */
    uint64_t coded_bits;                   /* suma antes de padding */
    uint64_t payload_bytes;                /* tamaño del buffer emitido */
    uint64_t blocks_y, blocks_c;
    uint64_t ac_nonzero[CDNG_PLANES];
    uint64_t ac_coded[CDNG_PLANES];        /* símbolos AC emitidos (tras drop) */
    uint64_t eob_count[CDNG_PLANES];
    uint64_t marker_count;
} CdngBitBudget;

CDNG_API int cdng_bit_budget_enabled(void);
CDNG_API void cdng_bit_budget_reset(void);
CDNG_API void cdng_bit_budget_add(const CdngBitBudget *slice);
CDNG_API void cdng_bit_budget_get(CdngBitBudget *out);
/* JSON compacto en buf; return bytes escritos (sin NUL) o -1 si no cabe. */
CDNG_API int cdng_bit_budget_json(char *buf, size_t cap);

#ifdef __cplusplus
}
#endif
