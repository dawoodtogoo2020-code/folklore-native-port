/* Bounded register observation. Addresses are leads, never object identities. */
#ifndef FOLKLORE_DRAW_PRODUCER_TRACE_H
#define FOLKLORE_DRAW_PRODUCER_TRACE_H
#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <mutex>
#include <cstdint>
#if defined(_WIN32)
#include <windows.h>
#endif
extern "C" uint32_t cellGcm_flip_request_count(void);
extern "C" int ppu_diagnostic_read(uint64_t,void*,uint32_t);

namespace folklore_draw_producer_trace {
struct State {
    const char* gate = std::getenv("FOLKLORE_DRAW_PRODUCER_GATE");
    bool enabled = [] { const char* e=std::getenv("FOLKLORE_DRAW_PRODUCER_TRACE");
                       return e && e[0]=='1' && e[1]=='\0'; }();
    std::atomic<bool> exhausted{false};
    std::mutex mutex;
    bool armed=false;
    uint32_t sequence=0;
    uint32_t stack_samples=0;
    std::atomic<uint64_t> next_scope{0};
    std::chrono::steady_clock::time_point next_probe{};
};
inline State& state() { static State value; return value; }
struct Scope;
inline thread_local Scope* current_scope=nullptr;
inline void entry(const ppu_context*,uint32_t);
struct Scope {
    Scope* previous=nullptr;
    uint64_t invocation=0,mesh=0,context=0;
    bool attached=false;
    Scope(const ppu_context* ctx,uint32_t function) {
        State& s=state();
        if(!ctx || !s.enabled || s.exhausted.load(std::memory_order_relaxed)) return;
        previous=current_scope;mesh=ctx->gpr[3];context=ctx->gpr[4];
        invocation=s.next_scope.fetch_add(1,std::memory_order_relaxed)+1;
        current_scope=this;attached=true;entry(ctx,function);
    }
    ~Scope() { if(attached) current_scope=previous; }
    Scope(const Scope&)=delete;
    Scope& operator=(const Scope&)=delete;
};
inline void entry(const ppu_context* ctx, uint32_t function)
{
    State& s=state();
    if(!ctx || !s.enabled || !s.gate || !*s.gate || s.exhausted.load(std::memory_order_relaxed)) return;
    std::lock_guard<std::mutex> lock(s.mutex);
    if(s.exhausted.load(std::memory_order_relaxed)) return;
    const auto now=std::chrono::steady_clock::now();
    if(!s.armed) {
        if(now<s.next_probe) return;
        s.next_probe=now+std::chrono::milliseconds(10);
        FILE* gate=std::fopen(s.gate,"rb");
        if(!gate) return;
        std::fclose(gate); s.armed=true;
    }
    const auto us=std::chrono::duration_cast<std::chrono::microseconds>(now.time_since_epoch()).count();
    std::fprintf(stderr,
        "[drawproducer] seq=%u mono_us=%llu flip_observed=%u tid=%u fn=0x%08X lr=0x%08X"
        " r3=0x%016llX r4=0x%016llX r5=0x%016llX r6=0x%016llX r7=0x%016llX r8=0x%016llX"
        " invocation=%llu mesh_candidate=0x%016llX context_candidate=0x%016llX\n",
        ++s.sequence,(unsigned long long)us,cellGcm_flip_request_count(),
        (unsigned)ctx->thread_id,function,(uint32_t)ctx->lr,
        (unsigned long long)ctx->gpr[3],(unsigned long long)ctx->gpr[4],
        (unsigned long long)ctx->gpr[5],(unsigned long long)ctx->gpr[6],
        (unsigned long long)ctx->gpr[7],(unsigned long long)ctx->gpr[8],
        (unsigned long long)(current_scope?current_scope->invocation:0),
        (unsigned long long)(current_scope?current_scope->mesh:0),
        (unsigned long long)(current_scope?current_scope->context:0));
#if defined(_WIN32)
    if(function==0x001C0884u && s.stack_samples<3) {
        void* frames[16]={0};
        const USHORT count=CaptureStackBackTrace(0,16,frames,nullptr);
        const uintptr_t base=(uintptr_t)GetModuleHandleW(nullptr);
        ++s.stack_samples;
        std::fprintf(stderr,"[drawproducer-stack] invocation=%llu module=0x%llX rva=",
            (unsigned long long)(current_scope?current_scope->invocation:0),(unsigned long long)base);
        for(USHORT i=0;i<count;i++)std::fprintf(stderr,"%s0x%llX",i?",":"",
            (unsigned long long)((uintptr_t)frames[i]-base));
        std::fprintf(stderr,"\n");
    }
#endif
    if(current_scope && function==0x001D8504u && (uint32_t)ctx->lr==0x001C09ECu) {
        uint32_t words[16]={0},fields[3]={0};
        const uint32_t offsets[3]={0xB8,0xD4,0xEC};unsigned valid=0;
        for(unsigned i=0;i<3;i++) if(current_scope->mesh<=UINT32_MAX-offsets[i] &&
            ppu_diagnostic_read(current_scope->mesh+offsets[i],&fields[i],4)) {
            fields[i]=__builtin_bswap32(fields[i]);valid|=1u<<i;
        }
        /* r4 is the uploaded vec4 count, not an index into the palette. */
        int matrix_valid=ctx->gpr[4]>=4 && ctx->gpr[4]<=220 &&
            ppu_diagnostic_read(ctx->gpr[5],words,sizeof words);
        std::fprintf(stderr,"[drawtransform] seq=%u invocation=%llu mesh_fields_valid=%u b8=0x%08X d4=0x%08X ec=0x%08X vec4_count=%llu first_matrix_valid=%d raw_words=",
            s.sequence,(unsigned long long)current_scope->invocation,valid,fields[0],fields[1],fields[2],
            (unsigned long long)ctx->gpr[4],matrix_valid);
        for(unsigned i=0;i<16;i++) std::fprintf(stderr,"%s%08X",i?",":"",words[i]);
        std::fprintf(stderr,"\n");
    }
    if(s.sequence==4096) {
        s.exhausted.store(true,std::memory_order_relaxed);
        std::fprintf(stderr,"[drawproducer] limit=4096 reached; register observations are not stable object identities\n");
    }
}
}
#endif
