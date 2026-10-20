#pragma once
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstdint>

/* Opt-in host wall-time attribution, owned by the graphics-service thread.
 * FIFO time includes any waits inside the renderer; it is not GPU time.
 * Pending wait is measured before present and excludes present-call duration.
 * Rows account for work since the preceding successful presentation. */
class GraphicsServiceProbe {
    std::FILE* file_ = nullptr;
    std::uint64_t fifo_ns_ = 0, fifo_calls_ = 0, pending_since_ = 0, rows_ = 0;
public:
    GraphicsServiceProbe() {
        const char* path = std::getenv("FOLKLORE_GRAPHICS_TIMING_CSV");
        if (!path || !*path) return;
        file_ = std::fopen(path, "wb");
        if (file_) std::fprintf(file_, "present_number,end_ns,fifo_ns,fifo_calls,pending_wait_ns,present_ns\n");
        else std::fprintf(stderr, "[graphics-timing] cannot open diagnostic CSV\n");
    }
    ~GraphicsServiceProbe() { if (file_) std::fclose(file_); }
    GraphicsServiceProbe(const GraphicsServiceProbe&) = delete;
    GraphicsServiceProbe& operator=(const GraphicsServiceProbe&) = delete;
    std::uint64_t tick() const {
        if (!file_) return 0;
        return std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::steady_clock::now().time_since_epoch()).count();
    }
    void fifo(std::uint64_t begin, std::uint64_t end, bool pending) {
        if (!file_) return;
        fifo_ns_ += end - begin; ++fifo_calls_;
        if (pending && !pending_since_) pending_since_ = end;
    }
    void presented(std::uint64_t number, std::uint64_t begin, std::uint64_t end) {
        if (!file_) return;
        std::fprintf(file_, "%llu,%llu,%llu,%llu,%llu,%llu\n",
            (unsigned long long)number, (unsigned long long)end,
            (unsigned long long)fifo_ns_, (unsigned long long)fifo_calls_,
            (unsigned long long)(pending_since_ ? begin - pending_since_ : 0),
            (unsigned long long)(end - begin));
        fifo_ns_ = fifo_calls_ = pending_since_ = 0;
        if (++rows_ % 60 == 0) std::fflush(file_);
    }
};
