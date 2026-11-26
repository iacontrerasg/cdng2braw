#include "encode_stats.hpp"

#include "cdng_bit_budget.h"
#include "geometry.hpp"
#include "signal_internal.hpp"

#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <mutex>
#include <sstream>

namespace cdng {
namespace {

thread_local EncodeStatsContext g_ctx{};
std::mutex g_stats_mu;

bool envOn(const char *name) {
    const char *v = std::getenv(name);
    return v && *v && std::string(v) != "0";
}

std::string statsDir() {
    const char *dir = std::getenv("CDNG_STATS_DIR");
    return dir && *dir ? std::string(dir) : std::string(".");
}

std::string payloadSha(const EncodedFrame &frame) {
    std::vector<uint8_t> buf;
    buf.reserve(frame.size);
    for (const auto &s : frame.slices) buf.insert(buf.end(), s.begin(), s.end());
    return sha256Hex(buf);
}

void appendLine(const std::string &path, const std::string &line) {
    std::lock_guard<std::mutex> lock(g_stats_mu);
    std::ofstream f(path, std::ios::app | std::ios::binary);
    f.write(line.data(), std::streamsize(line.size()));
    if (!line.empty() && line.back() != '\n') f.put('\n');
}

std::string escape(const std::string &s) {
    std::string o;
    o.reserve(s.size() + 8);
    for (char c : s) {
        if (c == '\\' || c == '"') o.push_back('\\');
        o.push_back(c);
    }
    return o;
}

std::string commonFields(const EncodedFrame *frame, int q_tried, int attempt_index, bool selected,
                         bool strict, const char *outcome, const char *stage,
                         const std::string &chunk_sha256) {
    const auto ctx = g_ctx;
    std::ostringstream o;
    o << "\"selected\":" << (selected ? "true" : "false")
      << ",\"discarded\":" << (selected ? "false" : "true")
      << ",\"attempt_index\":" << attempt_index
      << ",\"q_tried\":" << q_tried
      << ",\"q_effective\":" << (frame ? frame->q : q_tried)
      << ",\"q_requested\":" << ctx.q_requested
      << ",\"ratio_requested\":" << ctx.ratio_requested
      << ",\"strict\":" << (strict ? "true" : "false")
      << ",\"outcome\":\"" << escape(outcome ? outcome : "") << "\""
      << ",\"stage\":\"" << escape(stage && *stage ? stage : (selected ? "selected" : "discarded")) << "\""
      << ",\"frame_index\":" << ctx.frame_index
      << ",\"source_name\":\"" << escape(ctx.source_name) << "\""
      << ",\"clip\":\"" << escape(ctx.clip) << "\""
      << ",\"mode\":\"" << escape(ctx.mode) << "\""
      << ",\"label\":\"" << escape(ctx.label) << "\"";
    if (frame) {
        o << ",\"frame_size_bytes\":" << frame->size
          << ",\"first_slice_bytes\":" << FirstSlice
          << ",\"slices_payload_bytes\":" << (frame->size >= FirstSlice ? frame->size - FirstSlice : 0)
          << ",\"attempts\":" << frame->stats.attempts
          << ",\"payload_sha256\":\"" << (frame->diag.payload_sha256.empty() ? payloadSha(*frame)
                                                                            : frame->diag.payload_sha256)
          << "\"";
        if (!chunk_sha256.empty()) o << ",\"chunk_sha256\":\"" << escape(chunk_sha256) << "\"";
    }
    return o.str();
}

}  // namespace

void encodeStatsSetContext(const EncodeStatsContext &ctx) { g_ctx = ctx; }
void encodeStatsClearContext() { g_ctx = {}; }
EncodeStatsContext encodeStatsContext() { return g_ctx; }

bool encodeStatsEnabled() {
    return envOn("CDNG_BIT_BUDGET") || envOn("CDNG_COEFF_STATS");
}

void encodeStatsCapture(EncodedFrame &frame, int q_tried, bool strict, double qY, double qC,
                        const std::string &coeff_json) {
    if (!encodeStatsEnabled()) return;
    frame.diag.present = true;
    frame.diag.q_tried = q_tried;
    frame.diag.strict = strict;
    frame.diag.qY = qY;
    frame.diag.qC = qC;
    frame.diag.slices_payload_bytes = frame.size >= FirstSlice ? frame.size - FirstSlice : 0;
    frame.diag.payload_sha256 = payloadSha(frame);
    if (cdng_bit_budget_enabled()) {
        char json[4096];
        if (cdng_bit_budget_json(json, sizeof json) > 0) frame.diag.budget_json = json;
    }
    frame.diag.coeff_json = coeff_json;
}

void encodeStatsPublish(const EncodedFrame &frame, bool selected, const char *outcome,
                        const char *stage, const std::string &chunk_sha256) {
    if (!frame.diag.present && !encodeStatsEnabled()) return;
    if (!frame.diag.present) return;
    const int attempt = frame.diag.attempt_index > 0 ? frame.diag.attempt_index : frame.stats.attempts;
    const std::string common = commonFields(&frame, frame.diag.q_tried ? frame.diag.q_tried : frame.q,
                                            attempt, selected, frame.diag.strict, outcome, stage, chunk_sha256);
    const std::string base = statsDir();
    // Informes oficiales: solo sample_written (o selected legacy si no se indica stage).
    const bool authoritative = selected && (!stage || !*stage || std::string(stage) == "sample_written" ||
                                            std::string(stage) == "selected");
    if (selected && authoritative) {
        if (!frame.diag.budget_json.empty()) {
            std::ostringstream line;
            line << "{" << common << ",\"budget\":" << frame.diag.budget_json << "}\n";
            appendLine(base + "/bit-budget.jsonl", line.str());
        }
        if (!frame.diag.coeff_json.empty()) {
            std::ostringstream line;
            line << "{" << common << "," << frame.diag.coeff_json << "}\n";
            appendLine(base + "/coeff-stats.jsonl", line.str());
        }
    } else if (!selected) {
        std::ostringstream line;
        line << "{" << common;
        if (!frame.diag.budget_json.empty()) line << ",\"budget\":" << frame.diag.budget_json;
        if (!frame.diag.coeff_json.empty()) line << "," << frame.diag.coeff_json;
        line << "}\n";
        appendLine(base + "/encode-attempts.jsonl", line.str());
    }
}

void encodeStatsPublishDiscarded(int q_tried, int attempt_index, const char *outcome) {
    if (!encodeStatsEnabled()) return;
    const std::string common = commonFields(nullptr, q_tried, attempt_index, false, true, outcome,
                                            "discarded", {});
    appendLine(statsDir() + "/encode-attempts.jsonl", "{" + common + "}\n");
}

}  // namespace cdng
