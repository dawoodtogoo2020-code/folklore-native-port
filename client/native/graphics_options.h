#pragma once
#include <cstdint>
#include <cstring>
#include <initializer_list>

namespace folklore {
// anisotropy: 0 (auto) leaves filtering guest-controlled, 1 forces it off and 2/4/8/16 request an
// override that the renderer caps to hardware limits. Parsing only records the request.
struct GraphicsOptions {
    uint32_t width = 1280, height = 720, scale = 1, anisotropy = 0, antialiasing = 0, upscaler = 0, ambient_occlusion = 0, hud_scale = 100, camera_aspect = 0, path_tracing = 0, ray_denoiser = 1, path_strength = 35;
    uint32_t dlss = 0, sharpen = 0;
    bool dlss_set = false, sharpen_set = false;
    bool output_set = false, scale_set = false, anisotropy_set = false, antialiasing_set = false, upscaler_set = false,
         ambient_occlusion_set = false, hud_scale_set = false, camera_aspect_set = false, path_tracing_set = false, ray_denoiser_set = false, path_strength_set = false;
};

// Consume ASCII decimal digits without accepting signs, whitespace or overflow.
inline bool graphics_decimal(const char*& cursor, uint32_t limit, uint32_t& result)
{
    const char* start = cursor;
    uint32_t value = 0;
    while (*cursor >= '0' && *cursor <= '9') {
        if (cursor - start >= 10) return false;
        const uint32_t digit = static_cast<uint32_t>(*cursor++ - '0');
        if (digit > limit || value > (limit - digit) / 10) return false;
        value = value * 10 + digit;
    }
    if (cursor == start || value == 0) return false;
    result = value;
    return true;
}

// Shared anisotropy codec: exact spellings only, so leading zeros, signs, whitespace, suffixes and
// overflow are rejected without numeric parsing. Failure leaves result unchanged.
inline bool graphics_anisotropy(const char* text, uint32_t& result)
{
    if (!text) return false;
    static const struct { const char* text; uint32_t level; } levels[] = {
        {"auto", 0}, {"0", 0}, {"1", 1}, {"2", 2}, {"4", 4}, {"8", 8}, {"16", 16}
    };
    for (const auto& level : levels) {
        if (std::strcmp(text, level.text) == 0) {
            result = level.level;
            return true;
        }
    }
    return false;
}
inline const char* graphics_anisotropy_text(uint32_t level)
{
    switch (level) {
    case 0: return "auto";
    case 1: return "1";
    case 2: return "2";
    case 4: return "4";
    case 8: return "8";
    case 16: return "16";
    default: return nullptr;
    }
}

inline bool graphics_antialiasing(const char* text, uint32_t& result)
{
    if (!text) return false;
    if (!std::strcmp(text, "off")) { result = 0; return true; }
    if (!std::strcmp(text, "fxaa")) { result = 1; return true; }
    return false;
}
inline const char* graphics_antialiasing_text(uint32_t mode)
{
    return mode == 0 ? "off" : mode == 1 ? "fxaa" : nullptr;
}
// Explicit version name: this is spatial FSR1, not temporal reconstruction.
inline bool graphics_upscaler(const char* text, uint32_t& result)
{
    if (!text) return false;
    if (!std::strcmp(text, "off")) { result = 0; return true; }
    if (!std::strcmp(text, "fsr1")) { result = 1; return true; }
    return false;
}
inline const char* graphics_upscaler_text(uint32_t mode)
{
    return mode == 0 ? "off" : mode == 1 ? "fsr1" : nullptr;
}
// Ambient occlusion: 0 off, 1 CACAO, 2 ray query AO.
inline bool graphics_ambient_occlusion(const char* text, uint32_t& result)
{
    if (!text) return false;
    if (!std::strcmp(text, "off")) { result = 0; return true; }
    if (!std::strcmp(text, "cacao")) { result = 1; return true; }
    if (!std::strcmp(text, "rayquery")) { result = 2; return true; }
    return false;
}
inline const char* graphics_ambient_occlusion_text(uint32_t mode)
{
    return mode == 0 ? "off" : mode == 1 ? "cacao" : mode == 2 ? "rayquery" : nullptr;
}
inline bool graphics_path_tracing(const char* text, uint32_t& result)
{
    if (!text) return false;
    if (!std::strcmp(text, "off")) { result = 0; return true; }
    if (!std::strcmp(text, "diffuse-preview")) { result = 1; return true; }
    if (!std::strcmp(text, "hybrid")) { result = 2; return true; }
    return false;
}
inline const char* graphics_path_tracing_text(uint32_t mode)
{
    return mode == 0 ? "off" : mode == 1 ? "diffuse-preview" : mode == 2 ? "hybrid" : nullptr;
}
// DLSS Super Resolution: 0 off, then NGX modes by name. The renderer asks NGX
// for each mode's input extent; dlaa keeps the input at the output size.
inline bool graphics_dlss(const char* text, uint32_t& result)
{
    if (!text) return false;
    static const char* const names[] = {"off", "quality", "balanced", "performance", "ultra-performance", "dlaa"};
    for (uint32_t i = 0; i < 6; ++i)
        if (!std::strcmp(text, names[i])) { result = i; return true; }
    return false;
}
inline const char* graphics_dlss_text(uint32_t mode)
{
    static const char* const names[] = {"off", "quality", "balanced", "performance", "ultra-performance", "dlaa"};
    return mode < 6 ? names[mode] : nullptr;
}
// RCAS sharpening strength 0..100 (0 off).
inline bool graphics_sharpen(const char* text, uint32_t& result)
{
    if (!text) return false;
    if (!std::strcmp(text, "0")) { result = 0; return true; }
    if (text[0] == '0') return false;
    const char* cursor = text;
    uint32_t value;
    if (!graphics_decimal(cursor, 100, value) || *cursor) return false;
    result = value;
    return true;
}
inline bool graphics_ray_denoiser(const char* text, uint32_t& result)
{
    if (!text) return false;
    if (!std::strcmp(text, "off")) { result = 0; return true; }
    if (!std::strcmp(text, "spatial")) { result = 1; return true; }
    return false;
}
inline const char* graphics_ray_denoiser_text(uint32_t mode)
{
    return mode == 0 ? "off" : mode == 1 ? "spatial" : nullptr;
}
inline bool graphics_path_strength(const char* text, uint32_t& result)
{
    if (!text) return false;
    for (uint32_t value : {20u, 35u, 60u, 100u}) {
        const char* token = value == 20 ? "20" : value == 35 ? "35" : value == 60 ? "60" : "100";
        if (!std::strcmp(text, token)) { result = value; return true; }
    }
    return false;
}
inline const char* graphics_path_strength_text(uint32_t value)
{
    return value == 20 ? "20" : value == 35 ? "35" : value == 60 ? "60" : value == 100 ? "100" : nullptr;
}

inline bool graphics_hud_scale(const char* text, uint32_t& result)
{
    if (!text || text[0] == '0') return false;
    const char* cursor = text;
    uint32_t candidate;
    if (!graphics_decimal(cursor, 100, candidate) || *cursor || candidate < 50) return false;
    result = candidate;
    return true;
}

inline bool graphics_camera_aspect(const char* text, uint32_t& result)
{
    if (!text) return false;
    if (!std::strcmp(text, "original")) { result = 0; return true; }
    if (!std::strcmp(text, "output")) { result = 1; return true; }
    return false;
}
inline const char* graphics_camera_aspect_text(uint32_t mode)
{
    return mode == 0 ? "original" : mode == 1 ? "output" : nullptr;
}
// Zero means the native 16:9 presentation. Reject wider than the bridge supports.
inline bool graphics_camera_output_ratio(uint32_t width, uint32_t height, double& ratio)
{
    if (!width || !height) return false;
    const double candidate = static_cast<double>(width) / height;
    if (candidate > 32.0 / 9.0) return false;
    ratio = candidate > 16.0 / 9.0 ? candidate : 0.0;
    return true;
}
inline void graphics_camera_aspect_settings(GraphicsOptions& options, uint32_t stored_mode)
{
    if (!options.camera_aspect_set) options.camera_aspect = stored_mode;
    options.camera_aspect_set = true;
}

enum class GraphicsOptionResult { unrelated, accepted, invalid };
inline GraphicsOptionResult parse_graphics_option(const char* argument, GraphicsOptions& options)
{
    if (!argument) return GraphicsOptionResult::unrelated;
    if (std::strncmp(argument, "--render-scale=", 15) == 0) {
        const char* value = argument + 15;
        uint32_t scale;
        if (options.scale_set || !graphics_decimal(value, 4, scale) || *value)
            return GraphicsOptionResult::invalid;
        options.scale = scale;
        options.scale_set = true;
        return GraphicsOptionResult::accepted;
    }
    if (std::strncmp(argument, "--output-size=", 14) == 0) {
        const char* value = argument + 14;
        uint32_t width, height;
        if (options.output_set || !graphics_decimal(value, 8192, width) || *value++ != 'x' ||
            !graphics_decimal(value, 8192, height) || *value)
            return GraphicsOptionResult::invalid;
        options.width = width;
        options.height = height;
        options.output_set = true;
        return GraphicsOptionResult::accepted;
    }
    if (std::strncmp(argument, "--anisotropy=", 13) == 0) {
        uint32_t level;
        if (options.anisotropy_set || !graphics_anisotropy(argument + 13, level))
            return GraphicsOptionResult::invalid;
        options.anisotropy = level;
        options.anisotropy_set = true;
        return GraphicsOptionResult::accepted;
    }
    if (std::strncmp(argument, "--anti-aliasing=", 16) == 0) {
        uint32_t mode;
        if (options.antialiasing_set || !graphics_antialiasing(argument + 16, mode))
            return GraphicsOptionResult::invalid;
        options.antialiasing = mode;
        options.antialiasing_set = true;
        return GraphicsOptionResult::accepted;
    }
    if (std::strncmp(argument, "--upscaler=", 11) == 0) {
        uint32_t mode;
        if (options.upscaler_set || !graphics_upscaler(argument + 11, mode))
            return GraphicsOptionResult::invalid;
        options.upscaler = mode;
        options.upscaler_set = true;
        return GraphicsOptionResult::accepted;
    }
    if (std::strncmp(argument, "--ambient-occlusion=", 20) == 0) {
        uint32_t mode;
        if (options.ambient_occlusion_set || !graphics_ambient_occlusion(argument + 20, mode))
            return GraphicsOptionResult::invalid;
        options.ambient_occlusion = mode;
        options.ambient_occlusion_set = true;
        return GraphicsOptionResult::accepted;
    }
    if (std::strncmp(argument, "--path-tracing=", 15) == 0) {
        uint32_t mode;
        if (options.path_tracing_set || !graphics_path_tracing(argument + 15, mode))
            return GraphicsOptionResult::invalid;
        options.path_tracing = mode;
        options.path_tracing_set = true;
        return GraphicsOptionResult::accepted;
    }
    if (std::strncmp(argument, "--ray-denoiser=", 15) == 0) {
        uint32_t mode;
        if (options.ray_denoiser_set || !graphics_ray_denoiser(argument + 15, mode))
            return GraphicsOptionResult::invalid;
        options.ray_denoiser = mode;
        options.ray_denoiser_set = true;
        return GraphicsOptionResult::accepted;
    }
    if (std::strncmp(argument, "--path-strength=", 16) == 0) {
        uint32_t percent;
        if (options.path_strength_set || !graphics_path_strength(argument + 16, percent))
            return GraphicsOptionResult::invalid;
        options.path_strength = percent;
        options.path_strength_set = true;
        return GraphicsOptionResult::accepted;
    }
    if (std::strncmp(argument, "--dlss=", 7) == 0) {
        uint32_t mode;
        if (options.dlss_set || !graphics_dlss(argument + 7, mode))
            return GraphicsOptionResult::invalid;
        options.dlss = mode;
        options.dlss_set = true;
        return GraphicsOptionResult::accepted;
    }
    if (std::strncmp(argument, "--sharpen=", 10) == 0) {
        uint32_t strength;
        if (options.sharpen_set || !graphics_sharpen(argument + 10, strength))
            return GraphicsOptionResult::invalid;
        options.sharpen = strength;
        options.sharpen_set = true;
        return GraphicsOptionResult::accepted;
    }
    if (std::strncmp(argument, "--hud-scale=", 12) == 0) {
        uint32_t percent;
        if (options.hud_scale_set || !graphics_hud_scale(argument + 12, percent))
            return GraphicsOptionResult::invalid;
        options.hud_scale = percent;
        options.hud_scale_set = true;
        return GraphicsOptionResult::accepted;
    }
    if (std::strncmp(argument, "--camera-aspect=", 16) == 0) {
        uint32_t mode;
        if (options.camera_aspect_set || !graphics_camera_aspect(argument + 16, mode))
            return GraphicsOptionResult::invalid;
        options.camera_aspect = mode;
        options.camera_aspect_set = true;
        return GraphicsOptionResult::accepted;
    }
    return GraphicsOptionResult::unrelated;
}
inline bool graphics_options_valid(const GraphicsOptions& options, bool vulkan)
{
    return vulkan || (!options.scale_set && !options.output_set && !options.anisotropy_set && !options.antialiasing_set &&
                      !options.upscaler_set && !options.ambient_occlusion_set && !options.hud_scale_set && !options.camera_aspect_set && !options.path_tracing_set && !options.ray_denoiser_set && !options.path_strength_set &&
                      !options.dlss_set && !options.sharpen_set);
}
} // namespace folklore
