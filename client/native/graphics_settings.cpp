// Host settings only. No game data or default filesystem locations.
#include "graphics_settings.h"
#include "graphics_options.h"
#include <atomic>
#include <cerrno>
#include <exception>
#include <string_view>
#ifdef _WIN32
#define NOMINMAX
#include <windows.h>
#else
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>
#endif

namespace folklore {
namespace {
bool fail(std::string& error, const char* message) { error = message; return false; }
std::string_view trim(std::string_view text)
{
    while (!text.empty() && (text.front() == ' ' || text.front() == '\t')) text.remove_prefix(1);
    while (!text.empty() && (text.back() == ' ' || text.back() == '\t')) text.remove_suffix(1);
    return text;
}
bool decimal(std::string_view text, uint32_t limit, uint32_t& value)
{
    if (text.empty()) return false;
    uint32_t result = 0;
    for (const unsigned char c : text) {
        if (c < '0' || c > '9') return false;
        const uint32_t digit = c - '0';
        if (digit > limit || result > (limit - digit) / 10) return false;
        result = result * 10 + digit;
    }
    if (!result) return false;
    value = result;
    return true;
}
bool parse(std::string_view text, GraphicsSettings& output, std::string& error)
{
    if (text.size() > 4096 || text.find('\0') != text.npos) return fail(error, "settings contain NUL or exceed 4096 bytes");
    GraphicsSettings candidate;
    unsigned seen = 0;
    uint32_t version = 0;
    while (!text.empty()) {
        const auto end = text.find('\n');
        auto line = text.substr(0, end);
        text = end == text.npos ? std::string_view{} : text.substr(end + 1);
        if (!line.empty() && line.back() == '\r') line.remove_suffix(1);
        line = trim(line);
        if (line.empty() || line.front() == '#') continue;
        const auto equals = line.find('=');
        if (equals == line.npos) return fail(error, "expected key=value in settings");
        const auto key = trim(line.substr(0, equals));
        const auto value = trim(line.substr(equals + 1));
        unsigned bit;
        uint32_t number;
        if (key == "version") bit = 1;
        else if (key == "output_width") bit = 2;
        else if (key == "output_height") bit = 4;
        else if (key == "render_scale") bit = 8;
        else if (key == "anisotropy") bit = 16;
        else if (key == "anti_aliasing") bit = 32;
        else if (key == "upscaler") bit = 64;
        else if (key == "ambient_occlusion") bit = 128;
        else if (key == "hud_scale") bit = 256;
        else if (key == "camera_aspect") bit = 512;
        else if (key == "path_tracing") bit = 1024;
        else if (key == "ray_denoiser") bit = 2048;
        else if (key == "path_strength") bit = 4096;
        else if (key == "dlss") bit = 8192;
        else if (key == "sharpen") bit = 16384;
        else return fail(error, "unknown settings key");
        if (seen & bit) return fail(error, "duplicate settings key");
        const uint32_t limit = bit == 1 ? 10 : bit == 8 ? 4 : 8192;
        if (bit == 8192) {
            const std::string token(value);
            if (!graphics_dlss(token.c_str(), number))
                return fail(error, "invalid DLSS mode");
        } else if (bit == 16384) {
            const std::string token(value);
            if (!graphics_sharpen(token.c_str(), number))
                return fail(error, "sharpen must be an integer from 0 to 100");
        } else if (bit == 512) {
            const std::string token(value);
            if (!graphics_camera_aspect(token.c_str(), number))
                return fail(error, "camera aspect must be original or output");
        } else if (bit == 256) {
            const std::string token(value);
            if (!graphics_hud_scale(token.c_str(), number))
                return fail(error, "hud scale must be an integer from 50 to 100");
        } else if (bit == 1024) {
            const std::string token(value);
            if (!graphics_path_tracing(token.c_str(), number))
                return fail(error, "path tracing must be off, diffuse-preview or hybrid");
        } else if (bit == 2048) {
            const std::string token(value);
            if (!graphics_ray_denoiser(token.c_str(), number))
                return fail(error, "ray denoiser must be off or spatial");
        } else if (bit == 4096) {
            const std::string token(value);
            if (!graphics_path_strength(token.c_str(), number))
                return fail(error, "path strength must be 20, 35, 60 or 100");
        } else if (bit == 16 || bit == 32 || bit == 64 || bit == 128) {
            const std::string token(value);
            if (bit == 16 ? !graphics_anisotropy(token.c_str(), number) :
                bit == 32 ? !graphics_antialiasing(token.c_str(), number) :
                bit == 64 ? !graphics_upscaler(token.c_str(), number) : !graphics_ambient_occlusion(token.c_str(), number))
                return fail(error, "invalid filtering setting");
        } else if (!decimal(value, limit, number)) return fail(error, "unsupported version or invalid numeric settings value");
        seen |= bit;
        if (bit == 1) version = number;
        if (bit == 16) candidate.anisotropy = number;
        if (bit == 32) candidate.antialiasing = number;
        if (bit == 64) candidate.upscaler = number;
        if (bit == 128) candidate.ambient_occlusion = number;
        if (bit == 256) candidate.hud_scale = number;
        if (bit == 512) candidate.camera_aspect = number;
        if (bit == 1024) candidate.path_tracing = number;
        if (bit == 2048) candidate.ray_denoiser = number;
        if (bit == 4096) candidate.path_strength = number;
        if (bit == 8192) candidate.dlss = number;
        if (bit == 16384) candidate.sharpen = number;
        if (bit == 2) candidate.width = number;
        if (bit == 4) candidate.height = number;
        if (bit == 8) candidate.scale = number;
    }
    if ((version == 1 && seen != 15) || (version == 2 && seen != 31) || (version == 3 && seen != 63) || (version == 4 && seen != 127) || (version == 5 && seen != 255) || (version == 6 && seen != 511) || (version == 7 && seen != 1023) || (version == 8 && seen != 4095) || (version == 9 && seen != 8191) || (version == 10 && seen != 32767) || !version)
        return fail(error, "settings keys do not match version");
    if (!graphics_settings_valid(candidate, error)) return false;
    output = candidate;
    error.clear();
    return true;
}

#ifdef _WIN32
using NativeFile = HANDLE;
const NativeFile invalid_file = INVALID_HANDLE_VALUE;
bool close_file(NativeFile file) { return CloseHandle(file) != 0; }
#else
using NativeFile = int;
constexpr NativeFile invalid_file = -1;
bool close_file(NativeFile file) { return close(file) == 0; }
#endif
struct File {
    NativeFile value = invalid_file;
    ~File() { if (value != invalid_file) close_file(value); }
    bool finish() { const auto file = value; value = invalid_file; return close_file(file); }
};
struct Temporary {
    std::filesystem::path path;
    bool created = false;
    ~Temporary() { if (created) { std::error_code ignored; std::filesystem::remove(path, ignored); } }
};
std::atomic<uint64_t> sequence{0};
} // namespace

bool graphics_settings_valid(const GraphicsSettings& settings, std::string& error)
{
    if (!settings.width || settings.width > 8192 || !settings.height || settings.height > 8192 ||
        !settings.scale || settings.scale > 4) return fail(error, "output dimensions must be 1..8192 and scale 1..4");
    if (!graphics_anisotropy_text(settings.anisotropy)) return fail(error, "anisotropy must be auto, 1, 2, 4, 8 or 16");
    if (!graphics_upscaler_text(settings.upscaler)) return fail(error, "upscaler must be off or fsr1");
    if (!graphics_antialiasing_text(settings.antialiasing)) return fail(error, "anti-aliasing must be off or fxaa");
    if (!graphics_ambient_occlusion_text(settings.ambient_occlusion)) return fail(error, "ambient occlusion must be off, cacao or rayquery");
    if (!graphics_path_tracing_text(settings.path_tracing)) return fail(error, "path tracing must be off, diffuse-preview or hybrid");
    if (!graphics_ray_denoiser_text(settings.ray_denoiser)) return fail(error, "ray denoiser must be off or spatial");
    if (!graphics_path_strength_text(settings.path_strength)) return fail(error, "path strength must be 20, 35, 60 or 100");
    if (!graphics_dlss_text(settings.dlss)) return fail(error, "invalid DLSS mode");
    if (settings.sharpen > 100) return fail(error, "sharpen must be 0..100");
    if (settings.dlss && settings.ambient_occlusion) return fail(error, "DLSS requires ambient occlusion off");
    if (settings.path_tracing && settings.ambient_occlusion) return fail(error, "diffuse path tracing preview requires ambient occlusion off");
    if (settings.hud_scale < 50 || settings.hud_scale > 100) return fail(error, "hud scale must be 50..100");
    if (!graphics_camera_aspect_text(settings.camera_aspect)) return fail(error, "camera aspect must be original or output");
    error.clear();
    return true;
}

SettingsLoadResult load_graphics_settings(const std::filesystem::path& path, GraphicsSettings& output, std::string& error)
{
    try {
        File file;
#ifdef _WIN32
        file.value = CreateFileW(path.c_str(), GENERIC_READ, FILE_SHARE_READ, nullptr, OPEN_EXISTING,
                                 FILE_FLAG_OPEN_REPARSE_POINT, nullptr);
        if (file.value == invalid_file) {
            const auto code = GetLastError();
            if (code == ERROR_FILE_NOT_FOUND || code == ERROR_PATH_NOT_FOUND) { error.clear(); return SettingsLoadResult::missing; }
            error = "cannot open graphics settings"; return SettingsLoadResult::error;
        }
        BY_HANDLE_FILE_INFORMATION info{};
        if (!GetFileInformationByHandle(file.value, &info) || GetFileType(file.value) != FILE_TYPE_DISK ||
            (info.dwFileAttributes & (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT)) ||
            info.nFileSizeHigh || info.nFileSizeLow > 4096) {
            error = "settings must be a regular file of at most 4096 bytes"; return SettingsLoadResult::error;
        }
#else
        file.value = open(path.c_str(), O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC);
        if (file.value == invalid_file) {
            if (errno == ENOENT) { error.clear(); return SettingsLoadResult::missing; }
            error = "cannot open graphics settings"; return SettingsLoadResult::error;
        }
        struct stat info{};
        if (fstat(file.value, &info) || !S_ISREG(info.st_mode) || info.st_size < 0 || info.st_size > 4096) {
            error = "settings must be a regular file of at most 4096 bytes"; return SettingsLoadResult::error;
        }
#endif
        char data[4097];
        size_t count = 0;
        while (count < sizeof(data)) {
#ifdef _WIN32
            DWORD got = 0;
            if (!ReadFile(file.value, data + count, static_cast<DWORD>(sizeof(data) - count), &got, nullptr)) {
                error = "settings read failed"; return SettingsLoadResult::error;
            }
#else
            const auto got = read(file.value, data + count, sizeof(data) - count);
            if (got < 0) {
                if (errno == EINTR) continue;
                error = "settings read failed"; return SettingsLoadResult::error;
            }
#endif
            if (!got) break;
            count += static_cast<size_t>(got);
        }
        return parse(std::string_view(data, count), output, error) ? SettingsLoadResult::loaded : SettingsLoadResult::error;
    } catch (const std::exception&) { error = "settings filesystem operation failed"; return SettingsLoadResult::error; }
}

bool save_graphics_settings(const std::filesystem::path& path, const GraphicsSettings& settings, std::string& error)
{
    if (!graphics_settings_valid(settings, error)) return false;
    try {
        if (path.empty() || path.filename().empty()) return fail(error, "settings path must name a file");
#ifdef _WIN32
        const auto attributes = GetFileAttributesW(path.c_str());
        if (attributes != INVALID_FILE_ATTRIBUTES) {
            if (attributes & (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT)) return fail(error, "settings destination is a directory or reparse point");
        } else if (GetLastError() != ERROR_FILE_NOT_FOUND && GetLastError() != ERROR_PATH_NOT_FOUND) return fail(error, "cannot inspect settings destination");
        const auto process = GetCurrentProcessId();
#else
        struct stat info{};
        if (lstat(path.c_str(), &info) == 0) {
            if (!S_ISREG(info.st_mode)) return fail(error, "settings destination is not a regular file");
        } else if (errno != ENOENT) return fail(error, "cannot inspect settings destination");
        const auto process = getpid();
#endif
        Temporary temporary;
        File file; // Close before the temporary's destructor removes a failed write.
        for (unsigned attempt = 0; attempt < 32; ++attempt) {
            temporary.path = path;
            temporary.path += ".tmp-" + std::to_string(process) + "-" + std::to_string(sequence.fetch_add(1));
#ifdef _WIN32
            file.value = CreateFileW(temporary.path.c_str(), GENERIC_WRITE, 0, nullptr, CREATE_NEW, FILE_ATTRIBUTE_NORMAL, nullptr);
            if (file.value != invalid_file) break;
            if (GetLastError() != ERROR_FILE_EXISTS && GetLastError() != ERROR_ALREADY_EXISTS) return fail(error, "cannot create temporary settings file");
#else
            file.value = open(temporary.path.c_str(), O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600);
            if (file.value != invalid_file) break;
            if (errno != EEXIST) return fail(error, "cannot create temporary settings file");
#endif
        }
        if (file.value == invalid_file) return fail(error, "temporary settings names are occupied");
        temporary.created = true;
        const std::string text = "version=10\noutput_width=" + std::to_string(settings.width) +
            "\noutput_height=" + std::to_string(settings.height) + "\nrender_scale=" + std::to_string(settings.scale) +
            "\nanisotropy=" + graphics_anisotropy_text(settings.anisotropy) +
            "\nanti_aliasing=" + graphics_antialiasing_text(settings.antialiasing) +
            "\nupscaler=" + graphics_upscaler_text(settings.upscaler) +
            "\nambient_occlusion=" + graphics_ambient_occlusion_text(settings.ambient_occlusion) +
            "\nhud_scale=" + std::to_string(settings.hud_scale) +
            "\ncamera_aspect=" + graphics_camera_aspect_text(settings.camera_aspect) +
            "\npath_tracing=" + graphics_path_tracing_text(settings.path_tracing) +
            "\nray_denoiser=" + graphics_ray_denoiser_text(settings.ray_denoiser) +
            "\npath_strength=" + graphics_path_strength_text(settings.path_strength) +
            "\ndlss=" + graphics_dlss_text(settings.dlss) +
            "\nsharpen=" + std::to_string(settings.sharpen) + "\n";
        size_t written = 0;
        while (written < text.size()) {
#ifdef _WIN32
            DWORD count = 0;
            if (!WriteFile(file.value, text.data() + written, static_cast<DWORD>(text.size() - written), &count, nullptr) || !count)
                return fail(error, "settings write failed");
#else
            const auto count = write(file.value, text.data() + written, text.size() - written);
            if (count < 0 && errno == EINTR) continue;
            if (count <= 0) return fail(error, "settings write failed");
#endif
            written += static_cast<size_t>(count);
        }
#ifdef _WIN32
        if (!FlushFileBuffers(file.value)) return fail(error, "settings flush failed");
#else
        if (fsync(file.value)) return fail(error, "settings flush failed");
#endif
        if (!file.finish()) return fail(error, "settings close failed");
        // Atomic replacement protects the old file on failure. POSIX directory
        // metadata is not fsynced, so power-loss durability of the rename is not promised.
#ifdef _WIN32
        if (!MoveFileExW(temporary.path.c_str(), path.c_str(), MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH))
            return fail(error, "settings replacement failed");
#else
        if (rename(temporary.path.c_str(), path.c_str())) return fail(error, "settings replacement failed");
#endif
        temporary.created = false;
        error.clear();
        return true;
    } catch (const std::exception&) { return fail(error, "settings filesystem operation failed"); }
}
} // namespace folklore

