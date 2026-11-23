// Bloque C — tablas del códec y DCT. Ver codec_tables.hpp.
#include "codec_tables.hpp"
#include "codec.hpp"

#include <simdjson.h>

#include <algorithm>
#include <cmath>

namespace cdng {

const std::array<int, 63> ScanY = [] {
    constexpr int uv[63][2] = {
        {0, 1}, {1, 0}, {2, 0}, {1, 1}, {0, 2}, {0, 3}, {1, 2}, {2, 1}, {3, 0}, {4, 0}, {3, 1}, {2, 2}, {1, 3}, {0, 4}, {0, 5}, {1, 4},
        {2, 3}, {3, 2}, {4, 1}, {5, 0}, {6, 0}, {5, 1}, {4, 2}, {3, 3}, {2, 4}, {1, 5}, {0, 6}, {0, 7}, {1, 6}, {2, 5}, {3, 4}, {4, 3},
        {5, 2}, {6, 1}, {7, 0}, {7, 1}, {6, 2}, {5, 3}, {4, 4}, {3, 5}, {2, 6}, {1, 7}, {2, 7}, {3, 6}, {4, 5}, {5, 4}, {6, 3}, {7, 2},
        {7, 3}, {6, 4}, {5, 5}, {4, 6}, {3, 7}, {4, 7}, {5, 6}, {6, 5}, {7, 4}, {7, 5}, {6, 6}, {5, 7}, {6, 7}, {7, 6}, {7, 7}};
    std::array<int, 63> s{};
    for (int i = 0; i < 63; ++i) s[i] = uv[i][0] * 8 + uv[i][1];
    return s;
}();

const std::array<int, 31> ScanC = [] {
    constexpr int uv[31][2] = {
        {0, 1}, {0, 2}, {0, 3}, {1, 0}, {1, 1}, {2, 0}, {2, 1}, {1, 2}, {1, 3}, {0, 4}, {0, 5}, {0, 6}, {0, 7}, {1, 4}, {1, 5},
        {2, 2}, {2, 3}, {3, 0}, {3, 1}, {3, 2}, {3, 3}, {2, 4}, {2, 5}, {1, 6}, {1, 7}, {2, 6}, {2, 7}, {3, 4}, {3, 5}, {3, 6}, {3, 7}};
    std::array<int, 31> s{};
    for (int i = 0; i < 31; ++i) s[i] = uv[i][0] * 8 + uv[i][1];
    return s;
}();

namespace {

// Matriz de la DCT-II ortonormal de longitud N: M[k][n] = s(k) · cos(π (2n + 1) k / 2N).
template <int N> struct DctMatrix {
    double m[N][N];
    DctMatrix() {
        const double pi = std::acos(-1.0);
        for (int k = 0; k < N; ++k)
            for (int n = 0; n < N; ++n)
                m[k][n] = (k ? std::sqrt(2.0 / N) : std::sqrt(1.0 / N)) * std::cos(pi * (2 * n + 1) * k / (2.0 * N));
    }
};
const DctMatrix<8> M8;
const DctMatrix<4> M4;

// Bloque H × 8: primero por columnas (eje de las filas, como dctn sobre axes=(2, 3)) y luego por filas.
template <int H> void dctRows8(const double *in, double *out, const DctMatrix<H> &mh) {
    double tmp[H][8];
    for (int u = 0; u < H; ++u)
        for (int x = 0; x < 8; ++x) {
            double s = 0;
            for (int y = 0; y < H; ++y) s += mh.m[u][y] * in[y * 8 + x];
            tmp[u][x] = s;
        }
    for (int u = 0; u < H; ++u)
        for (int v = 0; v < 8; ++v) {
            double s = 0;
            for (int x = 0; x < 8; ++x) s += tmp[u][x] * M8.m[v][x];
            out[u * 8 + v] = s;
        }
}

uint64_t bits(const std::string &s) {
    uint64_t v = 0;
    for (char c : s) v = v << 1 | uint64_t(c == '1');
    return v;
}

double median(std::vector<double> v) {   // np.median con un número impar de valores: el central
    std::sort(v.begin(), v.end());
    if (v.size() % 2) return v[v.size() / 2];
    return (v[v.size() / 2 - 1] + v[v.size() / 2]) / 2;
}

}  // namespace

void dct8x8(const double *in, double *out) { dctRows8(in, out, M8); }
void dct4x8(const double *in, double *out) { dctRows8(in, out, M4); }

namespace {
template <int H> void idctRows8(const double *in, double *out, const DctMatrix<H> &mh) {
    double tmp[H][8];
    for (int u = 0; u < H; ++u)
        for (int x = 0; x < 8; ++x) {
            double s = 0;
            for (int v = 0; v < 8; ++v) s += in[u * 8 + v] * M8.m[v][x];
            tmp[u][x] = s;
        }
    for (int y = 0; y < H; ++y)
        for (int x = 0; x < 8; ++x) {
            double s = 0;
            for (int u = 0; u < H; ++u) s += mh.m[u][y] * tmp[u][x];
            out[y * 8 + x] = s;
        }
}
}  // namespace

void idct8x8(const double *in, double *out) { idctRows8(in, out, M8); }
void idct4x8(const double *in, double *out) { idctRows8(in, out, M4); }

CodecTables CodecTables::load(const std::filesystem::path &vlcTable, const std::filesystem::path &quantSteps) {
    CodecTables t;
    simdjson::dom::parser parser;
    try {
        // La tabla AC y el ZRL vienen del JSON; el DC y el EOB son fijos.
        simdjson::padded_string vlc = simdjson::padded_string::load(vlcTable.string());
        simdjson::dom::element doc = parser.parse(vlc);
        for (auto field : doc["ac"].get_object()) {
            const std::string key(field.key);
            const auto comma = key.find(',');
            t.ac[{std::stoi(key.substr(0, comma)), std::stoi(key.substr(comma + 1))}] = std::string(std::string_view(field.value));
        }
        std::string_view zrl;
        if (doc["zrl"].get(zrl) == simdjson::SUCCESS && !zrl.empty()) t.zrl = std::string(zrl);
    } catch (const simdjson::simdjson_error &) {
        throw Error("The embedded calibration is damaged: vlc-table.json");
    } catch (const std::invalid_argument &) {
        throw Error("The embedded calibration is damaged: vlc-table.json");
    }
    try {
        // steps(q) con FLAT_STEPS: paso plano; en croma, la mediana de las entradas AC corregidas por el sesgo de q = 8. El JSON
        // lleva NaN en "raw" (json.dump de Python), que no es JSON válido: se lee bajo demanda solo "steps".
        simdjson::padded_string json = simdjson::padded_string::load(quantSteps.string());
        simdjson::ondemand::parser lazy;
        simdjson::ondemand::document doc = lazy.iterate(json);
        struct Raw { double yUnit, cUnit; std::vector<double> y, c; };
        std::map<int, Raw> raw;
        const auto matrix = [](simdjson::ondemand::array rows) {
            std::vector<double> out;
            for (auto row : rows)
                for (auto value : row.get_array()) out.push_back(double(value));
            return out;
        };
        for (auto field : doc["steps"].get_object()) {
            const int q = std::stoi(std::string(std::string_view(field.unescaped_key())));
            simdjson::ondemand::object s = field.value().get_object();
            Raw r;
            r.yUnit = double(s["y_dc_unit"]);
            r.cUnit = double(s["c_dc_unit"]);
            r.y = matrix(s["y_ac"].get_array());
            r.c = matrix(s["c_ac"].get_array());
            if (r.y.size() != 64 || r.c.size() != 32) throw Error("The embedded calibration is damaged: quant_steps.json");
            raw[q] = std::move(r);
        }
        if (raw.empty() || !raw.count(8) || raw.begin()->first != 0 || raw.rbegin()->first != int(raw.size()) - 1)
            throw Error("The embedded calibration is damaged: quant_steps.json");
        const auto &refC = raw[8].c;
        std::map<int, QuantSteps> byQ;
        for (const auto &[q, r] : raw) {
            std::vector<double> c;
            for (std::size_t i = 1; i < 32; ++i) c.push_back(r.c[i] * (6.185 / refC[i]));
            // El paso AC de Y del decodificador sigue exactamente 12,386·(q + 4)/12 (la misma ley que el DC). La mediana
            // medida en cada q se aparta hasta un 1,2 % (q 0–2, q 13, q 23) y el descodificado perdía ese contraste.
            byQ[q] = QuantSteps{r.yUnit, r.cUnit, 12.386 * (q + 4) / 12, median(c)};
        }
        for (auto &[q, s] : byQ) t.quant.push_back(s);
    } catch (const simdjson::simdjson_error &) {
        throw Error("The embedded calibration is damaged: quant_steps.json");
    } catch (const std::invalid_argument &) {
        throw Error("The embedded calibration is damaged: quant_steps.json");
    }
    t.build();
    return t;
}

const QuantSteps &CodecTables::steps(int q) const {
    if (q < 0 || q >= int(quant.size()))
        throw Error("cuantizador " + std::to_string(q) + " fuera de la tabla medida (0…" + std::to_string(quant.size() - 1) + ")");
    return quant[std::size_t(q)];
}

std::pair<std::string, std::string> CodecTables::fallbackBits(int run, int k, char signBit) const {
    const auto code = [&](int r, int c) -> const std::string & {
        const auto it = ac.find({r, c});
        if (it == ac.end()) throw Error("sin recurso para (" + std::to_string(run) + ", " + std::to_string(k) + ")");
        return it->second;
    };
    if (ac.count({run, k})) return {"", ac.at({run, k})};
    if (run >= 16 && !zrl.empty() && ac.count({run % 16, k})) {
        std::string pre;
        for (int i = 0; i < run / 16; ++i) pre += zrl;
        return {pre, ac.at({run % 16, k})};
    }
    if (run >= 16) {
        auto [pre, fin] = fallbackBits(run - 16, k, signBit);
        return {code(15, 0) + signBit + pre, fin};
    }
    int r2 = run - 1;
    while (r2 >= 0 && !ac.count({r2, 0})) --r2;
    if (r2 < 0) throw Error("sin recurso para (" + std::to_string(run) + ", " + std::to_string(k) + ")");
    std::string fill;
    for (int i = 0; i < run - 1 - r2; ++i) fill += code(0, 0) + "1";
    auto [pre, fin] = fallbackBits(0, k, signBit);
    return {code(r2, 0) + signBit + fill + pre, fin};
}

void CodecTables::build() {
    const int nr = AcRuns, K = AcClasses;
    preVal.assign(std::size_t(AcPieces) * 2 * nr * K, 0); preLen.assign(preVal.size(), 0);
    finVal.assign(std::size_t(2) * nr * K, 0); finLen.assign(finVal.size(), 0);
    drop.assign(std::size_t(nr) * K, 0); adjust.assign(std::size_t(K), 0);
    // clases sin código con carrera 0: el valor se ajusta a la clase vecina antes de codificar
    for (int k = 0; k < K; ++k)
        if (!ac.count({0, k})) adjust[k] = (ac.count({0, k + 1}) && k + 1 < K) ? (int64_t(1) << (k + 1)) : (int64_t(1) << k) - 1;
    for (int run = 0; run < nr; ++run)
        for (int k = 0; k < K; ++k) {
            const auto it = ac.find({run, k});
            if (it != ac.end()) {
                for (int sg = 0; sg < 2; ++sg) {
                    finVal[(sg * nr + run) * K + k] = bits(it->second);
                    finLen[(sg * nr + run) * K + k] = uint8_t(it->second.size());
                }
                continue;
            }
            if (adjust[k]) continue;
            if (k == 0 && run >= 16 && !(!zrl.empty() && ac.count({run % 16, 0}))) drop[run * K + k] = 1;
            for (int sg = 0; sg < 2; ++sg) {
                const auto [pre, fin] = fallbackBits(run, k, sg ? '1' : '0');
                finVal[(sg * nr + run) * K + k] = bits(fin);
                finLen[(sg * nr + run) * K + k] = uint8_t(fin.size());
                const std::size_t pieces = (pre.size() + 59) / 60;
                if (pieces > std::size_t(AcPieces)) throw Error("fallback prefix too long");
                for (std::size_t p = 0; p < pieces; ++p) {
                    const auto chunk = pre.substr(p * 60, 60);
                    const auto pos = ((p * 2 + sg) * nr + run) * K + k;
                    preVal[pos] = bits(chunk);
                    preLen[pos] = uint8_t(chunk.size());
                }
            }
        }
    // Tabla DC (clase 12 incluida) y DC_ZERO = "00"
    static const char *const dc[13] = {"00", "010", "011", "100", "101", "110", "11100", "11101", "111110",
                                       "111111110100", "111111110101", "111111110111", "111111101101"};
    for (int c = 0; c < 13; ++c) { dcVal[c] = bits(dc[c]); dcLen[c] = int(std::string(dc[c]).size()); }
    eobValue = bits("1010"); eobLength = 4;   // EOB
}

Code CodecTables::dcCode(int64_t d) const {
    const uint64_t a = uint64_t(d < 0 ? -d : d);
    int c = 0;
    for (uint64_t x = a; x; x >>= 1) ++c;   // bit_length
    if (c > 12) throw CodecRangeError("DC difference exceeds verified VLC range; use a higher --q");
    const int k = std::max(c - 1, 0);
    uint64_t m = d > 0 ? a - (uint64_t(1) << k) : (uint64_t(1) << (k + 1)) - 1 - a;
    if (!c) m = 0;
    const uint64_t sign = d > 0;
    return Code{dcVal[c] << (k + 1) | ((sign << k) | m), dcLen[c] + (c ? k + 1 : 0)};
}

}  // namespace cdng
