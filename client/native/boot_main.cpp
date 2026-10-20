/* Private title bring-up adapter. Console diagnostics only; no rendering or
 * gameplay claim. Runtime API wiring follows MIT ps3recomp templates/project.
 * Copyright (c) 2026 Ned Heller / sp00nznet (upstream adapter basis).
 */
#include "ppu_recomp.h"
#include "win32_compat.h"
#include "ps3emu/guest_call.h"
#include "cellMsgDialog.h"
#include "cellSysutil.h"
#include "fs_stat_probe.h"
#include "fs_open_probe.h"
#include "fs_seek_probe.h"
#include "fs_unlink_probe.h"
#include "fs_aio_probe.h"
#include "ppu_fs_memory.h"
#include "folklore_process_param.h"
#include "graphics_options.h"
#include "graphics_settings.h"
#include "ps3emu/strict_boot.h"
#include "spurs_native_registry.h"
#include "ps3emu/error_codes.h"
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <functional>
#include <atomic>
#include <chrono>
#include <thread>
#include "frame_timing_probe.h"
#include "graphics_service_probe.h"
#ifdef _WIN32
#include <io.h>
#include "msgdialog_host_win32.h"
#include "native_fault_win32.h"
#elif defined(__ANDROID__)
#include "msgdialog_host_android.h"
// SDL supplies the Java/native entry bridge; its header declares SDL_main with
// C linkage and maps the existing main definition to that entry point.
#include <SDL_main.h>
#else
#include "msgdialog_host_linux.h"
#endif
#ifdef PS3RECOMP_RSX_VULKAN
#include "rsx_vulkan_backend.h"
#endif
#ifndef _WIN32
#include <sys/mman.h>
#endif

extern "C" {
int32_t ps3_savedata_set_root(const char* host_root);
uint8_t* vm_base = nullptr;
uint32_t g_ps3_sdk_version = folklore::kUnknownSdk;
int32_t ppu_fs_aio_shutdown(void);
int folklore_check_spurs_snapshot(const char*,const char*);
extern uint32_t ppu_vm_size;
extern const char* ppu_vfs_root;
extern ps3_guest_caller_fn g_ps3_guest_caller;
uint32_t ppu_load_elf(const char*);
void ppu_recomp_register(void);
#ifdef FOLKLORE_PRIVATE_NATIVE_JOBS
int folklore_register_native_jobs(void);
#endif
void ppu_hle_init(void);
void ppu_sysprx_register(void);
void ppu_fs_register(void);
void lv2_init_syscalls(void);
void lv2_syscall(ppu_context*);
int ppu_run(uint32_t, uint32_t);
void cellGame_init_from_paramsfo(const char*);
const char* cellGame_get_title_id(void);
uint64_t ppu_guest_call(uint32_t, uint64_t, uint64_t, uint64_t, uint64_t,
                       uint64_t, uint64_t, uint64_t, uint64_t);
void gcm_set_host_context_callback(int32_t (*)(uint32_t, uint32_t, uint32_t));
void mfc_strict_enable_verified_page_cache(void);
void ps3_hle_call(uint32_t,ppu_context*);
static std::atomic<unsigned> presented{0};
unsigned ppu_boot_frames_presented(void) { return presented.load(); }
}
static bool trace_startup = false;
static bool vulkan_graphics = false;
static folklore::GraphicsOptions graphics_options;
// C++ linkage in ppu_loader.cpp; diagnostic sampling is opt-in.
#ifdef _WIN32
void ps3_sampler_start(void);
#endif
// Called only by explicitly instrumented private source, never to change state.
extern "C" void folklore_trace_point(ppu_context* ctx, uint32_t point)
{
    if (!trace_startup) return;
    static std::atomic<unsigned> records{0};
    if (records.fetch_add(1) >= 96) return;
    char line[512];
    int size = std::snprintf(line, sizeof(line),
        "[startup-point] pc=%08X tid=%llu lr=%08llX r3=%016llX r4=%016llX r5=%016llX r6=%016llX r29=%016llX r30=%016llX r31=%016llX cr=%08X\n",
        point, static_cast<unsigned long long>(ctx->thread_id), static_cast<unsigned long long>(ctx->lr),
        static_cast<unsigned long long>(ctx->gpr[3]), static_cast<unsigned long long>(ctx->gpr[4]),
        static_cast<unsigned long long>(ctx->gpr[5]), static_cast<unsigned long long>(ctx->gpr[6]),
        static_cast<unsigned long long>(ctx->gpr[29]), static_cast<unsigned long long>(ctx->gpr[30]),
        static_cast<unsigned long long>(ctx->gpr[31]), ctx->cr);
    if (size > 0 && size < int(sizeof(line))) {
#ifdef _WIN32
        (void)_write(_fileno(stderr), line, unsigned(size));
#else
        std::fwrite(line, 1, size, stderr);
#endif
    }
}

extern "C" {
#ifdef _WIN32
int rsx_d3d12_backend_init(uint32_t, uint32_t, const char*);
void rsx_d3d12_backend_present(void);
uint64_t rsx_d3d12_backend_successful_presents(void);
void rsx_d3d12_backend_shutdown(void);
int rsx_d3d12_backend_pump_messages(void);
#endif
void cellGcm_rsx_process_fifo(void);
int cellGcm_take_flip_pending(void);
void cellGcmTickVBlank(void);
void cellGcmTickFlip(void);
void cellGcmSetHostPresentationSuspended(int);
void cellGcmSetHostRendererPhase(uint32_t);
}

// Consume real guest FIFO work. Complete a requested flip only after handing
// that batch to the backend at the modeled display boundary.
struct DialogCapture { u64 generation=0; unsigned count=0; };
static void dialog_presenter_tick(DialogCapture& captured)
{
    if(!folklore_dialog_host_tick()) {
        std::fprintf(stderr,"[folklore] system dialog presenter failed\n");
        std::fflush(nullptr);std::_Exit(89);
    }
    Ps3MsgDialogHostState dialog_state{};
#ifdef _WIN32
    if(trace_startup && captured.count<4 && folklore_dialog_host_window() &&
       folklore_dialog_host_displayed(&dialog_state) &&
       dialog_state.generation!=captured.generation) {
        extern int folklore_capture_dialog_host_window(const char*);
        char capture_path[64];
        if(!captured.count)std::strcpy(capture_path,"system-dialog.bmp");
        else std::snprintf(capture_path,sizeof(capture_path),"system-dialog-%llu.bmp",
            static_cast<unsigned long long>(dialog_state.generation));
        const int result=folklore_capture_dialog_host_window(capture_path);
        if(result<0) {
            std::fprintf(stderr,"[folklore] system dialog capture failed\n");
            std::fflush(nullptr);std::_Exit(89);
        }
        if(result>0) {
            captured.generation=dialog_state.generation;++captured.count;
            std::fprintf(stderr,"[folklore] captured actual system dialog in %s\n",capture_path);
        }
    }
#else
    (void)captured;
    (void)dialog_state;
#endif
}

// CPU diagnostic service for the real platform presenter. It creates no RSX
// backend, services no FIFO, and does not report display/flip completion.
static void native_dialog_service(std::atomic<int>& ready,std::atomic<bool>& stop)
{
    if(!folklore_dialog_host_start()) {ready.store(-1);return;}
    std::fprintf(stderr,"[folklore] native system dialog service active; RSX renderer disabled\n");
    DialogCapture captured;
    ready.store(1);
    while(!stop.load()) {
#ifdef _WIN32
        MSG message{};
        while(PeekMessageW(&message,nullptr,0,0,PM_REMOVE)) {
            if(message.message==WM_QUIT){std::fflush(nullptr);std::_Exit(88);}
            TranslateMessage(&message);DispatchMessageW(&message);
        }
#endif
        dialog_presenter_tick(captured);
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    folklore_dialog_host_stop();
}

#ifdef _WIN32
extern "C" int lv2_precise_wait_us(uint64_t usec);   /* SDK runtime/syscalls/sys_timer.c */
#endif

/* Between passes the render loop used to sleep_for(1 ms), which lasts 1-2 ms
 * at the usual 1 ms timer resolution: it polled the FIFO that rarely while the
 * guest was still writing a frame (and the guest, waiting for the ring to
 * drain, waited on it), and presented a pending flip up to 2 ms after its
 * tick. It now polls every 250 us and, holding a flip, wakes at the tick
 * (at most 1 ms later, so the window keeps pumping). PS3RECOMP_PRECISE_WAITS=0
 * restores the old sleep. */
static void render_loop_wait(bool pending, std::chrono::steady_clock::time_point next)
{
#ifdef _WIN32
    long long us = 250;
    if (pending) {
        us = std::chrono::duration_cast<std::chrono::microseconds>(next - std::chrono::steady_clock::now()).count();
        if (us < 50) us = 50;
        if (us > 1000) us = 1000;
    }
    if (lv2_precise_wait_us((uint64_t)us)) return;
#else
    (void)pending; (void)next;
#endif
    std::this_thread::sleep_for(std::chrono::milliseconds(1));
}

static void graphics_service(std::atomic<int>& ready, std::atomic<bool>& stop)
{
    cellGcmSetHostPresentationSuspended(0);
#ifdef _WIN32
    auto init = rsx_d3d12_backend_init;
    auto shutdown = rsx_d3d12_backend_shutdown;
    auto pump = rsx_d3d12_backend_pump_messages;
    auto present = rsx_d3d12_backend_present;
    auto successful = rsx_d3d12_backend_successful_presents;
    const char* backend = "D3D12";
#else
    int (*init)(uint32_t,uint32_t,const char*) = nullptr;
    void (*shutdown)(void) = nullptr;
    int (*pump)(void) = nullptr;
    void (*present)(void) = nullptr;
    uint64_t (*successful)(void) = nullptr;
    const char* backend = "unavailable";
#endif
#ifdef PS3RECOMP_RSX_VULKAN
    if (vulkan_graphics) {
        init = rsx_vulkan_backend_init;
        shutdown = rsx_vulkan_backend_shutdown;
        pump = rsx_vulkan_backend_pump_messages;
        present = rsx_vulkan_backend_present;
        successful = rsx_vulkan_backend_successful_presents;
        backend = "Vulkan";
    }
#endif
    if (!init || init(graphics_options.width, graphics_options.height, "Folklore native diagnostic") != 0) {
        std::fprintf(stderr, "[folklore] %s initialization failed\n", backend);
        ready.store(-1);
        return;
    }
#ifdef PS3RECOMP_RSX_VULKAN
    const char* window_request = std::getenv("PS3RECOMP_VK_WINDOW");
    const bool offscreen_diagnostic = window_request && !std::strcmp(window_request, "0");
    if (vulkan_graphics && !offscreen_diagnostic && !rsx_vulkan_backend_window_active()) {
        std::fprintf(stderr, "[folklore] Vulkan window unavailable\n");
        shutdown(); ready.store(-1); return;
    }
    if (vulkan_graphics && offscreen_diagnostic) {
        successful = rsx_vulkan_backend_successful_offscreen_frames;
        std::fprintf(stderr, "[folklore] offscreen diagnostic active; no gameplay preview window\n");
    }
#endif
    if(std::getenv("PS3RECOMP_RAY_PREWARM_ONLY")){
        shutdown();ready.store(2);return;
    }
    if(!folklore_dialog_host_start()) {
        std::fprintf(stderr,"[folklore] system dialog presenter initialization failed\n");
        shutdown();ready.store(-1);return;
    }
    FrameTimingProbe frame_timing;
    GraphicsServiceProbe service_timing;
    ready.store(1);
    auto next = std::chrono::steady_clock::now();
    constexpr auto period = std::chrono::nanoseconds(16666667);
    bool pending = false;
    DialogCapture captured;
    while (!stop.load()) {
        cellGcmSetHostRendererPhase(1);
        dialog_presenter_tick(captured);
        cellGcmSetHostRendererPhase(2);
        if (pump() != 0) {
            std::fprintf(stderr, "[folklore] diagnostic graphics window closed\n");
            std::fflush(nullptr);
            std::_Exit(88);
        }
        if (!pending) {
            cellGcmSetHostRendererPhase(3);
            const auto fifo_begin = service_timing.tick();
            cellGcm_rsx_process_fifo();
            pending = cellGcm_take_flip_pending() != 0;
            service_timing.fifo(fifo_begin, service_timing.tick(), pending);
        }
        auto now = std::chrono::steady_clock::now();
        if (now >= next) {
            cellGcmSetHostRendererPhase(5);
            cellGcmTickVBlank();
            if (pending) {
                uint64_t before = successful();
                cellGcmSetHostRendererPhase(4);
                const auto present_begin = frame_timing.active() ? frame_timing.tick() : 0;
                const auto service_present_begin = service_timing.tick();
                present();
                const auto service_present_end = service_timing.tick();
                const auto present_end = frame_timing.active() ? frame_timing.tick() : 0;
                cellGcmSetHostRendererPhase(5);
                uint64_t after = successful();
                bool deferred = false;
#ifdef PS3RECOMP_RSX_VULKAN
                deferred = vulkan_graphics && after == before &&
                    rsx_vulkan_backend_present_deferred();
#endif
                cellGcmSetHostPresentationSuspended(deferred ? 1 : 0);
                if (!deferred && after != before + 1) {
                    std::fprintf(stderr, "[strict-boot] FATAL category=rsx-flip-without-present exit=86\n");
                    std::fflush(nullptr);
                    std::_Exit(86);
                }
                if (!deferred) {
                    service_timing.presented(after, service_present_begin, service_present_end);
                    frame_timing.record(after, present_begin, present_end);
                    unsigned count = presented.fetch_add(1) + 1;
                    if (count <= 4) {
#ifdef PS3RECOMP_RSX_VULKAN
                        if (vulkan_graphics && offscreen_diagnostic)
                            std::fprintf(stderr, "[folklore] accepted guest flip %u; offscreen GPU readback succeeded\n", count);
                        else
#endif
                            std::fprintf(stderr, "[folklore] accepted guest flip %u; %s window present succeeded\n", count, backend);
                    }
                    cellGcmTickFlip();
                    pending = false;
                }
            }
            next += period;
            if (now - next > period * 4) next = now + period;
        }
        cellGcmSetHostRendererPhase(6);
        render_loop_wait(pending, next);
    }
    cellGcmSetHostRendererPhase(0);
    cellGcmSetHostPresentationSuspended(0);
    folklore_dialog_host_stop();
    shutdown();
}

static constexpr uint32_t VM_LIMIT = 0xE0000000u;

#ifdef _WIN32
static int prepare_guest_page(uint32_t ea)
{
    if (!vm_base || ea < 0x10000 || ea >= VM_LIMIT) return 0;
    MEMORY_BASIC_INFORMATION region{};
    const uintptr_t at = reinterpret_cast<uintptr_t>(vm_base) + ea;
    if (!VirtualQuery(reinterpret_cast<void*>(at), &region, sizeof region) ||
        region.AllocationBase != vm_base || region.State != MEM_RESERVE) return 0;
    return VirtualAlloc(reinterpret_cast<void*>(at & ~uintptr_t(0xFFFF)),
                        0x10000, MEM_COMMIT, PAGE_READWRITE) != nullptr;
}

static LONG CALLBACK commit_guest_page(PEXCEPTION_POINTERS ep)
{
    if (ep->ExceptionRecord->ExceptionCode != EXCEPTION_ACCESS_VIOLATION)
        return EXCEPTION_CONTINUE_SEARCH;
    uintptr_t at = ep->ExceptionRecord->ExceptionInformation[1];
    uintptr_t base = reinterpret_cast<uintptr_t>(vm_base);
    if (vm_base && at >= base + 0x10000 && at < base + VM_LIMIT) {
        ULONG_PTR kind = ep->ExceptionRecord->ExceptionInformation[0];
        if ((kind == 0 || kind == 1) && prepare_guest_page(uint32_t(at - base)))
            return EXCEPTION_CONTINUE_EXECUTION;
        std::fprintf(stderr, "[folklore] fatal guest-memory fault address=0x%08llX kind=%llu\n",
                     static_cast<unsigned long long>(at - base),
                     static_cast<unsigned long long>(kind));
        std::fflush(nullptr);
        std::_Exit(86);
    }
    return EXCEPTION_CONTINUE_SEARCH;
}
#endif

static void environment(const char* key, const char* value)
{
#ifdef _WIN32
    if (_putenv_s(key, value)) std::exit(2);
#else
    if (setenv(key, value, 1)) std::exit(2);
#endif
}

static void guest_callback(uint32_t opd, uint64_t a0, uint64_t a1,
                           uint64_t a2, uint64_t a3, uint64_t a4,
                           uint64_t a5, uint64_t a6, uint64_t a7)
{
#ifdef _WIN32
    const bool trace = trace_startup && opd == 0x00553AD0u;
    auto record = [&](const char* phase) {
        uint8_t raw[4] = {};
        SIZE_T got = 0;
        const bool valid = a0 <= VM_LIMIT - 4 &&
            ReadProcessMemory(GetCurrentProcess(), vm_base + a0, raw, 4, &got) && got == 4;
        uint32_t result = (uint32_t(raw[0]) << 24) | (uint32_t(raw[1]) << 16) |
                          (uint32_t(raw[2]) << 8) | raw[3];
        char line[256];
        int size = std::snprintf(line, sizeof(line),
            "[startup-trace] callback=%08X phase=%s cb=%08llX get=%08llX set=%08llX result_valid=%u result=%08X\n",
            opd, phase, static_cast<unsigned long long>(a0), static_cast<unsigned long long>(a1),
            static_cast<unsigned long long>(a2), unsigned(valid), result);
        if (size > 0 && size < int(sizeof(line))) (void)_write(_fileno(stderr), line, unsigned(size));
    };
    if (trace) record("before");
#endif
    ppu_guest_call(opd, a0, a1, a2, a3, a4, a5, a6, a7);
#ifdef _WIN32
    if (trace) record("after");
#endif
}

// Uses synthetic VM data and the real import dispatcher. No ELF or game runs.
static int check_msgdialog_abi()
{
    uint8_t memory[0x20000] = {};
    vm_base=memory;ppu_vm_size=sizeof(memory);
    environment("PS3_STRICT_BOOT","1");
    std::strcpy(reinterpret_cast<char*>(memory+0x10000),"Synthetic ABI notice");
    ppu_hle_init();
    if(cellMsgDialogOpen2(1,reinterpret_cast<const char*>(0x10000),nullptr,nullptr,nullptr))return 91;
    ppu_context ctx = {};
    ctx.gpr[1]=0x18000;ctx.gpr[2]=0x11000;
    ctx.gpr[3]=0xDEADBEEF;ctx.fpr[1]=200.25;
    ps3_hle_call(0x20543730u,&ctx);
    if(ctx.gpr[3]!=0)return 92;
    cellSysutilCheckCallback();
    if(cellMsgDialogOpen2(1,reinterpret_cast<const char*>(0x10000),nullptr,nullptr,nullptr)!=(s32)0x8002B105u)return 93;
    std::this_thread::sleep_for(std::chrono::milliseconds(230));
    cellSysutilCheckCallback();
    if(cellMsgDialogOpen2(1,reinterpret_cast<const char*>(0x10000),nullptr,nullptr,nullptr))return 94;
    ctx.gpr[3]=0xDEADBEEF;ctx.fpr[1]=-1.0;
    ps3_hle_call(0x20543730u,&ctx);
    if(ctx.gpr[3]!=0 || cellMsgDialogClose(0)!=(s32)0x8002B302u)return 95;
    cellSysutilCheckCallback();
    if(ps3_msgdialog_host_enable(1))return 96;
    ctx.gpr[3]=0x80010006u;ctx.gpr[4]=0;ctx.gpr[5]=0x01234567u;ctx.gpr[6]=0;
    ps3_hle_call(0x3E22CB4Bu,&ctx);
    Ps3MsgDialogHostState state{};
    if(ctx.gpr[3]!=0 || !ps3_msgdialog_host_snapshot(&state) || !state.open ||
       state.type!=CELL_MSGDIALOG_TYPE_BUTTON_TYPE_OK || !std::strstr(state.message,"80010006"))return 97;
    ctx.gpr[3]=0xDEADBEEF;
    ps3_hle_call(0x3E22CB4Bu,&ctx);
    if(ctx.gpr[3]!=0xFFFFFFFF8002B105ull)return 98;
    if(ps3_msgdialog_host_shutdown())return 99;
    std::puts("message-dialog f1 NID dispatch, fractional delay and signed delay passed; no game executed");
    std::puts("error-dialog actual NID dispatch, copied message and sign-extended busy passed");
    return 0;
}

static bool bootstrap_process_param(const char* path, folklore::ProcessParam& param)
{
    const auto status=folklore::read_process_param(path,param);
    if (status!=folklore::ProcessParamStatus::ok && status!=folklore::ProcessParamStatus::absent) {
        ps3_strict_boot_guard("process-param-invalid", "reason=%s",folklore::process_param_status_name(status));
        return false;
    }
    const char* override_sdk=std::getenv("PS3_SDK_VERSION");
    if (override_sdk && *override_sdk) {
        ps3_strict_boot_guard("process-param-sdk-override", "inherited SDK override conflicts with original ELF metadata");
        return false;
    }
    g_ps3_sdk_version=param.sdk_version;
    std::fprintf(stderr,"[process-param] source=%s sdk=0x%08X version=0x%08X size=%u offset=0x%llX guest=0x%llX\n",
        param.has_param?"original-elf":"absent-unknown",param.sdk_version,param.version,param.size,
        static_cast<unsigned long long>(param.file_offset),static_cast<unsigned long long>(param.guest_vaddr));
    return true;
}

// Metadata read plus the real registered SDK syscall; no ELF loading/game entry.
static int check_process_param(const char* path)
{
    folklore::ProcessParam param;
    if (!bootstrap_process_param(path,param)) return 86;
    vm_base=static_cast<uint8_t*>(std::calloc(1,0x20000));
    if (!vm_base) return 3;
    ppu_vm_size=0x20000;
    ppu_hle_init();
    lv2_init_syscalls();
    ppu_context ctx{};
    ctx.gpr[11]=25;ctx.gpr[3]=0;ctx.gpr[4]=0x10000;
    lv2_syscall(&ctx);
    const uint64_t result=ctx.gpr[3];
    const uint32_t queried=vm_read32(0x10000);
    ctx.gpr[11]=25;ctx.gpr[4]=0;
    lv2_syscall(&ctx);
    const bool fault=ctx.gpr[3]==uint64_t(int64_t(int32_t(CELL_EFAULT)));
    std::free(vm_base);vm_base=nullptr;ppu_vm_size=0;
    if (result || queried!=param.sdk_version || !fault) {
        std::fprintf(stderr,"[process-param-probe] failed result=0x%llX expected=0x%08X queried=0x%08X null_fault=%u\n",
            static_cast<unsigned long long>(result),param.sdk_version,queried,unsigned(fault));
        return 1;
    }
    std::fprintf(stderr,"[process-param-probe] actual syscall25 SDK=0x%08X and sign-extended null EFAULT passed; no guest entry\n",queried);
    return 0;
}

int main(int argc, char** argv)
{
#ifdef __ANDROID__
    extern bool folklore_android_start_log();
    if(!folklore_android_start_log())return 90;
#endif
    // The upstream sampler also starts a legacy guest-memory poke worker.
    // Refuse its trigger before creating any runtime threads.
    if (std::getenv("PS3_SAMPLE") && std::getenv("PPU_POKE_ADDR")) {
        std::fprintf(stderr, "[folklore] sampling refuses PPU_POKE_ADDR; guest memory must remain unmodified\n");
        return 86;
    }
#ifndef _WIN32
    if (std::getenv("PS3_SAMPLE")) {
        std::fprintf(stderr, "[folklore] PS3_SAMPLE requires the Windows thread sampler\n");
        return 86;
    }
#endif
#ifdef _WIN32
    ppu_fs_set_guest_page_preparer(prepare_guest_page);
#endif
    if(argc==3 && std::strcmp(argv[1],"--check-process-param")==0) {
        environment("PS3_STRICT_BOOT","1");
        return check_process_param(argv[2]);
    }
    if(argc==3 && std::strcmp(argv[1],"--check-fs-stat")==0) {
        environment("PS3_STRICT_BOOT","1");
        return folklore_check_fs_stat(argv[2]);
    }
    if(argc==3 && std::strcmp(argv[1],"--check-fs-open")==0) {
        environment("PS3_STRICT_BOOT","1");
        return folklore_check_fs_open(argv[2]);
    }
    if(argc==3 && std::strcmp(argv[1],"--check-fs-seek")==0) {
        environment("PS3_STRICT_BOOT","1");
        return folklore_check_fs_seek(argv[2]);
    }
    if(argc==3 && std::strcmp(argv[1],"--check-fs-seek-existing")==0) {
        environment("PS3_STRICT_BOOT","1");
        return folklore_check_fs_seek_existing(argv[2]);
    }
    if((argc==3 || argc==4) && std::strcmp(argv[1],"--check-fs-unlink")==0) {
        environment("PS3_STRICT_BOOT","1");
        return folklore_check_fs_unlink(argv[2],argc==4?argv[3]:nullptr);
    }
    if((argc==3 || argc==4) && std::strcmp(argv[1],"--check-fs-aio")==0) {
        environment("PS3_STRICT_BOOT","1");
        return folklore_check_fs_aio(argv[2],argc==4?argv[3]:nullptr);
    }
    if(argc==3 && std::strcmp(argv[1],"--check-fs-aio-capacity")==0) {
        environment("PS3_STRICT_BOOT","1");
        return folklore_check_fs_aio_capacity(argv[2]);
    }
    if(argc==4 && std::strcmp(argv[1],"--check-spurs-snapshot")==0) {
        environment("PS3_STRICT_BOOT","1");
        return folklore_check_spurs_snapshot(argv[2],argv[3]);
    }
#ifdef _WIN32
    if(argc==2 && std::strcmp(argv[1],"--check-host-fault")==0)return folklore_check_fault_capture();
    if(argc==3 && std::strcmp(argv[1],"--check-msgdialog-ui")==0) {
        environment("PS3_STRICT_BOOT","1");
        extern int folklore_check_dialog_host_ui(const char*);
        return folklore_check_dialog_host_ui(argv[2]);
    }
#endif
    if(argc==2 && std::strcmp(argv[1],"--check-msgdialog-abi")==0)return check_msgdialog_abi();
    bool contained_writes = false, graphics = false, native_dialogs = false;
    const char* settings_path = nullptr;
    bool save_settings = false;
    bool valid = argc >= 5;
    for (int i = 5; i < argc; ++i) {
        const auto option = folklore::parse_graphics_option(argv[i], graphics_options);
        if (option != folklore::GraphicsOptionResult::unrelated) {
            if (option == folklore::GraphicsOptionResult::invalid) valid = false;
            continue;
        }
        if (std::strcmp(argv[i], "--contained-writes") == 0 && !contained_writes)
            contained_writes = true;
        else if (std::strcmp(argv[i], "--d3d12") == 0 && !graphics) graphics = true;
        else if (std::strcmp(argv[i], "--vulkan") == 0 && !graphics) { graphics = true; vulkan_graphics = true; }
        else if (std::strcmp(argv[i], "--native-dialogs") == 0 && !native_dialogs) native_dialogs = true;
        else if (std::strcmp(argv[i], "--trace-startup") == 0 && !trace_startup) trace_startup = true;
        else if (std::strncmp(argv[i], "--settings=", 11) == 0 && !settings_path && argv[i][11])
            settings_path = argv[i] + 11;
        else if (std::strcmp(argv[i], "--save-settings") == 0 && !save_settings) save_settings = true;
        else valid = false;
    }
    if ((trace_startup && !graphics && !native_dialogs) || (graphics && native_dialogs)) valid = false;
    if (!folklore::graphics_options_valid(graphics_options, vulkan_graphics)) valid = false;
    if (((settings_path || save_settings) && !vulkan_graphics) || (save_settings && !settings_path)) valid = false;
    if (settings_path && !std::filesystem::path(settings_path).is_absolute()) valid = false;
    if (!valid) {
        std::fprintf(stderr, "usage: FolkloreBoot ELF VFS_ROOT ISOLATED_HDD0 DEV_FLASH [--contained-writes] [--vulkan | --d3d12 | --native-dialogs] [--trace-startup] [--render-scale=1..4] [--output-size=WIDTHxHEIGHT] [--anisotropy=auto|1|2|4|8|16] [--anti-aliasing=off|fxaa] [--upscaler=off|fsr1] [--ambient-occlusion=off|cacao|rayquery] [--path-tracing=off|diffuse-preview|hybrid] [--ray-denoiser=off|spatial] [--path-strength=20|35|60|100] [--dlss=off|quality|balanced|performance|ultra-performance|dlaa] [--sharpen=0..100] [--hud-scale=50..100] [--camera-aspect=original|output] [--settings=ABSOLUTE_PATH] [--save-settings]\nResolution/settings options require Vulkan; output dimensions must be 1..8192. Saving requires --settings. Output camera aspect supports up to 32:9.\n");
        return 2;
    }
#ifndef PS3RECOMP_RSX_VULKAN
    if (vulkan_graphics) {
        std::fprintf(stderr, "[folklore] Vulkan was not compiled into this build\n");
        return 2;
    }
#endif
#ifndef _WIN32
    if ((graphics && !vulkan_graphics) || trace_startup) {
        std::fprintf(stderr, "[folklore] D3D12 and startup-window capture require Windows; use --vulkan on Linux\n");
        return 2;
    }
#endif
    if (settings_path) {
        folklore::GraphicsSettings stored;
        std::string error;
        const auto result = folklore::load_graphics_settings(settings_path, stored, error);
        if (result == folklore::SettingsLoadResult::error) {
            std::fprintf(stderr, "[folklore] invalid graphics settings: %s; using defaults and explicit options\n", error.c_str());
            if (save_settings) {
                std::fprintf(stderr, "[folklore] refusing to overwrite invalid settings; preserve or repair the file first\n");
                return 2;
            }
        }
        if (result == folklore::SettingsLoadResult::loaded) {
            if (!graphics_options.output_set) {
                graphics_options.width = stored.width;
                graphics_options.height = stored.height;
            }
            if (!graphics_options.scale_set) graphics_options.scale = stored.scale;
            if (!graphics_options.anisotropy_set) graphics_options.anisotropy = stored.anisotropy;
            if (!graphics_options.antialiasing_set) graphics_options.antialiasing = stored.antialiasing;
            if (!graphics_options.upscaler_set) graphics_options.upscaler = stored.upscaler;
            if (!graphics_options.ambient_occlusion_set) graphics_options.ambient_occlusion = stored.ambient_occlusion;
            if (!graphics_options.path_tracing_set) graphics_options.path_tracing = stored.path_tracing;
            if (!graphics_options.ray_denoiser_set) graphics_options.ray_denoiser = stored.ray_denoiser;
            if (!graphics_options.path_strength_set) graphics_options.path_strength = stored.path_strength;
            if (!graphics_options.dlss_set) graphics_options.dlss = stored.dlss;
            if (!graphics_options.sharpen_set) graphics_options.sharpen = stored.sharpen;
            if (!graphics_options.hud_scale_set) graphics_options.hud_scale = stored.hud_scale;
        }
        // A settings file, including a missing first-launch file, takes precedence
        // over diagnostic environment overrides. Explicit CLI choices win above.
        graphics_options.scale_set = true;
        graphics_options.anisotropy_set = true;
        graphics_options.antialiasing_set = true;
        graphics_options.upscaler_set = true;
        graphics_options.ambient_occlusion_set = true;
        graphics_options.path_tracing_set = true;
        graphics_options.ray_denoiser_set = true;
        graphics_options.path_strength_set = true;
        graphics_options.dlss_set = true;
        graphics_options.sharpen_set = true;
        graphics_options.hud_scale_set = true;
        folklore::graphics_camera_aspect_settings(graphics_options, stored.camera_aspect);
    }
    if (vulkan_graphics && graphics_options.dlss && graphics_options.ambient_occlusion) {
        std::fprintf(stderr, "[folklore] DLSS uses the ray scene path; choose --ambient-occlusion=off\n");
        return 2;
    }
    if (vulkan_graphics && graphics_options.path_tracing && graphics_options.ambient_occlusion) {
        std::fprintf(stderr, "[folklore] diffuse path tracing preview requires ambient occlusion off; choose --ambient-occlusion=off\n");
        return 2;
    }
    double camera_output_ratio = 0.0;
    if (vulkan_graphics && graphics_options.camera_aspect_set && graphics_options.camera_aspect == 1 &&
        !folklore::graphics_camera_output_ratio(graphics_options.width, graphics_options.height, camera_output_ratio)) {
        std::fprintf(stderr,"[folklore] camera-aspect=output requires output width/height at most 32:9; choose a narrower output or camera-aspect=original\n");
        return 2;
    }
#ifndef FOLKLORE_CAMERA_ASPECT_BRIDGE
    if (vulkan_graphics && ((graphics_options.camera_aspect_set && camera_output_ratio > 0.0) ||
        (!graphics_options.camera_aspect_set && std::getenv("PS3RECOMP_FOLKLORE_CAMERA_ASPECT") &&
         *std::getenv("PS3RECOMP_FOLKLORE_CAMERA_ASPECT")))) {
        std::fprintf(stderr,"[folklore] output camera aspect requires a build with the verified Folklore camera aspect bridge; use --camera-aspect=original or rebuild with the bridge\n");
        return 2;
    }
#endif
#ifndef FOLKLORE_HAS_CACAO
    const char* diagnostic_ao = std::getenv("PS3RECOMP_RSX_AO");
    if (vulkan_graphics && (graphics_options.ambient_occlusion == 1 ||
        (!graphics_options.ambient_occlusion_set && diagnostic_ao && !std::strcmp(diagnostic_ao,"cacao")))) {
        std::fprintf(stderr,"[folklore] CACAO ambient occlusion was not compiled into this build; select off\n");
        return 2;
    }
#endif
    std::setvbuf(stdout, nullptr, _IONBF, 0);
    std::setvbuf(stderr, nullptr, _IONBF, 0);
    environment("PS3_STRICT_BOOT", "1");
    if (vulkan_graphics) {
        environment("PS3RECOMP_VK_GUEST_PROGRAMS", "1");
        // Preserve an explicit offscreen diagnostic request. Normal launches
        // still open a window when the caller has not selected either mode.
        if (!std::getenv("PS3RECOMP_VK_WINDOW"))
            environment("PS3RECOMP_VK_WINDOW", "1");
        environment("PS3RECOMP_RSX_ENGINE", "dispatch");
        if (graphics_options.scale_set) {
            const char scale[] = {static_cast<char>('0' + graphics_options.scale), '\0'};
            environment("PS3RECOMP_RENDER_SCALE", scale);
        }
        if (graphics_options.anisotropy_set)
            environment("PS3RECOMP_ANISOTROPY", folklore::graphics_anisotropy_text(graphics_options.anisotropy));
        if (graphics_options.upscaler_set)
            environment("PS3RECOMP_UPSCALER", folklore::graphics_upscaler_text(graphics_options.upscaler));
        environment("PS3RECOMP_RSX_AO", folklore::graphics_ambient_occlusion_text(graphics_options.ambient_occlusion));
        environment("PS3RECOMP_RSX_AO_RADIUS", "1.2");
        environment("PS3RECOMP_RSX_AO_STRENGTH", "1");
        // Clear inherited diagnostics even when this option was not explicitly set.
        environment("PS3RECOMP_RSX_PATH_PREVIEW", graphics_options.path_tracing ? "1" : "");
        environment("PS3RECOMP_RSX_RAY_DENOISE", (graphics_options.path_tracing || graphics_options.ambient_occlusion == 2) ?
                    folklore::graphics_ray_denoiser_text(graphics_options.ray_denoiser) : "");
        if (graphics_options.path_tracing) environment("PS3RECOMP_RSX_AO", "rayquery");
        if (graphics_options.path_tracing) {
            const char* mix = graphics_options.path_strength == 20 ? "0.20" :
                              graphics_options.path_strength == 35 ? "0.35" :
                              graphics_options.path_strength == 60 ? "0.60" : "1.00";
            environment("PS3RECOMP_RSX_AO_STRENGTH", mix);
        }
        // Hybrid: raster primary visibility with one stable traced key light,
        // a diffuse bounce, source reflections and temporal accumulation.
        environment("PS3RECOMP_RSX_HYBRID_PT", "");
        environment("PS3RECOMP_RSX_AUTHORED_LIGHTS", "");
        environment("PS3RECOMP_RSX_TRACE", "");
        if (graphics_options.path_tracing == 2) {
            environment("PS3RECOMP_RSX_HYBRID_PT", "1");
            environment("PS3RECOMP_RSX_AUTHORED_LIGHTS", "1");
            environment("PS3RECOMP_RSX_TRACE", "");
        }
        if (graphics_options.dlss_set) {
            environment("PS3RECOMP_DLSS", graphics_options.dlss ? "1" : "");
            environment("PS3RECOMP_DLSS_MODE", graphics_options.dlss ? folklore::graphics_dlss_text(graphics_options.dlss) : "");
            if (graphics_options.dlss && !graphics_options.path_tracing) {
                // DLSS alone still rebuilds the scene for its motion vectors.
                environment("PS3RECOMP_RSX_AO", "rayquery");
                environment("PS3RECOMP_RSX_HYBRID_PT", "1");
                environment("PS3RECOMP_RSX_AUTHORED_LIGHTS", "1");
                environment("PS3RECOMP_RSX_TRACE", "0");
            }
#ifdef _WIN32
            // The NGX bridge and runtime ship next to the executable unless the
            // caller names them; NGX requires existing absolute directories.
            if (graphics_options.dlss) {
                wchar_t module[32768];
                const DWORD length = GetModuleFileNameW(nullptr, module, 32768);
                if (length && length < 32768) {
                    std::filesystem::path dir = std::filesystem::path(module).parent_path();
                    std::error_code ignored;
                    std::filesystem::create_directories(dir / "ngx-logs", ignored);
                    auto set_default = [](const char* name, const std::filesystem::path& value) {
                        if (std::getenv(name) && *std::getenv(name)) return;
                        const std::u8string text = value.u8string();
                        environment(name, reinterpret_cast<const char*>(text.c_str()));
                    };
                    set_default("PS3RECOMP_DLSS_BRIDGE", dir / "rsx_dlss_vk.dll");
                    set_default("PS3RECOMP_DLSS_RUNTIME", dir);
                    set_default("PS3RECOMP_DLSS_LOG", dir / "ngx-logs");
                    if (!std::getenv("PS3RECOMP_DLSS_PROJECT_UUID"))
                        environment("PS3RECOMP_DLSS_PROJECT_UUID", "2924e792-424a-48cf-9944-757b36582b73");
                }
            }
#endif
        }
        if (graphics_options.sharpen_set) {
            char sharpen[16];
            std::snprintf(sharpen, sizeof sharpen, "%.2f", graphics_options.sharpen / 100.0);
            environment("PS3RECOMP_SHARPEN", graphics_options.sharpen ? sharpen : "");
        }
        std::printf("[folklore] Vulkan requested path-tracing=%s dlss=%s sharpen=%u (diffuse preview retains authored primary lighting and uses neutral bounce materials)\n",
                    folklore::graphics_path_tracing_text(graphics_options.path_tracing),
                    folklore::graphics_dlss_text(graphics_options.dlss), graphics_options.sharpen);
        std::printf("[folklore] Vulkan requested ambient-occlusion=%s\n",
                    std::getenv("PS3RECOMP_RSX_AO") ? std::getenv("PS3RECOMP_RSX_AO") : "off");
        std::printf("[folklore] Vulkan requested upscaler=%s\n",
                    std::getenv("PS3RECOMP_UPSCALER") ? std::getenv("PS3RECOMP_UPSCALER") : "off");
        if (graphics_options.antialiasing_set)
            environment("PS3RECOMP_ANTIALIASING", folklore::graphics_antialiasing_text(graphics_options.antialiasing));
        if (graphics_options.hud_scale_set) {
            const std::string hud_scale = std::to_string(graphics_options.hud_scale);
            environment("PS3RECOMP_FOLKLORE_HUD_SCALE", hud_scale.c_str());
        }
        if (graphics_options.camera_aspect_set) {
            if (camera_output_ratio > 0.0) {
                char value[32];
                std::snprintf(value,sizeof value,"%.17g",camera_output_ratio);
                environment("PS3RECOMP_FOLKLORE_CAMERA_ASPECT",value);
            } else environment("PS3RECOMP_FOLKLORE_CAMERA_ASPECT","");
        }
        std::printf("[folklore] Vulkan requested anti-aliasing=%s\n",
                    std::getenv("PS3RECOMP_ANTIALIASING") ? std::getenv("PS3RECOMP_ANTIALIASING") : "off");
        std::printf("[folklore] Vulkan requested anisotropy=%s (0/auto=guest,1=off)\n",
                    std::getenv("PS3RECOMP_ANISOTROPY") ? std::getenv("PS3RECOMP_ANISOTROPY") : "auto");
        std::printf("[folklore] Vulkan requested output=%ux%u render-scale=%s\n",
                    graphics_options.width, graphics_options.height,
                    std::getenv("PS3RECOMP_RENDER_SCALE") ? std::getenv("PS3RECOMP_RENDER_SCALE") : "1");
        std::printf("[folklore] Vulkan requested Folklore HUD scale=%s%%\n",
                    std::getenv("PS3RECOMP_FOLKLORE_HUD_SCALE") ?
                    std::getenv("PS3RECOMP_FOLKLORE_HUD_SCALE") : "100");
        std::printf("[folklore] Vulkan requested camera aspect=%s%s\n",
                    graphics_options.camera_aspect_set ? folklore::graphics_camera_aspect_text(graphics_options.camera_aspect) :
                    (std::getenv("PS3RECOMP_FOLKLORE_CAMERA_ASPECT") && *std::getenv("PS3RECOMP_FOLKLORE_CAMERA_ASPECT") ? "diagnostic environment" : "original"),
                    camera_output_ratio > 0.0 ? " (output width/height)" : "");
    }
    folklore::ProcessParam process_param;
    if (!bootstrap_process_param(argv[1],process_param)) return 86;
    std::filesystem::path hdd0(argv[3]);
    if (hdd0.filename() != "dev_hdd0" || !hdd0.is_absolute()) return 2;
    std::string volumes = hdd0.parent_path().string();
    std::string hdd1 = (hdd0.parent_path() / "dev_hdd1").string();
    environment("PS3_STRICT_FS_READONLY", contained_writes ? "0" : "1");
    environment("PS3_STRICT_FS_WRITE_ROOT", contained_writes ? volumes.c_str() : "");
    environment("PS3_HDD0_ROOT", argv[3]);
    environment("PS3_HDD1_ROOT", hdd1.c_str());
    environment("PS3_DEV_FLASH", argv[4]);
    /* Translated shaders persist beside the profile's volumes, so a scene's
     * programs are compiled once per profile rather than on every launch.
     * An explicit PS3RECOMP_SHADER_CACHE_DIR (empty disables it) wins. */
    if (vulkan_graphics && contained_writes && !std::getenv("PS3RECOMP_SHADER_CACHE_DIR")) {
        const std::filesystem::path shader_cache = hdd0.parent_path() / "shader-cache";
        std::error_code ec;
        std::filesystem::create_directories(shader_cache, ec);
        if (!ec) environment("PS3RECOMP_SHADER_CACHE_DIR", shader_cache.string().c_str());
        std::printf("[folklore] shader cache %s%s\n", shader_cache.string().c_str(), ec ? " unavailable" : "");
    }
    const std::string save_root = (hdd0 / "home" / "00000001" / "savedata").string();
    const int32_t save_root_result = ps3_savedata_set_root(save_root.c_str());
    if (save_root_result != 0) {
        std::fprintf(stderr, "[folklore] savedata root configuration failed result=0x%08X\n",
                     static_cast<uint32_t>(save_root_result));
        return 86;
    }
    environment("MSGDIALOG_NOBUTTON_WAITS", "1");
    environment("MSGDIALOG_AUTODISMISS_MS", "0");
    ppu_vfs_root = argv[2];
    std::printf("[folklore] BCUS98147 diagnostic startup; graphics=%s\n",
                graphics ? (vulkan_graphics ? "Vulkan requested" : "D3D12 requested") : "disabled");
    char sfo[4096];
    if (std::snprintf(sfo, sizeof(sfo), "%s/PS3_GAME/PARAM.SFO", argv[2]) >= int(sizeof(sfo)))
        return 2;
    cellGame_init_from_paramsfo(sfo);
    if (std::strcmp(cellGame_get_title_id(), "BCUS98147")) {
        std::fprintf(stderr, "[folklore] wrong or unreadable PARAM.SFO title id\n");
        return 2;
    }
#ifdef _WIN32
    vm_base = static_cast<uint8_t*>(VirtualAlloc(nullptr, VM_LIMIT,
                                               MEM_RESERVE, PAGE_READWRITE));
    if (vm_base && !AddVectoredExceptionHandler(1, commit_guest_page)) return 3;
#else
    void* mapping = mmap(nullptr, VM_LIMIT, PROT_READ | PROT_WRITE,
                         MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    vm_base = mapping == MAP_FAILED ? nullptr : static_cast<uint8_t*>(mapping);
#endif
    ppu_vm_size = VM_LIMIT;
    if (!vm_base) return 3;
    // Guest pages here only ever go from reserved to committed read-write
    // (commit_guest_page on Windows; one read-write mapping elsewhere) and are
    // never decommitted or downgraded, so strict SPU DMA may remember a 64 KB
    // page once its full strict check passes. See spu_vm_pagemap.c.
    mfc_strict_enable_verified_page_cache();
#ifdef _WIN32
    if(trace_startup)folklore_install_fault_capture(vm_base,VM_LIMIT);
#endif
    if (trace_startup)
        std::fprintf(stderr, "[startup-trace] vm_base=0x%llX limit=0xE0000000\n",
                     static_cast<unsigned long long>(reinterpret_cast<uintptr_t>(vm_base)));
    uint32_t entry = ppu_load_elf(argv[1]);
    if (entry != 0x0054D820u) {
        std::fprintf(stderr, "[folklore] unexpected entry OPD=0x%08X\n", entry);
        return 3;
    }
    if (vm_read32(entry) != 0x0001022Cu || vm_read32(entry + 4) != 0x0056EB00u) {
        std::fprintf(stderr, "[folklore] entry descriptor code/TOC identity differs\n");
        return 3;
    }
    ppu_recomp_register();
#ifdef FOLKLORE_PRIVATE_NATIVE_JOBS
    if(folklore_register_native_jobs()!=0){
        std::fprintf(stderr,"[folklore] private native job registration failed\n");return 3;
    }
    std::fprintf(stderr,"[folklore] private native job source registered before seal\n");
#endif
    if(spurs_native_registry_seal()!=SPURS_NATIVE_OK){
        std::fprintf(stderr,"[folklore] native job registry could not be sealed\n");return 3;
    }
    ppu_hle_init();
    ppu_sysprx_register();
    ppu_fs_register();
    lv2_init_syscalls();
    g_ps3_guest_caller = guest_callback;
    gcm_set_host_context_callback([](uint32_t opd, uint32_t context, uint32_t count) -> int32_t {
        return static_cast<int32_t>(ppu_guest_call(opd, context, count, 0, 0, 0, 0, 0, 0));
    });
    std::atomic<int> ready{0};
    std::atomic<bool> stop{false};
    std::thread render_thread;
    if (graphics || native_dialogs) {
        render_thread = std::thread(graphics ? graphics_service : native_dialog_service, std::ref(ready), std::ref(stop));
        while (ready.load() == 0) std::this_thread::sleep_for(std::chrono::milliseconds(1));
        if (ready.load() == 2) { render_thread.join(); return 0; }
        if (ready.load() < 0) { render_thread.join(); return 4; }
        if (save_settings) {
            const folklore::GraphicsSettings effective{graphics_options.width, graphics_options.height, graphics_options.scale, graphics_options.anisotropy, graphics_options.antialiasing, graphics_options.upscaler, graphics_options.ambient_occlusion, graphics_options.hud_scale, graphics_options.camera_aspect, graphics_options.path_tracing, graphics_options.ray_denoiser, graphics_options.path_strength, graphics_options.dlss, graphics_options.sharpen};
            std::string error;
            if (!folklore::save_graphics_settings(settings_path, effective, error)) {
                std::fprintf(stderr, "[folklore] graphics settings save failed: %s\n", error.c_str());
                stop.store(true); render_thread.join(); return 2;
            }
            std::printf("[folklore] graphics settings saved to %s\n", settings_path);
        }
    }
    std::printf("[folklore] dispatch entry OPD=0x%08X; stack=0x0FF00000\n", entry);
#ifdef _WIN32
    if (std::getenv("PS3_SAMPLE")) ps3_sampler_start();
#endif
    int rc = ppu_run(entry, 0x0FF00000u);
    const int32_t aio_shutdown=ppu_fs_aio_shutdown();
    if(aio_shutdown) {
        std::fprintf(stderr,"[folklore] AIO shutdown failed result=0x%08X\n",(uint32_t)aio_shutdown);
        std::fflush(nullptr);std::_Exit(86);
    }
    stop.store(true);
    if (render_thread.joinable()) render_thread.join();
    std::fprintf(stderr, "[folklore] entry unwound rc=%d; no gameplay acceptance\n", rc);
    return rc ? rc : 87;
}

