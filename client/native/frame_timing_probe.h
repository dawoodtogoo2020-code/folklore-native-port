#pragma once

/* Opt-in host-side timing only. No guest state or renderer commands change.
 * Successful-present completion intervals include the existing host limiter,
 * FIFO work, scheduling, presentation waits and enabled diagnostic overhead.
 * These are not GPU timestamps or scanout/display measurements.
 *
 * Clock source: QueryPerformanceCounter on Windows, CLOCK_MONOTONIC
 * nanoseconds (frequency 1e9) on Linux. Other platforms stay inactive. */
#include <cstdio>
#include <cstdlib>
#include <cstdint>
#include <algorithm>
#include <array>
#if defined(_WIN32)
#include <windows.h>
#elif defined(__linux__)
#include <time.h>
#endif

class FrameTimingProbe {
    std::FILE* file_ = nullptr;
    unsigned rows_ = 0;
    std::int64_t frequency_ = 0;

    static std::int64_t query_frequency() {
#if defined(_WIN32)
        LARGE_INTEGER frequency;
        return QueryPerformanceFrequency(&frequency) && frequency.QuadPart > 0 ? frequency.QuadPart : 0;
#elif defined(__linux__)
        timespec now;
        return clock_gettime(CLOCK_MONOTONIC, &now) == 0 ? 1000000000LL : 0;
#else
        return 0;
#endif
    }
    static const char* clock_name() {
#if defined(_WIN32)
        return "QPC";
#elif defined(__linux__)
        return "CLOCK_MONOTONIC";
#else
        return "unsupported";
#endif
    }
public:
    FrameTimingProbe() {
        const char* path = std::getenv("FOLKLORE_FRAME_TIMING_CSV");
        if (!path || !*path) return;
        const std::int64_t frequency = query_frequency();
        if (frequency <= 0) return;
        frequency_ = frequency;
        std::array<std::uint64_t, 10000> deltas{};
        for (auto& delta : deltas) {
            auto start = tick();
            delta = tick() - start;
        }
        std::sort(deltas.begin(), deltas.end());
        file_ = std::fopen(path, "wb");
        if (!file_) {
            std::fprintf(stderr, "[frame-timing] cannot open diagnostic CSV\n");
            return;
        }
        std::fprintf(file_, "present_number,present_begin_ticks,present_end_ticks,clock_frequency\n");
        std::fprintf(stderr, "[frame-timing] host %s frequency=%lld calibration_pairs=10000 pair_ticks_min=%llu median=%llu p99=%llu; CPU/present timing only, not GPU time\n",
            clock_name(),
            static_cast<long long>(frequency_),
            static_cast<unsigned long long>(deltas.front()),
            static_cast<unsigned long long>(deltas[5000]),
            static_cast<unsigned long long>(deltas[9899]));
    }
    ~FrameTimingProbe() { if (file_) std::fclose(file_); }
    FrameTimingProbe(const FrameTimingProbe&) = delete;
    FrameTimingProbe& operator=(const FrameTimingProbe&) = delete;
    std::uint64_t tick() const {
#if defined(_WIN32)
        LARGE_INTEGER value;
        return QueryPerformanceCounter(&value) ? static_cast<std::uint64_t>(value.QuadPart) : 0;
#elif defined(__linux__)
        timespec now;
        if (clock_gettime(CLOCK_MONOTONIC, &now) != 0) return 0;
        return static_cast<std::uint64_t>(now.tv_sec) * 1000000000ULL + static_cast<std::uint64_t>(now.tv_nsec);
#else
        return 0;
#endif
    }
    bool active() const { return file_ != nullptr; }
    void record(std::uint64_t number, std::uint64_t begin, std::uint64_t end) {
        if (!file_) return;
        std::fprintf(file_, "%llu,%llu,%llu,%lld\n",
            static_cast<unsigned long long>(number), static_cast<unsigned long long>(begin),
            static_cast<unsigned long long>(end), static_cast<long long>(frequency_));
        if (++rows_ % 60 == 0) std::fflush(file_);
    }
};
