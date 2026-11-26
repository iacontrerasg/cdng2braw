// brawdec — decodificador mínimo de .braw que usa el framework BlackmagicRawAPI ya instalado
// (Blackmagic RAW Player / DaVinci Resolve) sin las cabeceras del SDK.
//
// Las interfaces son COM-style (vtables Itanium); el orden de métodos sale del manual
// "Blackmagic RAW SDK — Developer Information" (tablas "Public Member Functions", en orden de
// declaración). Solo se declaran los métodos que hacen falta. En macOS las cadenas son
// CFStringRef, IUnknown/REFIID vienen de CFPlugInCOM.h (CFUUIDBytes por valor).
//
// Uso:
//   brawdec CLIP.braw --list                       opciones de gamma / gamut / color science
//   brawdec CLIP.braw FRAME [opciones]
//     --out SALIDA.f32          imagen procesada (RGB float32; cabecera "BRWD" + u32 w,h,3,planar)
//     --gamma G --gamut G --csgen N --scale full|half|quarter|eighth --planar
//     --framework PATH            usar otro BlackmagicRawAPI (p. ej. el incluido en Resolve)
//     --kelvin K --tint T --iso I --exposure E  atributos explícitos de revelado (no metadatos As Shot)
//     --frame-info                valores efectivos y lista ISO permitida por el perfil
//     --metadata-keys             enumerar claves de metadatos reconocidas por el SDK
//     --frame-metadata-keys       enumerar metadatos de frame expuestos por el SDK
//     --audit-attributes          consultar atributos públicos, rangos y opciones (solo lectura)
//     --audit-dispatch            offsets SDK de los métodos de atributos, sin modificar código
//     --clip-number FOURCC N      fijar un atributo numérico usando su tipo SDK real
//     --clip-meta NOMBRE          consultar un metadato (repetible; muestra estado y tipo)
//     --bitstream-out F.bin     volcar el bitstream del frame tal como lo lee el SDK
//     --bitstream-in F.bin      sustituir el bitstream por el de este fichero (flujo manual)
//     --decoded-out F.bin       volcar el buffer "decoded" del flujo manual (antes de procesar)
//     --state-out F.bin         volcar el frame state del flujo manual
//     --trim OUT.braw [--count N]   escribir un .braw recortado (remux, sin recodificar)
//     --embed-lut identity [--lut-size N] [--lut-name N] [--lut-title T] [--lutm Embedded]
//                                   SetEmbeddedPost3DLUT (harness E2b); pasar attrs al trim
//     --save-sidecar                IBlackmagicRawClip::SaveSidecarFile (no es persistencia en el .braw)
//
// Compilar: clang++ -std=c++17 -O1 brawdec.cpp -o brawdec -framework CoreFoundation

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>
#include <dlfcn.h>
#include <mach/mach.h>
#include <mach/mach_vm.h>
#include <CoreFoundation/CoreFoundation.h>
#include <CoreFoundation/CFPlugInCOM.h>

#define FOURCC(a, b, c, d) ((uint32_t)(uint8_t)(a) << 24 | (uint32_t)(uint8_t)(b) << 16 | (uint32_t)(uint8_t)(c) << 8 | (uint32_t)(uint8_t)(d))

enum : uint32_t {
    pipelineCPU = FOURCC('c', 'p', 'u', 'b'),
    scaleFull = FOURCC('f', 'u', 'l', 'l'), scaleHalf = FOURCC('h', 'a', 'l', 'f'),
    scaleQuarter = FOURCC('q', 'r', 't', 'r'), scaleEighth = FOURCC('e', 'i', 't', 'h'),
    fmtRGBF32 = FOURCC('f', '3', '2', 's'), fmtRGBF32Planar = FOURCC('f', '3', '2', 'p'),
    attrColorScienceGen = FOURCC('c', 's', 'g', 'n'), attrGamma = FOURCC('g', 'a', 'm', 'a'), attrGamut = FOURCC('g', 'a', 'm', 't'),
    vtU8 = 1, vtS16 = 2, vtU16 = 3, vtS32 = 4, vtU32 = 5, vtF32 = 6, vtString = 7, vtSafeArray = 8, vtF64 = 9,
};

// IIDs (los publica la cabecera del SDK; aquí solo los valores)
static const CFUUIDBytes IID_ClipEx6 = {0x7D, 0xB6, 0x0B, 0x13, 0x6B, 0xB2, 0x49, 0xDE, 0x9A, 0x24, 0xAA, 0x14, 0xA9, 0xF3, 0xCA, 0x6B};
static const CFUUIDBytes IID_ManualDecoderFlow1 = {0x27, 0x88, 0x15, 0xA6, 0xA3, 0xC1, 0x47, 0xC7, 0xA0, 0xA6, 0x67, 0x54, 0xDE, 0xAE, 0x5E, 0x7A};
static const CFUUIDBytes IID_FrameEx = {0xF8, 0xC6, 0xC3, 0x74, 0xD7, 0xFB, 0x4B, 0xD3, 0xAD, 0x0B, 0xC5, 0x33, 0x46, 0x4F, 0xF4, 0x50};
static const CFUUIDBytes IID_ToneCurve = {0x7E, 0x40, 0xC1, 0x3D, 0x35, 0x75, 0x46, 0xB5, 0xB2, 0xB7, 0x85, 0xDA, 0xE1, 0xEE, 0xFD, 0x77};

struct Variant {
    uint32_t vt;
    union { uint8_t u8; int16_t s16; uint16_t u16; int32_t s32; uint32_t u32; float f32; double f64; CFStringRef str; void* parray; };
};
static void (*clear_variant)(Variant*) = nullptr;
struct SafeArrayBound { uint32_t cElements; int32_t lLbound; };
struct SafeArray;
static SafeArray* (*safe_array_create)(uint16_t vt, uint32_t dims, SafeArrayBound*) = nullptr;
static HRESULT (*safe_array_access)(SafeArray*, void**) = nullptr;
static HRESULT (*safe_array_unaccess)(SafeArray*) = nullptr;
static HRESULT (*safe_array_destroy)(SafeArray*) = nullptr;

struct IJob : IUnknown {
    virtual HRESULT Submit() = 0;
    virtual HRESULT Abort() = 0;
    virtual HRESULT SetUserData(void*) = 0;
    virtual HRESULT GetUserData(void**) = 0;
};
struct IProcessedImage : IUnknown {
    virtual HRESULT GetWidth(uint32_t*) = 0;
    virtual HRESULT GetHeight(uint32_t*) = 0;
    virtual HRESULT GetResource(void**) = 0;
    virtual HRESULT GetResourceType(uint32_t*) = 0;
    virtual HRESULT GetResourceFormat(uint32_t*) = 0;
    virtual HRESULT GetResourceSizeBytes(uint32_t*) = 0;
    virtual HRESULT GetResourceContextAndCommandQueue(void**, void**) = 0;
};
struct IMetadataIterator : IUnknown {
    virtual HRESULT Next() = 0;
    virtual HRESULT GetKey(CFStringRef*) = 0;
    virtual HRESULT GetData(Variant*) = 0;
};
struct IPost3DLUT : IUnknown {
    virtual HRESULT GetName(CFStringRef*) = 0;
    virtual HRESULT GetTitle(CFStringRef*) = 0;
    virtual HRESULT GetSize(uint32_t*) = 0;
    virtual HRESULT GetResourceGPU(void*, void*, uint32_t*, void**) = 0;
    virtual HRESULT GetResourceCPU(void**) = 0;
    virtual HRESULT GetResourceSizeBytes(uint32_t*) = 0;
    virtual HRESULT WriteCubeFile(CFStringRef) = 0;
    virtual HRESULT WriteCubeToFile(void*) = 0;
};
struct IClipAttrs : IUnknown {
    virtual HRESULT GetClipAttribute(uint32_t attr, Variant* v) = 0;
    virtual HRESULT SetClipAttribute(uint32_t attr, Variant* v) = 0;
    virtual HRESULT GetClipAttributeRange(uint32_t attr, Variant* lo, Variant* hi, bool* ro) = 0;
    virtual HRESULT GetClipAttributeList(uint32_t attr, Variant* arr, uint32_t* n, bool* ro) = 0;
    virtual HRESULT GetISOList(uint32_t* array, uint32_t* n, bool* ro) = 0;
    virtual HRESULT GetPost3DLUT(IPost3DLUT** lut) = 0;
    virtual HRESULT GetSidecarPost3DLUT(IPost3DLUT** lut) = 0;
    virtual HRESULT SetSidecarPost3DLUT(CFStringRef name, CFStringRef title, uint16_t size, Variant* data) = 0;
    virtual HRESULT GetEmbeddedPost3DLUT(IPost3DLUT** lut) = 0;
    virtual HRESULT SetEmbeddedPost3DLUT(CFStringRef name, CFStringRef title, uint16_t size, Variant* data) = 0;
};
struct IFrameAttrs : IUnknown {
    virtual HRESULT GetFrameAttribute(uint32_t, Variant*) = 0;
    virtual HRESULT SetFrameAttribute(uint32_t, Variant*) = 0;
    virtual HRESULT GetFrameAttributeRange(uint32_t, Variant*, Variant*, bool*) = 0;
    virtual HRESULT GetFrameAttributeList(uint32_t, Variant*, uint32_t*, bool*) = 0;
    virtual HRESULT GetISOList(uint32_t*, uint32_t*, bool*) = 0;
};
struct IFrame : IUnknown {
    virtual HRESULT GetFrameIndex(uint64_t*) = 0;
    virtual HRESULT GetTimecode(CFStringRef*) = 0;
    virtual HRESULT GetMetadataIterator(void**) = 0;
    virtual HRESULT GetMetadata(CFStringRef, Variant*) = 0;
    virtual HRESULT SetMetadata(CFStringRef, Variant*) = 0;
    virtual HRESULT CloneFrameProcessingAttributes(IFrameAttrs**) = 0;
    virtual HRESULT SetResolutionScale(uint32_t) = 0;
    virtual HRESULT GetResolutionScale(uint32_t*) = 0;
    virtual HRESULT SetResourceFormat(uint32_t) = 0;
    virtual HRESULT GetResourceFormat(uint32_t*) = 0;
    virtual HRESULT GetSensorRate(float*) = 0;
    virtual HRESULT CreateJobDecodeAndProcessFrame(IClipAttrs*, IFrameAttrs*, IJob**) = 0;
};
struct IFrameEx : IUnknown {
    virtual HRESULT GetBitStreamSizeBytes(uint32_t*) = 0;
    virtual HRESULT GetProcessedImageResolution(uint32_t*, uint32_t*) = 0;
};
struct IClip : IUnknown {
    virtual HRESULT GetWidth(uint32_t*) = 0;
    virtual HRESULT GetHeight(uint32_t*) = 0;
    virtual HRESULT GetFrameRate(float*) = 0;
    virtual HRESULT GetFrameCount(uint64_t*) = 0;
    virtual HRESULT GetTimecodeForFrame(uint64_t, CFStringRef*) = 0;
    virtual HRESULT GetMetadataIterator(void**) = 0;
    virtual HRESULT GetMetadata(CFStringRef, Variant*) = 0;
    virtual HRESULT SetMetadata(CFStringRef, Variant*) = 0;
    virtual HRESULT GetCameraType(CFStringRef*) = 0;
    virtual HRESULT CloneClipProcessingAttributes(IClipAttrs**) = 0;
    virtual HRESULT GetMulticardFileCount(uint32_t*) = 0;
    virtual HRESULT IsMulticardFilePresent(uint32_t, bool*) = 0;
    virtual HRESULT GetSidecarFileAttached(bool*) = 0;
    virtual HRESULT SaveSidecarFile() = 0;
    virtual HRESULT ReloadSidecarFile() = 0;
    virtual HRESULT CreateJobReadFrame(uint64_t, IJob**) = 0;
    virtual HRESULT CreateJobReadAudio(uint64_t, uint64_t, IJob**) = 0;
};
struct IClipEx : IUnknown {
    virtual HRESULT GetMaxBitStreamSizeBytes(uint32_t*) = 0;
    virtual HRESULT GetBitStreamSizeBytes(uint64_t, uint32_t*) = 0;
    virtual HRESULT CreateJobReadFrame(uint64_t, void* bitStream, uint32_t size, IJob**) = 0;
    virtual HRESULT CreateJobTrim(CFStringRef path, uint64_t frameIndex, uint64_t frameCount, uint32_t frameStep, float frameRate, IClipAttrs*, IFrameAttrs*, IJob**) = 0;
    virtual HRESULT CreateJobTrimToFile(void*, uint64_t, uint64_t, uint32_t, float, IClipAttrs*, IFrameAttrs*, IJob**) = 0;
    virtual HRESULT QueryTimecodeInfo(uint32_t*, bool*) = 0;
    virtual HRESULT GetFrameImageOffsetAndSize(uint64_t, uint32_t* size, uint64_t* offset, uint32_t* fileIndex) = 0;
};
struct IFlow1 : IUnknown {
    virtual HRESULT PopulateFrameStateBuffer(IFrame*, IClipAttrs*, IFrameAttrs*, void* state, uint32_t stateSize) = 0;
    virtual HRESULT GetFrameStateSizeBytes(uint32_t*) = 0;
    virtual HRESULT GetDecodedSizeBytes(void* state, uint32_t*) = 0;
    virtual HRESULT GetProcessedSizeBytes(void* state, uint32_t*) = 0;
    virtual HRESULT GetPost3DLUTSizeBytes(void* state, uint32_t*) = 0;
    virtual HRESULT CreateJobDecode(void* state, void* bitstream, void* decoded, IJob**) = 0;
    virtual HRESULT CreateJobProcess(void* state, void* decoded, void* processed, void* lut, IJob**) = 0;
};
struct ICallback;
struct ICodec : IUnknown {
    virtual HRESULT OpenClip(CFStringRef, IClip**) = 0;
    virtual HRESULT OpenClipWithGeometry(CFStringRef, void*, IClip**) = 0;
    virtual HRESULT OpenClipFromFile(void*, IClip**) = 0;
    virtual HRESULT OpenClipFromFileWithGeometry(void*, void*, IClip**) = 0;
    virtual HRESULT SetCallback(ICallback*) = 0;
    virtual HRESULT PreparePipeline(uint32_t pipeline, void* ctx, void* queue, void* user) = 0;
    virtual HRESULT PreparePipelineForDevice(void*, void*) = 0;
    virtual HRESULT FlushJobs() = 0;
};
struct IFactory : IUnknown {
    virtual HRESULT CreateCodec(ICodec**) = 0;
};
// IBlackmagicRawToneCurve (QueryInterface sobre el códec): parámetros de la curva por cámara y gen.
struct IToneCurve : IUnknown {
    virtual HRESULT GetToneCurve(CFStringRef cameraType, CFStringRef gamma, uint16_t gen, float* contrast, float* saturation, float* midpoint,
                                 float* highlights, float* shadows, float* blackLevel, float* whiteLevel, uint16_t* videoBlackLevel) = 0;
    virtual HRESULT EvaluateToneCurve(CFStringRef cameraType, uint16_t gen, float contrast, float saturation, float midpoint, float highlights,
                                      float shadows, float blackLevel, float whiteLevel, uint16_t videoBlackLevel, float* array, uint32_t count) = 0;
};
struct ICallback : IUnknown {
    virtual void ReadComplete(IJob*, HRESULT, IFrame*) = 0;
    virtual void ReadAudioComplete(IJob*, HRESULT, void* audio) = 0;
    virtual void DecodeComplete(IJob*, HRESULT) = 0;
    virtual void ProcessComplete(IJob*, HRESULT, IProcessedImage*) = 0;
    virtual void TrimProgress(IJob*, float) = 0;
    virtual void TrimComplete(IJob*, HRESULT) = 0;
    virtual void SidecarMetadataParseWarning(IClip*, CFStringRef, uint32_t, CFStringRef) = 0;
    virtual void SidecarMetadataParseError(IClip*, CFStringRef, uint32_t, CFStringRef) = 0;
    virtual void PreparePipelineComplete(void*, HRESULT) = 0;
};

static const char* FRAMEWORK = "/Applications/Blackmagic RAW/Blackmagic RAW Player.app/Contents/Frameworks/BlackmagicRawAPI.framework/BlackmagicRawAPI";

static std::string cstr(CFStringRef s) {
    if (!s) return "(null)";
    char buf[1024]; return CFStringGetCString(s, buf, sizeof buf, kCFStringEncodingUTF8) ? std::string(buf) : "(?)";
}
static CFStringRef cfstr(const std::string& s) { return CFStringCreateWithCString(kCFAllocatorDefault, s.c_str(), kCFStringEncodingUTF8); }

static bool write_file(const std::string& path, const void* data, size_t n, const char* hdr = nullptr, size_t hn = 0) {
    FILE* f = fopen(path.c_str(), "wb");
    if (!f) { perror(path.c_str()); return false; }
    if (hdr) fwrite(hdr, 1, hn, f);
    fwrite(data, 1, n, f); fclose(f); return true;
}
static std::vector<uint8_t> read_file(const std::string& path) {
    std::vector<uint8_t> v; FILE* f = fopen(path.c_str(), "rb");
    if (!f) { perror(path.c_str()); return v; }
    fseek(f, 0, SEEK_END); long n = ftell(f); fseek(f, 0, SEEK_SET); v.resize(n);
    if (fread(v.data(), 1, n, f) != (size_t)n) v.clear();
    fclose(f); return v;
}

struct Options {
    std::string clip, out, gamma, gamut, csgen, bsOut, bsIn, decOut, decIn, stateOut, trim, toneCurve, memscan;
    std::string framework = FRAMEWORK, schemaOut, supportOut;
    std::string kelvin, tint, iso, exposure;
    std::string embedLut, lutName = "feat001-e2b", lutTitle = "E2b identity", lutm;
    uint16_t lutSize = 2;
    bool saveSidecar = false;
    bool frameInfo = false, metadataKeys = false, frameMetadataKeys = false;
    bool auditAttributes = false;
    bool auditDispatch = false;
    std::vector<std::pair<std::string, std::string>> clipNumbers;
    std::vector<std::string> metadataQueries;
    uint64_t frame = 0, count = 1; uint32_t scale = scaleFull, evalN = 0; bool planar = false, list = false;
    bool manual() const { return !bsIn.empty() || !decOut.empty() || !decIn.empty() || !stateOut.empty(); }
};
static void audit_frame_attributes(IFrameAttrs*);
static void audit_attribute_dispatch(const char*,void*);

// Use the cloned attribute's actual SDK type, rather than guessing its ABI.
static bool set_frame_number(IFrameAttrs* attrs, uint32_t key, const std::string& value) {
    if (value.empty()) return true;
    Variant v{};
    if (attrs->GetFrameAttribute(key, &v) < 0) return false;
    double n = strtod(value.c_str(), nullptr);
    fprintf(stderr, "frame attribute type %c%c%c%c: %u\n", key >> 24, key >> 16, key >> 8, key, v.vt);
    switch (v.vt) {
        case vtU16: v.u16 = (uint16_t)n; break;
        case vtS16: v.s16 = (int16_t)n; break;
        case vtU32: v.u32 = (uint32_t)n; break;
        case vtS32: v.s32 = (int32_t)n; break;
        case vtF32: v.f32 = (float)n; break;
        case vtF64: v.f64 = n; break;
        default: return false;
    }
    HRESULT h = attrs->SetFrameAttribute(key, &v);
    fprintf(stderr, "frame attribute %c%c%c%c=%s: %#x\n", key >> 24, key >> 16, key >> 8, key, value.c_str(), h);
    return h >= 0;
}

static bool write_image(const Options& o, uint32_t w, uint32_t h, const void* buf, uint32_t bytes) {
    if (bytes < (uint32_t)w * h * 3 * 4) { fprintf(stderr, "buffer inesperado (%u bytes para %ux%u)\n", bytes, w, h); return false; }  // el SDK puede devolver un buffer algo mayor (alineación) en otras geometrías
    uint32_t hdr[5]; memcpy(hdr, "BRWD", 4); hdr[1] = w; hdr[2] = h; hdr[3] = 3; hdr[4] = o.planar ? 1u : 0u;
    return write_file(o.out, buf, bytes, (const char*)hdr, sizeof hdr);
}

struct Callback : ICallback {
    Options* opt; IClipAttrs* attrs = nullptr; HRESULT status = 0; bool done = false;
    IFrame* frame = nullptr;   // flujo manual: el frame leído queda aquí
    IFrameAttrs* frameAttrs = nullptr;
    HRESULT QueryInterface(REFIID, LPVOID* p) override { *p = nullptr; return E_NOINTERFACE; }
    ULONG AddRef() override { return 1; }
    ULONG Release() override { return 1; }
    void ReadComplete(IJob* job, HRESULT r, IFrame* fr) override {
        job->Release();
        if (r < 0) { fprintf(stderr, "ReadComplete error %#x\n", r); status = r; done = true; return; }
        if (opt->frameMetadataKeys) {
            IMetadataIterator* iterator = nullptr;
            if (fr->GetMetadataIterator((void**)&iterator) >= 0 && iterator) {
                do {
                    CFStringRef key = nullptr;
                    if (iterator->GetKey(&key) < 0 || !key) break;
                    // Keys only: avoid dumping user metadata values or arrays.
                    printf("frame_metadata_key %s\n", cstr(key).c_str());
                    CFRelease(key);
                } while (iterator->Next() == 0);
                iterator->Release();
            }
        }
        fr->SetResourceFormat(opt->planar ? fmtRGBF32Planar : fmtRGBF32);
        fr->SetResolutionScale(opt->scale);
        if (opt->auditAttributes || opt->auditDispatch || opt->frameInfo || !opt->kelvin.empty() || !opt->tint.empty() || !opt->iso.empty() || !opt->exposure.empty()) {
            if (fr->CloneFrameProcessingAttributes(&frameAttrs) < 0 || !frameAttrs ||
                !set_frame_number(frameAttrs, FOURCC('w','b','k','v'), opt->kelvin) ||
                !set_frame_number(frameAttrs, FOURCC('w','b','t','n'), opt->tint) ||
                !set_frame_number(frameAttrs, FOURCC('f','i','s','o'), opt->iso) ||
                !set_frame_number(frameAttrs, FOURCC('e','x','p','o'), opt->exposure)) {
                status = -1; done = true; return;
            }
            if (opt->auditAttributes) audit_frame_attributes(frameAttrs);
            if (opt->auditDispatch) audit_attribute_dispatch("frame",frameAttrs);
            if (opt->frameInfo) {
                for (uint32_t key : {FOURCC('w','b','k','v'), FOURCC('w','b','t','n'), FOURCC('f','i','s','o'), FOURCC('a','g','p','f'), FOURCC('e','x','p','o')}) {
                    Variant v{}; HRESULT r = frameAttrs->GetFrameAttribute(key, &v);
                    fprintf(stderr, "effective %c%c%c%c type %u status %#x value %.9g\n", key >> 24, key >> 16, key >> 8, key, v.vt, r,
                            v.vt == vtF32 ? v.f32 : v.vt == vtU32 ? v.u32 : v.vt == vtS16 ? v.s16 : v.vt == vtU16 ? v.u16 : 0.);
                }
                uint32_t n = 0; bool ro = false;
                if (frameAttrs->GetISOList(nullptr, &n, &ro) >= 0 && n) {
                    std::vector<uint32_t> values(n); frameAttrs->GetISOList(values.data(), &n, &ro);
                    fprintf(stderr, "ISO list readonly=%u:", ro);
                    for (uint32_t value : values) fprintf(stderr, " %u", value);
                    fprintf(stderr, "\n");
                }
            }
        }
        if (opt->manual()) { fr->AddRef(); frame = fr; done = true; return; }
        if (opt->out.empty()) { done = true; return; }
        IJob* dj = nullptr;
        HRESULT h = fr->CreateJobDecodeAndProcessFrame(attrs, frameAttrs, &dj);
        if (h < 0 || !dj) { fprintf(stderr, "CreateJobDecodeAndProcessFrame error %#x\n", h); status = h; done = true; return; }
        dj->Submit();
    }
    void ReadAudioComplete(IJob* job, HRESULT, void*) override { job->Release(); }
    void DecodeComplete(IJob* job, HRESULT r) override { if (r < 0) { fprintf(stderr, "DecodeComplete error %#x\n", r); status = r; } if (opt->manual()) { job->Release(); done = true; } }
    void ProcessComplete(IJob* job, HRESULT r, IProcessedImage* img) override {
        job->Release();
        if (r < 0) { fprintf(stderr, "ProcessComplete error %#x\n", r); status = r; done = true; return; }
        uint32_t w = 0, h = 0, type = 0, fmt = 0, bytes = 0; void* buf = nullptr;
        img->GetWidth(&w); img->GetHeight(&h); img->GetResourceType(&type); img->GetResourceFormat(&fmt);
        img->GetResourceSizeBytes(&bytes); img->GetResource(&buf);
        fprintf(stderr, "procesado: %ux%u, tipo %c%c%c%c, formato %c%c%c%c, %u bytes\n", w, h,
                type >> 24, type >> 16, type >> 8, type, fmt >> 24, fmt >> 16, fmt >> 8, fmt, bytes);
        if (!opt->out.empty() && !write_image(*opt, w, h, buf, bytes)) status = -1;
        done = true;
    }
    void TrimProgress(IJob*, float) override {}
    void TrimComplete(IJob* job, HRESULT r) override { job->Release(); if (r < 0) { fprintf(stderr, "TrimComplete error %#x\n", r); status = r; } done = true; }
    void SidecarMetadataParseWarning(IClip*, CFStringRef fn, uint32_t line, CFStringRef info) override { fprintf(stderr, "sidecar aviso %s:%u %s\n", cstr(fn).c_str(), line, cstr(info).c_str()); }
    void SidecarMetadataParseError(IClip*, CFStringRef fn, uint32_t line, CFStringRef info) override { fprintf(stderr, "sidecar error %s:%u %s\n", cstr(fn).c_str(), line, cstr(info).c_str()); }
    void PreparePipelineComplete(void*, HRESULT r) override { if (r < 0) fprintf(stderr, "PreparePipeline error %#x\n", r); }
};

static void print_variant(const Variant& v) {
    switch (v.vt) {
        case vtU8: printf("%u", v.u8); break; case vtS16: printf("%d", v.s16); break; case vtU16: printf("%u", v.u16); break;
        case vtS32: printf("%d", v.s32); break; case vtU32: printf("%u", v.u32); break; case vtF32: printf("%g", v.f32); break;
        case vtF64: printf("%g", v.f64); break; case vtString: printf("\"%s\"", cstr(v.str).c_str()); break;
        default: printf("(vt=%u)", v.vt);
    }
}

static void audit_attribute_dispatch(const char* scope,void* object) {
    // Read the same COM vtable slots used by the declared public interfaces.
    // Only module-relative method addresses, no memory dumps or code patches.
    void** methods=*reinterpret_cast<void***>(object);
    const char* labels[]={"get","set","range","list"};
    for(size_t i=0;i<4;++i) {
        Dl_info info{};
        if(dladdr(methods[i+3],&info) && info.dli_fbase) {
            printf("attribute_dispatch\t%s\t%s\t%#llx\t%s\n",scope,labels[i],
                (unsigned long long)((uintptr_t)methods[i+3]-(uintptr_t)info.dli_fbase),
                info.dli_fname?info.dli_fname:"");
        }
    }
}

template<class Get, class Range, class List>
static void audit_attribute(const char* scope, const char* key, Get get, Range range, List list) {
    uint32_t attr = FOURCC(key[0],key[1],key[2],key[3]);
    Variant value{}, lo{}, hi{}; bool rangeRO = false, listRO = false; uint32_t n = 0;
    HRESULT gs = get(attr,&value), rs = range(attr,&lo,&hi,&rangeRO);
    HRESULT ls = list(attr,nullptr,&n,&listRO);
    printf("capability\t%s\t%s\t%#x\t%u\t",scope,key,gs,value.vt); print_variant(value);
    printf("\t%#x\t%u\t",rs,rangeRO); print_variant(lo); printf("\t"); print_variant(hi);
    printf("\t%#x\t%u\t%u",ls,listRO,n);
    if (ls >= 0 && n && n <= 512) {
        std::vector<Variant> values(n);
        uint32_t capacity = n;
        HRESULT populated = list(attr,values.data(),&capacity,&listRO);
        printf("\t%#x",populated);
        if (populated >= 0) for (uint32_t i=0;i<capacity && i<n;++i) {
            printf("\t"); print_variant(values[i]);
        }
        if (clear_variant) for (auto& v:values) clear_variant(&v);
    }
    printf("\n");
    if (clear_variant) { clear_variant(&value); clear_variant(&lo); clear_variant(&hi); }
}

static void audit_clip_attributes(IClipAttrs* a) {
    // Complete ClipProcessingAttribute enum in the September 2026 SDK manual.
    // These are documented processing attributes, not guessed metadata names.
    for (const char* key : {"csgn","gama","gamt","tcon","tsat","tmid","thih","tsha","tvbl","tblk","twit",
                           "hlry","agic","gain","lutm","emln","emlt","emls","emld","scln","sclt","scls","scld",
                           "gace","shde","dice","cace"}) {
        audit_attribute("clip",key,
            [&](uint32_t k,Variant* v){return a->GetClipAttribute(k,v);},
            [&](uint32_t k,Variant* lo,Variant* hi,bool* ro){return a->GetClipAttributeRange(k,lo,hi,ro);},
            [&](uint32_t k,Variant* v,uint32_t* n,bool* ro){return a->GetClipAttributeList(k,v,n,ro);});
    }
}

static void audit_frame_attributes(IFrameAttrs* a) {
    for (const char* key : {"wbkv","wbtn","expo","fiso","agpf"}) {
        audit_attribute("frame",key,
            [&](uint32_t k,Variant* v){return a->GetFrameAttribute(k,v);},
            [&](uint32_t k,Variant* lo,Variant* hi,bool* ro){return a->GetFrameAttributeRange(k,lo,hi,ro);},
            [&](uint32_t k,Variant* v,uint32_t* n,bool* ro){return a->GetFrameAttributeList(k,v,n,ro);});
    }
}

static bool set_clip_number(IClipAttrs* a, const std::string& key, const std::string& number) {
    if (key.size()!=4) return false;
    uint32_t attr=FOURCC(key[0],key[1],key[2],key[3]); Variant v{};
    HRESULT result=a->GetClipAttribute(attr,&v);
    if (result<0) { fprintf(stderr,"GetClipAttribute %s error %#x\n",key.c_str(),result); return false; }
    double value=strtod(number.c_str(),nullptr);
    switch(v.vt) {
        case vtU8:v.u8=(uint8_t)value;break; case vtS16:v.s16=(int16_t)value;break;
        case vtU16:v.u16=(uint16_t)value;break; case vtS32:v.s32=(int32_t)value;break;
        case vtU32:v.u32=(uint32_t)value;break; case vtF32:v.f32=(float)value;break;
        case vtF64:v.f64=value;break;
        default:if(clear_variant)clear_variant(&v);return false;
    }
    result=a->SetClipAttribute(attr,&v);
    fprintf(stderr,"clip attribute %s=%s type %u status %#x\n",key.c_str(),number.c_str(),v.vt,result);
    if(clear_variant)clear_variant(&v);
    return result>=0;
}

static void list_attr(IClipAttrs* a, const char* name, uint32_t attr) {
    Variant cur{}; a->GetClipAttribute(attr, &cur);
    printf("%s: actual=", name); print_variant(cur);
    uint32_t n = 0; bool ro = false;
    if (a->GetClipAttributeList(attr, nullptr, &n, &ro) >= 0 && n) {
        std::vector<Variant> arr(n);
        a->GetClipAttributeList(attr, arr.data(), &n, &ro);
        printf("  opciones(%u%s):", n, ro ? ", solo lectura" : "");
        for (uint32_t i = 0; i < n; i++) { printf(" "); print_variant(arr[i]); }
    }
    printf("\n");
}

static bool set_string_attr(IClipAttrs* a, uint32_t attr, const std::string& s) {
    Variant v{}; v.vt = vtString; v.str = cfstr(s);
    HRESULT h = a->SetClipAttribute(attr, &v);
    if (h < 0) fprintf(stderr, "SetClipAttribute %c%c%c%c=\"%s\" error %#x\n", attr >> 24, attr >> 16, attr >> 8, attr, s.c_str(), h);
    return h >= 0;
}

static void print_post_lut(const char* which, IPost3DLUT* lut) {
    if (!lut) { fprintf(stderr, "%s: (null)\n", which); return; }
    CFStringRef name = nullptr, title = nullptr; uint32_t size = 0, bytes = 0; void* cpu = nullptr;
    lut->GetName(&name); lut->GetTitle(&title); lut->GetSize(&size);
    lut->GetResourceCPU(&cpu); lut->GetResourceSizeBytes(&bytes);
    fprintf(stderr, "%s name=\"%s\" title=\"%s\" size=%u cpu=%p bytes=%u\n",
            which, cstr(name).c_str(), cstr(title).c_str(), size, cpu, bytes);
    if (name) CFRelease(name);
    if (title) CFRelease(title);
}

static void dump_vtable(void* object, const char* tag) {
    void** methods = *reinterpret_cast<void***>(object);
    fprintf(stderr, "vtable %s", tag);
    for (int i = 0; i < 16; i++) fprintf(stderr, " [%d]=%p", i, methods[i]);
    fprintf(stderr, "\n");
}

static void report_lut_attrs(IClipAttrs* a) {
    for (const char* key : {"lutm", "emln", "emlt", "emls", "emld", "scln", "sclt", "scls", "scld"}) {
        Variant v{};
        HRESULT r = a->GetClipAttribute(FOURCC(key[0], key[1], key[2], key[3]), &v);
        fprintf(stderr, "clip %s status %#x type %u ", key, r, v.vt);
        if (r >= 0) { print_variant(v); printf("\n"); } else fprintf(stderr, "\n");
        if (r >= 0 && clear_variant) clear_variant(&v);
    }
    IPost3DLUT *active = nullptr, *embedded = nullptr, *sidecar = nullptr;
    HRESULT ra = a->GetPost3DLUT(&active), re = a->GetEmbeddedPost3DLUT(&embedded), rs = a->GetSidecarPost3DLUT(&sidecar);
    fprintf(stderr, "GetPost3DLUT %#x GetEmbedded %#x GetSidecar %#x\n", ra, re, rs);
    if (ra >= 0) print_post_lut("active", active);
    if (re >= 0) print_post_lut("embedded", embedded);
    if (rs >= 0) print_post_lut("sidecar", sidecar);
    if (active) active->Release();
    if (embedded) embedded->Release();
    if (sidecar) sidecar->Release();
}

// Lattice identidad size³ RGB float32 (R más lento, B más rápido). SafeArray 1-D, vt = F32 (6).
static bool set_embedded_identity(IClipAttrs* a, const Options& o) {
    if (!safe_array_create || !safe_array_access || !safe_array_unaccess) {
        fprintf(stderr, "SafeArray* no está en el framework\n"); return false;
    }
    const uint16_t n = o.lutSize;
    if (n < 2 || n > 33) { fprintf(stderr, "lut-size %u fuera de 2–33\n", n); return false; }
    const uint32_t cells = (uint32_t)n * n * n;
    SafeArrayBound bound{cells * 3, 0};
    SafeArray* arr = safe_array_create(vtF32, 1, &bound);
    if (!arr) { fprintf(stderr, "SafeArrayCreate F32 × %u falló\n", bound.cElements); return false; }
    void* data = nullptr;
    if (safe_array_access(arr, &data) < 0 || !data) {
        fprintf(stderr, "SafeArrayAccessData falló\n"); safe_array_destroy(arr); return false;
    }
    float* rgb = (float*)data;
    for (uint16_t r = 0; r < n; r++)
        for (uint16_t g = 0; g < n; g++)
            for (uint16_t b = 0; b < n; b++) {
                const uint32_t i = ((uint32_t)r * n * n + (uint32_t)g * n + b) * 3;
                rgb[i] = n == 1 ? 0.f : float(r) / float(n - 1);
                rgb[i + 1] = n == 1 ? 0.f : float(g) / float(n - 1);
                rgb[i + 2] = n == 1 ? 0.f : float(b) / float(n - 1);
            }
    safe_array_unaccess(arr);
    Variant v{}; v.vt = vtSafeArray; v.parray = arr;
    dump_vtable(a, "clip-attrs");
    IPost3DLUT* probe = nullptr;
    fprintf(stderr, "GetEmbedded %#x lut=%p GetSidecar %#x GetPost %#x\n",
            a->GetEmbeddedPost3DLUT(&probe), (void*)probe,
            a->GetSidecarPost3DLUT(&probe), a->GetPost3DLUT(&probe));
    CFStringRef name = cfstr(o.lutName), title = cfstr(o.lutTitle);
    HRESULT h = a->SetEmbeddedPost3DLUT(name, title, n, &v);
    fprintf(stderr, "SetEmbeddedPost3DLUT F32×%u %#x\n", bound.cElements, h);
    HRESULT hSide = a->SetSidecarPost3DLUT(name, title, n, &v);
    fprintf(stderr, "SetSidecarPost3DLUT F32×%u %#x\n", bound.cElements, hSide);
    CFRelease(name); CFRelease(title);
    if (h < 0 && hSide < 0) return false;
    if (!o.lutm.empty() && !set_string_attr(a, FOURCC('l', 'u', 't', 'm'), o.lutm)) return false;
    return true;
}

// Valores efectivos de clip tras SetClipAttribute (para --frame-info). Un status ≠ 0 significa no verificable.
static void print_effective_clip(IClipAttrs* a, uint32_t attr, const char* name) {
    Variant v{};
    HRESULT r = a->GetClipAttribute(attr, &v);
    fprintf(stderr, "effective clip %s type %u status %#x value ", name, v.vt, r);
    if (r < 0) { fprintf(stderr, "?\n"); return; }
    switch (v.vt) {
        case vtU8: fprintf(stderr, "%u\n", v.u8); break;
        case vtS16: fprintf(stderr, "%d\n", v.s16); break;
        case vtU16: fprintf(stderr, "%u\n", v.u16); break;
        case vtS32: fprintf(stderr, "%d\n", v.s32); break;
        case vtU32: fprintf(stderr, "%u\n", v.u32); break;
        case vtF32: fprintf(stderr, "%.9g\n", v.f32); break;
        case vtF64: fprintf(stderr, "%.9g\n", v.f64); break;
        case vtString: fprintf(stderr, "\"%s\"\n", cstr(v.str).c_str()); if (v.str) CFRelease(v.str); break;
        default: fprintf(stderr, "?\n"); if (clear_variant) clear_variant(&v); break;
    }
}

static const char* scale_name(uint32_t scale) {
    if (scale == scaleHalf) return "half";
    if (scale == scaleQuarter) return "quarter";
    if (scale == scaleEighth) return "eighth";
    return "full";
}

// Busca un patrón en toda la memoria legible del proceso (las tablas que el SDK descifra en tiempo de ejecución)
// e imprime las cadenas imprimibles que hay alrededor de cada coincidencia.
static void memscan(const std::string& pat, const char* when) {
    mach_vm_address_t addr = 0; mach_vm_size_t size = 0; natural_t depth = 0; int hits = 0;
    fprintf(stderr, "--- memscan \"%s\" (%s)\n", pat.c_str(), when);
    while (true) {
        vm_region_submap_info_data_64_t info; mach_msg_type_number_t cnt = VM_REGION_SUBMAP_INFO_COUNT_64;
        if (mach_vm_region_recurse(mach_task_self(), &addr, &size, &depth, (vm_region_recurse_info_t)&info, &cnt) != KERN_SUCCESS) break;
        if (info.is_submap) { depth++; continue; }
        if ((info.protection & VM_PROT_READ) && size <= (1ull << 31)) {
            std::vector<char> buf(size); mach_vm_size_t got = 0;
            if (mach_vm_read_overwrite(mach_task_self(), addr, size, (mach_vm_address_t)buf.data(), &got) == KERN_SUCCESS && got >= pat.size()) {
                const char* b = buf.data(); const char* e = b + got; const char* p = b;
                while ((p = (const char*)memmem(p, e - p, pat.data(), pat.size())) != nullptr) {
                    hits++;
                    size_t off = p - b, lo = off > 400 ? off - 400 : 0, hi = off + 400 < got ? off + 400 : got;
                    std::string ctx; std::string run;
                    for (size_t i = lo; i < hi; i++) {
                        char c = b[i];
                        if (c >= 32 && c < 127) run += c;
                        else { if (run.size() >= 4) { if (!ctx.empty()) ctx += " | "; ctx += run; } run.clear(); }
                    }
                    if (run.size() >= 4) { if (!ctx.empty()) ctx += " | "; ctx += run; }
                    fprintf(stderr, "@%#llx (+%#zx, región %#llx tag %u share %u): %s\n", (unsigned long long)(addr + off), off, (unsigned long long)addr, info.user_tag, info.share_mode, ctx.c_str());
                    p += pat.size();
                }
            }
        }
        addr += size;
    }
    fprintf(stderr, "--- %d coincidencias\n", hits);
}

// Extract only a validated product schema string from anonymous SDK mappings.
// Do not print memory context or scan stacks/environment/other processes.
static bool save_product_schema(const std::string& path) {
    mach_vm_address_t addr = 0; mach_vm_size_t size = 0; natural_t depth = 0;
    const char* marker = "table Product";
    while (true) {
        vm_region_submap_info_data_64_t info{}; mach_msg_type_number_t cnt = VM_REGION_SUBMAP_INFO_COUNT_64;
        if (mach_vm_region_recurse(mach_task_self(), &addr, &size, &depth, (vm_region_recurse_info_t)&info, &cnt) != KERN_SUCCESS) break;
        if (info.is_submap) { depth++; continue; }
        if (info.user_tag == 0 && (info.protection & VM_PROT_READ) && size <= (32ull << 20)) {
            std::vector<char> bytes(size); mach_vm_size_t got = 0;
            if (mach_vm_read_overwrite(mach_task_self(), addr, size, (mach_vm_address_t)bytes.data(), &got) == KERN_SUCCESS) {
                const char* begin = bytes.data(); const char* end = begin + got;
                const char* p = (const char*)memmem(begin, got, marker, strlen(marker));
                if (p) {
                    const char* lo = p; const char* hi = p;
                    while (lo > begin && lo[-1] != 0) --lo;
                    while (hi < end && *hi != 0) ++hi;
                    size_t n = hi - lo;
                    if (n < 262144 && memmem(lo, n, "root_type", 9) && memmem(lo, n, "CSCParams", 9)) {
                        fprintf(stderr, "product schema: %zu bytes\n", n);
                        return write_file(path, lo, n);
                    }
                }
            }
        }
        addr += size;
    }
    fprintf(stderr, "product schema not found\n"); return false;
}

static bool save_support_packs(const std::string& prefix) {
    mach_vm_address_t addr = 0; mach_vm_size_t size = 0; natural_t depth = 0; unsigned found = 0;
    while (true) {
        vm_region_submap_info_data_64_t info{}; mach_msg_type_number_t cnt = VM_REGION_SUBMAP_INFO_COUNT_64;
        if (mach_vm_region_recurse(mach_task_self(), &addr, &size, &depth, (vm_region_recurse_info_t)&info, &cnt) != KERN_SUCCESS) break;
        if (info.is_submap) { depth++; continue; }
        // Restrict to SDK-style anonymous read-only mappings; no malloc heaps,
        // stacks, argv or environment. Validate FlatBuffer root before saving.
        if (info.user_tag == 0 && (info.protection & VM_PROT_READ) && size <= (16ull << 20)) {
            std::vector<uint8_t> bytes(size); mach_vm_size_t got = 0;
            if (mach_vm_read_overwrite(mach_task_self(), addr, size, (mach_vm_address_t)bytes.data(), &got) == KERN_SUCCESS) {
                for (size_t pos = 4; pos + 8 < got; ++pos) {
                    if (memcmp(bytes.data() + pos, "A006", 4)) continue;
                    size_t start = pos - 4; uint32_t root = 0; memcpy(&root, bytes.data() + start, 4);
                    if (root < 8 || root > got - start - 4) continue;
                    size_t obj = start + root; int32_t delta = 0; memcpy(&delta, bytes.data() + obj, 4);
                    int64_t table = (int64_t)obj - delta;
                    if (table < (int64_t)start || table + 4 >= (int64_t)got) continue;
                    uint16_t n = 0, objectSize = 0; memcpy(&n, bytes.data() + table, 2); memcpy(&objectSize, bytes.data() + table + 2, 2);
                    if (n < 4 || n > 128 || objectSize < 4 || objectSize > 256 || obj + objectSize > got) continue;
                    std::string path = prefix + std::to_string(found++) + ".bin";
                    if (!write_file(path, bytes.data() + start, got - start)) return false;
                    fprintf(stderr, "support pack candidate: %zu bytes, %s\n", (size_t)got - start, path.c_str());
                    if (found >= 8) return true;
                }
            }
        }
        addr += size;
    }
    fprintf(stderr, "support packs: %u\n", found); return found != 0;
}

template <class T> static T* query(IUnknown* u, const CFUUIDBytes& iid, const char* name) {
    void* p = nullptr; HRESULT h = u->QueryInterface(iid, &p);
    if (h < 0 || !p) { fprintf(stderr, "QueryInterface %s error %#x\n", name, h); return nullptr; }
    return (T*)p;
}

static bool wait_done(ICodec* codec, Callback& cb) {
    cb.done = false; codec->FlushJobs();
    if (!cb.done) { fprintf(stderr, "el trabajo no terminó\n"); return false; }
    return cb.status >= 0;
}

int main(int argc, char** argv) {
    Options o;
    std::vector<std::string> pos;
    for (int i = 1; i < argc; i++) {
        std::string a = argv[i];
        auto next = [&]() -> std::string { return i + 1 < argc ? argv[++i] : ""; };
        if (a == "--gamma") o.gamma = next();
        else if (a == "--kelvin") o.kelvin = next();
        else if (a == "--tint") o.tint = next();
        else if (a == "--iso") o.iso = next();
        else if (a == "--exposure") o.exposure = next();
        else if (a == "--frame-info") o.frameInfo = true;
        else if (a == "--metadata-keys") o.metadataKeys = true;
        else if (a == "--frame-metadata-keys") o.frameMetadataKeys = true;
        else if (a == "--audit-attributes") o.auditAttributes = true;
        else if (a == "--audit-dispatch") o.auditDispatch = true;
        else if (a == "--clip-number") { std::string key=next(), value=next(); o.clipNumbers.emplace_back(key,value); }
        else if (a == "--clip-meta") o.metadataQueries.push_back(next());
        else if (a == "--framework") o.framework = next();
        else if (a == "--schema-out") o.schemaOut = next();
        else if (a == "--support-out") o.supportOut = next();
        else if (a == "--gamut") o.gamut = next();
        else if (a == "--csgen") o.csgen = next();
        else if (a == "--out") o.out = next();
        else if (a == "--bitstream-out") o.bsOut = next();
        else if (a == "--bitstream-in") o.bsIn = next();
        else if (a == "--decoded-out") o.decOut = next();
        else if (a == "--decoded-in") o.decIn = next();
        else if (a == "--state-out") o.stateOut = next();
        else if (a == "--trim") o.trim = next();
        else if (a == "--embed-lut") o.embedLut = next();
        else if (a == "--lut-size") o.lutSize = (uint16_t)strtoul(next().c_str(), nullptr, 10);
        else if (a == "--lut-name") o.lutName = next();
        else if (a == "--lut-title") o.lutTitle = next();
        else if (a == "--lutm") o.lutm = next();
        else if (a == "--save-sidecar") o.saveSidecar = true;
        else if (a == "--count") o.count = strtoull(next().c_str(), nullptr, 10);
        else if (a == "--planar") o.planar = true;
        else if (a == "--list") o.list = true;
        else if (a == "--tonecurve") o.toneCurve = next();
        else if (a == "--memscan") o.memscan = next();
        else if (a == "--eval") o.evalN = (uint32_t)strtoul(next().c_str(), nullptr, 10);
        else if (a == "--scale") { std::string s = next(); o.scale = s == "half" ? scaleHalf : s == "quarter" ? scaleQuarter : s == "eighth" ? scaleEighth : scaleFull; }
        else pos.push_back(a);
    }
    if (o.toneCurve.empty() && (pos.empty() || (!o.list && pos.size() < 2))) {
        fprintf(stderr, "uso: brawdec CLIP.braw FRAME [--out X.f32] [...] | brawdec CLIP.braw --list | brawdec --tonecurve CAMERA [--gamma G] [--csgen N] [--eval N]\n"); return 2;
    }
    if (!pos.empty()) o.clip = pos[0];
    if (!o.list && pos.size() >= 2) o.frame = strtoull(pos[1].c_str(), nullptr, 10);

    void* lib = dlopen(o.framework.c_str(), RTLD_NOW);
    if (!lib) { fprintf(stderr, "dlopen: %s\n", dlerror()); return 1; }
    clear_variant = (void (*)(Variant*))dlsym(lib,"VariantClear");
    safe_array_create = (SafeArray* (*)(uint16_t, uint32_t, SafeArrayBound*))dlsym(lib, "SafeArrayCreate");
    safe_array_access = (HRESULT (*)(SafeArray*, void**))dlsym(lib, "SafeArrayAccessData");
    safe_array_unaccess = (HRESULT (*)(SafeArray*))dlsym(lib, "SafeArrayUnaccessData");
    safe_array_destroy = (HRESULT (*)(SafeArray*))dlsym(lib, "SafeArrayDestroy");
    auto create = (IFactory * (*)()) dlsym(lib, "CreateBlackmagicRawFactoryInstance");
    if (!create) { fprintf(stderr, "sin CreateBlackmagicRawFactoryInstance\n"); return 1; }
    IFactory* factory = create();
    if (!factory) { fprintf(stderr, "factory nulo\n"); return 1; }
    ICodec* codec = nullptr;
    if (factory->CreateCodec(&codec) < 0 || !codec) { fprintf(stderr, "CreateCodec falló\n"); return 1; }
    Callback cb; cb.opt = &o;
    codec->SetCallback(&cb);

    if (!o.toneCurve.empty()) {
        // Sin clip: pregunta por la curva de tono de una cámara (sirve para saber qué nombres conoce el SDK).
        IToneCurve* tc = query<IToneCurve>(codec, IID_ToneCurve, "IBlackmagicRawToneCurve");
        if (!tc) return 1;
        uint16_t gen = o.csgen.empty() ? 5 : (uint16_t)strtoul(o.csgen.c_str(), nullptr, 10);
        std::string gamma = o.gamma.empty() ? "Blackmagic Design Film" : o.gamma;
        float c = 0, s = 0, m = 0, hi = 0, sh = 0, bl = 0, wl = 0; uint16_t vbl = 0;
        HRESULT r = tc->GetToneCurve(cfstr(o.toneCurve), cfstr(gamma), gen, &c, &s, &m, &hi, &sh, &bl, &wl, &vbl);
        if (r < 0) { printf("%s\tgen %u\t%s\tERROR %#x\n", o.toneCurve.c_str(), gen, gamma.c_str(), r); return 3; }
        printf("%s\tgen %u\t%s\tcontrast %g saturation %g midpoint %g highlights %g shadows %g black %g white %g videoBlack %u\n",
               o.toneCurve.c_str(), gen, gamma.c_str(), c, s, m, hi, sh, bl, wl, vbl);
        if (o.evalN) {
            std::vector<float> arr(o.evalN, 0.f);
            r = tc->EvaluateToneCurve(cfstr(o.toneCurve), gen, c, s, m, hi, sh, bl, wl, vbl, arr.data(), o.evalN);
            if (r < 0) { printf("EvaluateToneCurve ERROR %#x\n", r); return 3; }
            for (uint32_t i = 0; i < o.evalN; i++) printf("%u\t%.7g\n", i, arr[i]);
        }
        return 0;
    }
    IClip* clip = nullptr;
    HRESULT h = codec->OpenClip(cfstr(o.clip), &clip);
    if (h < 0 || !clip) { fprintf(stderr, "OpenClip %s error %#x\n", o.clip.c_str(), h); return 1; }
    uint32_t w = 0, hh = 0; uint64_t n = 0; CFStringRef cam = nullptr;
    clip->GetWidth(&w); clip->GetHeight(&hh); clip->GetFrameCount(&n); clip->GetCameraType(&cam);
    fprintf(stderr, "%s: %ux%u, %llu frames, camera_type %s\n", o.clip.c_str(), w, hh, (unsigned long long)n, cstr(cam).c_str());
    if (o.metadataKeys) {
        IMetadataIterator* iterator = nullptr;
        if (clip->GetMetadataIterator((void**)&iterator) >= 0 && iterator) {
            do {
                CFStringRef key = nullptr;
                if (iterator->GetKey(&key) < 0 || !key) break;
                printf("metadata_key %s\n", cstr(key).c_str());
                CFRelease(key);
            } while (iterator->Next() == 0);
            iterator->Release();
        }
    }
    for (const auto& name : o.metadataQueries) {
        Variant value{}; CFStringRef key = cfstr(name);
        HRESULT result = clip->GetMetadata(key, &value); CFRelease(key);
        printf("metadata_query %s status %#x type %u\n", name.c_str(), result, value.vt);
        if (result >= 0 && value.vt == vtString && value.str) CFRelease(value.str);
    }
    IClipAttrs* attrs = nullptr;
    if (clip->CloneClipProcessingAttributes(&attrs) < 0 || !attrs) { fprintf(stderr, "CloneClipProcessingAttributes falló\n"); return 1; }
    if (!o.embedLut.empty()) {
        if (o.embedLut != "identity") { fprintf(stderr, "--embed-lut solo admite identity en este harness\n"); return 1; }
        if (!set_embedded_identity(attrs, o)) return 1;
        fprintf(stderr, "--- LUT tras SetEmbedded (misma sesión) ---\n");
        report_lut_attrs(attrs);
    }
    if (o.saveSidecar) {
        HRESULT sh = clip->SaveSidecarFile();
        fprintf(stderr, "SaveSidecarFile %#x\n", sh);
        if (sh < 0) return 1;
    }
    if(o.auditDispatch) audit_attribute_dispatch("clip",attrs);
    if (!o.schemaOut.empty() && !save_product_schema(o.schemaOut)) return 1;
    if (!o.supportOut.empty() && !save_support_packs(o.supportOut)) return 1;
    if (o.list) {
        for (const auto& entry:o.clipNumbers) if(!set_clip_number(attrs,entry.first,entry.second)) return 1;
        if (o.auditAttributes) audit_clip_attributes(attrs);
        list_attr(attrs, "color_science_gen", attrColorScienceGen);
        list_attr(attrs, "gamut", attrGamut);
        list_attr(attrs, "gamma", attrGamma);
        return 0;
    }
    if (!o.csgen.empty()) { Variant v{}; v.vt = vtU16; v.u16 = (uint16_t)atoi(o.csgen.c_str()); if (attrs->SetClipAttribute(attrColorScienceGen, &v) < 0) { fprintf(stderr, "csgen no aceptado\n"); return 1; } }
    if (!o.gamut.empty() && !set_string_attr(attrs, attrGamut, o.gamut)) return 1;
    if (!o.gamma.empty() && !set_string_attr(attrs, attrGamma, o.gamma)) return 1;
    for (const auto& entry:o.clipNumbers) if(!set_clip_number(attrs,entry.first,entry.second)) return 1;
    if (o.auditAttributes) audit_clip_attributes(attrs);
    if (o.frameInfo) {
        // Tras los Set*: lo que el SDK tiene realmente aplicado (no se asume el valor pedido).
        print_effective_clip(attrs, attrColorScienceGen, "csgn");
        print_effective_clip(attrs, attrGamut, "gamut");
        print_effective_clip(attrs, attrGamma, "gamma");
        print_effective_clip(attrs, FOURCC('h', 'l', 'r', 'y'), "hlry");
        fprintf(stderr, "effective scale %s\n", scale_name(o.scale));
    }
    cb.attrs = attrs;
    codec->PreparePipeline(pipelineCPU, nullptr, nullptr, nullptr);
    codec->FlushJobs();

    IClipEx* clipEx = query<IClipEx>(clip, IID_ClipEx6, "IBlackmagicRawClipEx");
    if (!clipEx) return 1;
    {
        uint32_t bsz = 0; uint64_t boff = 0; uint32_t fidx = 0, bsn = 0;
        clipEx->GetFrameImageOffsetAndSize(o.frame, &bsz, &boff, &fidx);
        clipEx->GetBitStreamSizeBytes(o.frame, &bsn);
        fprintf(stderr, "frame %llu: imagen en el fichero %u bytes @ %llu (fichero %u); bitstream %u bytes\n",
                (unsigned long long)o.frame, bsz, (unsigned long long)boff, fidx, bsn);
    }

    if (!o.trim.empty()) {
        IJob* job = nullptr;
        IClipAttrs* trimAttrs = o.embedLut.empty() ? nullptr : attrs;
        h = clipEx->CreateJobTrim(cfstr(o.trim), o.frame, o.count, 1, 0.0f, trimAttrs, nullptr, &job);
        if (h < 0 || !job) { fprintf(stderr, "CreateJobTrim error %#x\n", h); return 1; }
        job->Submit();
        if (!wait_done(codec, cb)) return 1;
        fprintf(stderr, "trim escrito: %s (%llu frames desde %llu)\n", o.trim.c_str(), (unsigned long long)o.count, (unsigned long long)o.frame);
        return 0;
    }

    if (!o.memscan.empty()) memscan(o.memscan, "tras OpenClip + PreparePipeline");
    if (!o.manual() && o.bsOut.empty()) {
        IJob* job = nullptr;
        h = clip->CreateJobReadFrame(o.frame, &job);
        if (h < 0 || !job) { fprintf(stderr, "CreateJobReadFrame error %#x\n", h); return 1; }
        job->Submit();
        bool ok = wait_done(codec, cb);
        if (!o.memscan.empty()) memscan(o.memscan, "tras decodificar y procesar un frame");
        return ok ? 0 : 1;
    }

    // Flujo manual (IBlackmagicRawManualDecoderFlow1): bitstream en nuestro buffer → decoded → processed.
    uint32_t bsn = 0; clipEx->GetBitStreamSizeBytes(o.frame, &bsn);
    std::vector<uint8_t> bitstream(bsn);
    IJob* job = nullptr;
    h = clipEx->CreateJobReadFrame(o.frame, bitstream.data(), bsn, &job);
    if (h < 0 || !job) { fprintf(stderr, "ClipEx::CreateJobReadFrame error %#x\n", h); return 1; }
    job->Submit();
    if (!wait_done(codec, cb) || !cb.frame) return 1;
    if (!o.bsOut.empty()) { write_file(o.bsOut, bitstream.data(), bsn); fprintf(stderr, "bitstream volcado: %s (%u bytes)\n", o.bsOut.c_str(), bsn); }
    if (!o.manual()) return 0;

    IFlow1* flow = query<IFlow1>(codec, IID_ManualDecoderFlow1, "IBlackmagicRawManualDecoderFlow1");
    if (!flow) return 1;
    uint32_t stateSize = 0; flow->GetFrameStateSizeBytes(&stateSize);
    std::vector<uint8_t> state(stateSize);
    h = flow->PopulateFrameStateBuffer(cb.frame, attrs, cb.frameAttrs, state.data(), stateSize);
    if (h < 0) { fprintf(stderr, "PopulateFrameStateBuffer error %#x\n", h); return 1; }
    uint32_t decSize = 0, procSize = 0, lutSize = 0;
    flow->GetDecodedSizeBytes(state.data(), &decSize); flow->GetProcessedSizeBytes(state.data(), &procSize); flow->GetPost3DLUTSizeBytes(state.data(), &lutSize);
    fprintf(stderr, "flujo manual: frame state %u bytes, decoded %u bytes, processed %u bytes, lut %u bytes\n", stateSize, decSize, procSize, lutSize);
    if (!o.stateOut.empty()) write_file(o.stateOut, state.data(), stateSize);
    if (!o.bsIn.empty()) {
        std::vector<uint8_t> in = read_file(o.bsIn);
        if (in.empty()) return 1;
        fprintf(stderr, "bitstream sustituido por %s (%zu bytes; original %u)\n", o.bsIn.c_str(), in.size(), bsn);
        bitstream.assign(in.begin(), in.end());
        if (bitstream.size() < bsn) bitstream.resize(bsn);
    }
    std::vector<uint8_t> decoded(decSize), processed(procSize), lut(lutSize);
    if (!o.decIn.empty()) {
        // Salta el decodificador: el buffer "decoded" (Y u16 + Cb/Cr int16) lo damos nosotros y solo se procesa.
        std::vector<uint8_t> in = read_file(o.decIn);
        if (in.empty()) return 1;
        if (in.size() > decSize) { fprintf(stderr, "decoded-in demasiado grande (%zu > %u)\n", in.size(), decSize); return 1; }
        memcpy(decoded.data(), in.data(), in.size());
        fprintf(stderr, "decoded sustituido por %s (%zu bytes)\n", o.decIn.c_str(), in.size());
    } else {
        h = flow->CreateJobDecode(state.data(), bitstream.data(), decoded.data(), &job);
        if (h < 0 || !job) { fprintf(stderr, "CreateJobDecode error %#x\n", h); return 1; }
        job->Submit();
        if (!wait_done(codec, cb)) return 1;
    }
    if (!o.decOut.empty()) { write_file(o.decOut, decoded.data(), decSize); fprintf(stderr, "decoded volcado: %s\n", o.decOut.c_str()); }
    if (!o.out.empty()) {
        h = flow->CreateJobProcess(state.data(), decoded.data(), processed.data(), lutSize ? lut.data() : nullptr, &job);
        if (h < 0 || !job) { fprintf(stderr, "CreateJobProcess error %#x\n", h); return 1; }
        job->Submit();
        if (!wait_done(codec, cb)) return 1;
    }
    return 0;
}
