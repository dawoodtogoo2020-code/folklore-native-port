// Opt-in, I-R3-only diagnostic. Include from the generated PPU translation unit.
// One producer (sgx-audio-thr) writes a bounded SPSC ring; one host writer
// periodically writes CSV. No guest state, clock rate, scheduling or PCM changes.
#pragma once
#ifndef _WIN32
#error Folklore audio producer trace currently supports Windows only
#endif
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <atomic>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

namespace folklore_audio_producer_trace {
enum : uint32_t { Q_ENTER=1, Q_RETURN=2, P_ENTER=3, P_RETURN=4, PHASE=5 };
enum : uint32_t { CAPACITY=16384, MAX_RECORDS=400000, MAX_BYTES=64*1024*1024 };
struct Record {
    uint64_t qpc_us;
    uint32_t sequence, kind, read_slot, write_slot, address;
    int32_t result;
    uint32_t pcm_valid, pcm_nonzero, pcm_nonfinite;
    float pcm_peak;
    uint64_t pcm_end_us;
    uint32_t phase, edge, callback_target, cycles_valid, producer_id;
    uint64_t thread_cycles, pcm_hash;
    uint32_t detail;
};
static Record records[CAPACITY];
static std::atomic<uint32_t> head(0), tail(0), dropped(0), admitted(0);
static std::atomic<int> stop(0), state(0); // 0 new, 2 ready, -1 unavailable
static HANDLE file_handle=INVALID_HANDLE_VALUE, wake_event=NULL, writer_thread=NULL;
static LARGE_INTEGER qpc_frequency={};
static uint32_t written_bytes=0;
static DWORD owner_tid=0;
static bool phase_enabled=false, phase_armed=false;
static const char *phase_request=nullptr;
static uint64_t phase_start_us=0, phase_poll_us=0;
static uint32_t target_address=0, target_slot=16, target_read_slot=0, producer_id=0;
static uint32_t phase_callbacks[6]={};
static bool phase2_context=false;
static uint32_t phase2_producer_id=0, nested_callback=0, nested_details[13]={};
static void configure_phase_mode() {
    const char *phase=getenv("FOLKLORE_AUDIO_PHASE_TRACE");
    phase_enabled=phase && strcmp(phase,"1")==0;
    phase_request=getenv("FOLKLORE_AUDIO_PCM_TRACE_REQUEST");
}
static uint64_t now_us() {
    LARGE_INTEGER t; QueryPerformanceCounter(&t);
    const uint64_t q=(uint64_t)t.QuadPart,f=(uint64_t)qpc_frequency.QuadPart;
    return (q/f)*1000000ULL+(q%f)*1000000ULL/f;
}
/* Only the owner thread polls. A missing sentinel never opens a phase window.
 * Phase mode also gates ordinary records, preserving room for the armed run. */
static bool phase_window(uint64_t time) {
    if (!phase_enabled) return false;
    if (!phase_armed) {
        if (time<phase_poll_us) return false;
        phase_poll_us=time+1000000ULL;
        if (phase_request && *phase_request &&
            GetFileAttributesA(phase_request)==INVALID_FILE_ATTRIBUTES) return false;
        phase_armed=true; phase_start_us=time;
    }
    return time>=phase_start_us && time-phase_start_us<64000000ULL;
}

static bool write_all(const char* data, uint32_t size) {
    if (written_bytes > MAX_BYTES || size > MAX_BYTES-written_bytes) return false;
    while (size) {
        DWORD n=0;
        if (!WriteFile(file_handle, data, size, &n, NULL) || !n) return false;
        data += n; size -= n; written_bytes += n;
    }
    return true;
}

static DWORD WINAPI writer(void*) {
    static const char header[]="sequence,qpc_us,kind,read_slot,write_slot,address,result,pcm_valid,pcm_nonzero,pcm_nonfinite,pcm_peak,pcm_end_us,phase,edge,callback_target,thread_cycles,cycles_valid,pcm_hash,producer_id,detail\r\n";
    bool ok=write_all(header, (uint32_t)(sizeof(header)-1));
    char batch[32768];
    while (ok) {
        if (tail.load(std::memory_order_relaxed)==head.load(std::memory_order_acquire) &&
            !stop.load(std::memory_order_acquire))
            WaitForSingleObject(wake_event, 250);
        uint32_t count=0, used=0;
        while (count<256) {
            if (sizeof(batch)-used<384) break;
            uint32_t r=tail.load(std::memory_order_relaxed);
            if (r==head.load(std::memory_order_acquire)) break;
            const Record& x=records[r % CAPACITY];
            int n=snprintf(batch+used, sizeof(batch)-used,
                "%u,%llu,%u,%u,%u,0x%08X,%d,%u,%u,%u,%.9g,%llu,%u,%u,0x%08X,%llu,%u,0x%016llX,%u,0x%08X\r\n", x.sequence,
                (unsigned long long)x.qpc_us, x.kind, x.read_slot,
                x.write_slot, x.address, x.result,x.pcm_valid,x.pcm_nonzero,
                x.pcm_nonfinite,x.pcm_peak,(unsigned long long)x.pcm_end_us,
                x.phase,x.edge,x.callback_target,(unsigned long long)x.thread_cycles,
                x.cycles_valid,(unsigned long long)x.pcm_hash,x.producer_id,x.detail);
            if (n<=0 || (size_t)n>=sizeof(batch)-used) { ok=false; break; }
            used+=(uint32_t)n;
            tail.store(r+1, std::memory_order_release);
            ++count;
        }
        if (used) ok=write_all(batch, used);
        if (stop.load(std::memory_order_acquire) &&
            tail.load(std::memory_order_relaxed)==head.load(std::memory_order_acquire)) break;
    }
    char footer[128];
    int n=snprintf(footer, sizeof(footer), "# admitted=%u,dropped=%u,io_ok=%u\r\n",
        admitted.load(), dropped.load(), ok ? 1u : 0u);
    if (n>0) write_all(footer, (uint32_t)n);
    FlushFileBuffers(file_handle);
    return 0;
}

static void shutdown() {
    if (state.load()!=2) return;
    stop.store(1, std::memory_order_release);
    SetEvent(wake_event);
    DWORD joined=writer_thread ? WaitForSingleObject(writer_thread, 3000) : WAIT_OBJECT_0;
    if (joined==WAIT_OBJECT_0) {
        if (writer_thread) CloseHandle(writer_thread);
        if (wake_event) CloseHandle(wake_event);
        if (file_handle!=INVALID_HANDLE_VALUE) CloseHandle(file_handle);
    }
    state.store(-1, std::memory_order_release);
}

static bool init() {
    // Called solely by the named guest audio loop. Failure leaves tracing off.
    if (state.load(std::memory_order_relaxed)==2) return true;
    if (state.load(std::memory_order_relaxed)==-1) return false;
    const char* path=getenv("FOLKLORE_AUDIO_PRODUCER_TRACE_FILE");
    if (!path || !(path[0]=='D'||path[0]=='d') || path[1]!=':' ||
        !(path[2]=='/'||path[2]=='\\') || strlen(path)>240 ||
        strlen(path)<7 || _stricmp(path+strlen(path)-4, ".csv")!=0) {
        state.store(-1); return false;
    }
    if (!QueryPerformanceFrequency(&qpc_frequency) || qpc_frequency.QuadPart<=0) {
        state.store(-1); return false;
    }
    file_handle=CreateFileA(path, GENERIC_WRITE, FILE_SHARE_READ, NULL,
        CREATE_NEW, FILE_ATTRIBUTE_NORMAL, NULL);
    if (file_handle==INVALID_HANDLE_VALUE) { state.store(-1); return false; }
    wake_event=CreateEventA(NULL, FALSE, FALSE, NULL);
    if (!wake_event) { CloseHandle(file_handle); file_handle=INVALID_HANDLE_VALUE; state.store(-1); return false; }
    writer_thread=CreateThread(NULL, 0, writer, NULL, 0, NULL);
    if (!writer_thread) {
        CloseHandle(wake_event); wake_event=NULL;
        CloseHandle(file_handle); file_handle=INVALID_HANDLE_VALUE;
        state.store(-1); return false;
    }
    owner_tid=GetCurrentThreadId();
    configure_phase_mode();
    state.store(2);
    atexit(shutdown);
    return true;
}

static inline bool active() {
    if (state.load(std::memory_order_acquire)==-1) return false;
    if (!init()) return false;
    return GetCurrentThreadId()==owner_tid;
}

typedef uint32_t (*ReadPCMWord)(uint32_t);
/* The accessor returns a guest big-endian word as a native integer, like
 * vm_read32. These are diagnostic snapshots, not atomic block completion. */
static inline void sample_pcm(Record& x, ReadPCMWord read) {
    x.pcm_valid=x.pcm_nonzero=x.pcm_nonfinite=0;
    x.pcm_peak=0; x.pcm_end_us=0; x.pcm_hash=0;
    if (!read || (x.kind!=P_ENTER && x.kind!=P_RETURN && x.kind!=PHASE) || x.write_slot>=16 ||
        x.address!=0x58000000u+x.write_slot*8192u) return;
    uint32_t peak_bits=0;
    uint64_t hash=14695981039346656037ULL;
    for (uint32_t i=0;i<2048;++i) {
        const uint32_t word=read(x.address+i*4u);
        /* FNV-style 64-bit word hash, in guest word order, including sign/NaN bits. */
        hash=(hash ^ word)*1099511628211ULL;
        const uint32_t magnitude=word&0x7fffffffu;
        if (magnitude) ++x.pcm_nonzero;
        if (magnitude>=0x7f800000u) ++x.pcm_nonfinite;
        else if (magnitude>peak_bits) peak_bits=magnitude;
    }
    memcpy(&x.pcm_peak,&peak_bits,sizeof(peak_bits));
    x.pcm_valid=1;
    x.pcm_hash=hash;
}

static inline void emit(uint32_t kind, uint32_t read_slot, uint32_t write_slot,
                        uint32_t address, int32_t result, ReadPCMWord read=nullptr,
                        uint32_t phase=0,uint32_t edge=0,uint32_t callback=0,uint32_t detail=0) {
    if (!active()) return;
    const uint64_t time=now_us();
    if (kind==P_ENTER) {
        target_address=address; target_slot=write_slot; target_read_slot=read_slot;
        ++producer_id;
        phase2_context=false;
    }
    if (phase_enabled && !phase_window(time)) return;
    uint32_t total=admitted.load(std::memory_order_relaxed);
    if (total>=MAX_RECORDS) { dropped.fetch_add(1, std::memory_order_relaxed); return; }
    uint32_t w=head.load(std::memory_order_relaxed);
    if (w-tail.load(std::memory_order_acquire)>=CAPACITY) {
        dropped.fetch_add(1, std::memory_order_relaxed); return;
    }
    LARGE_INTEGER now;
    QueryPerformanceCounter(&now);
    const uint64_t q=(uint64_t)now.QuadPart, f=(uint64_t)qpc_frequency.QuadPart;
    Record& x=records[w % CAPACITY];
    x.qpc_us=(q/f)*1000000ull+((q%f)*1000000ull)/f;
    x.sequence=w; x.kind=kind; x.read_slot=read_slot;
    x.write_slot=write_slot; x.address=address; x.result=result;
    x.phase=phase; x.edge=edge; x.callback_target=callback; x.producer_id=producer_id;
    x.detail=detail;
    ULONG64 cycles=0;
    x.cycles_valid=phase_enabled && QueryThreadCycleTime(GetCurrentThread(),&cycles);
    x.thread_cycles=x.cycles_valid ? (uint64_t)cycles : 0;
    sample_pcm(x,read);
    if (x.pcm_valid) {
        QueryPerformanceCounter(&now);
        const uint64_t end=(uint64_t)now.QuadPart;
        x.pcm_end_us=(end/f)*1000000ull+((end%f)*1000000ull)/f;
    }
    admitted.store(total+1, std::memory_order_relaxed);
    head.store(w+1, std::memory_order_release);
    if ((w&255u)==0) SetEvent(wake_event);
}
/* Phase ids:1=00280AD0,2=002A4BAC,3/4/5=callbacks,6=bookkeeping start.
 * Callback exits retain the entry target even if guest ctx->ctr changed. */
static inline void phase_event(uint32_t id,uint32_t edge,uint32_t callback,ReadPCMWord read) {
    if (!active() || !phase_enabled || !phase_window(now_us()) ||
        id<1 || id>6 || target_slot>=16 ||
        target_address!=0x58000000u+target_slot*8192u) return;
    if (id>=3 && id<=5) {
        if (edge==1) phase_callbacks[id]=callback;
        else callback=phase_callbacks[id];
    }
    if (id==2 && edge==1) { phase2_context=true;phase2_producer_id=producer_id; }
    emit(PHASE,target_read_slot,target_slot,target_address,0,read,id,edge,callback);
    if (id==2 && edge==2) phase2_context=false;
}
/* Nested phase2 markers never scan PCM. They are admitted only in the current
 * owner-thread producer call, inside the measured phase2 bracket. */
static inline void nested_phase_event(uint32_t id,uint32_t edge,uint32_t detail,
                                     int32_t result=0,uint32_t callback=0) {
    if (!active() || !phase_enabled || !phase_window(now_us()) ||
        !phase2_context || phase2_producer_id!=producer_id || id<7 || id>12) return;
    if (edge==1) nested_details[id]=detail;
    else detail=nested_details[id];
    if (id==12) {
        if (edge==1) nested_callback=callback;
        else callback=nested_callback;
    }
    emit(PHASE,target_read_slot,target_slot,target_address,result,nullptr,id,edge,callback,detail);
}
} // namespace folklore_audio_producer_trace
