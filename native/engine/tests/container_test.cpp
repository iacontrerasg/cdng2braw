// Bloque D: contenedor .braw. Pruebas autónomas con la plantilla model/cam/orig.braw y paridad byte a byte con los
// clips del prototipo en Python.
#include "container.hpp"
#include "check.hpp"

#include <algorithm>
#include <cstring>
#include <map>
#include <random>

using namespace cdng;
namespace fs = std::filesystem;

namespace {

fs::path templatePath() { return resourceRoot() / "model/cam/orig.braw"; }

fs::path tempDir() {
    std::random_device rd;
    fs::path dir = fs::temp_directory_path() / ("cdng_container_test_" + std::to_string(rd()) + std::to_string(rd()));
    fs::create_directories(dir);
    return dir;
}

std::vector<uint8_t> templateChunk(std::size_t index) {
    const auto samples = videoSamples(templatePath());
    const auto data = readFile(templatePath());
    const auto &s = samples.at(index);
    const FileChunk c = splitFileChunk(data.data() + s.offset + BmdfSize, s.size - BmdfSize);
    return buildFileChunk(c.header, c.gap, c.slices);
}

bool contains(const std::vector<uint8_t> &data, const std::string &text) {
    return std::search(data.begin(), data.end(), text.begin(), text.end()) != data.end();
}

// Valor de la entrada `tag` del bmdf que empieza en `frame`.
std::size_t bmdfValue(const std::vector<uint8_t> &data, std::size_t frame, const char *tag) {
    const std::size_t size = be32(&data[frame]);
    for (std::size_t off = 8; off + 8 <= size;) {
        const std::size_t sz = be32(&data[frame + off]);
        if (sz < 8) break;
        if (std::memcmp(&data[frame + off + 4], tag, 4) == 0) return frame + off + 8;
        off += sz;
    }
    return 0;
}

// Metadatos de clip QuickTime (keys + ilst) del primer meta del fichero: nombre → valor del átomo data.
std::map<std::string, std::vector<uint8_t>> clipMetadata(const std::vector<uint8_t> &data) {
    std::map<std::string, std::vector<uint8_t>> out;
    const std::string keysTag = "keys", ilstTag = "ilst";
    const auto k = std::search(data.begin(), data.end(), keysTag.begin(), keysTag.end());
    if (k == data.end()) return out;
    const std::size_t keys = std::size_t(k - data.begin()) - 4, keysEnd = keys + be32(&data[keys]);
    std::vector<std::string> names;
    for (std::size_t off = keys + 16; off + 8 <= keysEnd;) {
        const std::size_t size = be32(&data[off]);
        if (size < 8) break;
        names.emplace_back(reinterpret_cast<const char *>(&data[off + 8]), size - 8);
        off += size;
    }
    const auto l = std::search(data.begin() + std::ptrdiff_t(keysEnd), data.end(), ilstTag.begin(), ilstTag.end());
    if (l == data.end()) return out;
    const std::size_t ilst = std::size_t(l - data.begin()) - 4, ilstEnd = ilst + be32(&data[ilst]);
    for (std::size_t off = ilst + 8; off + 8 <= ilstEnd;) {
        const std::size_t size = be32(&data[off]), index = be32(&data[off + 4]);
        if (size < 24) break;
        // item → data: tamaño, "data", tipo (4), locale (4), valor
        if (index >= 1 && index <= names.size() && std::memcmp(&data[off + 12], "data", 4) == 0)
            out[names[index - 1]] = std::vector<uint8_t>(data.begin() + std::ptrdiff_t(off + 24), data.begin() + std::ptrdiff_t(off + 8 + be32(&data[off + 8])));
        off += size;
    }
    return out;
}

void standalone() {
    const auto samples = videoSamples(templatePath());
    CHECK(samples.size() == 2);
    CHECK(samples[0].offset == 8192 && samples[0].size == 2493952);
    CHECK(samples[1].offset == 2535424 && samples[1].size == 3260416);

    const TemplateFrame tpl = readTemplate(templatePath());
    CHECK(tpl.header.size() == 120);
    CHECK(tpl.bmdf.size() == std::size_t(BmdfSize) && std::memcmp(&tpl.bmdf[4], "bmdf", 4) == 0);
    CHECK(tpl.gap.size() == std::size_t(FirstSlice - 0x80 - 4 * tpl.header[2] * tpl.header[3]));

    const fs::path dir = tempDir();
    const fs::path clip = dir / "clip.braw", clipOg = dir / "clip_og.braw";
    const auto c0 = templateChunk(0), c1 = templateChunk(1);
    {
        StreamMux mux(templatePath(), clip);
        FrameMeta va; va.iso = 800; va.kelvin = 4300; va.tint = -3; va.rate = {24000, 1001}; va.route = Route::Va;
        mux.addFrame(c0, va);
        FrameMeta pocket = va; pocket.route = Route::Pocket;
        mux.addFrame(c1, pocket);
        CHECK(mux.frames() == 2);
        FrameMeta ogFrame = va; ogFrame.route = Route::Og; ogFrame.iso = 1600;
        StreamMux ogMux(templatePath(), clipOg);
        ogMux.addFrame(c0, ogFrame);
        ogMux.finish(1000, nullptr, {24000, 1001}, true);
        Pcm24 pcm; pcm.data.assign(6 * 50000, 7); pcm.frames = 50000; pcm.rate = 48000;
        mux.finish(1000, &pcm, {24000, 1001}, true);
    }
    // Nunca sobrescribe.
    bool refused = false;
    try { StreamMux again(templatePath(), clip); } catch (const Error &) { refused = true; }
    CHECK(refused);

    auto data = readFile(clip);
    CHECK(std::memcmp(&data[4], "wide", 4) == 0 && std::memcmp(&data[12], "mdat", 4) == 0);
    const auto out = videoSamples(clip);
    CHECK(out.size() == 2);
    CHECK(out[0].offset == 8 + 16 + 8192);
    CHECK(out[0].size % 8 == 0 && out[0].size >= c0.size() + BmdfSize && out[0].size < c0.size() + BmdfSize + 8);
    CHECK(out[1].offset == out[0].offset + out[0].size);
    // Campo de tamaño del chunk = sample − 1024; bmdf con ISO, WB, cadencia y agpf solo en la ruta va.
    CHECK(be32(&data[out[0].offset + BmdfSize + 4]) == out[0].size - BmdfSize);
    CHECK(be32(&data[bmdfValue(data, out[0].offset, "isoe")]) == 800);
    CHECK(be32(&data[bmdfValue(data, out[0].offset, "wkel")]) == 4300);
    CHECK(int16_t(be16(&data[bmdfValue(data, out[0].offset, "wtin")])) == -3);
    CHECK(be32(&data[bmdfValue(data, out[0].offset, "srte")]) == 24000);
    CHECK(be32(&data[bmdfValue(data, out[0].offset, "srte") + 4]) == 1001);
    const float gain = 1.0f;   // FrameMeta por defecto: SIGMA fp, ISO 800 / ISO de 0 dB 800 (fp L: 2^(1/3))
    CHECK(vaGainFor("SIGMA fp L") == float(std::pow(2.0, 1.0 / 3.0)) && vaGainFor("SIGMA fp") == 1.0f);
    uint32_t bits; std::memcpy(&bits, &gain, 4);
    CHECK(be32(&data[bmdfValue(data, out[0].offset, "agpf")]) == bits);
    CHECK(be32(&data[bmdfValue(data, out[1].offset, "agpf")]) == 0);
    CHECK(bmdfValue(data, out[0].offset, "shtv") == 0);   // minimal_frame quita lo que no está en KEEP_FRAME
    {   // Pocket original: ISO inferida en isoe, sin compensación en expo y ganancia analógica unitaria en agpf.
        const auto ogData = readFile(clipOg);
        const auto frame = videoSamples(clipOg).at(0).offset;
        const float one = ogAnalogGain, zero = 0.0f;
        uint32_t oneBits, zeroBits; std::memcpy(&oneBits, &one, 4); std::memcpy(&zeroBits, &zero, 4);
        CHECK(one == 1.0f);
        CHECK(be32(&ogData[bmdfValue(ogData, frame, "isoe")]) == 1600);
        CHECK(bmdfValue(ogData, frame, "expo") != 0 && be32(&ogData[bmdfValue(ogData, frame, "expo")]) == zeroBits);
        CHECK(be32(&ogData[bmdfValue(ogData, frame, "agpf")]) == oneBits);
        CHECK(be32(&ogData[bmdfValue(ogData, frame, "wkel")]) == 4300);
    }
    // mdat ampliado hasta el moov final.
    const uint64_t mdatEnd = 8 + be64(&data[16]);
    CHECK(mdatEnd < data.size() && std::memcmp(&data[mdatEnd + 4], "moov", 4) == 0);
    CHECK(be32(&data[mdatEnd]) == data.size() - mdatEnd);

    // Identidades del moov.
    const Geometry g(4096, 2160);
    const fs::path og = dir / "og.braw", pocket = dir / "pocket.braw", fpL = dir / "fpl.braw", bad = dir / "bad.braw";
    const fs::path isoClip = dir / "og_iso.braw";
    fs::copy_file(clip, og); fs::copy_file(clip, pocket); fs::copy_file(clip, fpL); fs::copy_file(clip, bad); fs::copy_file(clip, isoClip);
    finalizeClip(clip, g, Route::Va, nullptr);   // por defecto, SIGMA fp
    data = readFile(clip);
    CHECK(contains(data, "SIGMA fp") && !contains(data, "SIGMA fp L"));
    CHECK(videoSamples(clip).size() == 2);
    finalizeClip(fpL, g, Route::Va, nullptr, "SIGMA fp L");
    CHECK(contains(readFile(fpL), "SIGMA fp L") && videoSamples(fpL).size() == 2);
    bool rejected = false;
    try { finalizeClip(bad, g, Route::Va, nullptr, "Sony FX3"); } catch (const Error &) { rejected = true; }
    CHECK(rejected);
    finalizeClip(pocket, g, Route::Pocket, nullptr);
    CHECK(contains(readFile(pocket), "Blackmagic Pocket Cinema Camera 4K"));
    OgModel model; model.cameraType = "Blackmagic Pocket Cinema Camera"; model.gen = 5; model.revision = 0x43544e47;
    ClipExtras extras; extras.cameraId = "0f1e2d3c-4b5a-4978-8695-a4b3c2d1e0f9"; extras.clipNumber = "C0025";
    extras.date = "2024:07:24"; extras.lens = "Nikkor 24mm";
    finalizeClip(og, g, Route::Og, &model, "SIGMA fp", extras);
    data = readFile(og);
    for (const char *key : {"camera_id", "clip_number", "date_recorded", "lens_type"}) CHECK(contains(data, key));   // la identidad de la toma
    CHECK(contains(data, extras.cameraId) && contains(data, "C0025") && contains(data, "Nikkor 24mm"));
    CHECK(contains(data, "Blackmagic Pocket Cinema Camera") && !contains(data, "Pocket Cinema Camera 4K"));
    CHECK(contains(data, "DCI-P3 D65") && contains(data, "Linear"));   // se abre en P3-D65 lineal
    CHECK(!contains(data, "DaVinci Wide Gamut") && !contains(data, "DaVinci Intermediate"));
    CHECK(!contains(data, "post_3dlut_mode"));
    {
        extras.matchCinemaDngRendering = true;
        const fs::path ogMatch = dir / "og_match.braw";
        fs::copy_file(clip, ogMatch);
        finalizeClip(ogMatch, g, Route::Og, &model, "SIGMA fp", extras);
        const auto matchData = readFile(ogMatch);
        const auto matchMeta = clipMetadata(matchData);
        auto asText = [](const std::vector<uint8_t> &v) { return std::string(v.begin(), v.end()); };
        CHECK(asText(matchMeta.at("viewing_gamut")) == "DaVinci Wide Gamut");
        CHECK(asText(matchMeta.at("viewing_gamma")) == "DaVinci Intermediate");
        extras.matchCinemaDngRendering = false;
    }
    {   // ganancia analógica unitaria y constante, como la ruta va
        const auto meta = clipMetadata(data);
        uint32_t oneBits; const float one = 1.0f; std::memcpy(&oneBits, &one, 4);
        CHECK(meta.count("analog_gain") && meta.at("analog_gain").size() == 4 && be32(meta.at("analog_gain").data()) == oneBits);
        CHECK(meta.count("analog_gain_is_constant") && meta.at("analog_gain_is_constant").size() == 2 &&
              be16(meta.at("analog_gain_is_constant").data()) == 1);
        CHECK(meta.count("camera_type") && std::string(meta.at("camera_type").begin(), meta.at("camera_type").end()) ==
              "Blackmagic Pocket Cinema Camera");
    }
    const std::string ctrn = std::string("\0\0\0\x0c" "ctrn", 8) + "CTNG";
    CHECK(contains(data, ctrn));
    {   // cada revisión del perfil queda en el ctrn del clip que la usa
        model.revision = 1128878927u;   // "CISO"
        finalizeClip(isoClip, g, Route::Og, &model, "SIGMA fp", extras);
        CHECK(contains(readFile(isoClip), std::string("\0\0\0\x0c" "ctrn", 8) + "CISO"));
    }
    fs::remove_all(dir);
}

void parity(const char *name) {
    const check::Case c = check::Case::load("container", name);
    const auto chunks = c.bytes("chunks");
    const auto sizes = c.array<int64_t>("chunk_sizes");
    const auto iso = c.array<int32_t>("iso"), kelvin = c.array<int32_t>("kelvin"), tint = c.array<int32_t>("tint");
    const std::string routeName = c.str("route");
    const Route route = routeName == "va" ? Route::Va : routeName == "og" ? Route::Og : Route::Pocket;
    const Rate rate{int(c.num("num")), int(c.num("den"))};
    Pcm24 pcm;
    const bool audio = c.num("audio") != 0;
    if (audio) { pcm.data = c.bytes("pcm"); pcm.frames = (long long)(pcm.data.size() / 6); pcm.rate = 48000; }

    const fs::path dir = tempDir();
    const fs::path clip = dir / "clip.braw";
    {
        StreamMux mux(templatePath(), clip);
        std::size_t offset = 0;
        for (std::size_t i = 0; i < sizes.size(); ++i) {
            std::vector<uint8_t> chunk(chunks.begin() + std::ptrdiff_t(offset), chunks.begin() + std::ptrdiff_t(offset + sizes[i]));
            offset += std::size_t(sizes[i]);
            FrameMeta meta; meta.iso = iso[i]; meta.kelvin = kelvin[i]; meta.tint = tint[i]; meta.rate = rate; meta.route = route;
            mux.addFrame(chunk, meta);
        }
        CHECK(mux.frames() == std::size_t(c.num("frames")));
        mux.finish((long long)c.num("timecode"), audio ? &pcm : nullptr, rate, c.num("drop") != 0);
    }
    OgModel og;
    if (route == Route::Og) { og.cameraType = c.str("camera_type"); og.gen = int(c.num("gen")); og.revision = uint32_t(c.num("revision")); }
    finalizeClip(clip, Geometry(int(c.num("w")), int(c.num("h"))), route, route == Route::Og ? &og : nullptr);
    const auto got = readFile(clip), want = c.bytes("out");
    std::size_t diff = 0;
    while (diff < std::min(got.size(), want.size()) && got[diff] == want[diff]) ++diff;
    if (got.size() != want.size() || diff != got.size())
        std::fprintf(stderr, "%s: %zu bytes frente a %zu; primera diferencia en %zu\n", name, got.size(), want.size(), diff);
    CHECK(got == want);
    CHECK(videoSamples(clip).size() == sizes.size());
    // CDNG_CONTAINER_OUT: conserva los clips del C++ para comprobarlos con el SDK (brawdec).
    if (const char *keep = std::getenv("CDNG_CONTAINER_OUT")) {
        const fs::path target = fs::path(keep) / (std::string(name) + ".braw");
        fs::create_directories(keep); fs::remove(target); fs::copy_file(clip, target);
    }
    fs::remove_all(dir);
}

}  // namespace

int main() {
    try {
        standalone();
        if (!check::skipWithoutGolden("container_test"))
            for (const char *name : {"va24", "va23976df", "pocket25", "og24"}) parity(name);
    } catch (const std::exception &e) {
        std::fprintf(stderr, "excepción: %s\n", e.what());
        ++check::failures();
    }
    return check::finish("container_test");
}
