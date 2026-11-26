#pragma once
// Publicación diagnóstica del presupuesto de bits / coeficientes (EXP-002a).
// No altera el bitstream: solo escribe JSONL bajo CDNG_STATS_DIR.
#include "codec.hpp"

#include <cstddef>
#include <string>

namespace cdng {

// Contexto del fotograma en curso (thread_local). Lo fija el worker antes de encode*.
struct EncodeStatsContext {
    std::string clip;
    int frame_index = -1;       // índice en la secuencia enviada al worker
    std::string source_name;    // nombre del DNG (identidad inequívoca del fotograma)
    int q_requested = -1;       // q del manifiesto / petición; -1 si ratio
    double ratio_requested = -1;
    std::string mode;           // "quality" | "ratio"
    std::string label;          // opcional: conversion_key u otra etiqueta del banco
};

void encodeStatsSetContext(const EncodeStatsContext &ctx);
void encodeStatsClearContext();
EncodeStatsContext encodeStatsContext();

bool encodeStatsEnabled();

// Rellena frame.diag a partir del presupuesto / histograma actuales (sin escribir).
void encodeStatsCapture(EncodedFrame &frame, int q_tried, bool strict, double qY, double qC,
                        const std::string &coeff_json);

// Publica una línea JSONL completa (mutex). selected=true → bit-budget.jsonl + coeff-stats.jsonl;
// selected=false → encode-attempts.jsonl.
// stage: "selected" | "sample_written" | "conversion_completed" (identidad del informe).
void encodeStatsPublish(const EncodedFrame &frame, bool selected, const char *outcome,
                        const char *stage = nullptr, const std::string &chunk_sha256 = {});

// Atajo: intento descartado sin EncodedFrame completo (p. ej. CodecRangeError antes de return).
void encodeStatsPublishDiscarded(int q_tried, int attempt_index, const char *outcome);

}  // namespace cdng
