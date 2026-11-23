#include "common.hpp"

#include <cstdlib>

#if defined(__APPLE__)
#include <mach-o/dyld.h>
#elif defined(_WIN32)
#define NOMINMAX                // windows.h define min/max como macros y rompen std::min/std::max
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#endif

namespace cdng {

namespace {
std::filesystem::path executablePath() {
#if defined(__APPLE__)
    uint32_t size = 0;
    _NSGetExecutablePath(nullptr, &size);
    std::string path(size, '\0');
    if (_NSGetExecutablePath(path.data(), &size) == 0) return std::filesystem::weakly_canonical(path.c_str());
#elif defined(_WIN32)
    wchar_t path[MAX_PATH * 4];
    if (GetModuleFileNameW(nullptr, path, DWORD(std::size(path)))) return path;
#else
    std::error_code ignored;
    auto path = std::filesystem::read_symlink("/proc/self/exe", ignored);
    if (!ignored) return path;
#endif
    return {};
}
}  // namespace

// CDNG_RESOURCES; si no, la carpeta del ejecutable o la primera de sus superiores que contenga model/vlc-table.json
// (en el paquete, model/ va junto a cdng-convert; en desarrollo, el ejecutable está dentro de build/ del repositorio).
std::filesystem::path resourceRoot() {
    if (const char *env = std::getenv("CDNG_RESOURCES")) return env;
    for (auto dir = executablePath().parent_path(); !dir.empty(); dir = dir.parent_path()) {
        if (std::filesystem::is_regular_file(dir / "model/vlc-table.json")) return dir;
        if (dir == dir.parent_path()) break;
    }
    return std::filesystem::current_path();
}

}  // namespace cdng
