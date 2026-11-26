// Eventos JSON del worker: cada línea emitida debe ser JSON válido (simdjson).
#include "../worker/json.hpp"
#include "check.hpp"

#include <simdjson.h>

#include <string>
#include <vector>

using namespace cdng;

namespace {

bool parse_line(const std::string &line, simdjson::dom::element &out) {
    static simdjson::dom::parser parser;
    return !parser.parse(line).get(out);
}

// Réplica del merge de emit(): { "event": kind, …campos… }
std::string emit_line(const char *kind, const json::Object &values) {
    const auto line = json::Object().str("event", kind).text();
    const auto body = values.text();
    return line.substr(0, line.size() - 1) + (body.size() > 2 ? ", " + body.substr(1) : "}");
}

}  // namespace

int main() {
    // Eventos típicos del worker (started con og_deblock_precomp booleano, como convert.cpp).
    const std::vector<std::string> lines = {
        emit_line("ready", json::Object().str("encoder", "native").str("profile", "auto").boolean("frozen", true)),
        emit_line("started", json::Object()
                                 .integer("total", 2).integer("width", 1920).integer("height", 1080).integer("workers", 2)
                                 .num("fps", 24.0).str("compression", "ratio")
                                 .raw("q", "null").num("ratio", 3.0)
                                 .num("noise_scale", 1.0)
                                 .raw("nr", "[0.0, 0.0]")
                                 .boolean("noise_synthesis", false)
                                 .boolean("og_deblock_precomp", true)
                                 .str("build", "test").str("trace", "")),
        emit_line("started", json::Object().boolean("og_deblock_precomp", false).boolean("noise_synthesis", true)),
        emit_line("progress", json::Object().integer("done", 1).integer("total", 2).integer("workers", 2)),
        emit_line("completed", json::Object().str("output", "/tmp/x.braw").integer("frames", 2)
                                   .raw("gyro", "null").raw("ratio_requested", json::number(3.0))),
        emit_line("error", json::Object().str("message", "fallo")),
        emit_line("cancelled", json::Object()),
    };
    for (const auto &line : lines) {
        simdjson::dom::element doc;
        CHECK(parse_line(line, doc));
        std::string_view ev;
        CHECK(!doc["event"].get_string().get(ev));
        CHECK(!ev.empty());
    }

    // El bug P1: .raw(..., "on") sin comillas → JSON inválido.
    {
        const auto bad = emit_line("started", json::Object().raw("og_deblock_precomp", "on"));
        simdjson::dom::element doc;
        CHECK(!parse_line(bad, doc));
    }
    // .str("on") y .boolean(true) sí son válidos.
    {
        simdjson::dom::element doc;
        CHECK(parse_line(emit_line("started", json::Object().str("og_deblock_precomp", "on")), doc));
        CHECK(parse_line(emit_line("started", json::Object().boolean("og_deblock_precomp", true)), doc));
        bool flag = false;
        CHECK(!doc["og_deblock_precomp"].get_bool().get(flag));
        CHECK(flag);
    }
    return check::finish("json");
}
