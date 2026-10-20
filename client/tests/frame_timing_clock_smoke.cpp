#include "frame_timing_probe.h"
int main() {
    FrameTimingProbe probe;
    if (!probe.active()) return 2;
    for (unsigned i = 1; i <= 20; ++i) {
        auto before = probe.tick();
        Sleep(10);
        probe.record(i, before, probe.tick());
    }
    return 0;
}
