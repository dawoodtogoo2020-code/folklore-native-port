# Open engine issues and fix plans

Updated 20 October 2026.

These issues are in the private Windows development runtime. That runtime is not in this repository, so nothing below has been applied or tested yet. The causes listed are hypotheses based on how PS3-era games and recompiled runtimes usually behave. Confirm each one with a measurement before changing code.

| Issue | Status | Section |
| --- | --- | --- |
| Coat, tie and hair move strangely at 60 FPS | Investigating | [1](#1-coat-tie-and-hair-at-60-fps) |
| Audio and music hitches | Investigating | [2](#2-audio-continuity) |
| Hybrid path tracing plus DLSS slower than path tracing alone | Investigating | [3](#3-path-tracing-and-dlss-performance) |

## 1. Coat, tie and hair at 60 FPS

Folklore was built for 30 FPS. Garments and hair in games of this era are usually small spring or Verlet chains that update once per frame, with constants tuned for a 1/30 second step. Running at 60 FPS breaks that tuning in one or more of these ways:

| Likely cause | What it looks like |
| --- | --- |
| Update runs every frame but assumes a 1/30 s step | Twice as fast, twitchy, overreacts to movement |
| Damping or stiffness applied per step, such as `v *= 0.9` | Too stiff and laggy, or too bouncy |
| Fixed number of constraint iterations per frame | Stiffer than at 30 FPS |
| Verlet with a changing step size | Jitter or popping on uneven frames |
| Garments read the previous frame's bone positions | Clipping into the body, lag on fast turns |
| Wind or sway driven by a frame counter | Sways twice as fast |

### Fix plan

The safest fix for a recompilation keeps the original tuning: **run the garment and hair update at a fixed 30 Hz and interpolate between results.**

1. Find the update functions (see below).
2. Wrap them in a fixed-step accumulator. Add the real frame time each frame and call the original function only when 1/30 s has built up. Allow at most 2 catch-up steps per frame.
3. Keep the previous and current output bone transforms. For rendering, blend them with `alpha = accumulator / (1/30)`: lerp positions and slerp rotations.

Fallbacks, if interpolation is not practical:

- Rescale per-step constants, for example `damping_60 = damping_30 ^ 0.5`. This is fragile with constraint solvers.
- If the code uses Verlet with a variable step, use the time-corrected form `x + (x - x_prev) * (dt / dt_prev) + a * dt * dt`.

**Same class of bug:** the Sixaxis soul-capture gesture is probably detected from per-frame motion differences. At 60 FPS each difference is about half as large, so fixed thresholds are harder to reach. Check that code the same way.

### Finding the code

Run the scanner on your own decrypted executable:

```sh
python scripts/frame_constants.py --elf "C:\path\to\PS3_GAME\USRDIR\EBOOT.elf"
```

It lists 30/60 FPS constants (1/30 s, 1/60 s, 30, 60 and the NTSC 29.97/59.94 variants) and the instructions that load them. Each load is grouped under an estimated `func_XXXXXXXX` name, the same naming the recompiler uses. Add `--json` to save the results.

Then, in the runtime:

- `1/30 s` and `1/60 s` hits are the strongest leads. Plain `30` and `60` are often angles or field of view.
- Count calls per frame for each listed function at 30 and 60 FPS.
- In a development build, temporarily override one constant at a time and watch the coat. A change in garment motion points to the right function.
- Values computed at run time, such as from a vsync count, will not show up. Also log the game's own frame delta if you can find it.

### How to confirm

Record the same idle pose, run cycle and cutscene at 30 and 60 FPS. Garment and hair motion should match in speed and swing size.

## 2. Audio continuity

How PS3 audio reaches the speakers: the game opens a cellAudio port at 48 kHz that plays blocks of 256 samples, one block every 5.33 ms. The runtime signals an event when a block is used, and the game's audio thread mixes the next block. Music streams from disc in the background.

Likely causes, most common first:

1. **Coarse timers.** If audio events are paced with sleep calls, the Windows default timer resolution of 15.6 ms delivers them in bursts and the buffer runs dry.
2. **Audio tied to the frame.** Any wait on rendering, shader compiles, acceleration structure builds or DLSS also stalls audio.
3. **Clock drift.** The output device does not run on exactly the same 48 kHz clock. Without rate control, the buffer slowly fills or drains, then glitches at regular intervals.
4. **Buffer too small.** Under about 20 ms cannot ride out one slow frame.
5. **Blocking disc reads.** Music skips when stream reads wait behind other file work.
6. **Frame-counted audio logic.** Fades or envelopes timed in frames run twice as fast at 60 FPS. The scanner above can help find these too.

### Fix plan

1. Let the audio device set the pace. Use WASAPI event-driven mode, or the current backend's pull callback, on a dedicated thread registered with MMCSS as "Pro Audio".
2. Place a lock-free single-producer, single-consumer ring buffer between the cellAudio emulation and the device.
3. Signal the game's audio event once for each 256-sample block the device actually consumes, never from a timer or the frame loop.
4. Start with 30 to 40 ms of buffered audio (6 to 8 blocks). Lower it only after the underrun counter stays at zero.
5. Add gentle rate control: nudge the resampling ratio by up to 0.5% based on ring buffer fill level, instead of dropping or repeating samples.
6. Move stream file reads off the audio thread and read ahead.
7. Show counters in a log or overlay: underruns, overruns, buffer fill and the longest gap between audio events.

### How to confirm

Play 30 minutes across menus, field, cutscenes and combat with the counters on, then repeat with path tracing and DLSS on. Target: zero underruns, and no gap between audio events longer than 2 blocks (10.7 ms). Audio that is truly consistent means these counters stay clean across the whole campaign, which needs long play tests, not one scene.

## 3. Path tracing and DLSS performance

Recorded on an RTX 5080 at 2560x1440 output:

| Mode | FPS | Frame time |
| --- | --- | --- |
| Hybrid path tracing only | 26.27 | 38.1 ms |
| Hybrid path tracing plus DLSS Quality | 18.25 | 54.8 ms |

These were different scenes, so they are not a controlled comparison. Even so, DLSS Quality renders at 1707x960, about 44% of the output pixels, so it should be faster. DLSS Super Resolution itself usually costs about 1 to 2 ms at this resolution on this class of GPU. Roughly 15 ms per frame is coming from somewhere else.

The gap is about 16.7 ms, almost exactly one 60 Hz refresh. That hints at a missed present or a sync wait, but it may be coincidence.

### What to check, highest expected payoff first

1. **Tracing still runs at output resolution.** With DLSS Quality on, the trace dispatch, G-buffer, motion vectors and denoiser should all be 1707x960.
2. **CPU and GPU sync stalls.** Look for a fence wait, queue idle wait or readback every frame around the DLSS call. Also check the present mode and frame latency settings.
3. **Two denoisers.** Running a custom denoiser and then DLSS Super Resolution doubles the work. DLSS Ray Reconstruction denoises and upscales in one pass. It needs extra guide buffers such as diffuse and specular albedo, normals, roughness and, ideally, specular hit distance.
4. **Missing camera jitter.** DLSS needs a sub-pixel jitter (Halton 2,3 sequence) in the game's projection, with the same offsets passed to DLSS.
5. **Acceleration structure rebuilds.** In an emulated RSX pipeline, geometry arrives as draw calls every frame. Rebuilding every BLAS every frame is often the biggest single cost. Cache static BLAS by vertex buffer hash, refit skinned characters instead of rebuilding them, and compact BLAS after building.
6. **Foliage.** The Faery Realms are full of alpha-tested foliage. Use opacity micromaps where possible, and mark opaque geometry opaque so it skips any-hit shaders.
7. **Shader Execution Reordering** for the bounce shader on RTX 40 and 50 series cards, plus Russian roulette after the first bounce.
8. **Per-frame copies and format conversions** at output resolution, such as depth conversion. Reuse resources and convert once.
9. **Development DLSS runtime.** Benchmark with the release runtime. The development runtime shows a watermark and is not intended for performance testing.

### How to measure

Add GPU timestamp queries around each pass: acceleration structure builds, raster G-buffer, trace, denoise, DLSS, post-processing and present. Use the same save, camera position and random seed for every run, capture 60 seconds, and record average FPS, 1% low FPS and milliseconds per pass. Change one thing at a time and keep a before and after table.
