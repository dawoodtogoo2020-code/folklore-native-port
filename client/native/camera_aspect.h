#pragma once
#include <cmath>
#include <cstdlib>
#include <atomic>
#include <cstdio>

// The guarded generated bridge calls this only at the proven world projection
// input. Its matrix is also copied into the game's CPU visibility path.
inline double folklore_camera_aspect_value(const char* text)
{
    if (!text || !*text || *text < '0' || *text > '9') return 0;
    char* end = nullptr;
    const double value = std::strtod(text, &end);
    return end && !*end && std::isfinite(value) &&
           value >= 16.0 / 9.0 && value <= 32.0 / 9.0 ? value : 0;
}

inline double folklore_camera_aspect_apply(double original, double requested)
{
    return std::isfinite(original) && std::isfinite(requested) &&
           requested >= 16.0 / 9.0 && requested <= 32.0 / 9.0 &&
           std::fabs(original - 16.0 / 9.0) <= 0.000001 ? requested : original;
}

inline double folklore_camera_aspect_override(double original,unsigned function=0)
{
    static const double requested = folklore_camera_aspect_value(
        std::getenv("PS3RECOMP_FOLKLORE_CAMERA_ASPECT"));
    const double result=folklore_camera_aspect_apply(original, requested);
    if(result!=original) {
        static std::atomic<bool> logged[3]{};
        const unsigned slot=function==0x00145AD4?0:function==0x00147D10?1:2;
        if(!logged[slot].exchange(true))
            std::fprintf(stderr,"[folklore camera] function=%08X aspect %.9g -> %.9g\n",function,original,result);
    }
    return result;
}
