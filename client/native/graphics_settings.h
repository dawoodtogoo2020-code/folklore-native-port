#pragma once
#include <cstdint>
#include <filesystem>
#include <string>

namespace folklore {
struct GraphicsSettings {
    uint32_t width = 1280, height = 720, scale = 1, anisotropy = 0, antialiasing = 0, upscaler = 0, ambient_occlusion = 0, hud_scale = 100, camera_aspect = 0, path_tracing = 0, ray_denoiser = 1, path_strength = 35;
    uint32_t dlss = 0, sharpen = 0;
};
enum class SettingsLoadResult { loaded, missing, error };
bool graphics_settings_valid(const GraphicsSettings&, std::string& error);
SettingsLoadResult load_graphics_settings(const std::filesystem::path&, GraphicsSettings&, std::string& error);
bool save_graphics_settings(const std::filesystem::path&, const GraphicsSettings&, std::string& error);
} // namespace folklore
