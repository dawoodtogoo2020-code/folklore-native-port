#pragma once
#include <cstdio>
#include <cstdlib>
#include <cstring>

// Called only from the guarded main-loop site (scripts/prepare_frame_limiter.py).
// The guest waits until the frame's elapsed time, in 10 us units of its real
// 79.8 MHz timebase, exceeds the floor, sleeping folklore_frame_sleep_us() at a
// time, and then uses the measured elapsed time as the simulation delta.
//
// FOLKLORE_FRAME_RATE unset or "30": the original 3000 units (30 ms) and 10000 us.
// FOLKLORE_FRAME_RATE="60" (experimental): a 15 ms floor with 1 ms sleeps, so the
// game's own vsync flip wait sets a 16.67 ms cadence when the frame fits. The
// guest clock and the delta it measures are untouched.
struct FolkloreFrameLimit { int floor_units; int sleep_us; int rate; };

inline FolkloreFrameLimit folklore_frame_limit_parse(const char* text)
{
    if (text && std::strcmp(text, "60") == 0) return {1500, 1000, 60};
    return {3000, 10000, 30};
}

inline const FolkloreFrameLimit& folklore_frame_limit()
{
    static const FolkloreFrameLimit limit = [] {
        const char* text = std::getenv("FOLKLORE_FRAME_RATE");
        FolkloreFrameLimit l = folklore_frame_limit_parse(text);
        // Diagnostic, 60 only: FOLKLORE_FRAME_FLOOR_UNITS=1000..1600 moves the
        // floor (10 us units) to measure how much of the frame the host
        // renderer gets after the guest's flip. Players never set it (the
        // launcher drops FOLKLORE_* variables).
        const char* floor = std::getenv("FOLKLORE_FRAME_FLOOR_UNITS");
        if (l.rate == 60 && floor && *floor) {
            const int units = std::atoi(floor);
            if (units >= 1000 && units <= 1600) l.floor_units = units;
            else std::fprintf(stderr, "[folklore frame] ignoring FOLKLORE_FRAME_FLOOR_UNITS=%s\n", floor);
        }
        if (text && *text && l.rate != 60 && std::strcmp(text, "30") != 0)
            std::fprintf(stderr, "[folklore frame] unsupported FOLKLORE_FRAME_RATE=%s; original 30\n", text);
        std::fprintf(stderr, "[folklore frame] limiter floor %d x10us, sleep %d us (%d)\n",
                     l.floor_units, l.sleep_us, l.rate);
        return l;
    }();
    return limit;
}

inline int folklore_frame_floor_units() { return folklore_frame_limit().floor_units; }
inline int folklore_frame_sleep_us() { return folklore_frame_limit().sleep_us; }
