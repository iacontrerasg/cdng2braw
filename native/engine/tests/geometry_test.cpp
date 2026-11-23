#include "../geometry.hpp"
#include "check.hpp"

using namespace cdng;

int main() {
    // Autónoma: reglas de tamaño y franjas.
    Geometry uhd(3840, 2160);
    CHECK(uhd.ws == 3872 && uhd.hs == 2176 && uhd.slices == 31 && uhd.n == 124);
    CHECK(uhd.strips.size() == 4 && uhd.strips[0] + uhd.strips[1] + uhd.strips[2] + uhd.strips[3] == uhd.ws);

    if (!check::skipWithoutGolden("geometry_test")) {
        const auto tpl = check::Case::load("geometry", "template");
        const auto chunk = tpl.bytes("chunk");
        const auto split = splitFileChunk(chunk.data(), chunk.size());
        CHECK(split.header == tpl.bytes("header"));
        CHECK(split.gap == tpl.bytes("gap"));
        CHECK(int(split.slices.size()) == int(tpl.num("nslices")));
        CHECK(buildFileChunk(split.header, split.gap, split.slices) == tpl.bytes("rebuilt"));
        for (const char *name : {"g3840x2160s4", "g1920x1080s4", "g2560x1440s4", "g1280x720s4", "g3840x2160s5"}) {
            const auto c = check::Case::load("geometry", name);
            Geometry g(int(c.num("w")), int(c.num("h")), 0, 0, 72, int(c.num("nstrips")));
            CHECK(g.ws == c.num("ws") && g.hs == c.num("hs") && g.slices == c.num("slices"));
            CHECK(g.x0 == c.num("x0") && g.y0 == c.num("y0"));
            const auto strips = c.array<int32_t>("strips");
            CHECK(std::vector<int>(strips.begin(), strips.end()) == g.strips);
            CHECK(g.header(split.header) == c.bytes("header"));
            CHECK(g.gap(split.gap) == c.bytes("gap"));
            auto moov = c.bytes("moov_in"); g.patchMoov(moov, 0);
            CHECK(moov == c.bytes("moov_out"));
        }
    }
    return check::finish("geometry_test");
}
