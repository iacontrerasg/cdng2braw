#pragma once
// Escritura de eventos JSON (una línea por evento, como emit() en Python) y base64.
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <string>
#include <vector>

namespace cdng::json {

inline std::string quote(const std::string &text) {
    std::string out = "\"";
    for (unsigned char c : text) {
        switch (c) {
        case '"': out += "\\\""; break;
        case '\\': out += "\\\\"; break;
        case '\n': out += "\\n"; break;
        case '\r': out += "\\r"; break;
        case '\t': out += "\\t"; break;
        default:
            if (c < 0x20) { char b[8]; std::snprintf(b, sizeof b, "\\u%04x", c); out += b; }
            else out += char(c);   // UTF-8 tal cual, como ensure_ascii=False
        }
    }
    return out + "\"";
}

// Número como lo escribe json.dumps de Python para un float (5.0, 0.25…); null si no es finito.
inline std::string number(double value) {
    if (!std::isfinite(value)) return "null";
    char b[32];
    if (value == std::floor(value) && std::fabs(value) < 1e15) std::snprintf(b, sizeof b, "%.1f", value);
    else {
        // La representación más corta que vuelve al mismo valor, como repr() en Python.
        for (int precision = 1; precision <= 17; ++precision) {
            std::snprintf(b, sizeof b, "%.*g", precision, value);
            if (std::strtod(b, nullptr) == value) break;
        }
    }
    return b;
}

// Objeto JSON construido campo a campo, en orden.
class Object {
    std::string body_;
    void key(const char *name) { if (!body_.empty()) body_ += ", "; body_ += quote(name) + ": "; }
public:
    Object &raw(const char *name, const std::string &value) { key(name); body_ += value; return *this; }
    Object &str(const char *name, const std::string &value) { return raw(name, quote(value)); }
    Object &num(const char *name, double value) { return raw(name, number(value)); }
    Object &integer(const char *name, long long value) { return raw(name, std::to_string(value)); }
    Object &boolean(const char *name, bool value) { return raw(name, value ? "true" : "false"); }
    Object &null(const char *name) { return raw(name, "null"); }
    Object &object(const char *name, const Object &value) { return raw(name, value.text()); }
    std::string text() const { return "{" + body_ + "}"; }
};

inline std::string base64(const std::vector<uint8_t> &data) {
    static const char *alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    std::string out; out.reserve((data.size() + 2) / 3 * 4);
    std::size_t i = 0;
    for (; i + 2 < data.size(); i += 3) {
        const uint32_t v = uint32_t(data[i]) << 16 | uint32_t(data[i + 1]) << 8 | data[i + 2];
        out += alphabet[v >> 18]; out += alphabet[v >> 12 & 63]; out += alphabet[v >> 6 & 63]; out += alphabet[v & 63];
    }
    if (i < data.size()) {
        const uint32_t v = uint32_t(data[i]) << 16 | (i + 1 < data.size() ? uint32_t(data[i + 1]) << 8 : 0);
        out += alphabet[v >> 18]; out += alphabet[v >> 12 & 63];
        out += i + 1 < data.size() ? alphabet[v >> 6 & 63] : '='; out += '=';
    }
    return out;
}

}  // namespace cdng::json
