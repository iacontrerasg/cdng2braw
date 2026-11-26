#include "og_debayer.hpp"

#include <cctype>
#include <cstdlib>
#include <cstring>
#include <string>

namespace cdng {
namespace {

std::string lower(const char *v) {
    std::string s;
    if (!v) return s;
    for (; *v; ++v) s.push_back(char(std::tolower(static_cast<unsigned char>(*v))));
    return s;
}

}  // namespace

OgDebayerConfig ogDebayerFromEnv() {
    OgDebayerConfig c;
    const char *raw = std::getenv("CDNG_OG_DEBAYER");
    if (!raw || !*raw) return c;
    c.fromEnv = true;
    const std::string m = lower(raw);
    if (m == "linear" || m == "lin" || m == "0") {
        c.name = "linear";
        c.userQual = 0;
    } else if (m == "vng" || m == "1") {
        c.name = "vng";
        c.userQual = 1;
    } else if (m == "ppg" || m == "2") {
        c.name = "ppg";
        c.userQual = 2;
    } else if (m == "ahd" || m == "3") {
        c.name = "ahd";
        c.userQual = 3;
    } else if (m == "dcb" || m == "4") {
        c.name = "dcb";
        c.userQual = 4;
    } else if (m == "dht" || m == "11") {
        c.name = "dht";
        c.userQual = 11;
    } else if (m == "aahd" || m == "12") {
        c.name = "aahd";
        c.userQual = 12;
    } else if (m == "amaze" || m == "rcd" || m == "lmmse") {
        throw Error("CDNG_OG_DEBAYER='" + std::string(raw) +
                    "' is not in this LibRaw build (candidate for a later experiment, not EXP-005)");
    } else {
        throw Error("CDNG_OG_DEBAYER: unsupported value '" + std::string(raw) +
                    "' (use linear|vng|ppg|ahd|dcb|dht|aahd)");
    }
    return c;
}

const char *ogDebayerCanonicalName(int userQual) {
    switch (userQual) {
    case 0: return "linear";
    case 1: return "vng";
    case 2: return "ppg";
    case 3: return "ahd";
    case 4: return "dcb";
    case 11: return "dht";
    case 12: return "aahd";
    default: return "unknown";
    }
}

void ogDebayerRejectIfNotOg(Route route) {
    const OgDebayerConfig c = ogDebayerFromEnv();
    if (c.fromEnv && route != Route::Og)
        throw Error("CDNG_OG_DEBAYER is only supported on Pocket OG");
}

}  // namespace cdng
