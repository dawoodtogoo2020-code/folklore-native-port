/* CPU-only cache contract. production_sampler.inc is extracted source.
 * Local API stubs record sampler requests; no Vulkan headers/loader/device. */
#include <stdint.h>
#include <stdio.h>
#include <string.h>

typedef uint8_t u8;
typedef uint32_t u32;
typedef uint64_t u64;
typedef uint64_t VkSampler;
typedef int VkSamplerAddressMode;
enum {
    VK_SUCCESS = 0, VK_STRUCTURE_TYPE_SAMPLER_CREATE_INFO = 31,
    VK_FILTER_NEAREST = 0, VK_FILTER_LINEAR = 1,
    VK_SAMPLER_MIPMAP_MODE_NEAREST = 0, VK_SAMPLER_MIPMAP_MODE_LINEAR = 1,
    VK_SAMPLER_ADDRESS_MODE_REPEAT = 0, VK_SAMPLER_ADDRESS_MODE_MIRRORED_REPEAT = 1,
    VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE = 2, VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_BORDER = 3,
    VK_BORDER_COLOR_FLOAT_TRANSPARENT_BLACK = 0
};
typedef struct VkSamplerCreateInfo {
    int sType, magFilter, minFilter, mipmapMode;
    VkSamplerAddressMode addressModeU, addressModeV, addressModeW;
    float minLod, maxLod;
    int anisotropyEnable;
    float maxAnisotropy;
    int borderColor;
} VkSamplerCreateInfo;

static struct { u64 device; VkSampler sampler; int anisotropy_ok; float max_anisotropy; u32 anisotropy_override; } s_vk = {.sampler=999};
/* This legacy LOD/cache fixture models an unavailable anisotropy feature.
 * Real anisotropy selection/cache behavior is tested by the SDK backend suite. */
void rsx_vk_anisotropy_apply(VkSamplerCreateInfo* sampler, u32 code, u32 override,
                            int enabled, float maximum, int allow)
{
    (void)code; (void)override; (void)enabled; (void)maximum; (void)allow;
    sampler->anisotropyEnable = 0; sampler->maxAnisotropy = 1;
}
static VkSamplerCreateInfo recorded[128];
static u32 create_calls, fail_create;
static u32 destroy_calls;
static VkSampler destroyed[128];
static VkSampler next_sampler = 100;
static int pvkCreateSampler(u64 device, const VkSamplerCreateInfo* info,
                            const void* allocator, VkSampler* out)
{
    (void)device; (void)allocator;
    if (create_calls < 128) recorded[create_calls] = *info;
    ++create_calls;
    if (fail_create) return -1;
    *out = next_sampler++;
    return VK_SUCCESS;
}
static void pvkDestroySampler(u64 device, VkSampler sampler, const void* allocator)
{
    (void)device; (void)allocator;
    if (destroy_calls < 128) destroyed[destroy_calls] = sampler;
    ++destroy_calls;
}

#include "production_sampler.inc"

static rsx_be_sampler_desc descriptor(void)
{
    rsx_be_sampler_desc d;
    memset(&d, 0, sizeof d);
    d.min_linear = d.mag_linear = d.mip_linear = d.mip_present = 1;
    d.wrap_s = d.wrap_t = d.wrap_r = 1;
    d.min_lod = 1.0f; d.max_lod = 4095.0f / 256.0f;
    return d;
}
static void reset(void)
{
    memset(s_esamp, 0, sizeof s_esamp);
    memset(recorded, 0, sizeof recorded);
    memset(destroyed, 0, sizeof destroyed);
    destroy_calls = 0;
    s_esamp_count = create_calls = fail_create = 0;
    next_sampler = 100;
}
#define REQUIRE(condition) do { if (!(condition)) { \
    printf("assertion line=%d expression=%s\n", __LINE__, #condition); return 1; \
} } while (0)

static int adjacent_min_lod(void)
{
    reset();
    rsx_be_sampler_desc a = descriptor(), b = a;
    b.min_lod = 257.0f / 256.0f;
    VkSampler x = vk_eng_sampler(&a), y = vk_eng_sampler(&b);
    REQUIRE(x != y); REQUIRE(create_calls == 2);
    REQUIRE(recorded[0].minLod == 1.0f && recorded[1].minLod == 257.0f / 256.0f);
    return 0;
}
static int adjacent_max_lod(void)
{
    reset();
    rsx_be_sampler_desc a = descriptor(), b = a;
    a.min_lod = b.min_lod = 0;
    a.max_lod = 1; b.max_lod = 257.0f / 256.0f;
    REQUIRE(vk_eng_sampler(&a) != vk_eng_sampler(&b));
    REQUIRE(create_calls == 2);
    return 0;
}
static int all_guest_lod_quantums(void)
{
    for (u32 i = 0; i < 4095; ++i) {
        reset();
        rsx_be_sampler_desc a = descriptor(), b = a;
        a.min_lod = (float)i / 256.0f; b.min_lod = (float)(i + 1) / 256.0f;
        REQUIRE(vk_eng_sampler(&a) != vk_eng_sampler(&b));
        reset(); a = descriptor(); b = a;
        a.min_lod = b.min_lod = 0;
        a.max_lod = (float)i / 256.0f; b.max_lod = (float)(i + 1) / 256.0f;
        REQUIRE(vk_eng_sampler(&a) != vk_eng_sampler(&b));
    }
    return 0;
}
static int every_filter_and_wrap_field(void)
{
    for (u32 field = 0; field < 7; ++field) {
        reset();
        rsx_be_sampler_desc a = descriptor(), b = a;
        switch (field) {
        case 0: b.min_linear = 0; break; case 1: b.mag_linear = 0; break;
        case 2: b.mip_linear = 0; break; case 3: b.mip_present = 0; break;
        case 4: b.wrap_s = 4; break; case 5: b.wrap_t = 4; break;
        case 6: b.wrap_r = 4; break;
        }
        REQUIRE(vk_eng_sampler(&a) != vk_eng_sampler(&b));
        REQUIRE(create_calls == 2);
    }
    return 0;
}
static int repeat_and_interleaving(void)
{
    reset();
    rsx_be_sampler_desc a = descriptor(), b = a;
    b.wrap_t = 4;
    VkSampler first = vk_eng_sampler(&a), second = vk_eng_sampler(&b);
    REQUIRE(first != second);
    REQUIRE(vk_eng_sampler(&a) == first && vk_eng_sampler(&b) == second);
    REQUIRE(create_calls == 2 && s_esamp_count == 2);
    return 0;
}
static int padding_is_not_identity(void)
{
    reset();
    rsx_be_sampler_desc a, b, value = descriptor();
    memset(&a, 0xA5, sizeof a); memset(&b, 0x5A, sizeof b);
#define COPY_FIELD(field) a.field = value.field; b.field = value.field
    COPY_FIELD(min_linear); COPY_FIELD(mag_linear); COPY_FIELD(mip_linear);
    COPY_FIELD(mip_present); COPY_FIELD(wrap_s); COPY_FIELD(wrap_t); COPY_FIELD(wrap_r);
    COPY_FIELD(min_lod); COPY_FIELD(max_lod);
#ifdef FOLKLORE_SAMPLER_ANISOTROPY_FIELDS
    COPY_FIELD(anisotropy_code); COPY_FIELD(vertex_sampler);
#endif
#undef COPY_FIELD
    REQUIRE(vk_eng_sampler(&a) == vk_eng_sampler(&b));
    REQUIRE(create_calls == 1 && s_esamp_count == 1);
    return 0;
}
static int caller_mutation_preserves_snapshot(void)
{
    reset();
    rsx_be_sampler_desc a = descriptor(), original = a;
    VkSampler first = vk_eng_sampler(&a);
    a.min_lod = 2;
    REQUIRE(vk_eng_sampler(&a) != first);
    REQUIRE(vk_eng_sampler(&original) == first);
    REQUIRE(create_calls == 2);
    return 0;
}
static int effective_lod_clamps_are_preserved(void)
{
    reset();
    rsx_be_sampler_desc a = descriptor();
    a.mip_present = 0;
    VkSampler first = vk_eng_sampler(&a);
    REQUIRE(first != s_vk.sampler);
    REQUIRE(recorded[0].minLod == a.min_lod && recorded[0].maxLod == a.min_lod);
    a.max_lod = 0.25f;
    REQUIRE(vk_eng_sampler(&a) == first);
    a.max_lod = 3;
    REQUIRE(vk_eng_sampler(&a) == first && create_calls == 1);
    reset(); a = descriptor(); a.max_lod = 0.5f;
    first = vk_eng_sampler(&a);
    REQUIRE(first != s_vk.sampler);
    REQUIRE(recorded[0].maxLod == a.min_lod);
    a.max_lod = 0.75f;
    REQUIRE(vk_eng_sampler(&a) == first);
    a.max_lod = 1;
    REQUIRE(vk_eng_sampler(&a) == first && create_calls == 1);
    return 0;
}
static int normalized_filter_and_wrap_aliases(void)
{
    reset();
    rsx_be_sampler_desc a = descriptor(), b = a;
    a.wrap_s = a.wrap_t = a.wrap_r = 3;
    VkSampler first = vk_eng_sampler(&a);
    const u8 aliases[] = {3, 5, 6, 7, 8};
    for (u32 i = 0; i < sizeof aliases / sizeof aliases[0]; ++i) {
        b = a; b.wrap_s = b.wrap_t = b.wrap_r = aliases[i];
        b.min_linear = 2; b.mag_linear = 7; b.mip_linear = 9;
        REQUIRE(vk_eng_sampler(&b) == first);
    }
    REQUIRE(create_calls == 1);
    return 0;
}
static int creation_failure_is_not_cached(void)
{
    reset();
    rsx_be_sampler_desc a = descriptor();
    fail_create = 1;
    REQUIRE(vk_eng_sampler(&a) == s_vk.sampler);
    REQUIRE(s_esamp_count == 0 && create_calls == 1);
    fail_create = 0;
    REQUIRE(vk_eng_sampler(&a) != s_vk.sampler);
    REQUIRE(s_esamp_count == 1 && create_calls == 2);
    return 0;
}
static int full_cache_retains_hits_and_fallback(void)
{
    reset();
    rsx_be_sampler_desc a = descriptor();
    VkSampler first = 0;
    for (u32 i = 0; i < VK_ENG_SAMP_CACHE; ++i) {
        a.min_lod = (float)i / 256.0f;
        VkSampler handle = vk_eng_sampler(&a);
        REQUIRE(handle != s_vk.sampler);
        if (!i) first = handle;
    }
    REQUIRE(s_esamp_count == VK_ENG_SAMP_CACHE && create_calls == VK_ENG_SAMP_CACHE);
    a.min_lod = 2;
    REQUIRE(vk_eng_sampler(&a) == s_vk.sampler);
    a.min_lod = 0;
    REQUIRE(vk_eng_sampler(&a) == first);
    REQUIRE(create_calls == VK_ENG_SAMP_CACHE);
    production_sampler_teardown();
    REQUIRE(destroy_calls == VK_ENG_SAMP_CACHE && s_esamp_count == 0);
    for (u32 i = 0; i < destroy_calls; ++i) REQUIRE(destroyed[i] == 100u + i);
    production_sampler_teardown();
    REQUIRE(destroy_calls == VK_ENG_SAMP_CACHE);
    return 0;
}
static int teardown_destroys_unique_successes_once(void)
{
    reset();
    rsx_be_sampler_desc a = descriptor(), original = a;
    VkSampler first = vk_eng_sampler(&a);
    REQUIRE(vk_eng_sampler(&a) == first);
    a.wrap_s = 4;
    REQUIRE(vk_eng_sampler(&a) != first);
    a.wrap_t = 4; fail_create = 1;
    REQUIRE(vk_eng_sampler(&a) == s_vk.sampler && s_esamp_count == 2);
    fail_create = 0;
    REQUIRE(vk_eng_sampler(&a) != s_vk.sampler && s_esamp_count == 3);
    production_sampler_teardown();
    REQUIRE(s_esamp_count == 0 && destroy_calls == 3);
    REQUIRE(destroyed[0] == 100 && destroyed[1] == 101 && destroyed[2] == 102);
    production_sampler_teardown();
    REQUIRE(destroy_calls == 3);
    REQUIRE(vk_eng_sampler(&original) != first && s_esamp_count == 1);
    production_sampler_teardown();
    REQUIRE(destroy_calls == 4 && destroyed[3] == 103);
    return 0;
}
int main(void)
{
    const struct { const char* name; int (*run)(void); } cases[] = {
        {"adjacent_min_lod", adjacent_min_lod}, {"adjacent_max_lod", adjacent_max_lod},
        {"all_guest_lod_quantums", all_guest_lod_quantums},
        {"every_filter_and_wrap_field", every_filter_and_wrap_field},
        {"repeat_and_interleaving", repeat_and_interleaving},
        {"padding_is_not_identity", padding_is_not_identity},
        {"caller_mutation_preserves_snapshot", caller_mutation_preserves_snapshot},
        {"effective_lod_clamps_are_preserved", effective_lod_clamps_are_preserved},
        {"creation_failure_is_not_cached", creation_failure_is_not_cached},
        {"full_cache_retains_hits_and_fallback", full_cache_retains_hits_and_fallback},
        {"normalized_filter_and_wrap_aliases", normalized_filter_and_wrap_aliases},
        {"teardown_destroys_unique_successes_once", teardown_destroys_unique_successes_once}
    };
    u32 failed = 0;
    for (u32 i = 0; i < sizeof cases / sizeof cases[0]; ++i) {
        int status = cases[i].run(); failed += status != 0;
        printf("case=%s outcome=%s\n", cases[i].name, status ? "FAIL" : "PASS");
    }
    printf("cases=%u failed=%u gpu=0 loader=0 renderer_init=0\n",
           (u32)(sizeof cases / sizeof cases[0]), failed);
    return failed ? 1 : 0;
}
