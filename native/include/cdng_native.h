#pragma once

#include <stddef.h>
#include <stdint.h>

#if defined(CDNG_NATIVE_STATIC)
# define CDNG_API
#elif defined(_WIN32)
# if defined(CDNG_NATIVE_BUILD)
#  define CDNG_API __declspec(dllexport)
# else
#  define CDNG_API __declspec(dllimport)
# endif
#else
# define CDNG_API __attribute__((visibility("default")))
#endif

#ifdef __cplusplus
extern "C" {
#endif

/* ABI 3 (classes 0..11). Pointers are borrowed for the duration of each synchronous call.
 * All inputs are native-endian. Codes are written most significant bit first.
 * No camera calibration, quantization, or floating point operation lives here. */
typedef struct {
    uint8_t *data;
    size_t size;
} CdngBuffer;

typedef struct {
    const int64_t *ac;             /* block-major, 63 Y or 31 chroma coefficients */
    const uint64_t *dc_values;
    const uint8_t *dc_lengths;
    size_t blocks;
    size_t coefficients;
} CdngPlane;

typedef struct {
    const uint64_t *prefix_values; /* [4][2][63][12] */
    const uint8_t *prefix_lengths;
    const uint64_t *final_values;  /* [2][63][12] */
    const uint8_t *final_lengths;
    const uint8_t *drop;           /* [63][12] */
    const int64_t *adjust;         /* [12] */
    uint64_t eob_value;
    uint8_t eob_length;
} CdngEntropyTables;

typedef struct {
    uint64_t fallback;
    uint64_t clamped;
    uint64_t dropped;
} CdngEntropyStats;

CDNG_API uint32_t cdng_native_abi(void);
/* Returns 0 on success, 1 for invalid input, 2 on allocation failure.
 * Output must be initialized to {NULL, 0}; release it using cdng_buffer_free.
 * On failure output stays empty and stats stay zero. No exception crosses ABI. */
CDNG_API int cdng_pack_bits(const uint64_t *values, const uint8_t *lengths,
                           size_t count, CdngBuffer *output);
CDNG_API int cdng_encode_slice(const CdngPlane *y, const CdngPlane *cb,
                              const CdngPlane *cr, const CdngEntropyTables *tables,
                              CdngBuffer *output, CdngEntropyStats *stats);
CDNG_API void cdng_buffer_free(CdngBuffer *buffer);

#ifdef __cplusplus
}
#endif
