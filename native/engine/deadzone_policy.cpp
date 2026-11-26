#include "deadzone_policy.hpp"
#include "signal_internal.hpp"

#include <simdjson.h>

#include <atomic>
#include <cmath>
#include <cstdlib>
#include <fstream>
#include <memory>
#include <mutex>
#include <set>
#include <string>
#include <vector>

namespace cdng {
namespace {

std::mutex g_mu;
std::shared_ptr<const DeadzonePolicy> g_owned;
std::atomic<const DeadzonePolicy *> g_fast{nullptr};

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

double planeDefaultRequired(simdjson::dom::object plane, const char *planeName) {
    auto f = plane["default"];
    if (f.error()) throw Error(std::string("deadzone policy: missing default for plane ") + planeName);
    double v = 0;
    if (f.get(v)) throw Error(std::string("deadzone policy: invalid default for ") + planeName);
    requireFinite01(v, "default");
    return v;
}

std::array<double, 64> *tableFor(DeadzonePolicy &p, DeadzonePlane plane) {
    if (plane == DeadzonePlane::Y) return &p.zY;
    if (plane == DeadzonePlane::Cb) return &p.zCb;
    return &p.zCr;
}

int umaxFor(DeadzonePlane plane) { return plane == DeadzonePlane::Y ? 7 : 3; }

void rejectUnknownPlaneKeys(simdjson::dom::object planeObj, const std::string &mode) {
    for (auto [key, val] : planeObj) {
        const std::string k(key);
        if (k == "default") continue;
        if (mode == "per_band" && k == "bands") continue;
        if (mode == "per_coefficient" && k == "coeffs") continue;
        if (mode == "per_plane" || mode == "uniform" || mode == "legacy")
            throw Error("deadzone policy: unexpected key '" + k + "' for mode " + mode);
        if (mode == "per_band" && k != "bands")
            throw Error("deadzone policy: unexpected key '" + k + "' for per_band");
        if (mode == "per_coefficient" && k != "coeffs")
            throw Error("deadzone policy: unexpected key '" + k + "' for per_coefficient");
        (void)val;
    }
}

void applyBands(DeadzonePolicy &p, DeadzonePlane plane, simdjson::dom::object planeObj, double def,
                simdjson::dom::object bandsDef) {
    auto &tbl = *tableFor(p, plane);
    tbl.fill(def);
    tbl[0] = 0;
    auto bandsField = planeObj["bands"];
    if (bandsField.error()) return;
    simdjson::dom::object bands;
    if (bandsField.get(bands)) throw Error("deadzone policy: bands must be an object");
    struct Edge { double lo, hi, z; std::string name; };
    std::vector<Edge> edges;
    std::set<std::string> seen;
    for (auto [name, val] : bands) {
        const std::string n(name);
        if (!seen.insert(n).second) throw Error("deadzone policy: duplicate band key " + n);
        double z = 0;
        if (val.get(z)) throw Error("deadzone policy: band z must be number");
        requireFinite01(z, "band z");
        auto spec = bandsDef[name];
        if (spec.error()) throw Error("deadzone policy: unknown band " + n);
        simdjson::dom::object o;
        if (spec.get(o)) throw Error("deadzone policy: band definition must be object");
        double lo = 0, hi = 0;
        if (o["f_min"].get(lo) || o["f_max"].get(hi)) throw Error("deadzone policy: band needs f_min/f_max");
        if (!(lo < hi) || lo < 0.0 || hi > 1.01 + 1e-9) throw Error("deadzone policy: invalid band range");
        if (!std::isfinite(lo) || !std::isfinite(hi)) throw Error("deadzone policy: non-finite band range");
        edges.push_back({lo, hi, z, n});
    }
    // Solapes entre definiciones de bandas (no solo en un coeficiente).
    for (std::size_t i = 0; i < edges.size(); ++i)
        for (std::size_t j = i + 1; j < edges.size(); ++j)
            if (edges[i].lo < edges[j].hi && edges[j].lo < edges[i].hi)
                throw Error("deadzone policy: overlapping bands " + edges[i].name + "/" + edges[j].name);

    const int umax = umaxFor(plane);
    for (int u = 0; u <= umax; ++u)
        for (int v = 0; v <= 7; ++v) {
            if (u == 0 && v == 0) continue;
            const double f = deadzoneNormalizedFreq(plane, u, v);
            double z = def;
            int hits = 0;
            for (const auto &e : edges)
                if (f >= e.lo && f < e.hi) { z = e.z; ++hits; }
            if (hits > 1) throw Error("deadzone policy: overlapping bands at freq");
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
    std::set<int> seen;
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
        const int idx = int(u * 8 + v);
        if (!seen.insert(idx).second) throw Error("deadzone policy: duplicate coeff coordinate");
        tbl[std::size_t(idx)] = z;
    }
}

void loadPlane(DeadzonePolicy &p, simdjson::dom::object planes, const char *key, DeadzonePlane pl,
               const std::string &mode, simdjson::dom::object bandsObj, bool haveBands) {
    auto field = planes[key];
    if (field.error()) throw Error(std::string("deadzone policy: missing plane ") + key);
    simdjson::dom::object po;
    if (field.get(po)) throw Error(std::string("deadzone policy: plane ") + key + " must be object");
    rejectUnknownPlaneKeys(po, mode);
    const double def = planeDefaultRequired(po, key);
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

void rejectUnknownRootKeys(simdjson::dom::object root, const std::string &mode) {
    for (auto [key, val] : root) {
        const std::string k(key);
        if (k == "schema" || k == "name" || k == "mode" || k == "planes") continue;
        if (k == "z" && mode == "uniform") continue;
        if (k == "bands" && mode == "per_band") continue;
        throw Error("deadzone policy: unexpected root key '" + k + "'");
        (void)val;
    }
}

void rejectUnknownPlanes(simdjson::dom::object planes) {
    for (auto [key, val] : planes) {
        const std::string k(key);
        if (k != "Y" && k != "Cb" && k != "Cr")
            throw Error("deadzone policy: unknown plane '" + k + "'");
        (void)val;
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

    p.path = path;
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
    rejectUnknownRootKeys(root, m);

    simdjson::dom::object planes;
    if (root["planes"].get(planes)) throw Error("deadzone policy: planes required");
    rejectUnknownPlanes(planes);

    if (m == "uniform") {
        double z = 0;
        const bool hasZ = !root["z"].get(z);
        if (hasZ) {
            requireFinite01(z, "z");
            // Si hay default por plano, debe coincidir con z (sin ambigüedad).
            for (const char *pl : {"Y", "Cb", "Cr"}) {
                simdjson::dom::object po;
                if (planes[pl].get(po)) throw Error(std::string("deadzone policy: missing plane ") + pl);
                auto d = po["default"];
                if (!d.error()) {
                    double dv = 0;
                    if (d.get(dv)) throw Error("deadzone policy: invalid default");
                    requireFinite01(dv, "default");
                    if (std::fabs(dv - z) > 1e-15)
                        throw Error("deadzone policy: uniform z conflicts with plane default");
                }
            }
        } else {
            simdjson::dom::object y;
            if (planes["Y"].get(y)) throw Error("deadzone policy: planes.Y required for uniform without z");
            z = planeDefaultRequired(y, "Y");
            for (const char *pl : {"Cb", "Cr"}) {
                simdjson::dom::object po;
                if (planes[pl].get(po)) throw Error(std::string("deadzone policy: missing plane ") + pl);
                if (std::fabs(planeDefaultRequired(po, pl) - z) > 1e-15)
                    throw Error("deadzone policy: uniform requires equal plane defaults");
            }
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

void publish(std::shared_ptr<const DeadzonePolicy> pol) {
    g_owned = std::move(pol);
    g_fast.store(g_owned.get(), std::memory_order_release);
}

}  // namespace

double deadzoneNormalizedFreq(DeadzonePlane plane, int u, int v) {
    // Normalización relativa al bloque del plano: ||(u/umax, v/vmax)|| / √2 ∈ [0,1].
    // No es frecuencia espacial absoluta de imagen: Y 8×8 (umax=7) vs croma 4×8 (umax=3)
    // con muestreo 4:2:2 → el mismo (u,v) no es perceptualmente equivalente entre planos.
    // Aprox. espacial imagen: ciclo/bloque ≈ u/8 (Y horiz.) o v/8 (vert.); croma u/4 en
    // el plano croma ≈ u/8 en la imagen a resolución completa.
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

void deadzonePolicyEnsureLoaded() {
    if (g_fast.load(std::memory_order_acquire)) return;
    std::lock_guard<std::mutex> lock(g_mu);
    if (g_fast.load(std::memory_order_relaxed)) return;
    publish(std::make_shared<const DeadzonePolicy>(load()));
}

const DeadzonePolicy &deadzonePolicy() {
    if (const DeadzonePolicy *p = g_fast.load(std::memory_order_acquire)) return *p;
    deadzonePolicyEnsureLoaded();
    return *g_fast.load(std::memory_order_acquire);
}

void deadzonePolicyResetForTests() {
    std::lock_guard<std::mutex> lock(g_mu);
    g_fast.store(nullptr, std::memory_order_release);
    g_owned.reset();
}

int64_t quantizeAc(double coef, double step, DeadzonePlane plane, int dctIndex) {
    // Hot path: sin mutex. La política debe estar publicada (ensureLoaded / primera llamada).
    const DeadzonePolicy *pol = g_fast.load(std::memory_order_acquire);
    if (!pol) {
        deadzonePolicyEnsureLoaded();
        pol = g_fast.load(std::memory_order_acquire);
    }
    const double v = coef / step;
    const double z = pol->at(plane, dctIndex);
    if (z <= 0) return int64_t(std::nearbyint(v));
    return int64_t(std::copysign(std::floor(std::fabs(v) + 0.5 - z), v));
}

}  // namespace cdng
