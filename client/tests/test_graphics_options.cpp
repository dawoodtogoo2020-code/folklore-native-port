#include "../native/graphics_options.h"
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <initializer_list>

namespace {
int passed = 0, failed = 0;
void check(bool condition, const char* expression, int line)
{
    if (condition) {
        ++passed;
        return;
    }
    ++failed;
    std::fprintf(stderr, "FAIL line %d: %s\n", line, expression);
}
#define CHECK(condition) check((condition), #condition, __LINE__)

bool same(const folklore::GraphicsOptions& a, const folklore::GraphicsOptions& b)
{
    return a.camera_aspect == b.camera_aspect && a.camera_aspect_set == b.camera_aspect_set &&
           a.hud_scale == b.hud_scale && a.hud_scale_set == b.hud_scale_set &&
           a.ambient_occlusion == b.ambient_occlusion && a.ambient_occlusion_set == b.ambient_occlusion_set &&
           a.path_tracing == b.path_tracing && a.path_tracing_set == b.path_tracing_set &&
           a.ray_denoiser == b.ray_denoiser && a.ray_denoiser_set == b.ray_denoiser_set &&
           a.path_strength == b.path_strength && a.path_strength_set == b.path_strength_set &&
           a.width == b.width && a.height == b.height && a.scale == b.scale &&
           a.anisotropy == b.anisotropy && a.output_set == b.output_set &&
           a.scale_set == b.scale_set && a.anisotropy_set == b.anisotropy_set &&
           a.upscaler == b.upscaler && a.upscaler_set == b.upscaler_set && a.antialiasing == b.antialiasing && a.antialiasing_set == b.antialiasing_set;
}
bool text_is(const char* text, const char* expected)
{
    return text && std::strcmp(text, expected) == 0;
}
} // namespace

int main()
{
    using namespace folklore;
    const GraphicsOptions pristine;
    GraphicsOptions defaults;
    CHECK(defaults.width == 1280 && defaults.height == 720 && defaults.scale == 1);
    CHECK(defaults.anisotropy == 0 && !defaults.anisotropy_set);
    CHECK(defaults.hud_scale == 100 && !defaults.hud_scale_set);
    CHECK(defaults.camera_aspect == 0 && !defaults.camera_aspect_set);
    CHECK(!defaults.output_set && !defaults.scale_set);
    CHECK(graphics_options_valid(defaults, false) && graphics_options_valid(defaults, true));

    // Existing output/scale coverage.
    GraphicsOptions four_k;
    CHECK(parse_graphics_option("--output-size=3840x2160", four_k) == GraphicsOptionResult::accepted);
    CHECK(parse_graphics_option("--render-scale=3", four_k) == GraphicsOptionResult::accepted);
    CHECK(four_k.width == 3840 && four_k.height == 2160 && four_k.scale == 3);
    CHECK(!four_k.anisotropy_set && four_k.anisotropy == 0);
    CHECK(graphics_options_valid(four_k, true) && !graphics_options_valid(four_k, false));
    CHECK(parse_graphics_option("--output-size=1280x720", four_k) == GraphicsOptionResult::invalid);
    CHECK(parse_graphics_option("--render-scale=1", four_k) == GraphicsOptionResult::invalid);
    CHECK(four_k.width == 3840 && four_k.height == 2160 && four_k.scale == 3);
    const char* bad[] = {
        "--render-scale=", "--render-scale=0", "--render-scale=5", "--render-scale=-1",
        "--render-scale=1.5", "--render-scale=+3", "--render-scale= 3", "--render-scale=3 ",
        "--render-scale=4294967297", "--render-scale=000000000003",
        "--output-size=", "--output-size=3840", "--output-size=3840x", "--output-size=x2160",
        "--output-size=0x720", "--output-size=8193x2160", "--output-size=3840x8193",
        "--output-size=3840X2160", "--output-size=3840x2160junk", "--output-size=1x1x1",
        "--output-size=4294967297x720", "--output-size=-1x720", "--output-size=+1x720",
        "--output-size=3840x 2160", "--output-size=3840x2160 ", "--output-size=000000000001x720",
        // Anisotropy exact-form rejections.
        "--anisotropy=", "--anisotropy=3", "--anisotropy=5", "--anisotropy=6", "--anisotropy=12",
        "--anisotropy=32", "--anisotropy=00", "--anisotropy=01", "--anisotropy=02",
        "--anisotropy=016", "--anisotropy=-1", "--anisotropy=-0", "--anisotropy=+2",
        "--anisotropy= 2", "--anisotropy=2 ", "--anisotropy=\t4", "--anisotropy=16x",
        "--anisotropy=8x", "--anisotropy=2.0", "--anisotropy=0x2", "--anisotropy=AUTO",
        "--anisotropy=Auto", "--anisotropy=auto ", "--anisotropy= auto", "--anisotropy=auto0",
        "--anisotropy=autox", "--anisotropy=off", "--anisotropy=4294967296",
        "--anisotropy=4294967312", "--anisotropy=99999999999999999999", "--anisotropy==8",
        "--hud-scale=", "--hud-scale=49", "--hud-scale=101", "--hud-scale=0", "--hud-scale=050",
        "--hud-scale=075", "--hud-scale=1000", "--hud-scale=-75", "--hud-scale=+75",
        "--hud-scale=75.0", "--hud-scale=75 ", "--hud-scale= 75", "--hud-scale=75x",
        "--hud-scale=4294967297", "--camera-aspect=", "--camera-aspect=Original",
        "--camera-aspect=OUTPUT", "--camera-aspect=0", "--camera-aspect=output ",
        "--camera-aspect= output", "--camera-aspect=outputx"
    };
    int bad_count = 0;
    for (const char* argument : bad) {
        ++bad_count;
        GraphicsOptions options;
        CHECK(parse_graphics_option(argument, options) == GraphicsOptionResult::invalid);
        CHECK(same(options, pristine));

        // Invalid input must not disturb previously populated state either.
        GraphicsOptions populated;
        populated.width = 1920, populated.height = 1080, populated.scale = 2;
        populated.output_set = populated.scale_set = true;
        const GraphicsOptions before = populated;
        CHECK(parse_graphics_option(argument, populated) == GraphicsOptionResult::invalid);
        CHECK(same(populated, before));
    }
    for (const char* argument : {"--output-size=1x1", "--output-size=8192x8192", "--output-size=03840x02160"}) {
        GraphicsOptions options;
        CHECK(parse_graphics_option(argument, options) == GraphicsOptionResult::accepted);
    }

    // Every accepted anisotropy value, through the codec and the parser.
    const struct { const char* text; uint32_t level; } accepted[] = {
        {"auto", 0}, {"0", 0}, {"1", 1}, {"2", 2}, {"4", 4}, {"8", 8}, {"16", 16}
    };
    for (const auto& entry : accepted) {
        uint32_t level = 99;
        CHECK(graphics_anisotropy(entry.text, level) && level == entry.level);
        char argument[32];
        std::snprintf(argument, sizeof argument, "--anisotropy=%s", entry.text);
        GraphicsOptions options;
        CHECK(parse_graphics_option(argument, options) == GraphicsOptionResult::accepted);
        CHECK(options.anisotropy == entry.level && options.anisotropy_set);
        CHECK(options.width == 1280 && options.height == 720 && options.scale == 1);
        CHECK(!options.output_set && !options.scale_set);
    }

    // Codec failures leave the result unchanged, including null.
    for (const char* text : {"", "3", "00", "016", "+2", "-1", " 8", "8 ", "16x", "AUTO", "4294967296"}) {
        uint32_t level = 77;
        CHECK(!graphics_anisotropy(text, level) && level == 77);
    }
    uint32_t untouched = 77;
    CHECK(!graphics_anisotropy(nullptr, untouched) && untouched == 77);

    // Formatting roundtrip and invalid formatting.
    CHECK(text_is(graphics_anisotropy_text(0), "auto"));
    CHECK(text_is(graphics_anisotropy_text(1), "1"));
    CHECK(text_is(graphics_anisotropy_text(16), "16"));
    for (uint32_t level : {0u, 1u, 2u, 4u, 8u, 16u}) {
        const char* text = graphics_anisotropy_text(level);
        uint32_t parsed = 99;
        CHECK(text != nullptr && graphics_anisotropy(text, parsed) && parsed == level);
    }
    for (uint32_t level : {3u, 5u, 6u, 7u, 9u, 15u, 17u, 32u, 64u, 0xFFFFFFFFu})
        CHECK(graphics_anisotropy_text(level) == nullptr);

    // Duplicates are invalid even for auto/0 and preserve the original state.
    for (const char* first : {"--anisotropy=auto", "--anisotropy=0", "--anisotropy=8"}) {
        for (const char* second : {"--anisotropy=auto", "--anisotropy=0", "--anisotropy=16", "--anisotropy=bad"}) {
            GraphicsOptions options;
            CHECK(parse_graphics_option(first, options) == GraphicsOptionResult::accepted);
            const GraphicsOptions before = options;
            CHECK(parse_graphics_option(second, options) == GraphicsOptionResult::invalid);
            CHECK(same(options, before));
        }
    }

    // Null and unrelated arguments are reported unrelated without touching state.
    GraphicsOptions guarded;
    CHECK(parse_graphics_option(nullptr, guarded) == GraphicsOptionResult::unrelated);
    CHECK(same(guarded, pristine));
    for (const char* argument : {"--vulkan", "", "--anisotropy", "--anisotropy8", "-anisotropy=8",
                                 "--Anisotropy=8", "anisotropy=8", "--render-scale", "--output-size"}) {
        CHECK(parse_graphics_option(argument, guarded) == GraphicsOptionResult::unrelated);
        CHECK(same(guarded, pristine));
    }
    CHECK(parse_graphics_option("--vulkan", defaults) == GraphicsOptionResult::unrelated);

    // Anisotropy is independent of output and scale, in either order.
    GraphicsOptions combined;
    CHECK(parse_graphics_option("--anisotropy=4", combined) == GraphicsOptionResult::accepted);
    CHECK(parse_graphics_option("--output-size=2560x1440", combined) == GraphicsOptionResult::accepted);
    CHECK(parse_graphics_option("--render-scale=2", combined) == GraphicsOptionResult::accepted);
    CHECK(combined.anisotropy == 4 && combined.width == 2560 && combined.height == 1440 && combined.scale == 2);
    CHECK(combined.anisotropy_set && combined.output_set && combined.scale_set);
    GraphicsOptions reversed;
    CHECK(parse_graphics_option("--render-scale=4", reversed) == GraphicsOptionResult::accepted);
    CHECK(parse_graphics_option("--anisotropy=1", reversed) == GraphicsOptionResult::accepted);
    CHECK(reversed.anisotropy == 1 && reversed.scale == 4 && !reversed.output_set);
    CHECK(reversed.width == 1280 && reversed.height == 720);
    const GraphicsOptions reversed_before = reversed;
    CHECK(parse_graphics_option("--render-scale=2", reversed) == GraphicsOptionResult::invalid);
    CHECK(same(reversed, reversed_before));

    // Any explicit control requires Vulkan, including explicit auto.
    for (const char* argument : {"--anisotropy=auto", "--anisotropy=0", "--anisotropy=1", "--anisotropy=16"}) {
        GraphicsOptions options;
        CHECK(parse_graphics_option(argument, options) == GraphicsOptionResult::accepted);
        CHECK(!graphics_options_valid(options, false) && graphics_options_valid(options, true));
    }
    CHECK(!graphics_options_valid(combined, false) && graphics_options_valid(combined, true));

    CHECK(defaults.antialiasing == 0 && !defaults.antialiasing_set);
    for (const char* mode : {"off", "fxaa"}) {
        uint32_t parsed = 99;
        CHECK(graphics_antialiasing(mode, parsed));
        CHECK(text_is(graphics_antialiasing_text(parsed), mode));
        char argument[32]; std::snprintf(argument, sizeof argument, "--anti-aliasing=%s", mode);
        GraphicsOptions options;
        CHECK(parse_graphics_option(argument, options) == GraphicsOptionResult::accepted);
        CHECK(options.antialiasing == parsed && options.antialiasing_set);
        CHECK(!graphics_options_valid(options, false) && graphics_options_valid(options, true));
        const auto before = options;
        CHECK(parse_graphics_option("--anti-aliasing=fxaa", options) == GraphicsOptionResult::invalid);
        CHECK(same(options, before));
    }
    for (const char* bad_mode : {"", "FXAA", "taa", "1", "0", "fxaa ", " off", "offx"}) {
        uint32_t parsed = 99;
        CHECK(!graphics_antialiasing(bad_mode, parsed) && parsed == 99);
        char argument[48]; std::snprintf(argument, sizeof argument, "--anti-aliasing=%s", bad_mode);
        GraphicsOptions options;
        CHECK(parse_graphics_option(argument, options) == GraphicsOptionResult::invalid);
        CHECK(same(options, pristine));
    }
    uint32_t mode = 99;
    CHECK(!graphics_antialiasing(nullptr, mode) && mode == 99);
    CHECK(graphics_antialiasing_text(2) == nullptr);
    CHECK(parse_graphics_option("--anti-aliasing=fxaa", combined) == GraphicsOptionResult::accepted);
    CHECK(combined.antialiasing == 1 && combined.anisotropy == 4 && combined.scale == 2);

    CHECK(defaults.upscaler == 0 && !defaults.upscaler_set);
    for (const char* value : {"off", "fsr1"}) {
        GraphicsOptions option;
        char argument[32]; std::snprintf(argument, sizeof argument, "--upscaler=%s", value);
        CHECK(parse_graphics_option(argument, option) == GraphicsOptionResult::accepted);
        CHECK(option.upscaler_set && text_is(graphics_upscaler_text(option.upscaler), value));
        CHECK(!graphics_options_valid(option, false) && graphics_options_valid(option, true));
        const auto saved = option;
        CHECK(parse_graphics_option(argument, option) == GraphicsOptionResult::invalid && same(option, saved));
    }
    for (const char* value : {"", "FSR1", "fsr", "fsr2", "1", "off ", " fsr1"}) {
        uint32_t mode = 99;
        CHECK(!graphics_upscaler(value, mode) && mode == 99);
        GraphicsOptions option; const auto saved = option;
        char argument[40]; std::snprintf(argument, sizeof argument, "--upscaler=%s", value);
        CHECK(parse_graphics_option(argument, option) == GraphicsOptionResult::invalid && same(option, saved));
    }
    CHECK(!graphics_upscaler(nullptr, mode) && graphics_upscaler_text(2) == nullptr);

    CHECK(defaults.ambient_occlusion == 0 && !defaults.ambient_occlusion_set);
    for (const char* value : {"off", "cacao", "rayquery"}) {
        GraphicsOptions option;
        char argument[48]; std::snprintf(argument, sizeof argument, "--ambient-occlusion=%s", value);
        CHECK(parse_graphics_option(argument, option) == GraphicsOptionResult::accepted);
        CHECK(option.ambient_occlusion_set && text_is(graphics_ambient_occlusion_text(option.ambient_occlusion), value));
        CHECK(!graphics_options_valid(option, false) && graphics_options_valid(option, true));
        const auto saved = option;
        CHECK(parse_graphics_option("--ambient-occlusion=off", option) == GraphicsOptionResult::invalid && same(option, saved));
    }
    for (const char* value : {"", "CACAO", "ssao", "gtao", "1", "0", "off ", " cacao", "cacao=1"}) {
        uint32_t parsed = 99;
        CHECK(!graphics_ambient_occlusion(value, parsed) && parsed == 99);
        GraphicsOptions option; const auto saved = option;
        char argument[64]; std::snprintf(argument, sizeof argument, "--ambient-occlusion=%s", value);
        CHECK(parse_graphics_option(argument, option) == GraphicsOptionResult::invalid && same(option, saved));
    }
    CHECK(!graphics_ambient_occlusion(nullptr, mode) && graphics_ambient_occlusion_text(3) == nullptr);
    for (const char* value : {"off", "diffuse-preview", "hybrid"}) {
        GraphicsOptions option;
        char argument[64]; std::snprintf(argument, sizeof argument, "--path-tracing=%s", value);
        CHECK(parse_graphics_option(argument, option) == GraphicsOptionResult::accepted);
        CHECK(option.path_tracing_set && text_is(graphics_path_tracing_text(option.path_tracing), value));
        CHECK(!graphics_options_valid(option, false) && graphics_options_valid(option, true));
        const auto saved = option;
        CHECK(parse_graphics_option("--path-tracing=off", option) == GraphicsOptionResult::invalid && same(option, saved));
    }
    for (const char* value : {"", "on", "full", "diffuse-preview "}) {
        GraphicsOptions option; const auto saved = option;
        char argument[64]; std::snprintf(argument, sizeof argument, "--path-tracing=%s", value);
        CHECK(parse_graphics_option(argument, option) == GraphicsOptionResult::invalid && same(option, saved));
    }
    CHECK(!graphics_path_tracing(nullptr, mode) && graphics_path_tracing_text(3) == nullptr);
    for (const char* value : {"off", "quality", "balanced", "performance", "ultra-performance", "dlaa"}) {
        GraphicsOptions option;
        char argument[64]; std::snprintf(argument, sizeof argument, "--dlss=%s", value);
        CHECK(parse_graphics_option(argument, option) == GraphicsOptionResult::accepted);
        CHECK(option.dlss_set && text_is(graphics_dlss_text(option.dlss), value));
        CHECK(parse_graphics_option("--dlss=off", option) == GraphicsOptionResult::invalid);
    }
    for (const char* value : {"0", "37", "100"}) {
        GraphicsOptions option;
        char argument[64]; std::snprintf(argument, sizeof argument, "--sharpen=%s", value);
        CHECK(parse_graphics_option(argument, option) == GraphicsOptionResult::accepted);
        CHECK(option.sharpen_set && option.sharpen <= 100);
    }
    for (const char* value : {"", "-1", "101", "1.5", "01"}) {
        GraphicsOptions option;
        char argument[64]; std::snprintf(argument, sizeof argument, "--sharpen=%s", value);
        CHECK(parse_graphics_option(argument, option) == GraphicsOptionResult::invalid);
    }
    for (const char* value : {"off", "spatial"}) {
        GraphicsOptions option;
        char argument[64]; std::snprintf(argument, sizeof argument, "--ray-denoiser=%s", value);
        CHECK(parse_graphics_option(argument, option) == GraphicsOptionResult::accepted);
        CHECK(option.ray_denoiser_set && text_is(graphics_ray_denoiser_text(option.ray_denoiser), value));
        const auto saved = option;
        CHECK(parse_graphics_option("--ray-denoiser=off", option) == GraphicsOptionResult::invalid && same(option, saved));
    }
    CHECK(!graphics_ray_denoiser(nullptr, mode) && graphics_ray_denoiser_text(2) == nullptr);
    for (const char* value : {"20", "35", "60", "100"}) {
        GraphicsOptions option;
        char argument[64]; std::snprintf(argument, sizeof argument, "--path-strength=%s", value);
        CHECK(parse_graphics_option(argument, option) == GraphicsOptionResult::accepted);
        CHECK(option.path_strength_set && text_is(graphics_path_strength_text(option.path_strength), value));
        const auto saved = option;
        CHECK(parse_graphics_option("--path-strength=35", option) == GraphicsOptionResult::invalid && same(option, saved));
    }
    for (const char* value : {"", "21", "0", "035", "100 ", "-20"}) {
        GraphicsOptions option; const auto saved = option;
        char argument[64]; std::snprintf(argument, sizeof argument, "--path-strength=%s", value);
        CHECK(parse_graphics_option(argument, option) == GraphicsOptionResult::invalid && same(option, saved));
    }
    CHECK(!graphics_path_strength(nullptr, mode) && graphics_path_strength_text(21) == nullptr);
    CHECK(parse_graphics_option("--ambient-occlusion=cacao", combined) == GraphicsOptionResult::accepted);
    CHECK(combined.ambient_occlusion == 1 && combined.antialiasing == 1 && combined.anisotropy == 4 && combined.scale == 2);

    for (const char* value : {"50", "75", "100"}) {
        GraphicsOptions option;
        char argument[32]; std::snprintf(argument, sizeof argument, "--hud-scale=%s", value);
        uint32_t expected = 0;
        CHECK(graphics_hud_scale(value, expected));
        CHECK(parse_graphics_option(argument, option) == GraphicsOptionResult::accepted);
        CHECK(option.hud_scale == expected && option.hud_scale_set);
        CHECK(!graphics_options_valid(option, false) && graphics_options_valid(option, true));
        const auto before = option;
        CHECK(parse_graphics_option("--hud-scale=100", option) == GraphicsOptionResult::invalid && same(option, before));
    }
    uint32_t unchanged = 777;
    CHECK(!graphics_hud_scale(nullptr, unchanged) && unchanged == 777);
    CHECK(!graphics_hud_scale("nan", unchanged) && unchanged == 777);
    CHECK(parse_graphics_option("--hud-scale=75", combined) == GraphicsOptionResult::accepted);
    CHECK(combined.hud_scale == 75 && combined.ambient_occlusion == 1);

    for (const char* value : {"original", "output"}) {
        uint32_t parsed = 99;
        CHECK(graphics_camera_aspect(value, parsed));
        CHECK(text_is(graphics_camera_aspect_text(parsed), value));
        char argument[48]; std::snprintf(argument, sizeof argument, "--camera-aspect=%s", value);
        GraphicsOptions option;
        CHECK(parse_graphics_option(argument, option) == GraphicsOptionResult::accepted);
        CHECK(option.camera_aspect_set && option.camera_aspect == parsed);
        CHECK(!graphics_options_valid(option, false) && graphics_options_valid(option, true));
        const auto before = option;
        CHECK(parse_graphics_option("--camera-aspect=original", option) == GraphicsOptionResult::invalid);
        CHECK(same(option, before));
    }
    uint32_t camera_untouched = 99;
    CHECK(!graphics_camera_aspect(nullptr, camera_untouched) && camera_untouched == 99);
    CHECK(graphics_camera_aspect_text(2) == nullptr);
    double ratio = -1;
    CHECK(graphics_camera_output_ratio(3440,1440,ratio) && ratio > 2.38888 && ratio < 2.38890);
    CHECK(graphics_camera_output_ratio(5120,1440,ratio) && ratio > 3.55555 && ratio <= 32.0/9.0);
    CHECK(graphics_camera_output_ratio(1280,720,ratio) && ratio == 0);
    CHECK(graphics_camera_output_ratio(1440,900,ratio) && ratio == 0);
    CHECK(!graphics_camera_output_ratio(8192,1,ratio) && ratio == 0);
    CHECK(!graphics_camera_output_ratio(0,720,ratio) && ratio == 0);
    GraphicsOptions settings_camera;
    graphics_camera_aspect_settings(settings_camera, 1);
    CHECK(settings_camera.camera_aspect_set && settings_camera.camera_aspect == 1);
    GraphicsOptions cli_camera;
    CHECK(parse_graphics_option("--camera-aspect=original", cli_camera) == GraphicsOptionResult::accepted);
    graphics_camera_aspect_settings(cli_camera, 1);
    CHECK(cli_camera.camera_aspect_set && cli_camera.camera_aspect == 0);

    if (failed) {
        std::fprintf(stderr, "graphics options: %d of %d checks FAILED\n", failed, passed + failed);
        return 1;
    }
    std::printf("graphics options: %d checks passed (%d malformed inputs, anisotropy codec, duplicates, "
                "null, interaction and Vulkan requirement)\n", passed, bad_count);
    return 0;
}
