// CPU-only snapshot tests: no game, device, GPU, or guest process.
#include "../scripts/folklore_audio_producer_trace.h"
#include <assert.h>

static unsigned reads;
static uint32_t words[2048];
static uint32_t read_word(uint32_t ea) {
    assert(ea>=0x58004000 && ea<0x58006000 && !(ea&3));
    ++reads; return words[(ea-0x58004000)/4];
}
int main(int argc,char **argv) {
    using namespace folklore_audio_producer_trace;
    assert(argc==2);
    // Runtime-disabled instrumentation must never ask for guest words.
    assert(_putenv_s("FOLKLORE_AUDIO_PRODUCER_TRACE_FILE","")==0);
    emit(P_ENTER,0,2,0x58004000,0,read_word);
    assert(reads==0 && qpc_frequency.QuadPart==0);
    Record r={}; r.kind=P_RETURN;r.write_slot=2;r.address=0x58004000;
    words[0]=0x3f000000; // +0.5
    words[1]=0xc0200000; // -2.5
    words[2]=0x80000000; // -0, not nonzero
    words[3]=0x7f800000; // infinity
    words[4]=0xffc00000; // NaN
    sample_pcm(r,read_word);
    assert(reads==2048 && r.pcm_valid && r.pcm_nonzero==4);
    assert(r.pcm_nonfinite==2 && r.pcm_peak==2.5f);
    assert(r.pcm_hash!=0);
    uint64_t original_hash=r.pcm_hash;
    words[2]=0;sample_pcm(r,read_word);
    assert(reads==4096 && r.pcm_hash!=original_hash && r.pcm_nonzero==4);
    words[2]=0x80000000;
    r.address=0x58004004;sample_pcm(r,read_word);
    assert(reads==4096 && !r.pcm_valid && r.pcm_peak==0 && !r.pcm_hash);
    r.address=0x58004000;r.kind=Q_ENTER;sample_pcm(r,read_word);
    assert(reads==4096 && !r.pcm_valid);
    r.kind=P_ENTER;r.write_slot=16;sample_pcm(r,read_word);
    assert(reads==4096 && !r.pcm_valid);
    // Missing sentinel and expiry are deterministic policy checks, no wait.
    phase_enabled=true;phase_armed=false;phase_poll_us=0;
    phase_request="D:/Codex/does-not-exist-audio-fixture.arm";
    assert(!phase_window(1000) && !phase_armed);
    phase_request=nullptr;phase_poll_us=0;
    assert(phase_window(1000));
    assert(phase_window(64000999) && !phase_window(64001000));
    phase_enabled=false;phase_armed=false;phase_poll_us=0;
    state.store(0); // Reset only the disabled fixture instance, before init.
    assert(_putenv_s("FOLKLORE_AUDIO_PHASE_TRACE","0")==0);
    assert(_putenv_s("FOLKLORE_AUDIO_PCM_TRACE_REQUEST","")==0);
    assert(_putenv_s("FOLKLORE_AUDIO_PRODUCER_TRACE_FILE",argv[1])==0);
    assert(active());
    phase_event(3,1,0x12345678,read_word); // off unless explicit phase opt-in
    assert(reads==4096 && admitted.load()==0);
    assert(_putenv_s("FOLKLORE_AUDIO_PHASE_TRACE","true")==0);
    configure_phase_mode();assert(!phase_enabled);
    assert(_putenv_s("FOLKLORE_AUDIO_PHASE_TRACE","1")==0);
    configure_phase_mode();assert(phase_enabled);
    emit(P_ENTER,1,2,0x58004000,0,read_word);
    phase_event(3,1,0x12345678,read_word);
    phase_event(3,2,0x87654321,read_word);
    assert(phase_callbacks[3]==0x12345678);
    nested_phase_event(7,1,123); // outside phase2: rejected
    assert(admitted.load()==3);
    phase_event(2,1,0,nullptr);
    assert(phase2_context && phase2_producer_id==producer_id);
    const unsigned before_nested_reads=reads;
    phase2_producer_id=producer_id-1;
    nested_phase_event(7,1,123); // another producer call: rejected
    phase2_producer_id=producer_id;
    HANDLE other=CreateThread(nullptr,0,[](LPVOID)->DWORD {
        nested_phase_event(7,1,123);return 0;
    },nullptr,0,nullptr);
    assert(other && WaitForSingleObject(other,3000)==WAIT_OBJECT_0);CloseHandle(other);
    assert(admitted.load()==4); // another native owner thread: rejected
    for(unsigned id=7;id<=12;++id) {
        nested_phase_event(id,1,0x100+id,0,0x11111111);
        nested_phase_event(id,2,0xFFFF,42,0x22222222);
        assert(nested_details[id]==0x100+id);
    }
    assert(nested_callback==0x11111111 && reads==before_nested_reads);
    phase_event(2,2,0,nullptr);
    assert(!phase2_context);
    nested_phase_event(11,1,123); // after phase2: rejected
    emit(P_RETURN,2,2,0x58004000,0,read_word);
    phase_start_us=now_us()-64000000ULL;
    phase_event(3,1,0,read_word);
    emit(P_RETURN,2,2,0x58004000,0,read_word);
    shutdown();
    assert(reads==12288 && admitted.load()==18 && tail.load()==head.load());
    FILE *f=fopen(argv[1],"rb");assert(f);
    char line[512];assert(fgets(line,sizeof(line),f));
    assert(strstr(line,"pcm_valid,pcm_nonzero,pcm_nonfinite,pcm_peak,pcm_end_us"));
    assert(strstr(line,"phase,edge,callback_target,thread_cycles,cycles_valid,pcm_hash,producer_id"));
    assert(strstr(line,"producer_id,detail"));
    unsigned rows=0,callbacks=0,pcm_rows=0,nested_callbacks=0;
    while(fgets(line,sizeof(line),f)) {
        if(line[0]=='#') continue;
        if(strstr(line,",1,4,2,2.5,")) ++pcm_rows;
        ++rows;
        if(strstr(line,",3,1,0x12345678,") || strstr(line,",3,2,0x12345678,")) ++callbacks;
        if(strstr(line,",12,1,0x11111111,") || strstr(line,",12,2,0x11111111,")) ++nested_callbacks;
    }
    fclose(f);assert(rows==18 && pcm_rows==4 && callbacks==2 && nested_callbacks==2);
    puts("PCM/hash, nested no-read context/thread/call guards, arm/expiry and callback/detail retention passed");
    return 0;
}
