#pragma once
// Marco mínimo de pruebas del motor (sin dependencias, válido en Release) y lector de los datos de referencia
// que generaba el prototipo en Python, que no se publica.
#include "../common.hpp"

#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <map>
#include <optional>
#include <sstream>
#include <string>

#ifdef _WIN32
#include <stdlib.h>
#endif

namespace check {

inline int &failures() { static int n = 0; return n; }

#define CHECK(cond) do { if (!(cond)) { std::fprintf(stderr, "%s:%d: CHECK(%s) falló\n", __FILE__, __LINE__, #cond); ++check::failures(); } } while (0)
#define CHECK_NEAR(a, b, tol) do { const double va_ = double(a), vb_ = double(b); if (!(std::fabs(va_ - vb_) <= (tol))) { \
    std::fprintf(stderr, "%s:%d: |%s − %s| = |%.9g − %.9g| > %g\n", __FILE__, __LINE__, #a, #b, va_, vb_, double(tol)); ++check::failures(); } } while (0)

// Carpeta de datos de referencia: variable CDNG_GOLDEN. Sin ella, las pruebas de paridad se
// saltan y solo corren las autónomas.
inline const char *goldenRoot() { return std::getenv("CDNG_GOLDEN"); }
inline bool skipWithoutGolden(const char *test) {
    if (goldenRoot()) return false;
    std::fprintf(stderr, "%s: sin CDNG_GOLDEN, se salta la prueba de paridad\n", test);
    return true;
}

// Un caso de referencia: <dir>/<name>.txt con líneas "clave tipo forma valor|fichero" y ficheros .bin crudos
// little-endian. Tipos: f32 f64 i16 u16 i32 i64 u8 (arrays) y "num" / "str" (escalares).
struct Case {
    std::string dir;
    std::map<std::string, std::string> type, value;
    std::map<std::string, std::vector<long long>> shape;

    static Case load(const std::string &module, const std::string &name) {
        Case c; c.dir = std::string(goldenRoot()) + "/" + module;
        std::ifstream in(c.dir + "/" + name + ".txt");
        if (!in) { std::fprintf(stderr, "falta el caso de referencia %s/%s\n", module.c_str(), name.c_str()); std::exit(2); }
        std::string line;
        while (std::getline(in, line)) {
            std::istringstream s(line); std::string key, type, shape, value;
            s >> key >> type >> shape; std::getline(s >> std::ws, value);
            c.type[key] = type; c.value[key] = value;
            std::istringstream dims(shape); std::string d; std::vector<long long> v;
            while (std::getline(dims, d, 'x')) if (!d.empty() && d != "-") v.push_back(std::stoll(d));
            c.shape[key] = v;
        }
        return c;
    }
    double num(const std::string &key) const { return std::stod(value.at(key)); }
    std::string str(const std::string &key) const { return value.at(key); }
    template <class T> std::vector<T> array(const std::string &key) const {
        std::ifstream in(dir + "/" + value.at(key), std::ios::binary);
        std::vector<char> raw((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
        std::vector<T> out(raw.size() / sizeof(T)); std::memcpy(out.data(), raw.data(), out.size() * sizeof(T));
        return out;
    }
    std::vector<uint8_t> bytes(const std::string &key) const { return array<uint8_t>(key); }
};

inline int finish(const char *name) {
    if (failures()) { std::fprintf(stderr, "%s: %d fallos\n", name, failures()); return 1; }
    std::printf("%s: correcto\n", name);
    return 0;
}

// Guarda y restaura una variable de entorno (setenv/_putenv_s). Compatible con Windows y POSIX.
struct EnvVar {
    std::string name;
    std::optional<std::string> previous;
    bool had = false;

    explicit EnvVar(const char *n) : name(n) {
        if (const char *v = std::getenv(n)) { previous = v; had = true; }
    }
    EnvVar(const EnvVar &) = delete;
    EnvVar &operator=(const EnvVar &) = delete;

    void set(const char *value) {
#ifdef _WIN32
        _putenv_s(name.c_str(), value ? value : "");
#else
        if (value) setenv(name.c_str(), value, 1);
        else unsetenv(name.c_str());
#endif
    }
    void unset() {
#ifdef _WIN32
        _putenv_s(name.c_str(), "");
#else
        unsetenv(name.c_str());
#endif
    }
    ~EnvVar() {
        if (had) set(previous->c_str());
        else unset();
    }
};

}  // namespace check
