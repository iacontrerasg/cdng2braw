#include "deadzone_policy.hpp"
#include "signal_internal.hpp"

#include <simdjson.h>

#include <cmath>
#include <cstdlib>
#include <fstream>
#include <mutex>
#include <vector>

namespace cdng {
namespace {

std::mutex g_mu;
DeadzonePolicy g_policy;
bool g_loaded = false;

bool envSet(const char *name) {
    const char *v = std::getenv(name);
    return v && *v;
}

std::string readFileText(const std::string &path) {
    std::ifstream in(path, std::ios::binary);
    if (!in) throw Error("Cannot open CDNG_DEADZONE_POLICY: " + path);
    return std::string(std::istreambuf_iterator<char>(in), {});
}

void fillUniform(DeadzonePolicy &p, double z) {
    z = std::clamp(z, 0.0, 0.5);
    p.zY.fill(z);
    p.zCb.fill(z);
    p.zCr.fill(z);
    p.zY[0] = p.zCb[0] = p.zCr[0] = 0;
}

void requireFinite01(double v, const char *what) {
    if (!std::isfinite(v) || v < 0.0 || v > 0.5)
        throw Error(std::string("deadzone policy: ") + what + " out of range [0,0.5]");
}

double planeDefault(simdjson::dom::object plane, double fallback) {
    auto f = plane["default"];
    if (f.error()) return fallback;
    double v = 0;
    if (f.get(v)) throw Error("deadzone policy: invalid default");
    requireFinite01(v, "default");
    return v;
}

std::array<double, 64> *tableFor(DeadzonePolicy &p, DeadzonePlane plane) {
    if (plane == DeadzonePlane::Y) return &p.zY;
    if (plane == DeadzonePlane::Cb) return &p.zCb;
    return &p.zCr;
}

int umaxFor(DeadzonePlane plane) { return plane == DeadzonePlane::Y ? 7 : 3; }

void applyBands(DeadzonePolicy &p, DeadzonePlane plane, simdjson::dom::object planeObj, double def,
                simdjson::dom::object bandsDef) {
    auto &tbl = *tableFor(p, plane);
    tbl.fill(def);
    tbl[0] = 0;
    auto bandsField = planeObj["bands"];
    if (bandsField.error()) return;
    simdjson::dom::object bands;
    if (bandsField.get(bands)) throw Error("deadzone policy: bands must be an object");
    struct Edge { double lo, hi, z; };
    std::vector<Edge> edges;
    for (auto [name, val] : bands) {
        double z = 0;
        if (val.get(z)) throw Error("deadzone policy: band z must be number");
        requireFinite01(z, "band z");
        auto spec = bandsDef[name];
        if (spec.error()) throw Error("deadzone policy: unknown band " + std::string(name));
        simdjson::dom::object o;
        if (spec.get(o)) throw Error("deadzone policy: band definition must be object");
        double lo = 0, hi = 0;
        if (o["f_min"].get(lo) || o["f_max"].get(hi)) throw Error("deadzone policy: band needs f_min/f_max");
        // f_max puede ser 1.01 para incluir f=1.0 con el test semiabierto [lo, hi).
        if (!(lo < hi) || lo < 0.0 || hi > 1.01 + 1e-9) throw Error("deadzone policy: invalid band range");
        edges.push_back({lo, hi, z});
    }
    const int umax = umaxFor(plane);
    for (int u = 0; u <= umax; ++u)
        for (int v = 0; v <= 7; ++v) {
            if (u == 0 && v == 0) continue;
            const double f = deadzoneNormalizedFreq(plane, u, v);
            double z = def;
            int hits = 0;
            for (const auto &e : edges)
                if (f >= e.lo && f < e.hi) { z = e.z; ++hits; }
            if (hits > 1) throw Error("deadzone policy: overlapping bands");
            tbl[std::size_t(u * 8 + v)] = z;
        }
}

void applyPerCoeff(DeadzonePolicy &p, DeadzonePlane plane, simdjson::dom::object planeObj, double def) {
    auto &tbl = *tableFor(p, plane);
    tbl.fill(def);
    tbl[0] = 0;
    auto coeffs = planeObj["coeffs"];
    if (coeffs.error()) return;
    simdjson::dom::array arr;
    if (coeffs.get(arr)) throw Error("deadzone policy: coeffs must be array");
    for (auto item : arr) {
        simdjson::dom::object o;
        if (item.get(o)) throw Error("deadzone policy: coeff entry must be object");
        int64_t u = 0, v = 0;
        double z = 0;
        if (o["u"].get(u) || o["v"].get(v) || o["z"].get(z))
            throw Error("deadzone policy: coeff needs u,v,z");
        if (u < 0 || v < 0 || u > umaxFor(plane) || v > 7) throw Error("deadzone policy: invalid coeff coordinate");
        requireFinite01(z, "coeff z");
        if (u == 0 && v == 0) throw Error("deadzone policy: DC cannot be set");
        tbl[std::size_t(u * 8 + v)] = z;
    }
}

void loadPlane(DeadzonePolicy &p, simdjson::dom::object planes, const char *key, DeadzonePlane pl,
               const std::string &mode, simdjson::dom::object bandsObj, bool haveBands) {
    auto field = planes[key];
    if (field.error()) throw Error(std::string("deadzone policy: missing plane ") + key);
    simdjson::dom::object po;
    if (field.get(po)) throw Error(std::string("deadzone policy: plane ") + key + " must be object");
    const double def = planeDefault(po, 0.0);
    if (mode == "per_plane") {
        auto &tbl = *tableFor(p, pl);
        tbl.fill(def);
        tbl[0] = 0;
    } else if (mode == "per_band") {
        if (!haveBands) throw Error("deadzone policy: bands required for per_band");
        applyBands(p, pl, po, def, bandsObj);
    } else if (mode == "per_coefficient") {
        applyPerCoeff(p, pl, po, def);
    } else {
        throw Error("deadzone policy: unknown mode " + mode);
    }
}

DeadzonePolicy load() {
    DeadzonePolicy p;
    const bool hasLegacy = envSet("CDNG_DEADZONE");
    const char *path = std::getenv("CDNG_DEADZONE_POLICY");
    if (!path || !*path) {
        fillUniform(p, hasLegacy ? std::clamp(std::atof(std::getenv("CDNG_DEADZONE")), 0.0, 0.5) : 0.0);
        p.active = false;
        return p;
    }
    if (hasLegacy) throw Error("CDNG_DEADZONE and CDNG_DEADZONE_POLICY are mutually exclusive");

    const std::string text = readFileText(path);
    p.sha256 = sha256Hex(std::vector<uint8_t>(text.begin(), text.end()));
    simdjson::dom::parser parser;
    simdjson::padded_string padded(text);
    simdjson::dom::element doc;
    if (parser.parse(padded).get(doc)) throw Error("deadzone policy: JSON parse failed");
    simdjson::dom::object root;
    if (doc.get(root)) throw Error("deadzone policy: root must be object");

    int64_t schema = 0;
    if (root["schema"].get(schema) || schema != 1) throw Error("deadzone policy: schema must be 1");
    p.schema = int(schema);
    std::string_view name;
    if (root["name"].get(name)) throw Error("deadzone policy: name required");
    p.name = std::string(name);
    std::string_view mode;
    if (root["mode"].get(mode)) throw Error("deadzone policy: mode required");
    const std::string m(mode);

    simdjson::dom::object planes;
    if (root["planes"].get(planes)) throw Error("deadzone policy: planes required");

    if (m == "uniform") {
        double z = 0;
        if (!root["z"].get(z)) {
            requireFinite01(z, "z");
        } else {
            simdjson::dom::object y;
            if (planes["Y"].get(y)) throw Error("deadzone policy: planes.Y required for uniform without z");
            z = planeDefault(y, 0.0);
        }
        fillUniform(p, z);
        p.active = true;
        return p;
    }
    if (m == "legacy") {
        fillUniform(p, 0.0);
        p.active = false;
        return p;
    }

    simdjson::dom::object bandsObj;
    const bool haveBands = !root["bands"].error() && !root["bands"].get(bandsObj);
    loadPlane(p, planes, "Y", DeadzonePlane::Y, m, bandsObj, haveBands);
    loadPlane(p, planes, "Cb", DeadzonePlane::Cb, m, bandsObj, haveBands);
    loadPlane(p, planes, "Cr", DeadzonePlane::Cr, m, bandsObj, haveBands);
    p.active = true;
    return p;
}

}  // namespace

double deadzoneNormalizedFreq(DeadzonePlane plane, int u, int v) {
    // ||(u/umax, v/vmax)|| / √2 ∈ [0,1]. Y: 8×8; croma: 4×8 (umax=3).
    // Relación espacial: ciclo/bloque ≈ u/8 (horiz. Y) o v/8 (vert.); croma tiene
    // muestreo 4:2:2 → u/4 en horizontal del plano croma ≈ u/8 en imagen.
    const double umax = plane == DeadzonePlane::Y ? 7.0 : 3.0;
    const double vmax = 7.0;
    const double fu = umax > 0 ? double(u) / umax : 0;
    const double fv = vmax > 0 ? double(v) / vmax : 0;
    return std::sqrt(fu * fu + fv * fv) / std::sqrt(2.0);
}

int deadzoneBand(DeadzonePlane plane, int u, int v) {
    if (u == 0 && v == 0) return -1;
    const double f = deadzoneNormalizedFreq(plane, u, v);
    if (f < 0.25) return 0;
    if (f < 0.5) return 1;
    return 2;
}

double DeadzonePolicy::at(DeadzonePlane plane, int dctIndex) const {
    if (dctIndex < 0 || dctIndex >= 64) return 0;
    if (plane == DeadzonePlane::Y) return zY[std::size_t(dctIndex)];
    if (plane == DeadzonePlane::Cb) return zCb[std::size_t(dctIndex)];
    return zCr[std::size_t(dctIndex)];
}

const DeadzonePolicy &deadzonePolicy() {
    std::lock_guard<std::mutex> lock(g_mu);
    if (!g_loaded) {
        g_policy = load();
        g_loaded = true;
    }
    return g_policy;
}

void deadzonePolicyResetForTests() {
    std::lock_guard<std::mutex> lock(g_mu);
    g_loaded = false;
    g_policy = {};
}

int64_t quantizeAc(double coef, double step, DeadzonePlane plane, int dctIndex) {
    // Tablas memoizadas: con política activa → z(plane,u,v); sin ella → CDNG_DEADZONE uniforme (o 0).
    // DC siempre z=0 en las tablas. z=0 → nearbyint (paridad histórica, incluidos empates).
    const double v = coef / step;
    const double z = deadzonePolicy().at(plane, dctIndex);
    if (z <= 0) return int64_t(std::nearbyint(v));
    return int64_t(std::copysign(std::floor(std::fabs(v) + 0.5 - z), v));
}

}  // namespace cdng
