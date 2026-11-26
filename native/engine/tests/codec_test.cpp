// Pruebas del bloque C (códec). Autónomas: tablas, códigos DC por clase, DCT de bloques conocidos, desbordamientos,
// control de tasa e hilos. Paridad (CDNG_GOLDEN): slices de encode_strict, control de tasa y chunk de frame_chunk
// frente al prototipo.
#include "../codec.hpp"
#include "../codec_tables.hpp"
#include "../deadzone_policy.hpp"
#include "../encode_stats.hpp"
#include "check.hpp"

#include <chrono>
#include <cmath>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <functional>
#include <future>
#include <string>
#include <vector>

using namespace cdng;

namespace {

double seconds(std::chrono::steady_clock::time_point t0) {
    return std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();
}

// Código DC como cadena de bits (referencia independiente).
std::string encodeDc(int64_t d) {
    static const char *const table[13] = {"00", "010", "011", "100", "101", "110", "11100", "11101", "111110",
                                          "111111110100", "111111110101", "111111110111", "111111101101"};
    if (d == 0) return table[0];
    const int64_t a = d < 0 ? -d : d;
    int c = 0;
    for (int64_t x = a; x; x >>= 1) ++c;
    const int k = c - 1;
    const int64_t m = d > 0 ? a - (int64_t(1) << k) : (int64_t(1) << (k + 1)) - 1 - a;
    std::string s = std::string(table[c]) + (d > 0 ? "1" : "0");
    for (int b = k - 1; b >= 0; --b) s += (m >> b & 1) ? '1' : '0';
    return s;
}

std::string toBits(Code c) {
    std::string s;
    for (int b = c.length - 1; b >= 0; --b) s += (c.value >> b & 1) ? '1' : '0';
    return s;
}

CanvasPlanes canvas(const Geometry &g) {
    CanvasPlanes p;
    p.ws = g.ws; p.hs = g.hs;
    p.y.assign(std::size_t(g.ws) * g.hs, 2048);
    p.cb.assign(std::size_t(g.ws / 2) * (g.hs / 2), 0);
    p.cr = p.cb;
    return p;
}

std::string message(const std::function<void()> &f) {
    try { f(); } catch (const CodecRangeError &e) { return e.what(); }
    return "";
}

void autonomous(const Codec &codec, const CodecTables &t) {
    // Tablas: EOB, códigos DC de cada clase (extremos y signo), pasos planos medidos.
    CHECK(t.eobValue == 0b1010 && t.eobLength == 4);
    for (int c = 0; c <= 12; ++c)
        for (int64_t d : {int64_t(c ? 1 << (c - 1) : 0), int64_t((1 << c) - 1)})
            for (int64_t s : {1, -1}) CHECK(toBits(t.dcCode(s * d)) == encodeDc(s * d));
    CHECK(toBits(t.dcCode(4095)) == "111111101101" "111111111111");
    CHECK(toBits(t.dcCode(-2048)) == "111111101101" "011111111111");
    bool threw = false;
    try { t.dcCode(4096); } catch (const CodecRangeError &) { threw = true; }
    CHECK(threw);
    CHECK(t.quant.size() == 25);
    CHECK_NEAR(t.steps(8).qY, 12.386, 1e-12);
    CHECK_NEAR(t.steps(8).qC, 6.185, 1e-12);
    CHECK_NEAR(t.steps(0).qY, 12.386 * 4 / 12, 1e-12);   // ley exacta del decodificador, no la mediana medida
    CHECK_NEAR(t.steps(24).qC, 14.431529615103367, 1e-12);
    CHECK_NEAR(t.steps(8).yUnit, 1.5472909100464338, 0);
    CHECK_NEAR(t.steps(24).cUnit, 7.999986736166779, 0);
    // AC: código propio sin prefijo; (16, 0) va por ZRL; ninguna clase necesita ajuste con la tabla verificada.
    const auto own = t.fallbackBits(0, 1, '1');
    CHECK(own.first.empty() && own.second == t.ac.at({0, 1}));
    const auto zrl = t.fallbackBits(16, 0, '0');
    CHECK(zrl.first == t.zrl && zrl.second == t.ac.at({0, 0}));
    for (auto a : t.adjust) CHECK(a == 0);
    for (int run = 0; run < AcRuns; ++run)
        for (int k = 0; k < AcClasses; ++k) CHECK(t.finLen[(0 * AcRuns + run) * AcClasses + k] > 0);

    // DCT: bloque constante → solo DC; base coseno pura → un único coeficiente.
    double in[64], out[64];
    for (double &v : in) v = 1000;
    dct8x8(in, out);
    CHECK_NEAR(out[0], 8000, 1e-9);
    for (int i = 1; i < 64; ++i) CHECK_NEAR(out[i], 0, 1e-9);
    dct4x8(in, out);
    CHECK_NEAR(out[0], 1000 * std::sqrt(32.0), 1e-9);
    for (int i = 1; i < 32; ++i) CHECK_NEAR(out[i], 0, 1e-9);
    const double pi = std::acos(-1.0);
    for (int y = 0; y < 8; ++y)
        for (int x = 0; x < 8; ++x) in[y * 8 + x] = std::cos(pi * (2 * y + 1) * 3 / 16) * std::cos(pi * (2 * x + 1) * 5 / 16);
    dct8x8(in, out);
    for (int i = 0; i < 64; ++i) CHECK_NEAR(out[i], i == 3 * 8 + 5 ? 4.0 : 0.0, 1e-12);
    for (int y = 0; y < 4; ++y)
        for (int x = 0; x < 8; ++x) in[y * 8 + x] = std::cos(pi * (2 * y + 1) * 2 / 8) * std::cos(pi * (2 * x + 1) * 7 / 16);
    dct4x8(in, out);
    for (int i = 0; i < 32; ++i) CHECK_NEAR(out[i], i == 2 * 8 + 7 ? std::sqrt(8.0) : 0.0, 1e-12);

    // Frame plano: todo DC cero, solo EOB y relleno; tamaño y orden de slices.
    const Geometry g(1280, 720);
    auto p = canvas(g);
    const auto flat = codec.encode(p, 8, g);
    CHECK(int(flat.slices.size()) == g.n && flat.q == 8);
    std::size_t total = 0;
    for (const auto &s : flat.slices) { total += s.size(); CHECK(s.size() % 8 == 0); }
    CHECK(flat.size == FirstSlice + total);
    CHECK(flat.stats.clamped == 0 && flat.stats.fallback == 0 && flat.stats.dropped == 0);
    if (!std::getenv("CDNG_NOISE_SYNTH")) {
        // Por defecto, la tabla del Video Assist por q, también en un frame plano (la cámara lo pide siempre).
        CHECK(flat.noiseY == videoAssistNoise(8).first && flat.noiseC == videoAssistNoise(8).second);
        CHECK(videoAssistNoise(0) == std::make_pair(uint16_t(160), uint16_t(168)));
        CHECK(videoAssistNoise(9) == std::make_pair(uint16_t(365), uint16_t(300)));
        CHECK(videoAssistNoise(24) == std::make_pair(uint16_t(614), uint16_t(490)));
        CHECK(videoAssistNoise(30) == videoAssistNoise(24));
        CHECK(videoAssistNoise(14).first > videoAssistNoise(9).first && videoAssistNoise(14).first < videoAssistNoise(20).first);
    }
    // respuesta del descodificador: nada por debajo del escalón, crece, e inversa
    CHECK(decoderNoise(0, false) == 0 && decoderNoise(25, false) == 0 && decoderNoise(50, true) == 0);
    CHECK(decoderNoise(400, false) < decoderNoise(800, false) && decoderNoise(400, true) < decoderNoise(400, false));
    CHECK(decoderNoiseField(0, false) == 0);
    for (int f : {100, 400, 614, 1600, 3000}) CHECK(std::abs(decoderNoiseField(decoderNoise(f, false), false) - f) <= 1);

    // Desbordamientos: tablero de bloques 0/4095 desborda el DC a q bajo; ajedrez píxel a píxel desborda el AC.
    for (int y = 0; y < g.hs; ++y)
        for (int x = 0; x < g.ws; ++x) p.y[std::size_t(y) * g.ws + x] = ((y / 8 + x / 8) % 2) ? 4095 : 0;
    CHECK(message([&] { codec.encode(p, 0, g); }) == "DC difference exceeds verified VLC range; use a higher --q");
    CHECK(message([&] { codec.encode(p, 24, g); }).empty());
    const auto fixed = codec.encodeFixed(p, g, 0);
    CHECK(fixed.q > 0 && fixed.q <= QMax);
    CHECK(message([&] { codec.encode(p, fixed.q - 1, g); }) != "");
    for (int y = 0; y < g.hs; ++y)
        for (int x = 0; x < g.ws; ++x) p.y[std::size_t(y) * g.ws + x] = ((x + y) % 2) ? 4095 : 0;
    // Con las clases 10 y 11 (|ℓ| ≤ 4095) la luma ya no desborda el AC a ningún q: el ajedrez da ≈ 3230 a q 0.
    const auto chess = codec.encode(p, 0, g);
    CHECK(chess.stats.clamped == 0);
    const auto rc = codec.encodeRateControlled(p, g, 1, 4);
    CHECK(rc.q == QMax);   // nunca cabe: termina en QMax aunque supere el objetivo
    // La croma (−4096…4095, paso más fino) sí: ajedrez píxel a píxel a q 0.
    p = canvas(g);
    for (int y = 0; y < g.hs / 2; ++y)
        for (int x = 0; x < g.ws / 2; ++x) p.cb[std::size_t(y) * (g.ws / 2) + x] = ((x + y) % 2) ? 4095 : -4096;
    const auto ac = message([&] { codec.encode(p, 0, g); });
    CHECK(ac.find(" AC coefficients exceed the verified VLC range at q 0") != std::string::npos);
    CHECK(message([&] { codec.encode(p, 8, g); }).empty());
    p = canvas(g);
    for (int y = 0; y < g.hs / 2; ++y)
        for (int x = 0; x < g.ws / 2; ++x) p.cb[std::size_t(y) * (g.ws / 2) + x] = ((y / 4 + x / 8) % 2) ? 4095 : -4096;
    // Ni QMax cabe: en lugar de fallar, QMax recortando el DC en cadena cerrada.
    CHECK(message([&] { codec.encode(p, QMax, g); }) == "DC difference exceeds verified VLC range; use a higher --q");
    const auto lastRc = codec.encodeRateControlled(p, g, 1 << 30, 8);
    CHECK(lastRc.q == QMax && lastRc.stats.clamped > 0);
    const auto lastFixed = codec.encodeFixed(p, g, 4);
    CHECK(lastFixed.q == QMax && lastFixed.stats.clamped == lastRc.stats.clamped && lastFixed.size == lastRc.size);

    // EXP-002a: estadísticas del intento seleccionado (no de los descartados).
    {
        namespace fs = std::filesystem;
        const auto dir = fs::temp_directory_path() / ("cdng-stats-" + std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()));
        fs::create_directories(dir);
        setenv("CDNG_BIT_BUDGET", "1", 1);
        setenv("CDNG_STATS_DIR", dir.string().c_str(), 1);
        unsetenv("CDNG_COEFF_STATS");
        auto countLines = [](const fs::path &path) {
            if (!fs::exists(path)) return 0;
            std::ifstream in(path);
            int n = 0;
            for (std::string line; std::getline(in, line);) if (!line.empty()) ++n;
            return n;
        };
        auto clear = [&] {
            for (const char *name : {"bit-budget.jsonl", "encode-attempts.jsonl", "coeff-stats.jsonl"})
                fs::remove(dir / name);
        };
        // Calidad fija sin fallback: 1 selected, 0 discarded.
        clear();
        p = canvas(g);
        EncodeStatsContext ctx;
        ctx.clip = "test"; ctx.frame_index = 0; ctx.q_requested = 8; ctx.mode = "quality";
        encodeStatsSetContext(ctx);
        auto easy = codec.encodeFixed(p, g, 8);
        CHECK(easy.q == 8 && easy.diag.present);
        encodeStatsPublish(easy, true, "sample_written", "sample_written");
        encodeStatsClearContext();
        CHECK(countLines(dir / "bit-budget.jsonl") == 1);
        CHECK(countLines(dir / "encode-attempts.jsonl") == 0);
        // Calidad fija con fallback DC: intentos descartados + selected a q mayor.
        clear();
        for (int y = 0; y < g.hs; ++y)
            for (int x = 0; x < g.ws; ++x) p.y[std::size_t(y) * g.ws + x] = ((y / 8 + x / 8) % 2) ? 4095 : 0;
        ctx.frame_index = 1; ctx.q_requested = 0;
        encodeStatsSetContext(ctx);
        auto bumped = codec.encodeFixed(p, g, 0);
        encodeStatsPublish(bumped, true, "sample_written", "sample_written");
        encodeStatsClearContext();
        CHECK(bumped.q > 0);
        CHECK(countLines(dir / "bit-budget.jsonl") == 1);
        CHECK(countLines(dir / "encode-attempts.jsonl") >= 1);
        CHECK(bumped.stats.attempts >= 2);
        // Rate search con objetivo imposible → QMax non-strict / saturación.
        clear();
        ctx.frame_index = 2; ctx.q_requested = -1; ctx.ratio_requested = 12; ctx.mode = "ratio";
        encodeStatsSetContext(ctx);
        auto sat = codec.encodeRateControlled(p, g, 1, 4);
        encodeStatsPublish(sat, true, "sample_written", "sample_written");
        encodeStatsClearContext();
        CHECK(sat.q == QMax);
        CHECK(countLines(dir / "bit-budget.jsonl") == 1);
        CHECK(countLines(dir / "encode-attempts.jsonl") >= 1);
        // Payload del sample_written coincide con slices del frame.
        {
            std::ifstream in(dir / "bit-budget.jsonl");
            std::string line;
            std::getline(in, line);
            CHECK(line.find("\"selected\":true") != std::string::npos);
            CHECK(line.find("\"stage\":\"sample_written\"") != std::string::npos);
            CHECK(line.find("\"payload_sha256\"") != std::string::npos);
            const auto want = sat.size >= FirstSlice ? sat.size - FirstSlice : 0;
            CHECK(line.find("\"slices_payload_bytes\":" + std::to_string(want)) != std::string::npos);
        }
        // EXP-003: attempt_index = intento que produjo el EncodedFrame (no el acumulado al seleccionar).
        clear();
        p = canvas(g);
        for (int y = 0; y < g.hs; ++y)
            for (int x = 0; x < g.ws; ++x)
                p.y[std::size_t(y) * g.ws + x] = int16_t(2048 + ((x * 17 + y * 13) % 200));
        ctx.frame_index = 3; ctx.q_requested = -1; ctx.ratio_requested = 3; ctx.mode = "ratio";
        encodeStatsSetContext(ctx);
        auto rcOk = codec.encodeRateControlled(p, g, 1 << 20, 8);  // objetivo holgado → cabe pronto
        encodeStatsPublish(rcOk, true, "sample_written", "sample_written");
        encodeStatsClearContext();
        CHECK(rcOk.diag.attempt_index >= 1);
        CHECK(rcOk.diag.attempt_index <= rcOk.stats.attempts);
        {
            std::ifstream in(dir / "bit-budget.jsonl");
            std::string line; std::getline(in, line);
            CHECK(line.find("\"stage\":\"sample_written\"") != std::string::npos);
            CHECK(line.find("\"attempt_index\":" + std::to_string(rcOk.diag.attempt_index)) != std::string::npos);
        }
        unsetenv("CDNG_BIT_BUDGET");
        unsetenv("CDNG_STATS_DIR");
        fs::remove_all(dir);
    }

    // EXP-003: política de deadzone — legacy off, uniforme ≡ CDNG_DEADZONE, DC intacto, planos.
    {
        namespace fs = std::filesystem;
        check::EnvVar dz("CDNG_DEADZONE");
        check::EnvVar pol("CDNG_DEADZONE_POLICY");
        dz.unset();
        pol.unset();
        deadzonePolicyResetForTests();
        p = canvas(g);
        for (int y = 0; y < g.hs; ++y)
            for (int x = 0; x < g.ws; ++x)
                p.y[std::size_t(y) * g.ws + x] = int16_t(1800 + (x * 3 + y * 5) % 400);
        const auto legacy = codec.encode(p, 12, g);

        // Uniforme vía política ≡ CDNG_DEADZONE.
        for (double z : {0.0, 0.1, 0.25, 0.5}) {
            const auto dir = fs::temp_directory_path() / ("cdng-dz-" + std::to_string(int(z * 100)));
            fs::create_directories(dir);
            const auto path = dir / "uniform.json";
            {
                std::ofstream out(path);
                out << "{\"schema\":1,\"name\":\"u\",\"mode\":\"uniform\",\"z\":" << z
                    << ",\"planes\":{\"Y\":{\"default\":" << z << "},\"Cb\":{\"default\":" << z
                    << "},\"Cr\":{\"default\":" << z << "}}}\n";
            }
            dz.unset();
            pol.set(path.string().c_str());
            deadzonePolicyResetForTests();
            const auto viaPol = codec.encode(p, 12, g);
            pol.unset();
            dz.set(std::to_string(z).c_str());
            deadzonePolicyResetForTests();
            const auto viaDz = codec.encode(p, 12, g);
            CHECK(viaPol.slices == viaDz.slices);
            CHECK(viaPol.size == viaDz.size);
            fs::remove_all(dir);
        }

        // Política desactivada ≡ legacy (sin CDNG_DEADZONE).
        dz.unset();
        pol.unset();
        deadzonePolicyResetForTests();
        CHECK(codec.encode(p, 12, g).slices == legacy.slices);

        // Conflicto DEADZONE + POLICY → Error.
        {
            const auto dir = fs::temp_directory_path() / "cdng-dz-conflict";
            fs::create_directories(dir);
            const auto path = dir / "u.json";
            { std::ofstream out(path); out << "{\"schema\":1,\"name\":\"u\",\"mode\":\"uniform\",\"z\":0.25,"
                                              "\"planes\":{\"Y\":{\"default\":0.25},\"Cb\":{\"default\":0.25},"
                                              "\"Cr\":{\"default\":0.25}}}\n"; }
            dz.set("0.25");
            pol.set(path.string().c_str());
            deadzonePolicyResetForTests();
            {
                std::string err;
                try { deadzonePolicy(); } catch (const Error &e) { err = e.what(); }
                CHECK(err == "CDNG_DEADZONE and CDNG_DEADZONE_POLICY are mutually exclusive");
            }
            dz.unset();
            pol.unset();
            deadzonePolicyResetForTests();
            fs::remove_all(dir);
        }

        // DC: zY[0]==0 siempre; bandas / frecuencias.
        CHECK(deadzoneBand(DeadzonePlane::Y, 0, 0) == -1);
        CHECK(deadzoneBand(DeadzonePlane::Y, 0, 1) == 0);
        CHECK(deadzoneNormalizedFreq(DeadzonePlane::Y, 7, 7) > 0.9);
        // quantizeAc con z=0 ≡ nearbyint.
        CHECK(quantizeAc(1.5, 1.0, DeadzonePlane::Y, 1) == int64_t(std::nearbyint(1.5)));
        CHECK(quantizeAc(-1.5, 1.0, DeadzonePlane::Y, 1) == int64_t(std::nearbyint(-1.5)));
    }

    // Hilos: el mismo Codec desde varios hilos a la vez da lo mismo que en serie.
    p = canvas(g);
    for (int y = 0; y < g.hs; ++y)
        for (int x = 0; x < g.ws; ++x) p.y[std::size_t(y) * g.ws + x] = int16_t(2048 + 1000 * std::sin(x / 9.0) * std::cos(y / 7.0) + (x * y) % 17);
    const auto serial = codec.encode(p, 6, g);
    std::vector<std::future<EncodedFrame>> jobs;
    for (int i = 0; i < 6; ++i) jobs.push_back(std::async(std::launch::async, [&] { return codec.encode(p, 6, g); }));
    for (auto &j : jobs) CHECK(j.get().slices == serial.slices);

    // frameChunk: q en el byte 1 de la cabecera, slices desde FirstSlice.
    const auto header = g.header(std::vector<uint8_t>(120, 0));
    const std::vector<uint8_t> gap(std::size_t(FirstSlice - 0x80 - 4 * g.n), 0);
    const auto chunk = frameChunk(header, gap, serial);
    CHECK(chunk.size() == serial.size && chunk[8 + 1] == 6);   // "braw" + tamaño y luego la cabecera
    CHECK(chunk[8 + 20] == serial.noiseY >> 8 && chunk[8 + 21] == (serial.noiseY & 255) && chunk[8 + 22] == serial.noiseC >> 8 && chunk[8 + 23] == (serial.noiseC & 255));
    // Síntesis de ruido adaptativa (CDNG_NOISE_SYNTH=adaptativo): un lienzo plano con ruido determinista de ±2 pierde
    // casi todo su AC a q 24 (campo > 0) y lo conserva a q 0 (campo pequeño); la croma plana no repone nada.
    {
        auto noisy = canvas(g);
        uint32_t seed = 12345;
        for (auto &v : noisy.y) { seed = seed * 1664525u + 1013904223u; v = int16_t(2048 + int(seed >> 30) - 2); }
        const auto coarse = codec.encode(noisy, 24, g), fine = codec.encode(noisy, 0, g);
        if (const char *mode = std::getenv("CDNG_NOISE_SYNTH"); mode && std::string(mode) == "adaptativo") {
            CHECK(coarse.noiseY > 200 && coarse.noiseC == 0);
            CHECK(fine.noiseY < coarse.noiseY);
        }
        const auto noisyChunk = frameChunk(header, gap, coarse);
        CHECK(noisyChunk[8 + 20] == coarse.noiseY >> 8 && noisyChunk[8 + 21] == (coarse.noiseY & 255));
    }
    const auto split = splitFileChunk(chunk.data(), chunk.size());
    CHECK(split.slices == serial.slices);
}

CanvasPlanes loadCanvas(const check::Case &c, const Geometry &g) {
    CanvasPlanes p;
    p.ws = g.ws; p.hs = g.hs;
    p.y = c.array<int16_t>("y"); p.cb = c.array<int16_t>("cb"); p.cr = c.array<int16_t>("cr");
    CHECK(p.y.size() == std::size_t(g.ws) * g.hs && p.cb.size() == std::size_t(g.ws / 2) * (g.hs / 2));
    return p;
}

// Compara slices y cuenta las diferencias (slices distintos y primer byte distinto).
void compareSlices(const char *name, int q, const EncodedFrame &f, const check::Case &c, const std::string &key) {
    const auto data = c.bytes(key + "_data");
    const auto lengths = c.array<int32_t>(key + "_lengths");
    CHECK(lengths.size() == f.slices.size());
    std::size_t offset = 0, differ = 0;
    for (std::size_t i = 0; i < lengths.size() && i < f.slices.size(); ++i) {
        const std::vector<uint8_t> ref(data.begin() + long(offset), data.begin() + long(offset + std::size_t(lengths[i])));
        offset += std::size_t(lengths[i]);
        if (ref != f.slices[i]) {
            if (!differ) {
                std::size_t j = 0;
                while (j < ref.size() && j < f.slices[i].size() && ref[j] == f.slices[i][j]) ++j;
                std::fprintf(stderr, "%s q %d: slice %zu distinto (byte %zu de %zu/%zu)\n", name, q, i, j, ref.size(),
                             f.slices[i].size());
            }
            ++differ;
        }
    }
    if (differ) std::fprintf(stderr, "%s q %d: %zu slices distintos\n", name, q, differ);
    CHECK(differ == 0);
    CHECK(f.stats.fallback == uint64_t(c.num(key + "_fallback")));
    CHECK(f.stats.dropped == uint64_t(c.num(key + "_dropped")));
}

void parity(const Codec &codec) {
    const char *names[] = {"dc_overflow", "ac_overflow", "chroma_overflow", "ramp", "sigma_iso400", "sigma_iso400_1080",
                           "sigma_iso800", "sigma_iso800_1080", "sigma_fhd", "pocket_og", "pocket_4k", "pocket_4k_1080",
                           "pocket_og_night"};
    std::size_t compared = 0;
    for (const char *name : names) {
        const auto c = check::Case::load("codec", name);
        const Geometry g(int(c.num("w")), int(c.num("h")));
        const auto p = loadCanvas(c, g);
        for (int q : {0, 2, 4, 8, 13, 24}) {
            const auto key = "q" + std::to_string(q);
            const auto t0 = std::chrono::steady_clock::now();
            std::string error;
            EncodedFrame f;
            try { f = codec.encode(p, q, g); } catch (const CodecRangeError &e) { error = e.what(); }
            const double cpp = seconds(t0), py = c.num(key + "_seconds");
            if (c.value.count(key + "_error")) {
                if (error != c.str(key + "_error"))
                    std::fprintf(stderr, "%s q %d: «%s» frente a «%s»\n", name, q, error.c_str(), c.str(key + "_error").c_str());
                CHECK(error == c.str(key + "_error"));
            } else {
                CHECK(error.empty());
                if (error.empty()) { compareSlices(name, q, f, c, key); ++compared; }
            }
            if (q == 8)
                std::printf("%-18s %4d×%-4d q 8: C++ %.3f s, Python %.3f s (×%.1f)\n", name, g.w, g.h, cpp, py, py / cpp);
        }
        for (const char *rate : {"r3_8", "r5_8", "r8_8", "r5_16", "r5_4"}) {
            const std::string key(rate);
            const auto q0 = std::stoi(key.substr(key.find('_') + 1));
            std::string error;
            EncodedFrame f;
            try {
                f = codec.encodeRateControlled(p, g, std::size_t(c.num(key + "_target")), q0);
            } catch (const CodecRangeError &e) { error = e.what(); }
            if (c.value.count(key + "_error")) {
                CHECK(error == c.str(key + "_error"));
            } else {
                CHECK(error.empty());
                if (f.q != int(c.num(key + "_q")) || f.size != std::size_t(c.num(key + "_size")))
                    std::fprintf(stderr, "%s %s: q %d / %zu B frente a q %d / %zu B\n", name, rate, f.q, f.size,
                                 int(c.num(key + "_q")), std::size_t(c.num(key + "_size")));
                CHECK(f.q == int(c.num(key + "_q")) && f.size == std::size_t(c.num(key + "_size")));
                CHECK(f.stats.fallback == uint64_t(c.num(key + "_fallback")));
                CHECK(f.stats.dropped == uint64_t(c.num(key + "_dropped")));
            }
        }
        const std::string chunkName = std::string(name) + "_chunk";
        if (std::ifstream(std::string(check::goldenRoot()) + "/codec/" + chunkName + ".txt")) {
            const auto k = check::Case::load("codec", chunkName);
            const auto f = codec.encodeFixed(p, g, 4);
            CHECK(f.q == int(k.num("q")));
            CHECK(frameChunk(k.bytes("header"), k.bytes("gap"), f) == k.bytes("chunk"));
            CHECK(f.stats.fallback == uint64_t(k.num("fallback")) && f.stats.dropped == uint64_t(k.num("dropped")));
        }
    }
    std::printf("paridad: %zu frames codificados comparados slice a slice\n", compared);
}

}  // namespace

int main() {
    const auto resources = resourceRoot();   // CDNG_RESOURCES (lo pone ctest)
    const Codec codec(resources);
    const auto tables = CodecTables::load(resources / "model/vlc-table.json", resources / "model/quant_steps.json");
    autonomous(codec, tables);
    if (!check::skipWithoutGolden("codec_test")) parity(codec);
    return check::finish("codec_test");
}
