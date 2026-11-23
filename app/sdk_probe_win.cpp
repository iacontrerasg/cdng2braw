#include "sdk_probe.h"
#include <QDir>
#include <mutex>
#include <windows.h>
#include <objbase.h>

namespace cdng {
namespace {
std::mutex seenMutex;
QStringList *seenListed = nullptr, *seenRead = nullptr;

void record(QStringList *const *list, LPCWSTR name) {
    if (!name) return;
    const auto path = QDir::fromNativeSeparators(QString::fromWCharArray(name));
    if (!path.contains("Camera Support", Qt::CaseInsensitive)) return;
    std::lock_guard<std::mutex> lock(seenMutex);
    if (*list && !(*list)->contains(path)) (*list)->append(path);
}
HANDLE WINAPI watchCreateFileW(LPCWSTR name, DWORD access, DWORD share, LPSECURITY_ATTRIBUTES security, DWORD creation,
                               DWORD flags, HANDLE templ) {
    const HANDLE handle = CreateFileW(name, access, share, security, creation, flags, templ);
    if (handle != INVALID_HANDLE_VALUE) record(&seenRead, name);
    return handle;
}
HANDLE WINAPI watchFindFirstFileExW(LPCWSTR name, FINDEX_INFO_LEVELS level, LPVOID data, FINDEX_SEARCH_OPS op,
                                    LPVOID filter, DWORD flags) {
    const HANDLE handle = FindFirstFileExW(name, level, data, op, filter, flags);
    if (handle != INVALID_HANDLE_VALUE) record(&seenListed, name);
    return handle;
}
// Sustituye en la tabla de importaciones de la DLL (en memoria, en este proceso) las dos funciones de KERNEL32.
int watchImports(HMODULE module) {
    auto *base = reinterpret_cast<BYTE *>(module);
    const auto *nt = reinterpret_cast<IMAGE_NT_HEADERS *>(base + reinterpret_cast<IMAGE_DOS_HEADER *>(base)->e_lfanew);
    const auto directory = nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT];
    if (!directory.VirtualAddress) return 0;
    int patched = 0;
    for (auto *entry = reinterpret_cast<IMAGE_IMPORT_DESCRIPTOR *>(base + directory.VirtualAddress); entry->Name; ++entry) {
        if (_stricmp(reinterpret_cast<const char *>(base + entry->Name), "KERNEL32.dll") || !entry->OriginalFirstThunk) continue;
        auto *names = reinterpret_cast<IMAGE_THUNK_DATA *>(base + entry->OriginalFirstThunk);
        auto *thunks = reinterpret_cast<IMAGE_THUNK_DATA *>(base + entry->FirstThunk);
        for (; names->u1.AddressOfData; ++names, ++thunks) {
            if (IMAGE_SNAP_BY_ORDINAL(names->u1.Ordinal)) continue;
            const char *name = reinterpret_cast<IMAGE_IMPORT_BY_NAME *>(base + names->u1.AddressOfData)->Name;
            ULONG_PTR target = 0;
            if (!strcmp(name, "CreateFileW")) target = reinterpret_cast<ULONG_PTR>(&watchCreateFileW);
            else if (!strcmp(name, "FindFirstFileExW")) target = reinterpret_cast<ULONG_PTR>(&watchFindFirstFileExW);
            if (!target) continue;
            DWORD old = 0;
            if (!VirtualProtect(&thunks->u1.Function, sizeof(ULONG_PTR), PAGE_READWRITE, &old)) continue;
            thunks->u1.Function = target;
            VirtualProtect(&thunks->u1.Function, sizeof(ULONG_PTR), old, &old);
            ++patched;
        }
    }
    return patched;
}
}

SdkProbe probeCameraSupport(const QString &dll) {
    SdkProbe result;
    result.dll = dll;
    {
        std::lock_guard<std::mutex> lock(seenMutex);
        seenListed = &result.listed; seenRead = &result.read;
    }
    struct Stop { ~Stop() { std::lock_guard<std::mutex> lock(seenMutex); seenListed = seenRead = nullptr; } } stop;
    const auto native = QDir::toNativeSeparators(dll).toStdWString();
    HMODULE module = LoadLibraryExW(native.c_str(), nullptr, LOAD_WITH_ALTERED_SEARCH_PATH);
    if (!module) { result.error = QString("LoadLibrary %1").arg(GetLastError()); return result; }
    if (watchImports(module) != 2) { result.error = "unexpected imports"; return result; }
    using Create = IUnknown *(*)();
    const auto create = reinterpret_cast<Create>(GetProcAddress(module, "CreateBlackmagicRawFactoryInstance"));
    if (!create) { result.error = "no factory export"; return result; }
    CoInitializeEx(nullptr, COINIT_MULTITHREADED);
    IUnknown *factory = create();
    if (!factory) { result.error = "factory failed"; return result; }
    // IBlackmagicRawFactory : IUnknown { CreateCodec(IBlackmagicRaw **) … }: cuarta entrada de la vtable.
    using CreateCodec = HRESULT(STDMETHODCALLTYPE *)(IUnknown *, IUnknown **);
    IUnknown *codec = nullptr;
    const HRESULT hr = reinterpret_cast<CreateCodec>((*reinterpret_cast<void ***>(factory))[3])(factory, &codec);
    result.codec = SUCCEEDED(hr) && codec;
    if (codec) codec->Release();
    factory->Release();
    // Sin FreeLibrary: el proceso termina enseguida y la DLL puede dejar hilos vivos.
    return result;
}
}
