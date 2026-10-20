#include "../native/graphics_settings.h"
#include <fstream>
#include <iostream>
#include <iterator>
#include <stdexcept>
#include <vector>
#ifdef _WIN32
#define NOMINMAX
#include <windows.h>
#else
#include <sys/stat.h>
#include <unistd.h>
#endif
namespace fs = std::filesystem;
using namespace folklore;
static unsigned checks = 0, skips = 0;
static void check(bool okay, const char* description)
{
    ++checks;
    if (!okay) throw std::runtime_error(description);
}
static std::string read(const fs::path& path)
{
    std::ifstream file(path, std::ios::binary);
    if (!file) throw std::runtime_error("fixture read failed");
    return {std::istreambuf_iterator<char>(file), {}};
}
static void write(const fs::path& path, const std::string& data)
{
    std::ofstream file(path, std::ios::binary | std::ios::trunc);
    file.write(data.data(), static_cast<std::streamsize>(data.size()));
    file.close();
    if (!file) throw std::runtime_error("fixture write failed");
}
static bool equal(const GraphicsSettings& a, const GraphicsSettings& b)
{ return a.width == b.width && a.height == b.height && a.scale == b.scale && a.anisotropy == b.anisotropy && a.antialiasing == b.antialiasing && a.upscaler == b.upscaler && a.ambient_occlusion == b.ambient_occlusion && a.hud_scale == b.hud_scale && a.camera_aspect == b.camera_aspect && a.path_tracing == b.path_tracing && a.ray_denoiser == b.ray_denoiser && a.path_strength == b.path_strength && a.dlss == b.dlss && a.sharpen == b.sharpen; }
int main(int argc, char** argv)
{
    try {
        if (argc != 2) throw std::runtime_error("pass one absolute fresh fixture-directory path");
        const fs::path root(argv[1]);
        check(root.is_absolute() && !fs::exists(root) && fs::is_directory(root.parent_path()), "fixture path must be absolute, fresh and have an existing parent");
        check(fs::create_directory(root), "create owned fixture directory");
        const auto file = root/"graphics.ini", link = root/"link.ini", directory = root/"directory";
        std::string error = "stale";
        GraphicsSettings defaults, loaded{9, 8, 4, 16, 1, 1, 1, 75, 1}, sentinel = loaded;
        check(equal(defaults, {1280,720,1}), "defaults");
        check(load_graphics_settings(file, loaded, error) == SettingsLoadResult::missing && equal(loaded, sentinel) && error.empty(), "missing leaves caller unchanged and clears error");
        check(save_graphics_settings(file, defaults, error) && error.empty(), "new save");
        const std::string legacy = "version=1\noutput_width=1280\noutput_height=720\nrender_scale=1\n";
        const std::string legacy2 = "version=2\noutput_width=1280\noutput_height=720\nrender_scale=1\nanisotropy=auto\n";
        const std::string legacy4 = "version=4\noutput_width=1280\noutput_height=720\nrender_scale=1\nanisotropy=auto\nanti_aliasing=off\nupscaler=off\n";
        const std::string legacy5 = "version=5\noutput_width=1280\noutput_height=720\nrender_scale=1\nanisotropy=auto\nanti_aliasing=off\nupscaler=off\nambient_occlusion=off\n";
        const std::string legacy6 = "version=6\noutput_width=1280\noutput_height=720\nrender_scale=1\nanisotropy=auto\nanti_aliasing=off\nupscaler=off\nambient_occlusion=off\nhud_scale=100\n";
        const std::string legacy7 = "version=7\noutput_width=1280\noutput_height=720\nrender_scale=1\nanisotropy=auto\nanti_aliasing=off\nupscaler=off\nambient_occlusion=off\nhud_scale=100\ncamera_aspect=original\n";
        const std::string legacy8 = "version=8\noutput_width=1280\noutput_height=720\nrender_scale=1\nanisotropy=auto\nanti_aliasing=off\nupscaler=off\nambient_occlusion=off\nhud_scale=100\ncamera_aspect=original\npath_tracing=off\nray_denoiser=spatial\n";
        const std::string legacy9 = "version=9\noutput_width=1280\noutput_height=720\nrender_scale=1\nanisotropy=auto\nanti_aliasing=off\nupscaler=off\nambient_occlusion=off\nhud_scale=100\ncamera_aspect=original\npath_tracing=off\nray_denoiser=spatial\npath_strength=35\n";
        const std::string canonical = "version=10" + legacy9.substr(9) + "dlss=off\nsharpen=0\n";
        check(read(file) == canonical, "canonical save format");
        error = "stale";
        check(load_graphics_settings(file, loaded, error) == SettingsLoadResult::loaded && equal(loaded, defaults) && error.empty(), "saved settings load and clear error");
        write(file, legacy);
        loaded = sentinel;
        check(load_graphics_settings(file, loaded, error) == SettingsLoadResult::loaded && equal(loaded, defaults), "version1 migrates to guest anisotropy");
        check(save_graphics_settings(file, loaded, error) && read(file) == canonical, "version1 saves as version9 without losing prior values");
        write(file, legacy2);
        loaded = sentinel;
        check(load_graphics_settings(file, loaded, error) == SettingsLoadResult::loaded && equal(loaded, defaults), "version2 defaults anti-aliasing off");
        write(file, legacy5); loaded = sentinel;
        check(load_graphics_settings(file, loaded, error) == SettingsLoadResult::loaded && equal(loaded, defaults), "version5 defaults HUD scale to 100");
        write(file, legacy6); loaded = sentinel;
        check(load_graphics_settings(file, loaded, error) == SettingsLoadResult::loaded && equal(loaded, defaults), "version6 defaults camera aspect to original");
        write(file, legacy7); loaded = sentinel;
        check(load_graphics_settings(file, loaded, error) == SettingsLoadResult::loaded && equal(loaded, defaults), "version7 defaults path tracing off");
        write(file, legacy8); loaded = sentinel;
        check(load_graphics_settings(file, loaded, error) == SettingsLoadResult::loaded && equal(loaded, defaults), "version8 defaults path strength to 35");
        write(file, legacy9); loaded = sentinel;
        check(load_graphics_settings(file, loaded, error) == SettingsLoadResult::loaded && equal(loaded, defaults), "version9 defaults DLSS and sharpening off");
        for (uint32_t mode = 0; mode < 6; ++mode) {
            GraphicsSettings choice;
            choice.dlss = mode;
            choice.sharpen = mode * 16;
            choice.path_tracing = 2;
            check(save_graphics_settings(file, choice, error), "save DLSS and sharpen");
            check(load_graphics_settings(file, loaded, error) == SettingsLoadResult::loaded && equal(loaded, choice), "DLSS and sharpen roundtrip");
        }
        write(file, canonical);
        for (uint32_t mode : {0u,1u}) {
            const GraphicsSettings choice{3840,2160,3,16,mode};
            check(save_graphics_settings(file, choice, error), "save anti-aliasing choice");
            check(load_graphics_settings(file, loaded, error) == SettingsLoadResult::loaded && equal(loaded, choice), "read back anti-aliasing choice");
        }
        for (uint32_t level : {0u,1u,2u,4u,8u,16u}) {
            const GraphicsSettings choice{1920,1080,2,level};
            check(save_graphics_settings(file, choice, error), "save anisotropy choice");
            check(load_graphics_settings(file, loaded, error) == SettingsLoadResult::loaded && equal(loaded, choice), "read back anisotropy choice");
        }
        for (uint32_t percent : {50u,75u,100u}) {
            auto choice = defaults; choice.hud_scale = percent;
            check(save_graphics_settings(file, choice, error), "save HUD scale");
            loaded = sentinel;
            check(load_graphics_settings(file, loaded, error) == SettingsLoadResult::loaded && equal(loaded, choice), "HUD scale roundtrip");
        }
        for (uint32_t mode : {0u,1u}) {
            auto choice = defaults; choice.camera_aspect = mode;
            check(save_graphics_settings(file, choice, error), "save camera aspect");
            loaded = sentinel;
            check(load_graphics_settings(file, loaded, error) == SettingsLoadResult::loaded && equal(loaded, choice), "camera aspect roundtrip");
        }
        write(file, legacy7.substr(0, legacy7.find("anisotropy=")) + "anisotropy=0\nanti_aliasing=off\nupscaler=off\nambient_occlusion=off\nhud_scale=100\ncamera_aspect=original\n");
        check(load_graphics_settings(file, loaded, error) == SettingsLoadResult::loaded && equal(loaded, defaults), "zero alias means guest");
        const GraphicsSettings changed{3840,2160,3,8,1};
        check(save_graphics_settings(file, changed, error), "replace existing file");
        check(load_graphics_settings(file, loaded, error) == SettingsLoadResult::loaded && equal(loaded, changed), "replacement readback");
        const auto preserved = read(file);
        for (const auto invalid : {GraphicsSettings{0,720,1}, {8193,720,1}, {1280,0,1}, {1280,8193,1}, {1280,720,0}, {1280,720,5}, {1280,720,1,3}, {1280,720,1,32}, {1280,720,1,0,2}, {1280,720,1,0,0,0,0,49}, {1280,720,1,0,0,0,0,101}, {1280,720,1,0,0,0,0,100,2}}) {
            check(!save_graphics_settings(file, invalid, error) && !error.empty(), "invalid save rejected");
            check(read(file) == preserved, "invalid save preserves original bytes");
        }
        write(file, "# comment\r\n \t\r\nrender_scale = 4\r\n output_height\t= 8192 \r\nversion=1\r\noutput_width=1\r\n");
        check(load_graphics_settings(file, loaded, error) == SettingsLoadResult::loaded && equal(loaded, {1,8192,4}), "reordered CRLF, comments, whitespace and inclusive bounds");
        const std::string legacy3 = "version=3\noutput_width=1280\noutput_height=720\nrender_scale=1\nanisotropy=auto\nanti_aliasing=fxaa\n";
        write(file, legacy3);
        check(load_graphics_settings(file, loaded, error) == SettingsLoadResult::loaded && loaded.antialiasing == 1 && loaded.upscaler == 0 && loaded.hud_scale == 100 && loaded.camera_aspect == 0, "v3 defaults FSR off, HUD100 and original camera");
        GraphicsSettings fsr{1920,1080,1,16,1,1};
        check(save_graphics_settings(file, fsr, error), "save FSR1 with FXAA");
        check(load_graphics_settings(file, loaded, error) == SettingsLoadResult::loaded && equal(loaded, fsr), "FSR1 settings roundtrip");
        for (const auto* value : {"FSR1", "fsr2", "", "1"}) {
            write(file, legacy7.substr(0, legacy7.find("upscaler=")) + "upscaler=" + value + "\nambient_occlusion=off\nhud_scale=100\n");
            loaded=sentinel;
            check(load_graphics_settings(file, loaded, error) == SettingsLoadResult::error && equal(loaded,sentinel), "bad upscaler leaves state unchanged");
        }
        std::vector<std::string> bad = {
            "", "version=1\n", canonical + "extra=1\n", canonical + "version=1\n",
            "version=1\noutput_width=1280\noutput_height=720\n", canonical + "garbage\n",
            std::string(4097, '#'), canonical + std::string(1, '\0'),
            legacy + "anisotropy=16\n", canonical + "anisotropy=2\n",
            "version=2\noutput_width=1280\noutput_height=720\nrender_scale=1\n",
            "version=3\noutput_width=1280\noutput_height=720\nrender_scale=1\nanisotropy=auto\n",
            legacy2 + "anti_aliasing=fxaa\n", canonical + "anti_aliasing=off\n"
        };
        for (const std::string& value : {"0", "11", "-1", "+1", "1x", "4294967297"}) {
            auto text = canonical;
            text.replace(text.find("version=10"), 10, "version=" + value);
            bad.push_back(text);
        }
        for (const std::string& value : {"0", "8193", "-1", "+1", "1.0", "4294967297", "\xD9\xA1"}) {
            auto text = canonical;
            text.replace(text.find("output_width=1280"), 17, "output_width=" + value);
            bad.push_back(text);
        }
        for (const std::string& value : {"0", "5", "-1", "+1", "1x", "4294967297"}) {
            auto text = canonical;
            text.replace(text.find("render_scale=1"), 14, "render_scale=" + value);
            bad.push_back(text);
        }
        for (const std::string& value : {"", "3", "12", "32", "016", "AUTO", "+2", "2x", "4294967296"}) {
            auto text = canonical;
            text.replace(text.find("anisotropy=auto"), 15, "anisotropy=" + value + "\n");
            bad.push_back(text);
        }
        for (const std::string& value : {"", "FXAA", "taa", "0", "1", "offx"}) {
            auto text = canonical;
            text.replace(text.find("anti_aliasing=off"), 17, "anti_aliasing=" + value + "\n");
            bad.push_back(text);
        }
        bad.push_back(legacy4 + "ambient_occlusion=cacao\n");
        auto missing_ao = legacy4; missing_ao.replace(0,9,"version=5"); bad.push_back(missing_ao);
        auto missing_hud = legacy5; missing_hud.replace(0,9,"version=6"); bad.push_back(missing_hud);
        auto missing_camera = legacy6; missing_camera.replace(0,9,"version=7"); bad.push_back(missing_camera);
        bad.push_back(legacy6 + "camera_aspect=output\n");
        bad.push_back(canonical + "ambient_occlusion=off\n");
        bad.push_back(canonical + "hud_scale=75\n");
        bad.push_back(canonical + "camera_aspect=output\n");
        bad.push_back(legacy5 + "hud_scale=75\n");
        for (const std::string& value : {"", "49", "101", "075", "75.0", "+75", "-75", "75x", "4294967297"}) {
            auto text = canonical;
            text.replace(text.find("hud_scale=100"), 13, "hud_scale=" + value);
            bad.push_back(text);
        }
        for (const std::string& value : {"", "Original", "OUTPUT", "0", "1", "outputx", "+output"}) {
            auto text = canonical;
            text.replace(text.find("camera_aspect=original"), 22, "camera_aspect=" + value);
            bad.push_back(text);
        }
        for (const std::string& value : {"", "CACAO", "ssao", "0", "1", "cacaoX"}) {
            auto text = canonical;
            text.replace(text.find("ambient_occlusion=off"),21,"ambient_occlusion="+value);
            bad.push_back(text);
        }
        for (uint32_t mode : {0u,1u,2u}) {
            auto choice = defaults; choice.ambient_occlusion = mode;
            check(save_graphics_settings(file,choice,error), "save AO mode");
            loaded=sentinel;
            check(load_graphics_settings(file,loaded,error)==SettingsLoadResult::loaded && equal(loaded,choice), "AO mode roundtrip");
        }
        auto preview=defaults; preview.path_tracing=1;
        check(save_graphics_settings(file,preview,error), "save diffuse preview");
        check(load_graphics_settings(file,loaded,error)==SettingsLoadResult::loaded && equal(loaded,preview), "diffuse preview roundtrip");
        preview.ambient_occlusion=2;
        check(!save_graphics_settings(file,preview,error) && error.find("ambient occlusion off")!=std::string::npos, "AO and preview conflict");
        preview.ambient_occlusion=0; preview.ray_denoiser=0;
        check(save_graphics_settings(file,preview,error), "save preview with denoiser off");
        check(load_graphics_settings(file,loaded,error)==SettingsLoadResult::loaded && equal(loaded,preview), "denoiser off roundtrip");
        for (uint32_t strength : {20u, 35u, 60u, 100u}) {
            preview.path_strength = strength;
            check(save_graphics_settings(file,preview,error), "save preview mix strength");
            check(load_graphics_settings(file,loaded,error)==SettingsLoadResult::loaded && equal(loaded,preview), "preview mix strength roundtrip");
        }
        preview.path_strength = 21;
        check(!save_graphics_settings(file,preview,error), "reject unsupported preview mix strength");
        auto invalid_ao=defaults;invalid_ao.ambient_occlusion=3;
        const auto retained=read(file);
        check(!save_graphics_settings(file,invalid_ao,error) && read(file)==retained, "invalid AO preserves file");
        write(file,legacy4);loaded=sentinel;
        check(load_graphics_settings(file,loaded,error)==SettingsLoadResult::loaded && equal(loaded,defaults), "version4 defaults AO off");
        for (const auto& text : bad) {
            write(file, text);
            loaded = sentinel;
            check(load_graphics_settings(file, loaded, error) == SettingsLoadResult::error && !error.empty(), "malformed file rejected");
            check(equal(loaded, sentinel), "malformed load leaves caller unchanged");
        }
        write(file, canonical);
        check(!save_graphics_settings(root/"missing-parent"/"graphics.ini", changed, error), "missing parent save fails");
        check(!fs::exists(root/"missing-parent"), "no implicit parent creation");
        check(fs::create_directory(directory), "create directory fixture");
        check(!save_graphics_settings(directory, changed, error), "directory destination refused");
        check(load_graphics_settings(directory, loaded, error) == SettingsLoadResult::error, "directory load refused");

        std::error_code link_error;
        fs::create_symlink(file, link, link_error);
        if (link_error) { ++skips; std::cout << "SKIP symlink creation unavailable: " << link_error.message() << '\n'; }
        else {
            check(load_graphics_settings(link, loaded, error) == SettingsLoadResult::error, "symlink load refused");
            check(!save_graphics_settings(link, changed, error), "symlink save refused");
            check(read(file) == canonical && fs::is_symlink(link), "symlink target and link preserved");
            check(fs::remove(link), "remove owned link fixture");
        }
#ifdef _WIN32
        HANDLE held = CreateFileW(file.c_str(), GENERIC_READ, FILE_SHARE_READ, nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
        check(held != INVALID_HANDLE_VALUE, "open destination without delete sharing");
        const bool rejected = !save_graphics_settings(file, changed, error);
        CloseHandle(held);
        check(rejected, "locked destination replacement fails");
        check(read(file) == canonical, "failed replacement preserves original bytes");
#else
        if (geteuid() == 0) { ++skips; std::cout << "SKIP non-writable-parent check under root\n"; }
        else {
            check(chmod(root.c_str(), 0500) == 0, "protect owned parent for failure fixture");
            const bool rejected = !save_graphics_settings(file, changed, error);
            const int restored = chmod(root.c_str(), 0700);
            check(restored == 0, "restore owned fixture permissions");
            check(rejected, "non-writable parent rejected");
            check(read(file) == canonical, "failed save preserves original bytes");
        }
#endif
        unsigned entries = 0;
        for (const auto& item : fs::directory_iterator(root)) {
            check(item.path() == file || item.path() == directory, "no orphan temporary file");
            ++entries;
        }
        check(entries == 2, "exact fixture inventory");
        check(fs::remove(file) && fs::remove(directory) && fs::remove(root), "remove only exact owned fixtures");
        std::cout << "graphics settings: " << checks << " checks passed, " << skips << " skipped\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "graphics settings FAIL after " << checks << " checks: " << error.what() << '\n';
        return 1;
    }
}
