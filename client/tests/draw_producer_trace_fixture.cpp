/* No guest VM or renderer: verifies opt-in, gating, limits and register purity. */
#include <cstdint>
struct ppu_context { uint64_t gpr[32]; uint64_t lr; uint32_t thread_id; };
#include "../native/draw_producer_trace.h"
#include <cstring>
#include <thread>
#include <vector>
extern "C" uint32_t cellGcm_flip_request_count(void) { return 7; }
extern "C" int ppu_diagnostic_read(uint64_t,void*,uint32_t) { return 0; }
int main(int argc,char** argv) {
    ppu_context ctx{};
    for(int i=0;i<32;i++) ctx.gpr[i]=0xabcdef1234567800ull+i;
    ctx.lr=0x12345678; ctx.thread_id=9;
    const ppu_context before=ctx;
    using namespace folklore_draw_producer_trace;
    entry(&ctx,0x001DA62C);
    if(state().sequence!=0) return 1;
    if(argc==1) return std::memcmp(&ctx,&before,sizeof(ctx))?2:0;
    FILE* gate=std::fopen(argv[1],"wb");
    if(!gate) return 3;
    std::fclose(gate);
    std::this_thread::sleep_for(std::chrono::milliseconds(12));
    {
        Scope outer(&ctx,0x001C0884);
        if(current_scope!=&outer || outer.mesh!=ctx.gpr[3] || outer.context!=ctx.gpr[4]) return 6;
        {
            Scope inner(&ctx,0x001C0884);
            if(current_scope!=&inner || inner.previous!=&outer || inner.invocation==outer.invocation) return 7;
        }
        if(current_scope!=&outer) return 8;
    }
    if(current_scope) return 9;
    std::vector<std::thread> threads;
    for(int i=0;i<8;i++) threads.emplace_back([&] {
        Scope local(&ctx,0x001C0884);
        for(int n=0;n<1000;n++) entry(&ctx,0x0027A5A4);
    });
    for(auto& thread:threads) thread.join();
    if(state().sequence!=4096 || !state().exhausted.load()) return 4;
    return std::memcmp(&ctx,&before,sizeof(ctx))?5:0;
}
